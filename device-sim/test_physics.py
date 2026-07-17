"""Pure-physics tests for PumpPhysics (stdlib unittest, no network, no wall-clock sleeps).

Run: python3 -m unittest (from device-sim/)
"""
import unittest

from physics import PumpPhysics


class TestPumpPhysics(unittest.TestCase):
    def test_steady_state_settles_near_64(self):
        """At speed 60 (the thing.json baseline) temp_target = 40 + 0.4*60 = 64, and the
        model already starts there, so it should stay within tolerance for a long run."""
        p = PumpPhysics()
        for _ in range(300):
            p.tick(1.0)
        temp = p.telemetry["temperature"]
        self.assertAlmostEqual(temp, 64.0, delta=2.0)

    def test_overheat_at_speed_80_crosses_85_then_95(self):
        """Overheat fault adds +1.0*(speed/60) C/s to dT/dt on top of the first-order
        relaxation toward temp_target. At speed 80: temp_target = 72, fault rate =
        1.0*80/60 = 1.333 C/s. Fixed point of
        dT/dt = (temp_target - T)/20 + fault_rate = 0 is
        T_eq = 72 + 20*1.333 = 98.67 C — comfortably above the 95 C critical
        threshold, so the demo's warn (85) -> critical (95) escalation is reachable:
        ~13s to cross 85, ~40s to cross 95 (from the speed-80 steady state)."""
        p = PumpPhysics()
        p.set_fault("overheat")
        p.set_desired(pump_speed=80)
        t_85 = t_95 = None
        for s in range(1, 151):
            p.tick(1.0)
            temp = p.telemetry["temperature"]
            if t_85 is None and temp > 85.0:
                t_85 = s
            if t_95 is None and temp > 95.0:
                t_95 = s
                break
        self.assertIsNotNone(t_85, "temperature never crossed 85C (warn) under overheat at speed 80")
        self.assertIsNotNone(t_95, "temperature never crossed 95C (critical) under overheat at speed 80")
        self.assertLess(t_85, 60, "warn threshold should be crossed well within a demo minute")

    def test_reducing_speed_recovers_and_speed_slews(self):
        p = PumpPhysics()
        p.set_fault("overheat")
        p.set_desired(pump_speed=80)
        for _ in range(150):
            p.tick(1.0)
            if p.telemetry["temperature"] > 85.0:
                break
        self.assertGreater(p.telemetry["temperature"], 85.0)  # precondition sanity check

        p.set_desired(pump_speed=35)
        fell_back = False
        saw_intermediate_speed = False
        for _ in range(120):
            p.tick(1.0)
            speed = p.reported["pump_speed"]
            if 35 < speed < 80:
                saw_intermediate_speed = True
            if p.telemetry["temperature"] < 85.0:
                fell_back = True
                break
        self.assertTrue(saw_intermediate_speed, "reported speed jumped instead of slewing at 5 units/s")
        self.assertTrue(fell_back, "temperature never fell back below 85C after reducing speed")

    def test_leak_drops_pressure_and_closing_valve_recovers(self):
        p = PumpPhysics()
        p.set_fault("leak")
        dropped = False
        for _ in range(60):
            p.tick(1.0)
            if p.telemetry["pressure"] < 3.0:
                dropped = True
                break
        self.assertTrue(dropped, "pressure never dropped below 3.0 bar under leak fault at speed 60")

        p.set_desired(valve_state="closed")
        recovered = False
        for _ in range(60):
            p.tick(1.0)
            if p.telemetry["pressure"] > 3.0:
                recovered = True
                break
        self.assertTrue(recovered, "pressure never recovered above 3.0 bar after closing the valve")

    def test_clear_fault_returns_to_baseline(self):
        p = PumpPhysics()
        p.set_fault("leak")
        p.set_desired(valve_state="closed")
        for _ in range(30):
            p.tick(1.0)

        p.set_fault(None)
        p.set_desired(pump_speed=60, valve_state="open")
        for _ in range(200):
            p.tick(1.0)

        telemetry = p.telemetry
        self.assertIsNone(p.fault)
        self.assertAlmostEqual(telemetry["temperature"], 64.0, delta=2.0)
        self.assertAlmostEqual(telemetry["pressure"], 5.0, delta=0.5)
        self.assertAlmostEqual(telemetry["flow_rate"], 120.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
