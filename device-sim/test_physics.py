"""Pure-physics tests for SystemPhysics (stdlib unittest, no network, no wall-clock sleeps).

Run: python3 -m unittest -v (from device-sim/)

These are the v2 acceptance gate (CONTRACTS.md §2.1). Timings come from the bearing-fault
cascade reference table in the contract; per the task brief they are asserted within the
stated tolerance. Where a timing assertion would fail, the fix is to re-derive the
equilibrium/crossing analytically from the contract's equations (the prose narration can
be optimistic; the equations are authoritative) and fix a code bug — never to loosen the
physics.
"""
import unittest

from physics import SystemPhysics


class TestSteadyState(unittest.TestCase):
    def test_steady_state_holds_at_defaults_for_300s(self):
        """All four components start exactly at their contract-specified baseline
        equilibria (motor 1800rpm/55C/7.6A, pump 140 L/min / 3.8 bar / 54.5C, valve 100,
        tank 50%), so a long run with no commands and no faults should barely move."""
        sp = SystemPhysics()
        for _ in range(300):
            sp.tick(1.0)

        motor = sp.telemetry("motor")
        pump = sp.telemetry("pump")
        tank = sp.telemetry("tank")

        self.assertAlmostEqual(motor["temp"], 55.0, delta=2.0)
        self.assertAlmostEqual(motor["rpm"], 1800.0, delta=5.0)
        self.assertAlmostEqual(pump["flow"], 140.0, delta=3.0)
        self.assertAlmostEqual(pump["pressure"], 3.8, delta=0.2)
        self.assertAlmostEqual(tank["level_pct"], 50.0, delta=2.0)


class TestMotorOverheat(unittest.TestCase):
    def test_overheat_crosses_70_then_85_and_mitigation_recovers(self):
        """Fault `overheat` adds 1.6*(rpm/1800) C/s on top of the temp relaxation. At the
        1800rpm baseline: dT/dt = (55-T)/25 + 1.6, equilibrium T_eq = 95C, giving
        T(t) = 95 - 40*exp(-t/25). Solving for T=70 -> t=25*ln(40/25)=11.75s; for T=85 ->
        t=25*ln(40/10)=34.66s. Mitigation (setpoint 900): new equilibrium
        T_eq = 45 + 20 = 65C < 70, a genuine recovery while the fault stays active."""
        sp = SystemPhysics()
        sp.set_fault("motor", "overheat")

        t_warn = t_crit = None
        for s in range(1, 121):
            sp.tick(1.0)
            temp = sp.telemetry("motor")["temp"]
            if t_warn is None and temp > 70.0:
                t_warn = s
            if t_crit is None and temp > 85.0:
                t_crit = s

        self.assertIsNotNone(t_warn, "temp never crossed 70C (warn) under overheat")
        self.assertIsNotNone(t_crit, "temp never crossed 85C (critical) under overheat")
        # ~12s +/-60% -> [4.8, 19.2]; ~35s +/-40% -> [21, 49]
        self.assertTrue(4.8 <= t_warn <= 19.2, f"t_warn={t_warn}s outside ~12s +/-60%")
        self.assertTrue(21.0 <= t_crit <= 49.0, f"t_crit={t_crit}s outside ~35s +/-40%")

        sp.set_desired("motor", rpm_setpoint=900)
        recovered = False
        for _ in range(200):
            sp.tick(1.0)
            if sp.telemetry("motor")["temp"] < 70.0:
                recovered = True
                break
        self.assertTrue(recovered, "temp never recovered below 70C after mitigation setpoint")
        self.assertEqual(sp.faults["motor"], "overheat", "fault must still be active during recovery")


class TestBearingCascade(unittest.TestCase):
    def test_bearing_cascade_matches_reference_timings(self):
        """CONTRACTS.md §2.1 cascade table (bearing fault @ defaults): sag ramps to full
        (0.30) in 30s -> rpm 1260, line flow -> ~98 L/min, tank net ~ -42 L/min
        (-0.35 %/s from a 50% starting level)."""
        sp = SystemPhysics()
        # Reach (already-at) steady state before injecting the fault.
        for _ in range(60):
            sp.tick(1.0)

        sp.set_fault("motor", "bearing")

        snapshots = {}
        for s in range(1, 91):
            sp.tick(1.0)
            if s in (35, 40, 60, 90):
                snapshots[s] = {
                    "rpm": sp.telemetry("motor")["rpm"],
                    "flow": sp.telemetry("valve")["flow"],
                    "level": sp.telemetry("tank")["level_pct"],
                }

        self.assertLessEqual(snapshots[35]["rpm"], 1300, "rpm should have sagged to ~1260 by t=35s")
        self.assertLessEqual(snapshots[40]["flow"], 105, "line flow should have dropped to ~98 L/min by t=40s")
        self.assertLess(snapshots[60]["level"], 40, "tank level should be below 40% by t=60s")
        self.assertLess(snapshots[90]["level"], 30, "tank level should be below 30% by t=90s")


