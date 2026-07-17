"""Pure-stdlib system physics per CONTRACTS.md §2 (v2: four coupled components).

Deterministic, no randomness, no I/O. Time `t` advances only via `SystemPhysics.tick(dt)`,
never wall-clock, so behavior is fully testable without sleeping.

Topology (CONTRACTS.md §0): motor --rpm--> pump --capacity--> valve --throttled flow-->
tank --drain--> out. `SystemPhysics` composes one physics model per component and ticks
them each simulation step in that order: motor, pump, valve, tank. Two couplings run
"against" that order (pump's current-target formula needs the pump's own speed for the
motor, and pump's flow/pressure formulas need the valve's position) — those read the
value as it stood at the end of the *previous* tick, a standard one-step lag for explicit
time-stepping coupled systems; harmless here since dt (1s) is far below every time
constant in play.

Each component model exposes: `tick(dt, ...)`, `set_desired(**kwargs)` (malformed values
are ignored rather than raised — callers include a Ditto SSE/poll watcher that must never
crash the sim on unexpected upstream data), `set_fault(mode)`, and read-only `telemetry` /
`reported` / `desired` dict views. Sinusoidal ripple is applied only when reading
`telemetry`, so it never feeds back into the relaxation dynamics.
"""
import math
from datetime import datetime, timezone
from typing import Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _slew_toward(current: float, target: float, rate: float, dt: float) -> float:
    """Rate-limited approach of `current` toward `target`, at most `rate` per second."""
    diff = target - current
    step = rate * dt
    if abs(diff) <= step:
        return target
    return current + (step if diff > 0 else -step)


def _coerce_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MotorPhysics:
    """Motor: writable `rpm_setpoint` (0-3000, default 1800, CONTRACTS.md §2.1)."""

    RPM_MIN, RPM_MAX = 0.0, 3000.0
    RPM_SLEW_RATE = 60.0  # rpm/s
    TEMP_TAU_S = 25.0
    CURRENT_TAU_S = 5.0
    TEMP_RIPPLE_AMPLITUDE = 0.15
    TEMP_RIPPLE_PERIOD_S = 7.0

    SAG_RATE = 0.01  # /s -> full sag (0.30) in 30s
    SAG_FULL = 0.30
    BEARING_HEAT_COEFF = 3.0  # deg C / s per unit sag
    OVERHEAT_COEFF = 1.6  # deg C / s scaled by rpm/1800

    FAULT_MODES = {"bearing", "overheat"}

    def __init__(self) -> None:
        # Setpoint: no actuation dynamics of its own (only "rpm", the physical output,
        # slews) so reported == desired always, updated instantly by set_desired().
        self._rpm_setpoint = 1800.0

        self._rpm = 1800.0  # physical state, slews toward rpm_setpoint*(1-sag)
        self._T = 55.0
        self._I = 7.6
        self._sag = 0.0

        self._fault: Optional[str] = None

    # -- commands --

    def set_desired(self, rpm_setpoint=None) -> None:
        value = _coerce_float(rpm_setpoint) if rpm_setpoint is not None else None
        if value is not None:
            self._rpm_setpoint = _clamp(value, self.RPM_MIN, self.RPM_MAX)

    def set_fault(self, mode: Optional[str]) -> None:
        if mode is not None and mode not in self.FAULT_MODES:
            raise ValueError(f"unknown motor fault mode: {mode!r}")
        self._fault = mode

    # -- simulation step --

    def tick(self, dt: float, pump_speed_reported: float) -> None:
        sag_target = self.SAG_FULL if self._fault == "bearing" else 0.0
        self._sag = _slew_toward(self._sag, sag_target, self.SAG_RATE, dt)

        rpm_target = self._rpm_setpoint * (1.0 - self._sag)
        self._rpm = _slew_toward(self._rpm, rpm_target, self.RPM_SLEW_RATE, dt)

        temp_target = 35.0 + self._rpm / 90.0
        self._T += (temp_target - self._T) / self.TEMP_TAU_S * dt
        if self._fault == "bearing":
            self._T += self.BEARING_HEAT_COEFF * self._sag * dt
        elif self._fault == "overheat":
            self._T += self.OVERHEAT_COEFF * (self._rpm / 1800.0) * dt

        current_target = 2.0 + 8.0 * (self._rpm / 1800.0) * (pump_speed_reported / 100.0) + 8.0 * self._sag
        self._I += (current_target - self._I) / self.CURRENT_TAU_S * dt

    # -- cross-component coupling (raw values, no rounding/ripple) --

    @property
    def rpm(self) -> float:
        return self._rpm

    # -- read-only views --

    @property
    def fault(self) -> Optional[str]:
        return self._fault

    def telemetry_at(self, t: float) -> dict:
        temp = self._T + self.TEMP_RIPPLE_AMPLITUDE * math.sin(2 * math.pi * t / self.TEMP_RIPPLE_PERIOD_S)
        return {
            "rpm": round(self._rpm, 3),
            "temp": round(temp, 3),
            "current": round(self._I, 3),
            "ts": _iso_now(),
        }

    @property
    def reported(self) -> dict:
        return {"rpm_setpoint": int(round(self._rpm_setpoint))}

    @property
    def desired(self) -> dict:
        return {"rpm_setpoint": int(round(self._rpm_setpoint))}


