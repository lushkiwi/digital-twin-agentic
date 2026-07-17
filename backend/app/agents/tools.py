"""Tool layer — the safety boundary for the interactive agent (v2, four components).

Every tool call from the LLM is validated against a strict Pydantic model enforcing
the CONTRACTS §3 bounds BEFORE anything executes.  An unknown tool or a validation
failure produces a structured error ``ToolOutcome`` (ok=False) that is handed back to
the model as the tool result — it is never executed and never raises.

The write tools live here (this is the write-capable module).  Their schemas, arg
validators and Ditto dispatch are ALL derived from the frozen param registry
(``app.params``) so bounds/ids live in exactly one place.  The sleeper must NOT import
this module — but this module may import the sleeper (one-directional) to read the
current per-component status for ``get_system_state`` / ``GET /api/system``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Tuple, Type

from pydantic import BaseModel, Field, ValidationError

from .. import params
from ..config import settings
from ..params import COMPONENTS, PARAMS, make_args_model, make_tool_schema
from ..state import app_state, downsample
from ..twin.client import ditto_client
from .sleeper import sleeper


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
# Read-tool argument models (write-tool models come from the registry)
# --------------------------------------------------------------------------- #
_Component = Literal["motor", "pump", "valve", "tank"]


class NoArgs(BaseModel):
    model_config = {"extra": "ignore"}


class TelemetryWindowArgs(BaseModel):
    model_config = {"extra": "ignore"}
    minutes: int = Field(ge=1, le=10)
    component: Optional[_Component] = None


class ObservationsArgs(BaseModel):
    model_config = {"extra": "ignore"}
    limit: int = Field(ge=1, le=20)


class RunStressTestArgs(BaseModel):
    model_config = {"extra": "forbid"}
    profile: Literal["thermal", "flow"]
    duration_s: int = Field(ge=10, le=120)


# --------------------------------------------------------------------------- #
# Shared system-state builder (used by get_system_state AND GET /api/system)
# --------------------------------------------------------------------------- #
async def build_system_state() -> dict:
    """All four components: reported + desired + latest telemetry + status.

    Reads reported/desired/telemetry live from Ditto (never raises — a down/missing
    component reads as empty), and the status from the sleeper's current rule flags.
    """
    things = await ditto_client.get_all_things()  # component -> thing | None
    status = sleeper.component_status()
    out: dict = {"components": {}}
    for comp in COMPONENTS:
        thing = things.get(comp) or {}
        features = thing.get("features", {}) if isinstance(thing, dict) else {}
        act = features.get(comp, {}) or {}
        telem = (features.get("telemetry", {}) or {}).get("properties", {}) or {}
        out["components"][comp] = {
            "reported": act.get("properties", {}) or {},
            "desired": act.get("desiredProperties", {}) or {},
            "telemetry": telem,
            "status": status.get(comp, "ok"),
        }
    out["ditto_connected"] = app_state.ditto_connected
    return out


# --------------------------------------------------------------------------- #
# Read handlers
# --------------------------------------------------------------------------- #
async def _get_system_state(_: NoArgs) -> ToolOutcome:
    try:
        state = await build_system_state()
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(ok=False, error=f"Could not read system state: {exc}")
    return ToolOutcome(ok=True, result=state)


async def _get_telemetry_window(args: TelemetryWindowArgs) -> ToolOutcome:
    points = downsample(app_state.telemetry.window(args.minutes), 60)
    if args.component:
        # Slim each frame down to the requested component only.
        points = [
            {
                "ts": p.get("ts"),
                "components": {
                    args.component: (p.get("components", {}) or {}).get(args.component, {})
                },
            }
            for p in points
        ]
    return ToolOutcome(
        ok=True,
        result={
            "minutes": args.minutes,
            "component": args.component,
            "count": len(points),
            "points": points,
        },
    )


async def _get_observations(args: ObservationsArgs) -> ToolOutcome:
    obs = app_state.observations.recent(args.limit)
    return ToolOutcome(ok=True, result={"observations": obs})


# --------------------------------------------------------------------------- #
# Write handlers — one per registry param, dispatched via put_desired
# --------------------------------------------------------------------------- #
def _make_write_handler(param: params.Param) -> "Handler":
    async def handler(args: BaseModel) -> ToolOutcome:
        value = getattr(args, param.arg_name)
        reason = getattr(args, "reason")
        status, body, request_desc = await ditto_client.put_desired(
            param.thing_id, param.feature, param.name, value
        )
        ok = 200 <= status < 300
        return ToolOutcome(
            ok=ok,
            result={f"desired_{param.name}": value, "reason": reason, "response": body},
            ditto_request=request_desc,
            ditto_status=status,
            error=None if ok else f"Ditto write failed (status {status}).",
        )

    return handler


# Keep references to background stress tasks so they aren't garbage-collected.
_stress_tasks: set = set()

# Stress profiles (CONTRACTS §3.4): thermal ramps pump speed to 90, flow ramps motor
# setpoint to 2700; both snapshot -> ramp -> hold -> restore via the same put_desired.
_STRESS_PROFILES = {
    "thermal": {"component": "pump", "prop": "pump_speed", "peak": 90, "step": 10, "default": 70},
    "flow": {"component": "motor", "prop": "rpm_setpoint", "peak": 2700, "step": 200, "default": 1800},
}


def _current_desired(component: str, prop: str, default: int) -> int:
    """Best-effort read of a current desired property from the per-thing cache."""
    try:
        thing = app_state.get_twin(settings.thing_ids[component])
        val = thing.get("features", {}).get(component, {}).get("desiredProperties", {}).get(prop)
        if isinstance(val, (int, float)):
            return int(val)
    except Exception:  # noqa: BLE001
        pass
    return default


async def _stress_ramp(profile: str, duration_s: int) -> None:
    """Snapshot -> ramp desired up to peak in steps -> hold -> restore.

    Writes go through the same ``put_desired`` path as the interactive tools.
    """
    spec = _STRESS_PROFILES[profile]
    component, prop = spec["component"], spec["prop"]
    thing_id = settings.thing_ids[component]
    feature = component
    peak, step = spec["peak"], spec["step"]
    snapshot = _current_desired(component, prop, spec["default"])
    interval = max(duration_s / 6.0, 0.5)

    ladder: list[int] = []
    v = snapshot
    while v < peak:
        v = min(v + step, peak)
        ladder.append(v)
    if not ladder:  # already at/above peak
        ladder = [peak]
    try:
        for target in ladder:
            await ditto_client.put_desired(thing_id, feature, prop, target)
            await asyncio.sleep(interval)
        await asyncio.sleep(interval)  # hold one extra step at the peak
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — never let a stress task crash the app
        pass
    finally:
        # Always attempt to restore the original desired value.
        try:
            await ditto_client.put_desired(thing_id, feature, prop, snapshot)
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
# OpenAI-format tool JSON schemas (advertised set)
# --------------------------------------------------------------------------- #
_READ_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_system_state",
            "description": (
                "Read the full system: reported + desired actuator state, latest telemetry "
                "and status for all four components (motor, pump, valve, tank)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_telemetry_window",
            "description": (
                "Recent telemetry frames from the ring buffer, downsampled to <=60 points. "
                "Optionally slim each frame to a single component."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "How many minutes back to fetch (1-10).",
                    },
                    "component": {
                        "type": "string",
                        "enum": ["motor", "pump", "valve", "tank"],
                        "description": "Optional: restrict each frame to this component.",
                    },
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
]

_STRESS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "run_stress_test",
        "description": (
            "Start a background stress ramp (snapshot -> ramp -> hold -> restore). "
            "'thermal' ramps pump speed to 90; 'flow' ramps motor setpoint to 2700. "
            "Returns immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "enum": ["thermal", "flow"]},
                "duration_s": {"type": "integer", "minimum": 10, "maximum": 120},
            },
            "required": ["profile", "duration_s"],
        },
    },
}

# Advertised: 3 reads + 4 registry write tools + run_stress_test.
TOOL_SCHEMAS: list[dict] = (
    _READ_SCHEMAS + [make_tool_schema(p) for p in PARAMS] + [_STRESS_SCHEMA]
)


# --------------------------------------------------------------------------- #
# Registries
# --------------------------------------------------------------------------- #
Handler = Callable[[Any], Awaitable[ToolOutcome]]

READ_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {
    "get_system_state": (NoArgs, _get_system_state),
    # Unadvertised v1 alias kept so old callers/tests still resolve (CONTRACTS §3.4).
    "get_twin_state": (NoArgs, _get_system_state),
    "get_telemetry_window": (TelemetryWindowArgs, _get_telemetry_window),
    "get_observations": (ObservationsArgs, _get_observations),
}

# One validated write tool per registry param, plus the stress test.
_PARAM_WRITE_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {
    p.tool_name: (make_args_model(p), _make_write_handler(p)) for p in PARAMS
}
WRITE_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {
    **_PARAM_WRITE_TOOLS,
    "run_stress_test": (RunStressTestArgs, _run_stress_test),
}

ALL_TOOLS: Dict[str, Tuple[Type[BaseModel], Handler]] = {**READ_TOOLS, **WRITE_TOOLS}


def tool_schemas() -> list[dict]:
    """The advertised OpenAI-format tool schemas passed to the LLM."""
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
