"""Ditto ingest pipeline (v2, four components).

Two cooperating tasks:

* ``run_ingest`` consumes the single multi-thing Ditto change stream (CONTRACTS §1),
  routes each partial into its per-thing cache by ``thingId``, deep-merges it, and sets
  a dirty flag whenever a ``telemetry`` feature changed.  It also flips + broadcasts the
  ``ditto_connected`` status.

* ``run_frame_flusher`` is a 1 Hz coalescing task (CONTRACTS §3.2): when the dirty flag
  is set it builds ONE v2 telemetry frame from all four caches, runs the sleeper's fast
  rules FIRST, attaches each component's ``status`` from the sleeper's active flags,
  appends the frame to the ring buffer, and broadcasts it.

Both retry / continue forever in the background, so the app serves ``/api/health``
cleanly even when Ditto is completely down.
"""
from __future__ import annotations

import asyncio
import logging

from .agents.sleeper import sleeper
from .config import settings
from .state import app_state, now_iso
from .twin.client import ditto_client
from .twin.sse import ditto_sse

logger = logging.getLogger("ingest")


def _feature_props(thing: dict, feature: str) -> dict:
    features = thing.get("features", {}) if isinstance(thing, dict) else {}
    feat = features.get(feature, {}) or {}
    return feat.get("properties", {}) or {}


def _feature_desired(thing: dict, feature: str) -> dict:
    features = thing.get("features", {}) if isinstance(thing, dict) else {}
    feat = features.get(feature, {}) or {}
    return feat.get("desiredProperties", {}) or {}


def build_v2_frame(caches: dict) -> dict:
    """Flatten the four per-thing caches into a v2 telemetry frame's ``data`` object.

    Shape per CONTRACTS §3.2 (field names load-bearing).  ``status`` is NOT set here —
    the flusher attaches it after the sleeper evaluates the frame.
    """
    tids = settings.thing_ids

    def parts(comp: str):
        thing = caches.get(tids[comp], {})
        telem = _feature_props(thing, "telemetry")
        rep = _feature_props(thing, comp)
        des = _feature_desired(thing, comp)
        return telem, rep, des

    m_t, m_r, m_d = parts("motor")
    p_t, p_r, p_d = parts("pump")
    v_t, v_r, v_d = parts("valve")
    k_t, k_r, k_d = parts("tank")

    ts = m_t.get("ts") or p_t.get("ts") or v_t.get("ts") or k_t.get("ts") or now_iso()

    components = {
        "motor": {
            "rpm": m_t.get("rpm"),
            "temp": m_t.get("temp"),
            "current": m_t.get("current"),
            "rpm_setpoint_reported": m_r.get("rpm_setpoint"),
            "rpm_setpoint_desired": m_d.get("rpm_setpoint"),
        },
        "pump": {
            "flow": p_t.get("flow"),
            "pressure": p_t.get("pressure"),
            "temp": p_t.get("temp"),
            "pump_speed_reported": p_r.get("pump_speed"),
            "pump_speed_desired": p_d.get("pump_speed"),
        },
        "valve": {
            "flow": v_t.get("flow"),
            "position_reported": v_r.get("position"),
            "position_desired": v_d.get("position"),
        },
        "tank": {
            "level_pct": k_t.get("level_pct"),
            "inflow": k_t.get("inflow"),
            "outflow": k_t.get("outflow"),
            "drain_rate_reported": k_r.get("drain_rate"),
            "drain_rate_desired": k_d.get("drain_rate"),
        },
    }
    return {"ts": ts, "components": components}


async def _set_connected(value: bool) -> None:
    if app_state.ditto_connected != value:
        app_state.ditto_connected = value
        await app_state.broadcast(app_state.status_frame())


async def _handle_event(event: dict) -> None:
    if not isinstance(event, dict):
        return
    thing_id = event.get("thingId")
    if not thing_id:
        return
    app_state.merge_twin(thing_id, event)
    features = event.get("features")
    if isinstance(features, dict) and "telemetry" in features:
        # Coalesce: the 1 Hz flusher builds at most one frame per tick.
        app_state.mark_dirty()


async def run_ingest() -> None:
    """Long-running SSE consumer task (started from the app lifespan)."""

    async def on_connect() -> None:
        # Re-GET all four things to reseed the caches after every (re)connection.
        try:
            things = await ditto_client.get_all_things()  # component -> thing | None
            for comp, thing in things.items():
                app_state.seed_twin(settings.thing_ids[comp], thing)
            app_state.mark_dirty()  # emit a fresh frame from the reseeded caches
        except Exception:  # noqa: BLE001 — a failed reseed just means we wait for events
            pass
        await _set_connected(True)

    async def on_disconnect() -> None:
        await _set_connected(False)

    ids = ",".join(settings.thing_ids.values())
    async for event in ditto_sse(
        base_url=settings.ditto_base_url,
        auth=(settings.ditto_user, settings.ditto_pass),
        thing_ids=ids,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
    ):
        try:
            await _handle_event(event)
        except Exception:  # noqa: BLE001 — one bad event must not kill the stream
            pass


async def run_frame_flusher() -> None:
    """1 Hz coalescing flusher (CONTRACTS §3.2), started from the app lifespan."""
    interval = settings.telemetry_interval_s or 1.0
    while True:
        try:
            await asyncio.sleep(interval)
            if not app_state.take_dirty():
                continue
            frame = build_v2_frame(app_state.twin_cache)
            # Rules FIRST, then attach per-component status from the sleeper's flags.
            await sleeper.evaluate(frame)
            status = sleeper.component_status()
            for comp, cdata in frame["components"].items():
                cdata["status"] = status.get(comp, "ok")
            app_state.telemetry.append(frame)
            await app_state.broadcast({"type": "telemetry", "data": frame})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the flusher
            logger.warning("frame flush cycle failed", exc_info=True)
