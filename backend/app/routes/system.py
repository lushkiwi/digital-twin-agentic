"""System REST endpoints: health, observations, telemetry, system, params (CONTRACTS §3)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..agents.tools import build_system_state
from ..params import registry_public_dict
from ..state import app_state

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    return {"ok": True, "ditto_connected": app_state.ditto_connected}


@router.get("/api/observations")
async def observations(limit: int = Query(50, ge=1, le=200)) -> dict:
    return {"observations": app_state.observations.recent(limit)}


@router.get("/api/telemetry")
async def telemetry(minutes: int = Query(5, ge=1, le=10)) -> dict:
    # Points are v2 frame data objects (oldest first) — CONTRACTS §3.3.
    return {"points": app_state.telemetry.window(minutes)}


@router.get("/api/system")
async def system() -> dict:
    # Same builder the LLM's get_system_state tool uses (CONTRACTS §3.3).
    return await build_system_state()


@router.get("/api/params")
async def params() -> dict:
    # Writable-parameter registry payload for the frontend drawer (CONTRACTS §3.3).
    return registry_public_dict()
