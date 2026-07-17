"""The writable-parameter registry — single source of truth (CONTRACTS.md §3.1).

Leaf module: imports nothing from tools/state/routes. Everything that needs to know
"what can be written, within what bounds, to which Ditto path" derives it from here:
LLM tool schemas + validators (agents/tools.py), the manual-control REST route
(routes/control.py), and the frontend's drawer editors (GET /api/params).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, create_model

THING_NS = os.environ.get("THING_NS", "org.acme")

COMPONENTS: Dict[str, str] = {  # component id -> human label
    "motor": "Motor",
    "pump": "Pump",
    "valve": "Valve",
    "tank": "Tank",
}


def thing_id(component: str) -> str:
    return f"{THING_NS}:{component}-01"


@dataclass(frozen=True)
class Param:
    component: str      # "motor" | "pump" | "valve" | "tank"
    name: str           # Ditto desiredProperties key, e.g. "rpm_setpoint"
    arg_name: str       # LLM tool argument name, e.g. "rpm"
    label: str
    unit: str
    kind: str           # "int" (all v2 params are ints)
    min: int
    max: int
    step: int
    feature: str        # Ditto actuator feature name
    tool_name: str      # LLM write-tool name
    description: str

    @property
    def thing_id(self) -> str:
        return thing_id(self.component)


PARAMS: List[Param] = [
    Param("motor", "rpm_setpoint", "rpm", "RPM setpoint", "rpm", "int", 0, 3000, 50,
          "motor", "set_motor_rpm",
          "Set the motor's target RPM. The pump's capacity scales with motor rpm."),
    Param("pump", "pump_speed", "speed", "Pump speed", "%", "int", 0, 100, 5,
          "pump", "set_pump_speed",
          "Set the pump's speed percentage. Capacity = 200·(rpm/1800)·(speed/100) L/min."),
    Param("valve", "position", "position", "Valve position", "%", "int", 0, 100, 5,
          "valve", "set_valve_position",
          "Set the valve opening (100 = fully open, 0 = closed). Throttles line flow."),
    Param("tank", "drain_rate", "drain_rate", "Drain rate", "L/min", "int", 0, 200, 10,
          "tank", "set_tank_drain_rate",
          "Set the tank's outflow drain rate. Level rises when inflow exceeds it."),
]


def by_tool_name() -> Dict[str, Param]:
    return {p.tool_name: p for p in PARAMS}


def by_component_param() -> Dict[Tuple[str, str], Param]:
    return {(p.component, p.name): p for p in PARAMS}


def make_args_model(param: Param) -> Type[BaseModel]:
    """Pydantic model validating {<arg_name>: int within bounds, reason: str}."""
    return create_model(
        f"{param.tool_name.title().replace('_', '')}Args",
        __config__=ConfigDict(extra="forbid"),
        **{
            param.arg_name: (int, Field(ge=param.min, le=param.max, description=param.description)),
            "reason": (str, Field(min_length=1, description="Why this change is being made")),
        },
    )


def make_tool_schema(param: Param) -> dict:
    """OpenAI-format function schema for this write tool."""
    return {
        "type": "function",
        "function": {
            "name": param.tool_name,
            "description": f"{param.description} Bounds: {param.min}–{param.max} {param.unit}.",
            "parameters": {
                "type": "object",
                "properties": {
                    param.arg_name: {
                        "type": "integer",
                        "minimum": param.min,
                        "maximum": param.max,
                        "description": f"{param.label} ({param.unit})",
                    },
                    "reason": {"type": "string", "description": "Why this change is being made"},
                },
                "required": [param.arg_name, "reason"],
            },
        },
    }


def registry_public_dict() -> dict:
    """Payload for GET /api/params (CONTRACTS.md §3.3)."""
    out: dict = {"components": {}}
    for comp, label in COMPONENTS.items():
        out["components"][comp] = {
            "label": label,
            "thing_id": thing_id(comp),
            "params": [
                {"name": p.name, "label": p.label, "unit": p.unit, "kind": p.kind,
                 "min": p.min, "max": p.max, "step": p.step}
                for p in PARAMS if p.component == comp
            ],
        }
    return out
