"""Executor safety-boundary tests (CONTRACTS §3).

Run with the venv python:  ``.venv/bin/python -m pytest backend/tests/test_tools.py``

These verify that the tool executor is a real validating boundary: unknown tools and
out-of-bounds / invalid arguments are rejected WITHOUT ever touching Ditto, while a
valid call flows through to ``put_desired``.
"""
import asyncio
import os
import sys

# Make the backend package importable regardless of the pytest invocation dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import tools  # noqa: E402


class StubDitto:
    """Records writes and returns a canned 204 without hitting the network."""

    def __init__(self):
        self.calls = []

    async def put_desired(self, prop, value):
        self.calls.append((prop, value))
        path = f"/api/2/things/org.acme:pump-01/features/pump/desiredProperties/{prop}"
        return 204, None, {"method": "PUT", "path": path, "body": value}

    async def get_thing(self):
        return {
            "features": {
                "pump": {
                    "properties": {"pump_speed": 60, "valve_state": "open"},
                    "desiredProperties": {"pump_speed": 60, "valve_state": "open"},
                },
                "telemetry": {"properties": {}},
            }
        }


def test_unknown_tool_rejected():
    outcome = asyncio.run(tools.execute("frobnicate", {}))
    assert outcome.ok is False
    assert "Unknown tool" in outcome.error
    assert outcome.ditto_request is None


def test_speed_above_bound_rejected(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    outcome = asyncio.run(tools.execute("set_pump_speed", {"speed": 150, "reason": "x"}))
    assert outcome.ok is False
    assert "Invalid arguments" in outcome.error
    assert stub.calls == []  # never executed


def test_valve_state_invalid_rejected(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    outcome = asyncio.run(
        tools.execute("set_valve_state", {"state": "ajar", "reason": "x"})
    )
    assert outcome.ok is False
    assert "Invalid arguments" in outcome.error
    assert stub.calls == []  # never executed


def test_valid_speed_accepted(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    outcome = asyncio.run(
        tools.execute("set_pump_speed", {"speed": 50, "reason": "reduce thermal load"})
    )
    assert outcome.ok is True
    assert stub.calls == [("pump_speed", 50)]
    assert outcome.ditto_status == 204
    assert outcome.ditto_request["body"] == 50
    assert outcome.ditto_request["path"].endswith("/desiredProperties/pump_speed")


def test_valid_valve_accepted(monkeypatch):
    stub = StubDitto()
    monkeypatch.setattr(tools, "ditto_client", stub)
    outcome = asyncio.run(
        tools.execute("set_valve_state", {"state": "closed", "reason": "isolate leak"})
    )
    assert outcome.ok is True
    assert stub.calls == [("valve_state", "closed")]
    assert outcome.ditto_status == 204
