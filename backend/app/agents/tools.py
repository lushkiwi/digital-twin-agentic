"""Tool layer — the safety boundary for the interactive agent.

Every tool call from the LLM is validated against a strict Pydantic model enforcing
the CONTRACTS §3 bounds BEFORE anything executes.  An unknown tool or a validation
failure produces a structured error ``ToolOutcome`` (ok=False) that is handed back to
the model as the tool result — it is never executed and never raises.

The write tools live here (this is the write-capable module).  The sleeper must NOT
import this module.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Tuple, Type

from pydantic import BaseModel, Field, ValidationError

from ..state import app_state, downsample
from ..twin.client import ditto_client


# --------------------------------------------------------------------------- #
# Outcome type
# --------------------------------------------------------------------------- #
@dataclass
class ToolOutcome:
    ok: bool
    result: Any = None
    ditto_request: Optional[dict] = None
    ditto_status: Optional[int] = None
    error: Optional[str] = None

    def llm_content(self) -> dict:
        """The JSON content shown to the model as this tool's result."""
        if self.ok:
            return {"ok": True, "result": self.result}
        return {"ok": False, "error": self.error}


# --------------------------------------------------------------------------- #
# Per-tool argument models (the enforced bounds)
# --------------------------------------------------------------------------- #
class NoArgs(BaseModel):
    model_config = {"extra": "ignore"}


class TelemetryWindowArgs(BaseModel):
    model_config = {"extra": "ignore"}
    minutes: int = Field(ge=1, le=10)


class ObservationsArgs(BaseModel):
    model_config = {"extra": "ignore"}
    limit: int = Field(ge=1, le=20)


class SetPumpSpeedArgs(BaseModel):
    model_config = {"extra": "forbid"}
    speed: int = Field(ge=0, le=100)
    reason: str


class SetValveStateArgs(BaseModel):
    model_config = {"extra": "forbid"}
    state: Literal["open", "closed"]
    reason: str


class RunStressTestArgs(BaseModel):
    model_config = {"extra": "forbid"}
    profile: Literal["thermal", "pressure"]
    duration_s: int = Field(ge=10, le=120)