class TestCompensation(unittest.TestCase):
    def test_raising_setpoint_and_drain_rate_compensates_for_sag(self):
        """With bearing fault at full sag, effective rpm at setpoint 2600 is
        2600*(1-0.30)=1820 -> capacity 200*(1820/1800)*0.7 ~= 141.6, comfortably clearing
        the 135 L/min bar well inside 25s (flow tau is only 3s). Then raising drain_rate
        to 110 turns the tank's net flow strongly positive (~+31 L/min => +0.26 %/s),
        clearing the required 3% rise over 60s with large margin."""
        sp = SystemPhysics()
        sp.set_fault("motor", "bearing")
        for _ in range(60):  # run to full sag
            sp.tick(1.0)

        sp.set_desired("motor", rpm_setpoint=2600)
        flow_ok = False
        for _ in range(25):
            sp.tick(1.0)
            if sp.telemetry("valve")["flow"] >= 135:
                flow_ok = True
                break
        self.assertTrue(flow_ok, "flow never reached >=135 L/min within 25s of setpoint 2600")

        level_before = sp.telemetry("tank")["level_pct"]
        sp.set_desired("tank", drain_rate=110)
        for _ in range(60):
            sp.tick(1.0)
        level_after = sp.telemetry("tank")["level_pct"]
        self.assertGreaterEqual(
            level_after - level_before, 3.0, "tank level did not rise >=3% over 60s after drain_rate=110"
        )


class TestValveStuck(unittest.TestCase):
    def test_stuck_freezes_position_and_clear_resumes_slew(self):
        sp = SystemPhysics()
        sp.set_fault("valve", "stuck")
        for _ in range(5):
            sp.tick(1.0)
        frozen_at = sp.reported("valve")["position"]

        sp.set_desired("valve", position=20)
        for _ in range(20):
            sp.tick(1.0)
        self.assertEqual(
            sp.reported("valve")["position"], frozen_at, "valve position moved despite `stuck` fault"
        )

        sp.set_fault("valve", None)
        for _ in range(20):
            sp.tick(1.0)
        self.assertEqual(
            sp.reported("valve")["position"], 20, "valve did not resume slewing toward desired after clear"
        )


class TestPumpLeak(unittest.TestCase):
    def test_leak_settles_low_pressure_and_reduced_flow_then_clears(self):
        sp = SystemPhysics()
        sp.set_fault("pump", "leak")
        for _ in range(120):
            sp.tick(1.0)

        pressure = sp.telemetry("pump")["pressure"]
        flow = sp.telemetry("valve")["flow"]
        self.assertAlmostEqual(pressure, 1.5, delta=0.3)
        self.assertLess(pressure, 2.5)
        self.assertAlmostEqual(flow, 105.0, delta=3.0)  # 140 * 0.75

        sp.set_fault("pump", None)
        for _ in range(120):
            sp.tick(1.0)
        self.assertAlmostEqual(sp.telemetry("pump")["pressure"], 3.8, delta=0.3)
        self.assertAlmostEqual(sp.telemetry("valve")["flow"], 140.0, delta=3.0)


class TestFaultValidation(unittest.TestCase):
    def test_set_fault_rejects_unknown_component_or_mode(self):
        sp = SystemPhysics()
        with self.assertRaises(ValueError):
            sp.set_fault("motor", "leak")  # leak is a pump-only mode
        with self.assertRaises(ValueError):
            sp.set_fault("bogus", "bearing")
        with self.assertRaises(ValueError):
            sp.set_fault("tank", "anything")

    def test_clear_faults_resets_all_components(self):
        sp = SystemPhysics()
        sp.set_fault("motor", "bearing")
        sp.set_fault("pump", "leak")
        sp.set_fault("valve", "stuck")
        sp.clear_faults()
        self.assertEqual(sp.faults, {"motor": None, "pump": None, "valve": None, "tank": None})


class TestMalformedDesiredIgnored(unittest.TestCase):
    def test_set_desired_ignores_malformed_values(self):
        sp = SystemPhysics()
        sp.set_desired("motor", rpm_setpoint="not-a-number")
        self.assertEqual(sp.desired("motor")["rpm_setpoint"], 1800)
        sp.set_desired("pump", pump_speed=None)
        self.assertEqual(sp.desired("pump")["pump_speed"], 70)


if __name__ == "__main__":
    unittest.main()
