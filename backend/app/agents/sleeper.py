"""Sleeper — the read-only background monitor (v2, four components).

STRUCTURAL read-only guarantee: this module imports only read paths (state buffers,
the LLM shim, prompts, config). It never imports ``tools`` or any write capability,
so there is no code path here that can mutate the twin.

Two tiers:
  1. Fast per-component threshold rules (CONTRACTS §3.5) evaluated on every flushed
     telemetry frame, each with a 60s re-fire cooldown and a "recovered" observation
     when the condition clears.  Rule keys are prefixed by component (``motor.*`` etc.);
     cross-component rules use ``component=None``.
  2. An LLM reflection pass every ``SLEEPER_REFLECT_INTERVAL_S`` seconds — only when
     an API key is configured (rules must work key-less).

``component_status()`` collapses the active rule flags into a per-component
``ok|warn|critical`` map that the frame flusher stamps onto each component.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from ..config import llm_config, settings
from ..state import app_state, downsample
from .llm import LLMError, complete
from .prompts import SYSTEM_REFLECT

logger = logging.getLogger("sleeper")

REFIRE_COOLDOWN_S = 60.0

COMPONENTS = ("motor", "pump", "valve", "tank")


def _sev_high(v: float, warn: Optional[float], crit: Optional[float]) -> Optional[str]:
    if crit is not None and v > crit:
        return "critical"
    if warn is not None and v > warn:
        return "warn"
    return None


def _sev_low(v: float, warn: Optional[float], crit: Optional[float]) -> Optional[str]:
    if crit is not None and v < crit:
        return "critical"
    if warn is not None and v < warn:
        return "warn"
    return None


def _fmt(v) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{v:.0f}"
    return "?"


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
        # Per-rule state: key -> {"active": bool, "last_fire": monotonic | None, "severity"}
        self._rules: dict[str, dict] = {}
        # Active flags: key -> (component | None, severity) — the source for status.
        self._active_flags: dict[str, tuple[Optional[str], str]] = {}
        self._lock = asyncio.Lock()

        # Motor RPM-sag slew-aware tracking (grace anchored at last desired change).
        self._motor_sp_desired: Optional[float] = None
        self._motor_change_mono: Optional[float] = None
        self._motor_delta: float = 0.0
        self._sag_since: Optional[float] = None

        # Valve stall (position mismatch persistence).
        self._valve_mismatch_since: Optional[float] = None

        # Cross-component flow-deficit persistence (per blame variant).
        self._deficit_motor_since: Optional[float] = None
        self._deficit_valve_since: Optional[float] = None

    def _rs(self, key: str) -> dict:
        return self._rules.setdefault(
            key, {"active": False, "last_fire": None, "severity": None}
        )

    async def _emit(
        self, severity: str, component: Optional[str], title: str, detail: str
    ) -> None:
        obs = app_state.observations.add(
            severity, "rule", title, detail, component=component
        )
        await app_state.broadcast({"type": "observation", "data": obs})

    async def _fire_or_recover(
        self,
        key: str,
        component: Optional[str],
        severity: Optional[str],
        fire_title: str,
        fire_detail: str,
        rec_title: str,
        rec_detail: str,
    ) -> None:
        """Fire on activation (respecting the cooldown) or on warn->critical escalation;
        emit a ``recovered`` observation when the condition clears.  ``severity`` is
        ``None`` when the condition is not met."""
        st = self._rs(key)
        now = time.monotonic()
        if severity is not None:
            self._active_flags[key] = (component, severity)
            escalated = (
                st["active"] and st.get("severity") == "warn" and severity == "critical"
            )
            last = st["last_fire"]
            cooled = last is None or (now - last) >= REFIRE_COOLDOWN_S
            if (not st["active"] and cooled) or escalated:
                st["active"] = True
                st["severity"] = severity
                st["last_fire"] = now
                await self._emit(severity, component, fire_title, fire_detail)
        else:
            self._active_flags.pop(key, None)
            if st["active"]:
                st["active"] = False
                st["severity"] = None
                await self._emit("recovered", component, rec_title, rec_detail)

    # ---- tier 1: fast rules --------------------------------------------------
    async def evaluate(self, frame: dict) -> None:
        """Evaluate all fast rules against a freshly built telemetry frame."""
        async with self._lock:
            await self._evaluate(frame)

    async def _evaluate(self, frame: dict) -> None:
        comps = frame.get("components", {}) if isinstance(frame, dict) else {}
        motor = comps.get("motor", {}) or {}
        pump = comps.get("pump", {}) or {}
        valve = comps.get("valve", {}) or {}
        tank = comps.get("tank", {}) or {}
        now = time.monotonic()

        # ---- motor: temperature high (warn >70, critical >85) ----
        mtemp = motor.get("temp")
        mrpm = motor.get("rpm")
        if isinstance(mtemp, (int, float)):
            sev = _sev_high(mtemp, 70, 85)
            await self._fire_or_recover(
                "motor.temp", "motor", sev,
                f"Motor temperature {'critical' if sev == 'critical' else 'high'}",
                f"motor temp {mtemp:.1f}°C (rpm {_fmt(mrpm)})",
                "Motor temperature recovered",
                f"motor temp back to {mtemp:.1f}°C",
            )

        # ---- motor: RPM sag (slew-aware, warn only) ----
        sp_r = motor.get("rpm_setpoint_reported")
        sp_d = motor.get("rpm_setpoint_desired")
        if isinstance(sp_d, (int, float)):
            if self._motor_sp_desired is None:
                self._motor_sp_desired = float(sp_d)
                self._motor_change_mono = now
                self._motor_delta = 0.0
            elif float(sp_d) != self._motor_sp_desired:
                self._motor_delta = abs(float(sp_d) - self._motor_sp_desired)
                self._motor_sp_desired = float(sp_d)
                self._motor_change_mono = now
        grace = max(20.0, (self._motor_delta or 0.0) / 60.0 + 5.0)
        sag_now = (
            isinstance(mrpm, (int, float))
            and isinstance(sp_r, (int, float))
            and sp_r > 0
            and mrpm < 0.93 * sp_r
        )
        if sag_now:
            if self._sag_since is None:
                self._sag_since = now
            sustained = now - self._sag_since
            since_change = now - (self._motor_change_mono or now)
            sag_fire = sustained > grace and since_change > grace
        else:
            self._sag_since = None
            sag_fire = False
        pct = 0
        if isinstance(mrpm, (int, float)) and isinstance(sp_r, (int, float)) and sp_r > 0:
            pct = round((1 - mrpm / sp_r) * 100)
        await self._fire_or_recover(
            "motor.sag", "motor", "warn" if sag_fire else None,
            "Motor RPM sag",
            f"rpm {_fmt(mrpm)} vs setpoint {_fmt(sp_r)} ({pct}% below) for >{int(grace)}s",
            "Motor RPM sag cleared",
            f"rpm {_fmt(mrpm)} converged to setpoint {_fmt(sp_r)}",
        )

        # ---- pump: pressure low (leak) / high (deadhead) / temp ----
        ppress = pump.get("pressure")
        if isinstance(ppress, (int, float)):
            await self._fire_or_recover(
                "pump.pressure_low", "pump", "warn" if ppress < 2.5 else None,
                "Pump pressure low — possible leak",
                f"pressure dropped to {ppress:.2f} bar (< 2.5)",
                "Pump pressure recovered",
                f"pressure back to {ppress:.2f} bar",
            )
            sev = _sev_high(ppress, 6.0, 7.5)
            await self._fire_or_recover(
                "pump.pressure_high", "pump", sev,
                f"Pump pressure {'critical' if sev == 'critical' else 'high'} — possible deadhead",
                f"pressure rose to {ppress:.2f} bar",
                "Pump pressure normalized",
                f"pressure back to {ppress:.2f} bar",
            )
        ptemp = pump.get("temp")
        if isinstance(ptemp, (int, float)):
            await self._fire_or_recover(
                "pump.temp", "pump", "warn" if ptemp > 75 else None,
                "Pump temperature high",
                f"pump temp {ptemp:.1f}°C (> 75)",
                "Pump temperature recovered",
                f"pump temp back to {ptemp:.1f}°C",
            )

        # ---- valve: stall (reported != desired sustained > 15s) ----
        vpr = valve.get("position_reported")
        vpd = valve.get("position_desired")
        mism = (
            isinstance(vpr, (int, float))
            and isinstance(vpd, (int, float))
            and abs(vpr - vpd) > 2
        )
        if mism:
            if self._valve_mismatch_since is None:
                self._valve_mismatch_since = now
            stall_fire = (now - self._valve_mismatch_since) > 15.0
        else:
            self._valve_mismatch_since = None
            stall_fire = False
        await self._fire_or_recover(
            "valve.stall", "valve", "warn" if stall_fire else None,
            "Valve stall",
            f"reported position {_fmt(vpr)} has not reached desired {_fmt(vpd)} for >15s",
            "Valve stall cleared",
            f"valve position converged to desired ({_fmt(vpd)})",
        )

        # ---- tank: level low / high ----
        lvl = tank.get("level_pct")
        if isinstance(lvl, (int, float)):
            sev = _sev_low(lvl, 40, 30)
            await self._fire_or_recover(
                "tank.level_low", "tank", sev,
                f"Tank level {'critical' if sev == 'critical' else 'low'}",
                f"level {lvl:.1f}% (inflow {_fmt(tank.get('inflow'))} / "
                f"outflow {_fmt(tank.get('outflow'))} L/min)",
                "Tank level recovered",
                f"level back to {lvl:.1f}%",
            )
            sev2 = _sev_high(lvl, 90, 95)
            await self._fire_or_recover(
                "tank.level_high", "tank", sev2,
                f"Tank level {'critical' if sev2 == 'critical' else 'high'}",
                f"level {lvl:.1f}% (inflow {_fmt(tank.get('inflow'))} / "
                f"outflow {_fmt(tank.get('outflow'))} L/min)",
                "Tank level normalized",
                f"level back to {lvl:.1f}%",
            )

        # ---- cross-component: flow deficit (component=None) ----
        speed_r = pump.get("pump_speed_reported")
        pos_r = valve.get("position_reported")
        pos_d = valve.get("position_desired")
        flow = valve.get("flow")
        if flow is None:
            flow = pump.get("flow")

        expected = None
        if all(isinstance(x, (int, float)) for x in (sp_r, speed_r, pos_r)):
            expected = 200.0 * (sp_r / 1800.0) * (speed_r / 100.0) * (pos_r / 100.0)
        deficit = (
            expected is not None
            and expected > 20
            and isinstance(flow, (int, float))
            and flow < 0.8 * expected
        )
        # Motor-blame is gated on the slew-aware sag rule being ACTIVE, so a clean
        # setpoint ramp (transient sag) never trips a false root-cause observation.
        motor_sag_active = self._rs("motor.sag")["active"]
        motor_converged = (
            isinstance(mrpm, (int, float))
            and isinstance(sp_r, (int, float))
            and sp_r > 0
            and mrpm >= 0.93 * sp_r
        )
        valve_mismatch = (
            isinstance(pos_r, (int, float))
            and isinstance(pos_d, (int, float))
            and abs(pos_r - pos_d) > 2
        )

        cond_m = bool(deficit and motor_sag_active)
        if cond_m:
            if self._deficit_motor_since is None:
                self._deficit_motor_since = now
            fire_m = (now - self._deficit_motor_since) > 5.0
        else:
            self._deficit_motor_since = None
            fire_m = False
        await self._fire_or_recover(
            "system.flow_deficit_motor", None, "warn" if fire_m else None,
            "Flow deficit — root cause upstream: motor",
            f"line flow {_fmt(flow)} L/min vs expected {_fmt(expected)} while rpm "
            f"{_fmt(mrpm)} sags below setpoint {_fmt(sp_r)}",
            "Flow deficit cleared",
            f"line flow recovered to {_fmt(flow)} L/min",
        )

        cond_v = bool(deficit and motor_converged and valve_mismatch)
        if cond_v:
            if self._deficit_valve_since is None:
                self._deficit_valve_since = now
            fire_v = (now - self._deficit_valve_since) > 5.0
        else:
            self._deficit_valve_since = None
            fire_v = False
        await self._fire_or_recover(
            "system.flow_deficit_valve", None, "warn" if fire_v else None,
            "Flow deficit — root cause: valve",
            f"line flow {_fmt(flow)} L/min vs expected {_fmt(expected)}; motor converged "
            f"but valve at {_fmt(pos_r)} not desired {_fmt(pos_d)}",
            "Flow deficit cleared",
            f"line flow recovered to {_fmt(flow)} L/min",
        )

    # ---- status derivation ---------------------------------------------------
    def component_status(self) -> dict:
        """Collapse active rule flags into a per-component ``ok|warn|critical`` map.

        Any active critical rule for a component -> ``critical``; else any active warn
        -> ``warn``.  Cross-component (``component=None``) flags do not map to a node.
        """
        status = {c: "ok" for c in COMPONENTS}
        for component, severity in self._active_flags.values():
            if component is None or component not in status:
                continue
            if severity == "critical":
                status[component] = "critical"
            elif severity == "warn" and status[component] != "critical":
                status[component] = "warn"
        return status

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
        points = downsample(app_state.telemetry.window(2), 30)
        if not points:
            return
        payload = {
            "frames": points,
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
            component = item.get("component")
            if component not in COMPONENTS:
                component = None
            obs = app_state.observations.add(
                sev, "llm", title, detail, component=component
            )
            await app_state.broadcast({"type": "observation", "data": obs})


# Module-level singleton shared by the flusher (tier 1) and the lifespan task (tier 2).
sleeper = Sleeper()