# --------------------------------------------------------------------------- #
# OpenAI-format tool JSON schemas
# --------------------------------------------------------------------------- #
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_twin_state",
            "description": "Read the full twin: reported and desired pump state plus latest telemetry.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_telemetry_window",
            "description": "Recent telemetry from the ring buffer, downsampled to <=60 points.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "How many minutes back to fetch (1-10).",
                    }
                },
                "required": ["minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_observations",
            "description": "Recent anomaly observations raised by the background sleeper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "How many recent observations to return (1-20).",
                    }
                },
                "required": ["limit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_pump_speed",
            "description": "Write the desired pump speed (0-100). Reported speed slews toward it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "speed": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reason": {"type": "string", "description": "Why this change is needed."},
                },
                "required": ["speed", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_valve_state",
            "description": "Write the desired valve state (open or closed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": ["open", "closed"]},
                    "reason": {"type": "string", "description": "Why this change is needed."},
                },
                "required": ["state", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_stress_test",
            "description": (
                "Start a background stress ramp: snapshot current desired speed, ramp to 90 "
                "in steps of 10, hold, then restore. Returns immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string", "enum": ["thermal", "pressure"]},
                    "duration_s": {"type": "integer", "minimum": 10, "maximum": 120},
                },
                "required": ["profile", "duration_s"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
async def _get_twin_state(_: NoArgs) -> ToolOutcome:
    try:
        thing = await ditto_client.get_thing()
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(ok=False, error=f"Could not read twin: {exc}")
    features = thing.get("features", {}) if isinstance(thing, dict) else {}
    pump = features.get("pump", {})
    telemetry = features.get("telemetry", {}).get("properties", {})
    return ToolOutcome(
        ok=True,
        result={
            "reported": pump.get("properties", {}),
            "desired": pump.get("desiredProperties", {}),
            "telemetry": telemetry,
        },
    )


async def _get_telemetry_window(args: TelemetryWindowArgs) -> ToolOutcome:
    points = app_state.telemetry.window(args.minutes)
    points = downsample(points, 60)
    return ToolOutcome(ok=True, result={"minutes": args.minutes, "count": len(points), "points": points})


async def _get_observations(args: ObservationsArgs) -> ToolOutcome:
    obs = app_state.observations.recent(args.limit)
    return ToolOutcome(ok=True, result={"observations": obs})


async def _set_pump_speed(args: SetPumpSpeedArgs) -> ToolOutcome:
    status, body, request_desc = await ditto_client.put_desired("pump_speed", args.speed)
    ok = 200 <= status < 300
    return ToolOutcome(
        ok=ok,
        result={"desired_pump_speed": args.speed, "reason": args.reason, "response": body},
        ditto_request=request_desc,
        ditto_status=status,
        error=None if ok else f"Ditto write failed (status {status}).",
    )


async def _set_valve_state(args: SetValveStateArgs) -> ToolOutcome:
    status, body, request_desc = await ditto_client.put_desired("valve_state", args.state)
    ok = 200 <= status < 300
    return ToolOutcome(
        ok=ok,
        result={"desired_valve_state": args.state, "reason": args.reason, "response": body},
        ditto_request=request_desc,
        ditto_status=status,
        error=None if ok else f"Ditto write failed (status {status}).",
    )


# Keep references to background stress tasks so they aren't garbage-collected.
_stress_tasks: set = set()


def _current_desired_speed() -> int:
    """Best-effort read of the current desired pump speed from the twin cache."""
    try:
        pump = app_state.twin_cache.get("features", {}).get("pump", {})
        val = pump.get("desiredProperties", {}).get("pump_speed")
        if isinstance(val, (int, float)):
            return int(val)
    except Exception:  # noqa: BLE001
        pass
    return 60  # thing.json default


async def _stress_ramp(profile: str, duration_s: int) -> None:
    """Snapshot -> ramp desired speed to 90 in steps of 10 -> hold -> restore.

    Writes go through the same ``put_desired`` path as the interactive tools.
    """
    snapshot = _current_desired_speed()
    interval = max(duration_s / 6.0, 0.5)
    # Ascending ladder from just above the snapshot up to 90 in +10 steps.
    ladder: list[int] = []
    v = snapshot
    while v < 90:
        v = min(v + 10, 90)
        ladder.append(v)
    if not ladder:  # already at/above 90
        ladder = [90]
    try:
        for target in ladder:
            await ditto_client.put_desired("pump_speed", target)
            await asyncio.sleep(interval)
        # Hold one extra step at the peak.
        await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — never let a stress task crash the app
        pass
    finally:
        # Always attempt to restore the original desired speed.
        try:
            await ditto_client.put_desired("pump_speed", snapshot)
        except Exception:  # noqa: BLE001
            pass


async def _run_stress_test(args: RunStressTestArgs) -> ToolOutcome:
    task = asyncio.create_task(_stress_ramp(args.profile, args.duration_s))
    _stress_tasks.add(task)
    task.add_done_callback(_stress_tasks.discard)
    return ToolOutcome(
        ok=True,
        result={"status": "started", "profile": args.profile, "duration_s": args.duration_s},
    )


# --------------------------------------------------------------------------- #
# Registries
# --------------------------------------------------------------------------- #
Handler = Callable[[Any], Awaitable[ToolOutcome]]

READ_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {
    "get_twin_state": (NoArgs, _get_twin_state),
    "get_telemetry_window": (TelemetryWindowArgs, _get_telemetry_window),
    "get_observations": (ObservationsArgs, _get_observations),
}

WRITE_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {
    "set_pump_speed": (SetPumpSpeedArgs, _set_pump_speed),
    "set_valve_state": (SetValveStateArgs, _set_valve_state),
    "run_stress_test": (RunStressTestArgs, _run_stress_test),
}

ALL_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {**READ_TOOLS, **WRITE_TOOLS}


def tool_schemas() -> list[dict]:
    """The 6 OpenAI-format tool schemas passed to the LLM."""
    return TOOL_SCHEMAS


async def execute(name: str, raw_args: Any) -> ToolOutcome:
    """Validate and run a tool call. Never raises; never runs invalid input."""
    entry = ALL_TOOLS.get(name)
    if entry is None:
        return ToolOutcome(ok=False, error=f"Unknown tool: {name!r}")

    args_model, handler = entry

    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return ToolOutcome(
            ok=False, error=f"Invalid arguments for {name}: expected an object."
        )

    try:
        args = args_model(**raw_args)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(args)'}: {e['msg']}"
            for e in exc.errors()
        )
        return ToolOutcome(
            ok=False, error=f"Invalid arguments for {name}: {problems}"
        )

    try:
        return await handler(args)
    except Exception as exc:  # noqa: BLE001 — a handler bug must not escape the executor
        return ToolOutcome(ok=False, error=f"Tool {name} failed: {exc}")