class PumpPhysics:
    """Pump: writable `pump_speed` (0-100, default 70, slew 5/s, CONTRACTS.md §2.1)."""

    SPEED_MIN, SPEED_MAX = 0.0, 100.0
    SPEED_SLEW_RATE = 5.0  # units/s

    FLOW_TAU_S = 3.0
    PRESSURE_TAU_S = 5.0
    TEMP_TAU_S = 20.0

    PRESSURE_RIPPLE_AMPLITUDE = 0.05
    PRESSURE_RIPPLE_PERIOD_S = 5.0

    LEAK_FACTOR = 0.75
    LEAK_PRESSURE_FIXED = 1.5

    FAULT_MODES = {"leak"}

    def __init__(self) -> None:
        self._speed = 70.0
        self._desired_speed = 70.0

        self._F = 140.0
        self._P = 3.8
        self._T = 54.5

        self._fault: Optional[str] = None

    # -- commands --

    def set_desired(self, pump_speed=None) -> None:
        value = _coerce_float(pump_speed) if pump_speed is not None else None
        if value is not None:
            self._desired_speed = _clamp(value, self.SPEED_MIN, self.SPEED_MAX)

    def set_fault(self, mode: Optional[str]) -> None:
        if mode is not None and mode not in self.FAULT_MODES:
            raise ValueError(f"unknown pump fault mode: {mode!r}")
        self._fault = mode

    # -- simulation step --

    def tick(self, dt: float, rpm: float, valve_position: float) -> None:
        self._speed = _slew_toward(self._speed, self._desired_speed, self.SPEED_SLEW_RATE, dt)

        capacity = 200.0 * (rpm / 1800.0) * (self._speed / 100.0)
        leak_factor = self.LEAK_FACTOR if self._fault == "leak" else 1.0
        flow_target = capacity * (valve_position / 100.0) * leak_factor
        self._F += (flow_target - self._F) / self.FLOW_TAU_S * dt

        valve_open = valve_position > 0.0
        if self._fault == "leak" and valve_open:
            pressure_target = self.LEAK_PRESSURE_FIXED
        else:
            pressure_target = (
                1.0
                + 4.0 * (self._speed / 100.0) * (rpm / 1800.0)
                + 3.0 * (1.0 - valve_position / 100.0)
            )
        self._P += (pressure_target - self._P) / self.PRESSURE_TAU_S * dt

        temp_target = 30.0 + 0.35 * self._speed
        self._T += (temp_target - self._T) / self.TEMP_TAU_S * dt

    # -- cross-component coupling --

    @property
    def flow(self) -> float:
        return self._F

    @property
    def speed(self) -> float:
        return self._speed

    # -- read-only views --

    @property
    def fault(self) -> Optional[str]:
        return self._fault

    def telemetry_at(self, t: float) -> dict:
        pressure = self._P + self.PRESSURE_RIPPLE_AMPLITUDE * math.sin(2 * math.pi * t / self.PRESSURE_RIPPLE_PERIOD_S)
        return {
            "flow": round(self._F, 3),
            "pressure": round(pressure, 3),
            "temp": round(self._T, 3),
            "ts": _iso_now(),
        }

    @property
    def reported(self) -> dict:
        return {"pump_speed": int(round(self._speed))}

    @property
    def desired(self) -> dict:
        return {"pump_speed": int(round(self._desired_speed))}


class ValvePhysics:
    """Valve: writable `position` (0-100, default 100, slews 20%/s, CONTRACTS.md §2.1)."""

    POSITION_MIN, POSITION_MAX = 0.0, 100.0
    SLEW_RATE = 20.0  # %/s (full stroke 5s)

    FAULT_MODES = {"stuck"}

    def __init__(self) -> None:
        self._position = 100.0
        self._desired_position = 100.0
        self._flow = 140.0  # mirrors line flow (post-throttle); no dynamics of its own

        self._fault: Optional[str] = None

    # -- commands --

    def set_desired(self, position=None) -> None:
        value = _coerce_float(position) if position is not None else None
        if value is not None:
            self._desired_position = _clamp(value, self.POSITION_MIN, self.POSITION_MAX)

    def set_fault(self, mode: Optional[str]) -> None:
        if mode is not None and mode not in self.FAULT_MODES:
            raise ValueError(f"unknown valve fault mode: {mode!r}")
        self._fault = mode

    # -- simulation step --

    def tick(self, dt: float, line_flow: float) -> None:
        if self._fault != "stuck":
            self._position = _slew_toward(self._position, self._desired_position, self.SLEW_RATE, dt)
        # else: frozen at current value; desired keeps updating but has no effect.
        self._flow = line_flow

    # -- cross-component coupling --

    @property
    def position(self) -> float:
        return self._position

    @property
    def flow(self) -> float:
        return self._flow

    # -- read-only views --

    @property
    def fault(self) -> Optional[str]:
        return self._fault

    def telemetry_at(self, t: float) -> dict:
        return {"flow": round(self._flow, 3), "ts": _iso_now()}

    @property
    def reported(self) -> dict:
        return {"position": int(round(self._position))}

    @property
    def desired(self) -> dict:
        return {"position": int(round(self._desired_position))}


