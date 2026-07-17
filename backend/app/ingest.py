"""Ditto ingest pipeline.

Consumes the Ditto change stream (CONTRACTS §1), deep-merges each partial into the
twin cache, and — whenever ``features/telemetry`` changed — builds the flat telemetry
frame (CONTRACTS §3), appends it to the ring buffer, broadcasts it over WS, and feeds
it to the sleeper's fast rules.  Also flips + broadcasts the ``ditto_connected`` status.

The consumer retries forever in the background, so the app serves ``/api/health``
cleanly even when Ditto is completely down.
"""
from __future__ import annotations

from .agents.sleeper import sleeper
from .config import settings
from .state import app_state
from .twin.client import ditto_client
from .twin.sse import ditto_sse


def build_telemetry_frame(cache: dict) -> dict:
    """Flatten the merged twin cache into a telemetry frame (CONTRACTS §3 shape)."""
    features = cache.get("features", {}) if isinstance(cache, dict) else {}
    telem = features.get("telemetry", {}).get("properties", {})
    pump = features.get("pump", {})
    reported = pump.get("properties", {})
    desired = pump.get("desiredProperties", {})
    return {
        "ts": telem.get("ts"),
        "temperature": telem.get("temperature"),
        "pressure": telem.get("pressure"),
        "flow_rate": telem.get("flow_rate"),
        "pump_speed_reported": reported.get("pump_speed"),
        "valve_state_reported": reported.get("valve_state"),
        "pump_speed_desired": desired.get("pump_speed"),
        "valve_state_desired": desired.get("valve_state"),
    }


async def _set_connected(value: bool) -> None:
    if app_state.ditto_connected != value:
        app_state.ditto_connected = value
        await app_state.broadcast(app_state.status_frame())


async def _handle_event(event: dict) -> None:
    if not isinstance(event, dict):
        return
    app_state.merge_twin(event)
    features = event.get("features")
    if isinstance(features, dict) and "telemetry" in features:
        frame = build_telemetry_frame(app_state.twin_cache)
        app_state.telemetry.append(frame)
        await app_state.broadcast({"type": "telemetry", "data": frame})
        await sleeper.evaluate(frame)


async def run_ingest() -> None:
    """Long-running SSE consumer task (started from the app lifespan)."""

    async def on_connect() -> None:
        # Re-GET the full thing to reseed the cache after every (re)connection.
        try:
            thing = await ditto_client.get_thing()
            app_state.seed_twin(thing)
        except Exception:  # noqa: BLE001 — a failed reseed just means we wait for events
            pass
        await _set_connected(True)

    async def on_disconnect() -> None:
        await _set_connected(False)

    async for event in ditto_sse(
        base_url=settings.ditto_base_url,
        auth=(settings.ditto_user, settings.ditto_pass),
        thing_id=settings.thing_id,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
    ):
        try:
            await _handle_event(event)
        except Exception:  # noqa: BLE001 — one bad event must not kill the stream
            pass
