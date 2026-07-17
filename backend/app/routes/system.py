"""System REST endpoints: health, observations, telemetry (CONTRACTS §3)."""
from __future__ import annotations

from fastapi import APIRouter, Query

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
    return {"points": app_state.telemetry.window(minutes)}