class TankPhysics:
    """Tank: writable `drain_rate` (0-200 L/min, default 140, CONTRACTS.md §2.1). The only
    integrator in the system: `d(level_pct)/dt = (inflow - outflow)/120`, clamped 0-100."""

    DRAIN_MIN, DRAIN_MAX = 0.0, 200.0
    LEVEL_DIVISOR = 120.0

    def __init__(self) -> None:
        self._level = 50.0
        self._drain_rate = 140.0  # no actuation dynamics: reported == desired, instant

        self._inflow = 140.0
        self._outflow = 140.0

    # -- commands --

    def set_desired(self, drain_rate=None) -> None:
        value = _coerce_float(drain_rate) if drain_rate is not None else None
        if value is not None:
            self._drain_rate = _clamp(value, self.DRAIN_MIN, self.DRAIN_MAX)

    def set_fault(self, mode: Optional[str]) -> None:
        # Tank has no fault modes (CONTRACTS.md §2.1); only a no-op clear is valid.
        if mode is not None:
            raise ValueError(f"unknown tank fault mode: {mode!r}")

    # -- simulation step --

    def tick(self, dt: float, inflow: float) -> None:
        self._inflow = inflow
        self._outflow = self._drain_rate if self._level > 0.0 else 0.0
        d_level = (self._inflow - self._outflow) / self.LEVEL_DIVISOR * dt
        self._level = _clamp(self._level + d_level, 0.0, 100.0)

    # -- read-only views --

    @property
    def fault(self) -> Optional[str]:
        return None

    def telemetry_at(self, t: float) -> dict:
        return {
            "level_pct": round(self._level, 3),
            "inflow": round(self._inflow, 3),
            "outflow": round(self._outflow, 3),
            "ts": _iso_now(),
        }

    @property
    def reported(self) -> dict:
        return {"drain_rate": int(round(self._drain_rate))}

    @property
    def desired(self) -> dict:
        return {"drain_rate": int(round(self._drain_rate))}


VALID_FAULTS = {
    "motor": MotorPhysics.FAULT_MODES,
    "pump": PumpPhysics.FAULT_MODES,
    "valve": ValvePhysics.FAULT_MODES,
    "tank": frozenset(),
}


class SystemPhysics:
    """Composes the four component models, ticked each simulation step in coupling order
    motor -> pump -> valve -> tank (CONTRACTS.md §2.1). Owns simulation time `t` and the
    per-component fault dict."""

    COMPONENTS = ("motor", "pump", "valve", "tank")

    def __init__(self) -> None:
        self.t = 0.0
        self.motor = MotorPhysics()
        self.pump = PumpPhysics()
        self.valve = ValvePhysics()
        self.tank = TankPhysics()
        self._by_name = {
            "motor": self.motor,
            "pump": self.pump,
            "valve": self.valve,
            "tank": self.tank,
        }

    # -- simulation step --

    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        self.t += dt

        # Couplings that run "against" the primary motor->pump->valve->tank order (pump
        # needs the motor's rpm computed *this* tick; motor's current and pump's flow/
        # pressure need the *other* component's speed/position as of the end of the
        # previous tick — see module docstring) read those values before the owning
        # component ticks this round.
        pump_speed_prev = self.pump.speed
        valve_position_prev = self.valve.position

        self.motor.tick(dt, pump_speed_reported=pump_speed_prev)
        self.pump.tick(dt, rpm=self.motor.rpm, valve_position=valve_position_prev)
        self.valve.tick(dt, line_flow=self.pump.flow)
        self.tank.tick(dt, inflow=self.valve.flow)

    # -- commands --

    def set_desired(self, component: str, **kwargs) -> None:
        if component not in self._by_name:
            raise ValueError(f"unknown component: {component!r}")
        self._by_name[component].set_desired(**kwargs)

    def set_fault(self, component: str, mode: Optional[str]) -> None:
        if component not in self._by_name:
            raise ValueError(f"unknown component: {component!r}")
        if mode is not None and mode not in VALID_FAULTS[component]:
            raise ValueError(f"unknown fault mode for {component}: {mode!r}")
        self._by_name[component].set_fault(mode)

    def clear_faults(self) -> None:
        for model in self._by_name.values():
            model.set_fault(None)

    # -- read-only views --

    @property
    def faults(self) -> dict:
        return {name: model.fault for name, model in self._by_name.items()}

    def telemetry(self, component: str) -> dict:
        return self._by_name[component].telemetry_at(self.t)

    def reported(self, component: str) -> dict:
        return self._by_name[component].reported

    def desired(self, component: str) -> dict:
        return self._by_name[component].desired
