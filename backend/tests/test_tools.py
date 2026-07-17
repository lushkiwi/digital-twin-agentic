"""Executor + control-route + sleeper safety tests (CONTRACTS §3, v2).

Run with the venv python:  ``.venv/bin/python -m pytest backend/tests/test_tools.py``

These verify:
  * the tool executor is a real validating boundary — unknown tools and out-of-bounds
    args are rejected WITHOUT ever touching Ditto, while valid calls flow through to
    ``put_desired`` on the correct per-component path (looped over the whole registry);
  * ``get_system_state`` returns the four-component shape;
  * ``POST /api/control/...`` 404s unknown params, rejects out-of-bounds without touching
    Ditto, and on success routes through the executor + adds an operator observation;
  * the sleeper does NOT false-fire the sag / root-cause rules on a clean setpoint ramp.
"""
import asyncio
import os
import sys

# Make the backend package importable regardless of the pytest invocation dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import params  # noqa: E402
from app.agents import tools  # noqa: E402
from app.main import app  # noqa: E402
from app.state import app_state  # noqa: E402


def _thing(component, reported, desired, telemetry):
    return {
        "thingId": params.thing_id(component),
        "features": {
            component: {"properties": reported, "desiredProperties": desired},
            "telemetry": {"properties": telemetry},
        },
    }


class StubDitto:
    """Records writes and serves canned reads without hitting the network."""

    def __init__(self):
        self.calls = []  # (thing_id, feature, prop, value)

    async def put_desired(self, thing_id, feature, prop, value):
        self.calls.append((thing_id, feature, prop, value))
        path = f"/api/2/things/{thing_id}/features/{feature}/desiredProperties/{prop}"
        return 204, None, {"method": "PUT", "path": path, "body": value}

    async def get_thing(self, thing_id):
        return {"thingId": thing_id, "features": {}}

    async def get_all_things(self):
        return {
            "motor": _thing(
                "motor", {"rpm_setpoint": 1800}, {"rpm_setpoint": 1800},
                {"rpm": 1800.0, "temp": 55.0, "current": 7.6, "ts": "t"},
            ),
            "pump": _thing(
                "pump", {"pump_speed": 70}, {"pump_speed": 70},
                {"flow": 140.0, "pressure": 3.8, "temp": 54.5, "ts": "t"},
            ),
            "valve": _thing(
                "valve", {"position": 100}, {"position": 100},
                {"flow": 140.0, "ts": "t"},
            ),
            "tank": _thing(
                "tank", {"drain_rate": 140}, {"drain_rate": 140},
                {"level_pct": 50.0, "inflow": 140.0, "outflow": 140.0, "ts": "t"},
            ),
        }


# --------------------------------------------------------------------------- #
# Executor: unknown tool + full-registry bounds loop
# --------------------------------------------------------------------------- #
def test_unknown_tool_rejected():
    outcome = asyncio.run(tools.execute("frobnicate", {}))
    assert outcome.ok is False
    assert "Unknown tool" in outcome.error
    assert outcome.ditto_request is None


