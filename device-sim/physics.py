"""Pure-stdlib pump physics per CONTRACTS.md §2.

Deterministic, no randomness, no I/O. Time `t` advances only via `tick(dt)`, never
wall-clock, so behavior is fully testable without sleeping.

Model: reported `pump_speed` and `valve_state` slew/actuate toward a `desired` setpoint;
temperature/pressure/flow relax first-order toward speed-dependent targets, with a small
sinusoidal ripple layered on the reported temperature/pressure only (not fed back into the
state, so it can't drift the equilibrium). Fault modes perturb the targets/rates as specified
in the contract.
"""
import math
from datetime import datetime, timezone
from typing import Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PumpPhysics:
    """Single-pump physics model. Call `tick(dt)` at TELEMETRY_INTERVAL_S cadence."""

    # -- convergence rates (CONTRACTS.md §2) --
    SPEED_SLEW_RATE = 5.0  # units/s
    VALVE_ACTUATION_DELAY_S = 2.0

    # -- first-order time constants (seconds) --
    TEMP_TAU_S = 20.0
    PRESSURE_TAU_S = 8.0
    FLOW_TAU_S = 3.0

    # -- deterministic ripple --
    TEMP_RIPPLE_AMPLITUDE = 0.15
    TEMP_RIPPLE_PERIOD_S = 7.0
    PRESSURE_RIPPLE_AMPLITUDE = 0.05
    PRESSURE_RIPPLE_PERIOD_S = 5.0

    # -- fault parameters --
    OVERHEAT_RATE_COEFF = 1.0  # (speed/60) °C/s added while overheat is active
    OVERHEAT_SPEED_REF = 60.0
    LEAK_PRESSURE_TARGET = 1.5

    def __init__(self) -> None:
        self.t = 0.0

        # Reported (actual, physical) state — seeded from thing.json's initial values.
        self._speed = 60.0
        self._valve = "open"

        # Desired setpoint the sim slews/actuates toward. Sim never writes desiredProperties;
        # this is only ever updated by set_desired() (fed by ditto_io watching Ditto).
        self._desired_speed = 60.0
        self._desired_valve = "open"
        self._valve_pending_elapsed = 0.0

        # Underlying continuous state (pre-ripple). Ripple is applied only when reading
        # `telemetry`, so it never feeds back into the relaxation dynamics.
        self._T = 64.0
        self._P = 5.0
        self._F = 120.0

        self._fault: Optional[str] = None

    # -- commands --

    def set_desired(self, pump_speed: Optional[float] = None, valve_state: Optional[str] = None) -> None:
        """Update the setpoint the reported state slews toward. Malformed values are ignored
        rather than raised, since callers include a Ditto SSE/poll watcher that must never
        crash the sim on unexpected upstream data."""
        if pump_speed is not None:
            try:
                speed = float(pump_speed)
            except (TypeError, ValueError):
                speed = None
            if speed is not None:
                self._desired_speed = max(0.0, min(100.0, speed))
        if valve_state is not None and valve_state in ("open", "closed"):
            self._desired_valve = valve_state

    def set_fault(self, mode: Optional[str]) -> None:
        if mode is not None and mode not in ("overheat", "leak"):
            raise ValueError(f"unknown fault mode: {mode!r}")
        self._fault = mode

    # -- simulation step --

    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        self.t += dt

        self._slew_speed(dt)
        self._actuate_valve(dt)

        speed = self._speed
        valve_open = self._valve == "open"

        # Targets (CONTRACTS.md §2). Temperature target is independent of valve state.
        temp_target = 40.0 + 0.4 * speed
        if valve_open:
            flow_target = 2.0 * speed
            pressure_target = self.LEAK_PRESSURE_TARGET if self._fault == "leak" else (2.0 + 0.05 * speed)
        else:
            flow_target = 0.0
            pressure_target = 2.0 + 0.05 * speed  # isolated, holds — leak has no effect

        self._T += (temp_target - self._T) / self.TEMP_TAU_S * dt
        self._P += (pressure_target - self._P) / self.PRESSURE_TAU_S * dt
        self._F += (flow_target - self._F) / self.FLOW_TAU_S * dt

        if self._fault == "overheat":
            self._T += self.OVERHEAT_RATE_COEFF * (speed / self.OVERHEAT_SPEED_REF) * dt

    def _slew_speed(self, dt: float) -> None:
        diff = self._desired_speed - self._speed
        step = self.SPEED_SLEW_RATE * dt
        if abs(diff) <= step:
            self._speed = self._desired_speed
        else:
            self._speed += step if diff > 0 else -step

    def _actuate_valve(self, dt: float) -> None:
        if self._desired_valve == self._valve:
            self._valve_pending_elapsed = 0.0
            return
        self._valve_pending_elapsed += dt
        if self._valve_pending_elapsed >= self.VALVE_ACTUATION_DELAY_S:
            self._valve = self._desired_valve
            self._valve_pending_elapsed = 0.0

    # -- read-only views --

    @property
    def fault(self) -> Optional[str]:
        return self._fault

    @property
    def telemetry(self) -> dict:
        temperature = self._T + self.TEMP_RIPPLE_AMPLITUDE * math.sin(2 * math.pi * self.t / self.TEMP_RIPPLE_PERIOD_S)
        pressure = self._P + self.PRESSURE_RIPPLE_AMPLITUDE * math.sin(2 * math.pi * self.t / self.PRESSURE_RIPPLE_PERIOD_S)
        return {
            "temperature": round(temperature, 3),
            "pressure": round(pressure, 3),
            "flow_rate": round(self._F, 3),
            "ts": _iso_now(),
        }

    @property
    def reported(self) -> dict:
        return {
            "pump_speed": int(round(self._speed)),
            "valve_state": self._valve,
        }

    @property
    def desired(self) -> dict:
        return {
            "pump_speed": int(round(self._desired_speed)),
            "valve_state": self._desired_valve,
        }
