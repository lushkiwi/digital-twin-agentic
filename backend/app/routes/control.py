"""Manual operator control endpoint (CONTRACTS §3.3).

``POST /api/control/{component}/{param}`` routes an operator write through the SAME
``tools.execute()`` safety boundary as the LLM's write tools — the executor stays the
only writer.  On success it emits an ``operator`` observation; on a validation failure
it returns ``{"ok": false, "error": ...}`` with HTTP 200 and never touches Ditto.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents import tools
from ..params import by_component_param
from ..state import app_state

router = APIRouter()


class ControlBody(BaseModel):
    value: int
    reason: Optional[str] = None


@router.post("/api/control/{component}/{param}")
async def control(component: str, param: str, body: ControlBody) -> dict:
    registry = by_component_param()
    p = registry.get((component, param))
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown component/param: {component}/{param}",
        )

    reason = (body.reason or "").strip() or "manual operator adjustment"
    args = {p.arg_name: body.value, "reason": reason}

    # Same executor as LLM writes: validates bounds, never touches Ditto on failure.
    outcome = await tools.execute(p.tool_name, args)
    if not outcome.ok:
        return {"ok": False, "error": outcome.error}

    title = f"Operator set {p.label} → {body.value}{p.unit}"
    obs = app_state.observations.add(
        "info", "operator", title, reason, component=component
    )
    await app_state.broadcast({"type": "observation", "data": obs})

    return {
        "ok": True,
        "ditto_status": outcome.ditto_status,
        "ditto_request": outcome.ditto_request,
    }