def test_every_param_over_bounds_rejected(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    for p in params.PARAMS:
        args = {p.arg_name: p.max + 1, "reason": "x"}
        outcome = asyncio.run(tools.execute(p.tool_name, args))
        assert outcome.ok is False, f"{p.tool_name} should reject {p.max + 1}"
        assert "Invalid arguments" in outcome.error
    assert stub.calls == []  # nothing ever executed


def test_every_param_valid_accepted(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    for p in params.PARAMS:
        args = {p.arg_name: p.max, "reason": "within bounds"}
        outcome = asyncio.run(tools.execute(p.tool_name, args))
        assert outcome.ok is True, f"{p.tool_name} should accept {p.max}"
        assert outcome.ditto_status == 204
        assert outcome.ditto_request["body"] == p.max
        assert outcome.ditto_request["path"].endswith(f"/desiredProperties/{p.name}")
        assert (p.thing_id, p.feature, p.name, p.max) in stub.calls
    assert len(stub.calls) == len(params.PARAMS)


def test_get_system_state_shape(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    outcome = asyncio.run(tools.execute("get_system_state", {}))
    assert outcome.ok is True
    result = outcome.result
    assert set(result["components"].keys()) == {"motor", "pump", "valve", "tank"}
    for comp in ("motor", "pump", "valve", "tank"):
        block = result["components"][comp]
        assert set(block.keys()) == {"reported", "desired", "telemetry", "status"}
    assert "ditto_connected" in result
    # Unadvertised v1 alias still resolves to the same builder.
    alias = asyncio.run(tools.execute("get_twin_state", {}))
    assert alias.ok is True
    assert set(alias.result["components"].keys()) == {"motor", "pump", "valve", "tank"}


def test_advertised_schema_count():
    names = {s["function"]["name"] for s in tools.tool_schemas()}
    # 3 reads + 4 registry write tools + run_stress_test; alias stays UNadvertised.
    assert names == {
        "get_system_state", "get_telemetry_window", "get_observations",
        "set_motor_rpm", "set_pump_speed", "set_valve_position", "set_tank_drain_rate",
        "run_stress_test",
    }
    assert "get_twin_state" not in names


# --------------------------------------------------------------------------- #
# Control route (FastAPI TestClient; no lifespan -> no background tasks)
# --------------------------------------------------------------------------- #
def test_control_unknown_param_404(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    client = TestClient(app)
    resp = client.post("/api/control/motor/not_a_param", json={"value": 10})
    assert resp.status_code == 404
    assert stub.calls == []


def test_control_over_bounds_ok_false_untouched(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    client = TestClient(app)
    resp = client.post("/api/control/motor/rpm_setpoint", json={"value": 9999})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "error" in body
    assert stub.calls == []  # Ditto never touched on validation failure


def test_control_valid_executes_and_observes(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    client = TestClient(app)
    resp = client.post(
        "/api/control/motor/rpm_setpoint", json={"value": 2000, "reason": "test trim"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["ditto_status"] == 204
    assert stub.calls == [(params.thing_id("motor"), "motor", "rpm_setpoint", 2000)]
    # An operator observation was recorded.
    latest = app_state.observations.recent(1)[0]
    assert latest["source"] == "operator"
    assert latest["component"] == "motor"
    assert latest["severity"] == "info"
    assert "2000" in latest["title"]


# --------------------------------------------------------------------------- #
# Sleeper false-positive guard: a clean 1800 -> 2600 ramp fires nothing
# --------------------------------------------------------------------------- #
def _ramp_frame(t, sp_d, sp_r, rpm):
    flow = 200.0 * (rpm / 1800.0) * 0.70  # pump_speed 70, valve fully open
    return {
        "ts": f"2026-07-17T00:00:{t:02d}Z",
        "components": {
            "motor": {
                "rpm": float(rpm), "temp": 55.0, "current": 7.6,
                "rpm_setpoint_reported": sp_r, "rpm_setpoint_desired": sp_d,
            },
            "pump": {
                "flow": flow, "pressure": 3.8, "temp": 54.5,
                "pump_speed_reported": 70, "pump_speed_desired": 70,
            },
            "valve": {"flow": flow, "position_reported": 100, "position_desired": 100},
            "tank": {
                "level_pct": 50.0, "inflow": flow, "outflow": 140.0,
                "drain_rate_reported": 140, "drain_rate_desired": 140,
            },
        },
    }


def test_clean_ramp_does_not_fire_sag_or_root_cause(monkeypatch):
    import app.agents.sleeper as sleeper_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(sleeper_mod.time, "monotonic", lambda: clock["t"])

    s = sleeper_mod.Sleeper()

    # t=0: steady baseline at 1800. t>=1: desired jumps to 2600, rpm slews 60 rpm/s.
    for t in range(0, 26):
        clock["t"] = 1000.0 + t
        if t == 0:
            frame = _ramp_frame(t, 1800, 1800, 1800)
        else:
            rpm = min(1800 + 60 * (t - 1), 2600)
            frame = _ramp_frame(t, 2600, 2600, rpm)
        asyncio.run(s.evaluate(frame))

    assert s._rs("motor.sag")["last_fire"] is None, "sag must not false-fire on a ramp"
    assert s._rs("system.flow_deficit_motor")["last_fire"] is None
    assert s._rs("system.flow_deficit_valve")["last_fire"] is None
    # No component should be worse than ok from this clean ramp.
    assert s.component_status() == {c: "ok" for c in ("motor", "pump", "valve", "tank")}
