"""Async Ditto I/O for the device simulator (CONTRACTS.md §1 + §2).

Four independent background loops, all sharing one httpx client:
  - telemetry PUT (1 Hz)
  - reported pump-properties PUT (every tick)
  - desired-state SSE watcher (deep-merged cache, exponential backoff reconnect)
  - 2s polling fallback for desired properties, active only while SSE is down

Ditto being unreachable must never crash the sim or stop the physics loop: every request
is wrapped, failures only flip a `ditto_connected` flag (logged once per transition) and
the loop retries on its own cadence.
"""
import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from physics import PumpPhysics

logger = logging.getLogger("ditto_io")

POLL_INTERVAL_S = 2.0
BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 30.0


@dataclass
class DittoConfig:
    base_url: str = "http://localhost:8080"
    user: str = "ditto"
    password: str = "ditto"
    thing_id: str = "org.acme:pump-01"
    telemetry_interval_s: float = 1.0

    @classmethod
    def from_env(cls) -> "DittoConfig":
        d = cls()
        return cls(
            base_url=os.environ.get("DITTO_BASE_URL", d.base_url),
            user=os.environ.get("DITTO_USER", d.user),
            password=os.environ.get("DITTO_PASS", d.password),
            thing_id=os.environ.get("THING_ID", d.thing_id),
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
    """Owns the httpx client and background tasks that sync `physics` with Ditto."""

    def __init__(self, physics: PumpPhysics, config: DittoConfig):
        self.physics = physics
        self.config = config
        self.connected = False
        self._sse_active = False
        self._merged_thing: dict[str, Any] = {}
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
            asyncio.create_task(self._telemetry_loop(), name="ditto-telemetry"),
            asyncio.create_task(self._reported_loop(), name="ditto-reported"),
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

    # -- telemetry / reported PUT loops --

    async def _telemetry_loop(self) -> None:
        interval = self.config.telemetry_interval_s
        path = f"/api/2/things/{self.config.thing_id}/features/telemetry/properties"
        while True:
            try:
                resp = await self._client.put(self._url(path), json=self.physics.telemetry)
                resp.raise_for_status()
                self._set_connected(True)
            except httpx.HTTPError as exc:
                self._set_connected(False)
                logger.debug("telemetry PUT failed: %s", exc)
            await asyncio.sleep(interval)

    async def _reported_loop(self) -> None:
        interval = self.config.telemetry_interval_s
        path = f"/api/2/things/{self.config.thing_id}/features/pump/properties"
        while True:
            try:
                resp = await self._client.put(self._url(path), json=self.physics.reported)
                resp.raise_for_status()
                self._set_connected(True)
            except httpx.HTTPError as exc:
                self._set_connected(False)
                logger.debug("reported PUT failed: %s", exc)
            await asyncio.sleep(interval)

    # -- desired-state watch: SSE + polling fallback --

    def _apply_desired_from_cache(self) -> None:
        try:
            desired = self._merged_thing["features"]["pump"]["desiredProperties"]
        except (KeyError, TypeError):
            return
        self.physics.set_desired(
            pump_speed=desired.get("pump_speed"),
            valve_state=desired.get("valve_state"),
        )

    async def _resync_full_thing(self) -> None:
        resp = await self._client.get(self._url(f"/api/2/things/{self.config.thing_id}"))
        resp.raise_for_status()
        self._merged_thing = resp.json()
        self._apply_desired_from_cache()

    async def _desired_watch_loop(self) -> None:
        backoff = BACKOFF_INITIAL_S
        url = self._url(f"/api/2/things?ids={self.config.thing_id}&fields=thingId,features")
        headers = {"Accept": "text/event-stream"}
        while True:
            try:
                await self._resync_full_thing()
                self._set_connected(True)
                backoff = BACKOFF_INITIAL_S
                self._sse_active = True
                async with self._client.stream("GET", url, headers=headers, timeout=None) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
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
                        _deep_merge(self._merged_thing, partial)
                        self._apply_desired_from_cache()
            except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
                logger.debug("SSE connection error: %s", exc)
            finally:
                self._sse_active = False
            self._set_connected(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _poll_fallback_loop(self) -> None:
        path = f"/api/2/things/{self.config.thing_id}/features/pump/desiredProperties"
        while True:
            if not self._sse_active:
                try:
                    resp = await self._client.get(self._url(path))
                    resp.raise_for_status()
                    desired = resp.json()
                    self.physics.set_desired(
                        pump_speed=desired.get("pump_speed"),
                        valve_state=desired.get("valve_state"),
                    )
                    self._set_connected(True)
                except httpx.HTTPError as exc:
                    self._set_connected(False)
                    logger.debug("desired poll fallback failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_S)
