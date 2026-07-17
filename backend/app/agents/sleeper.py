"""Sleeper — the read-only background monitor.

STRUCTURAL read-only guarantee: this module imports only read paths (state buffers,
the LLM shim, prompts, config). It never imports ``tools`` or any write capability,
so there is no code path here that can mutate the twin.

Two tiers:
  1. Fast threshold rules evaluated on every new telemetry frame, each with a 60s
     re-fire cooldown and a "recovered" observation when the condition clears.
  2. An LLM reflection pass every ``SLEEPER_REFLECT_INTERVAL_S`` seconds — only when
     an API key is configured (rules must work key-less).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

from ..config import llm_config, settings
from ..state import app_state, downsample
from .llm import LLMError, complete
from .prompts import SYSTEM_REFLECT

logger = logging.getLogger("sleeper")

REFIRE_COOLDOWN_S = 60.0
CONVERGENCE_STALL_S = 30.0


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _seconds_between(a: Optional[str], b: Optional[str]) -> Optional[float]:
    da, db = _parse_ts(a), _parse_ts(b)
    if da is None or db is None:
        return None
    return (db - da).total_seconds()


def _parse_reflection(text: str) -> list:
    """Defensively extract the first ``[...]`` JSON array of observation dicts."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


class Sleeper:
    def __init__(self) -> None:
        # Per-rule state: key -> {"active": bool, "last_fire": monotonic-secs | None}
        self._rules: dict[str, dict] = {}
        self._prev_temp: Optional[float] = None
        self._prev_ts: Optional[str] = None
        self._mismatch_since: Optional[float] = None
        self._active_flags: set[str] = set()
        self._lock = asyncio.Lock()

    def _rs(self, key: str) -> dict:
        return self._rules.setdefault(key, {"active": False, "last_fire": None})

    async def _emit(self, severity: str, title: str, detail: str) -> None:
        obs = app_state.observations.add(severity, "rule", title, detail)
        await app_state.broadcast({"type": "observation", "data": obs})

    async def _fire_or_recover(
        self,
        key: str,
        cond: bool,
        severity: str,
        fire_title: str,
        fire_detail: str,
        rec_title: str,
        rec_detail: str,
    ) -> None:
        st = self._rs(key)
        now = time.monotonic()
        if cond:
            self._active_flags.add(key)
            if not st["active"]:
                last = st["last_fire"]
                if last is None or (now - last) >= REFIRE_COOLDOWN_S:
                    st["active"] = True
                    st["last_fire"] = now
                    await self._emit(severity, fire_title, fire_detail)
        else:
            self._active_flags.discard(key)
            if st["active"]:
                st["active"] = False
                await self._emit("recovered", rec_title, rec_detail)

    # ---- tier 1: fast rules --------------------------------------------------
    async def evaluate(self, frame: dict) -> None:
        """Evaluate all fast rules against a freshly ingested telemetry frame."""
        async with self._lock:
            await self._evaluate(frame)

    async def _evaluate(self, frame: dict) -> None:
        temp = frame.get("temperature")
        pressure = frame.get("pressure")
        sp_r = frame.get("pump_speed_reported")
        sp_d = frame.get("pump_speed_desired")
        ts = frame.get("ts")

        if isinstance(temp, (int, float)):
            await self._fire_or_recover(
                "temp_critical",
                temp > 95,
                "critical",
                "Temperature critical (>95°C)",
                f"temperature reached {temp:.1f}°C while pump_speed={sp_r}",
                "Temperature no longer critical",
                f"temperature fell to {temp:.1f}°C",
            )
            await self._fire_or_recover(
                "temp_warn",
                temp > 85,
                "warn",
                "Temperature above 85°C",
                f"temperature reached {temp:.1f}°C while pump_speed={sp_r}",
                "Temperature back below 85°C",
                f"temperature fell to {temp:.1f}°C",
            )

        if isinstance(pressure, (int, float)):
            await self._fire_or_recover(
                "pressure_low",
                pressure < 3.0,
                "warn",
                "Low pressure — possible leak",
                f"pressure dropped to {pressure:.2f} bar",
                "Pressure recovered",
                f"pressure back to {pressure:.2f} bar",
            )

        # Rate of temperature change (°C/s) from the previous frame.
        rate: Optional[float] = None
        if isinstance(temp, (int, float)) and self._prev_temp is not None:
            dt = _seconds_between(self._prev_ts, ts)
            if dt and dt > 0:
                rate = (temp - self._prev_temp) / dt
        if rate is not None:
            await self._fire_or_recover(
                "temp_rate",
                abs(rate) > 2.0,
                "warn",
                "Rapid temperature change",
                f"temperature changing at {rate:+.1f} °C/s",
                "Temperature change stabilized",
                f"rate back to {rate:+.1f} °C/s",
            )

        # Convergence stall: reported vs desired speed mismatch persisting > 30s.
        now = time.monotonic()
        mismatch = (
            isinstance(sp_r, (int, float))
            and isinstance(sp_d, (int, float))
            and sp_r != sp_d
        )
        if mismatch:
            if self._mismatch_since is None:
                self._mismatch_since = now
            persisted = (now - self._mismatch_since) > CONVERGENCE_STALL_S
        else:
            self._mismatch_since = None
            persisted = False
        await self._fire_or_recover(
            "convergence_stall",
            persisted,
            "warn",
            "Convergence stall",
            f"reported pump_speed={sp_r} has not reached desired={sp_d} for >30s",
            "Convergence restored",
            f"reported pump_speed converged to desired ({sp_d})",
        )

        if isinstance(temp, (int, float)):
            self._prev_temp = temp
            self._prev_ts = ts

    # ---- tier 2: LLM reflection ---------------------------------------------
    async def reflect_loop(self) -> None:
        interval = max(1, settings.sleeper_reflect_interval_s)
        while True:
            try:
                await asyncio.sleep(interval)
                await self._reflect_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — reflection must never crash the loop
                logger.warning("reflection cycle failed", exc_info=True)

    async def _reflect_once(self) -> None:
        # Rules work key-less; reflection is skipped silently without a key.
        if not llm_config.has_key():
            return
        points = downsample(app_state.telemetry.window(2), 40)
        if not points:
            return
        payload = {
            "telemetry": points,
            "active_rule_flags": sorted(self._active_flags),
            "recent_observations": app_state.observations.recent(5),
        }
        messages = [
            {"role": "system", "content": SYSTEM_REFLECT},
            {"role": "user", "content": json.dumps(payload)},
        ]
        try:
            # Generous budget: small caps make some models truncate or bail to `[]`
            # on large telemetry payloads (observed with claude-sonnet-5 at 400).
            resp = await complete(messages, tools=None, max_tokens=2000)
        except LLMError as e:
            logger.warning("reflection LLM call failed: %s", e)
            return
        text = resp.choices[0].message.content or ""
        parsed = _parse_reflection(text)
        logger.info("reflection cycle: %d insight(s)", len(parsed))
        for item in parsed[:1]:  # cap at 1 observation per cycle
            sev = item.get("severity")
            if sev not in ("info", "warn", "critical"):
                continue
            title = str(item.get("title", "")).strip() or "Observation"
            detail = str(item.get("detail", "")).strip()
            obs = app_state.observations.add(sev, "llm", title, detail)
            await app_state.broadcast({"type": "observation", "data": obs})


# Module-level singleton shared by ingest (tier 1) and the lifespan task (tier 2).
sleeper = Sleeper()
