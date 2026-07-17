"""Async multi-thing Ditto I/O for the device simulator (CONTRACTS.md §1 + §2).

v2: four independent things (one per component: motor, pump, valve, tank), all synced
by ONE `DittoIO` instance sharing one httpx client. Background loops:
  - telemetry + reported PUT for all four things every tick (~8 PUTs/s), dispatched via
    `asyncio.gather(..., return_exceptions=True)` so one failing PUT never stalls the tick.
  - ONE SSE stream covering all four thing ids for desired-state changes, routed by
    `thingId` into per-thing deep-merged caches, with exponential backoff reconnect
    (1s -> 30s cap) and a 60s no-data watchdog that forces a reconnect.
  - a 2s polling fallback iterating the four desired-properties endpoints, active only
    while the SSE stream is down.

Ditto being unreachable must never crash the sim or stop the physics loop: every request
is wrapped, failures only flip a `ditto_connected` flag (logged once per transition) and
each loop retries on its own cadence.
"""
import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from physics import SystemPhysics

logger = logging.getLogger("ditto_io")

POLL_INTERVAL_S = 2.0
BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 30.0
SSE_WATCHDOG_S = 60.0

COMPONENTS = ("motor", "pump", "valve", "tank")

# Actuator feature name per component == component id (CONTRACTS.md §1 table), and the
# single writable desiredProperties key each feature holds.
FEATURE_NAME = {"motor": "motor", "pump": "pump", "valve": "valve", "tank": "tank"}
PARAM_NAME = {
    "motor": "rpm_setpoint",
    "pump": "pump_speed",
    "valve": "position",
    "tank": "drain_rate",
}


@dataclass
class DittoConfig:
    base_url: str = "http://localhost:8080"
    user: str = "ditto"
    password: str = "ditto"
    thing_ns: str = "org.acme"
    telemetry_interval_s: float = 1.0

    @classmethod
    def from_env(cls) -> "DittoConfig":
        d = cls()
        return cls(
            base_url=os.environ.get("DITTO_BASE_URL", d.base_url),
            user=os.environ.get("DITTO_USER", d.user),
            password=os.environ.get("DITTO_PASS", d.password),
            thing_ns=os.environ.get("THING_NS", d.thing_ns),
            telemetry_interval_s=float(os.environ.get("TELEMETRY_INTERVAL_S", d.telemetry_interval_s)),
        )


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge `src` into `dst` in place; returns `dst`."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


