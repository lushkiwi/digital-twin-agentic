"""In-process application state: telemetry ring buffer, observation store, merged
twin cache, and the broadcast choke point that fans frames out to the WS hub.

This module has no dependency on the WS layer — the WS ``ConnectionManager``
registers itself as the broadcaster at startup (dependency inversion), which keeps
``state`` importable by the read-only sleeper without pulling in any write path.
"""
from __future__ import annotations

import copy
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional


def now_iso() -> str:
    """Current UTC time as an ISO8601 ``...Z`` string (matches CONTRACTS examples)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def deep_merge(dst: dict, partial: dict) -> dict:
    """Recursively merge ``partial`` into ``dst`` (mutating ``dst``) and return it.

    Nested dicts are merged key-by-key; every other value overwrites.  This is the
    Ditto SSE merge required by CONTRACTS §1.
    """
    for key, value in partial.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def downsample(points: list, max_points: int) -> list:
    """Evenly pick at most ``max_points`` items, always keeping the newest one."""
    n = len(points)
    if max_points <= 0 or n <= max_points:
        return list(points)
    # Evenly spaced indices across the range, inclusive of the last element.
    step = (n - 1) / (max_points - 1) if max_points > 1 else n
    idxs = sorted({int(round(i * step)) for i in range(max_points)})
    if idxs[-1] != n - 1:
        idxs[-1] = n - 1
    return [points[i] for i in idxs]


class TelemetryBuffer:
    """Ring buffer of flat telemetry frame dicts (CONTRACTS §3)."""

    def __init__(self, maxlen: int = 600) -> None:
        self._buf: deque = deque(maxlen=maxlen)

    def append(self, frame: dict) -> None:
        self._buf.append(frame)

    def latest(self) -> Optional[dict]:
        return self._buf[-1] if self._buf else None

    def all(self) -> list:
        return list(self._buf)

    def window(self, minutes: int) -> list:
        """Frames within the last ``minutes`` (oldest first)."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        out = []
        for frame in self._buf:
            ts = _parse_ts(frame.get("ts", ""))
            if ts is None or ts >= cutoff:
                out.append(frame)
        return out


class ObservationStore:
    """Ring buffer of observations with monotonically increasing ``obs-N`` ids."""

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque = deque(maxlen=maxlen)
        self._counter = 0

    def add(
        self,
        severity: str,
        source: str,
        title: str,
        detail: str,
        ts: Optional[str] = None,
    ) -> dict:
        self._counter += 1
        obs = {
            "id": f"obs-{self._counter}",
            "ts": ts or now_iso(),
            "severity": severity,
            "source": source,
            "title": title,
            "detail": detail,
        }
        self._buf.append(obs)
        return obs

    def recent(self, limit: int) -> list:
        """Most recent ``limit`` observations, oldest first."""
        items = list(self._buf)
        if limit > 0:
            items = items[-limit:]
        return items


class AppState:
    """Shared mutable app state + broadcast choke point."""

    def __init__(self) -> None:
        self.telemetry = TelemetryBuffer(maxlen=600)
        self.observations = ObservationStore(maxlen=200)
        self.twin_cache: dict = {}
        self.ditto_connected: bool = False
        self._broadcaster: Optional[Callable[[dict], Awaitable[None]]] = None

    # ---- broadcast wiring ----------------------------------------------------
    def set_broadcaster(self, fn: Callable[[dict], Awaitable[None]]) -> None:
        self._broadcaster = fn

    async def broadcast(self, frame: dict) -> None:
        if self._broadcaster is not None:
            await self._broadcaster(frame)

    # ---- twin cache ----------------------------------------------------------
    def seed_twin(self, thing: dict) -> None:
        """Replace the cached thing wholesale (used after a full GET)."""
        self.twin_cache = copy.deepcopy(thing) if thing else {}

    def merge_twin(self, partial: dict) -> None:
        deep_merge(self.twin_cache, partial)

    def status_frame(self) -> dict:
        return {"type": "status", "data": {"ditto_connected": self.ditto_connected}}


# Module-level singleton shared across the app.
app_state = AppState()