class DittoIO:
    """Owns the httpx client and background tasks that sync `physics` (a `SystemPhysics`)
    with the four per-component things in Ditto."""

    def __init__(self, physics: SystemPhysics, config: DittoConfig):
        self.physics = physics
        self.config = config
        self.connected = False
        self._sse_active = False

        # component -> (thing_id, feature_name)
        self.things: dict[str, tuple[str, str]] = {
            component: (f"{config.thing_ns}:{component}-01", FEATURE_NAME[component])
            for component in COMPONENTS
        }
        self._id_to_component = {tid: component for component, (tid, _feat) in self.things.items()}

        self._merged_things: dict[str, Any] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._tasks: list[asyncio.Task] = []

    @property
    def ditto_connected(self) -> bool:
        return self.connected

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(auth=(self.config.user, self.config.password), timeout=10.0)
        self._tasks = [
            asyncio.create_task(self._put_loop(), name="ditto-put"),
            asyncio.create_task(self._desired_watch_loop(), name="ditto-desired-watch"),
            asyncio.create_task(self._poll_fallback_loop(), name="ditto-poll-fallback"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _set_connected(self, value: bool) -> None:
        if value == self.connected:
            return
        self.connected = value
        logger.warning("ditto: %s", "connected" if value else "disconnected")

    # -- telemetry / reported PUT loop (all four things, every tick) --

    async def _put_telemetry(self, component: str) -> bool:
        thing_id, _feature = self.things[component]
        path = f"/api/2/things/{thing_id}/features/telemetry/properties"
        try:
            resp = await self._client.put(self._url(path), json=self.physics.telemetry(component))
            resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.debug("telemetry PUT failed for %s: %s", component, exc)
            return False

    async def _put_reported(self, component: str) -> bool:
        thing_id, feature = self.things[component]
        path = f"/api/2/things/{thing_id}/features/{feature}/properties"
        try:
            resp = await self._client.put(self._url(path), json=self.physics.reported(component))
            resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.debug("reported PUT failed for %s: %s", component, exc)
            return False

    async def _put_loop(self) -> None:
        interval = self.config.telemetry_interval_s
        while True:
            coros = []
            for component in COMPONENTS:
                coros.append(self._put_telemetry(component))
                coros.append(self._put_reported(component))
            results = await asyncio.gather(*coros, return_exceptions=True)
            self._set_connected(any(r is True for r in results))
            await asyncio.sleep(interval)

    # -- desired-state watch: SSE (all four ids, one stream) + polling fallback --

    def _apply_desired_for(self, component: str) -> None:
        thing_id, feature = self.things[component]
        thing = self._merged_things.get(thing_id)
        if not thing:
            return
        try:
            desired = thing["features"][feature]["desiredProperties"]
        except (KeyError, TypeError):
            return
        if not isinstance(desired, dict):
            return
        param = PARAM_NAME[component]
        self.physics.set_desired(component, **{param: desired.get(param)})

    async def _resync_all_things(self) -> None:
        """Re-GET all four things (CONTRACTS.md §1: 4 GETs via asyncio.gather). Always
        called on (re)connect since SSE events can be missed during subscription setup."""
        coros = [self._client.get(self._url(f"/api/2/things/{tid}")) for tid, _feat in self.things.values()]
        responses = await asyncio.gather(*coros, return_exceptions=True)
        any_success = False
        for (component, (tid, _feat)), resp in zip(self.things.items(), responses):
            if isinstance(resp, Exception):
                logger.debug("GET %s failed: %s", tid, resp)
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.debug("GET %s failed: %s", tid, exc)
                continue
            self._merged_things[tid] = resp.json()
            any_success = True
        if not any_success:
            raise httpx.ConnectError("all thing GETs failed during resync")
        for component in COMPONENTS:
            self._apply_desired_for(component)

    async def _desired_watch_loop(self) -> None:
        backoff = BACKOFF_INITIAL_S
        ids_param = ",".join(tid for tid, _feat in self.things.values())
        url = self._url(f"/api/2/things?ids={ids_param}&fields=thingId,features")
        headers = {"Accept": "text/event-stream"}
        while True:
            try:
                await self._resync_all_things()
                self._set_connected(True)
                backoff = BACKOFF_INITIAL_S
                self._sse_active = True
                async with self._client.stream("GET", url, headers=headers, timeout=None) as resp:
                    resp.raise_for_status()
                    line_iter = resp.aiter_lines().__aiter__()
                    while True:
                        try:
                            line = await asyncio.wait_for(line_iter.__anext__(), timeout=SSE_WATCHDOG_S)
                        except asyncio.TimeoutError:
                            logger.debug("SSE watchdog: no data in %ss, forcing reconnect", SSE_WATCHDOG_S)
                            break
                        except StopAsyncIteration:
                            break
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data:
                            continue  # keep-alive
                        try:
                            partial = json.loads(data)
                        except json.JSONDecodeError:
                            logger.debug("SSE: unparseable data line, ignoring")
                            continue
                        thing_id = partial.get("thingId")
                        if not thing_id:
                            continue
                        existing = self._merged_things.setdefault(thing_id, {})
                        _deep_merge(existing, partial)
                        component = self._id_to_component.get(thing_id)
                        if component:
                            self._apply_desired_for(component)
            except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
                logger.debug("SSE connection error: %s", exc)
            finally:
                self._sse_active = False
            self._set_connected(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _poll_one(self, component: str) -> bool:
        thing_id, feature = self.things[component]
        path = f"/api/2/things/{thing_id}/features/{feature}/desiredProperties"
        try:
            resp = await self._client.get(self._url(path))
            resp.raise_for_status()
            desired = resp.json()
            if isinstance(desired, dict):
                param = PARAM_NAME[component]
                self.physics.set_desired(component, **{param: desired.get(param)})
            return True
        except httpx.HTTPError as exc:
            logger.debug("desired poll fallback failed for %s: %s", component, exc)
            return False

    async def _poll_fallback_loop(self) -> None:
        while True:
            if not self._sse_active:
                results = await asyncio.gather(
                    *(self._poll_one(component) for component in COMPONENTS), return_exceptions=True
                )
                self._set_connected(any(r is True for r in results))
            await asyncio.sleep(POLL_INTERVAL_S)
