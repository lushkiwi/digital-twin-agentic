import type {
  ChatTurn,
  FrameComponents,
  Observation,
  ParamsResponse,
  Status,
  TelemetryFrame,
} from '../types'

/**
 * Deterministic v2 demo dataset for `?demo=1`. It ONLY seeds the store so UI
 * work has realistic content while the v2 backend is absent — it never blocks
 * live WS frames (the socket still connects and overwrites/extends this data).
 *
 * Story (CONTRACTS §2.1 bearing cascade, ~3 min of 1 Hz frames):
 *   baseline → motor bearing sag → pump capacity/flow decay → tank draining
 *   → agent raises motor setpoint to 2600 to overcome friction → flow recovers
 *   → operator trims drain_rate to 110 → tank climbing back out of critical.
 *
 * The last frame is the "now" the schematic renders: motor warn (bearing still
 * degraded), pump/valve ok, tank still critical but recovering — three status
 * colors on screen at once. Physics follow §2.1 so the curves match the sim.
 */

const N = 170 // frames, 1 Hz (~2.8 min)
const FAULT_START = 18 // bearing fault begins
const RPM_FIX_AT = 120 // agent raises rpm_setpoint 1800 → 2600
const DRAIN_TRIM_AT = 134 // operator drops drain_rate 140 → 110

const SPEED = 70 // pump_speed (unchanged)
const POS = 100 // valve position (unchanged, wide open)

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const round = (n: number, d: number) => {
  const f = 10 ** d
  return Math.round(n * f) / f
}

function motorStatus(temp: number, rpm: number, setpoint: number): Status {
  if (temp > 85) return 'critical'
  if (temp > 70 || rpm < 0.93 * setpoint) return 'warn'
  return 'ok'
}
function pumpStatus(pressure: number, temp: number): Status {
  if (pressure > 7.5) return 'critical'
  if (pressure < 2.5 || pressure > 6.0 || temp > 75) return 'warn'
  return 'ok'
}
function valveStatus(reported: number, desired: number): Status {
  return Math.abs(reported - desired) > 2 ? 'warn' : 'ok'
}
function tankStatus(level: number): Status {
  if (level < 30 || level > 95) return 'critical'
  if (level < 40 || level > 90) return 'warn'
  return 'ok'
}

/** Run the deterministic cascade and emit full v2 frames. */
export function makeDemoTelemetry(now = Date.now()): TelemetryFrame[] {
  let rpm = 1800
  let mTemp = 55
  let cur = 7.6
  let flow = 140
  let press = 3.8
  let pTemp = 54.5
  let level = 50

  const frames: TelemetryFrame[] = []

  for (let t = 0; t < N; t++) {
    const sag = t >= FAULT_START ? clamp((t - FAULT_START) * 0.01, 0, 0.3) : 0
    const rpmSet = t >= RPM_FIX_AT ? 2600 : 1800
    const drain = t >= DRAIN_TRIM_AT ? 110 : 140

    // motor (first-order relaxation + actuator slew + bearing heat)
    rpm += clamp(rpmSet * (1 - sag) - rpm, -60, 60)
    mTemp += (35 + rpm / 90 - mTemp) / 25 + 3.0 * sag
    cur += (2 + 8 * (rpm / 1800) * (SPEED / 100) + 8 * sag - cur) / 5

    // pump
    const capacity = 200 * (rpm / 1800) * (SPEED / 100)
    flow += (capacity * (POS / 100) - flow) / 3
    press += (1 + 4 * (SPEED / 100) * (rpm / 1800) + 3 * (1 - POS / 100) - press) / 5
    pTemp += (30 + 0.35 * SPEED - pTemp) / 20

    // tank (the only integrator)
    const inflow = flow
    const outflow = level <= 0 ? 0 : drain
    level = clamp(level + (inflow - outflow) / 120, 0, 100)

    // read-only ripple for display (never fed back into state)
    const mTempR = mTemp + 0.15 * Math.sin((2 * Math.PI * t) / 7)
    const pressR = press + 0.05 * Math.sin((2 * Math.PI * t) / 5)

    const lineFlow = round(flow, 1)
    const components: FrameComponents = {
      motor: {
        rpm: round(rpm, 0),
        temp: round(mTempR, 1),
        current: round(cur, 2),
        rpm_setpoint_reported: rpmSet,
        rpm_setpoint_desired: rpmSet,
        status: motorStatus(mTempR, rpm, rpmSet),
      },
      pump: {
        flow: lineFlow,
        pressure: round(pressR, 2),
        temp: round(pTemp, 1),
        pump_speed_reported: SPEED,
        pump_speed_desired: SPEED,
        status: pumpStatus(pressR, pTemp),
      },
      valve: {
        flow: lineFlow,
        position_reported: POS,
        position_desired: POS,
        status: valveStatus(POS, POS),
      },
      tank: {
        level_pct: round(level, 1),
        inflow: lineFlow,
        outflow: round(outflow, 1),
        drain_rate_reported: drain,
        drain_rate_desired: drain,
        status: tankStatus(level),
      },
    }

    frames.push({ ts: new Date(now - (N - 1 - t) * 1000).toISOString(), components })
  }

  return frames
}

/** Observations that narrate the cascade — every severity/source/component mix. */
export function makeDemoObservations(now = Date.now()): Observation[] {
  const atT = (t: number) => new Date(now - (N - 1 - t) * 1000).toISOString()
  return [
    {
      id: 'obs-1',
      ts: atT(2),
      severity: 'info',
      source: 'rule',
      component: null,
      title: 'System nominal',
      detail: 'All four things reporting · motor 1800 rpm, line flow 140 L/min, tank holding 50%.',
    },
    {
      id: 'obs-2',
      ts: atT(41),
      severity: 'warn',
      source: 'rule',
      component: 'motor',
      title: 'Motor RPM sag',
      detail: 'rpm 1386 vs setpoint 1800 (23% below) sustained past grace — motor not converging.',
    },
    {
      id: 'obs-3',
      ts: atT(48),
      severity: 'warn',
      source: 'rule',
      component: null,
      title: 'Flow deficit — root cause upstream: motor',
      detail:
        'line flow 102 L/min vs expected 140 (27% deficit) while rpm 1300 lags setpoint 1800 — deficit originates at the motor, not the valve.',
    },
    {
      id: 'obs-4',
      ts: atT(64),
      severity: 'warn',
      source: 'rule',
      component: 'tank',
      title: 'Tank level low',
      detail: 'level 39.7% below warn threshold 40%, draining ~0.35 %/s as inflow (98) trails drain (140).',
    },
    {
      id: 'obs-5',
      ts: atT(75),
      severity: 'warn',
      source: 'llm',
      component: null,
      title: 'Bearing-friction cascade',
      detail:
        'Motor current is elevated (8.3 A) while rpm is depressed (1260) — the signature of bearing friction, not load. The 30% sag is starving pump capacity (flow ~98) and the tank is now draining. Compensating the motor setpoint would restore flow without clearing the underlying fault.',
    },
    {
      id: 'obs-6',
      ts: atT(92),
      severity: 'critical',
      source: 'rule',
      component: 'tank',
      title: 'Tank level critically low',
      detail: 'level 29.9% below critical threshold 30% — refill required before it empties.',
    },
    {
      id: 'obs-7',
      ts: atT(131),
      severity: 'recovered',
      source: 'rule',
      component: null,
      title: 'Flow deficit cleared',
      detail: 'line flow restored to 138 L/min after motor setpoint raised to 2600 — effective rpm back near 1820.',
    },
    {
      id: 'obs-8',
      ts: atT(134),
      severity: 'info',
      source: 'operator',
      component: 'tank',
      title: 'Operator set drain_rate → 110',
      detail: 'trim drain to rebuild tank level while flow recovers (net +30 L/min).',
    },
  ]
}

/** Writable-parameter registry (mirrors CONTRACTS §3.1 / §3.3 for demo mode). */
export function makeDemoParams(): ParamsResponse {
  return {
    components: {
      motor: {
        label: 'Motor',
        thing_id: 'org.acme:motor-01',
        params: [
          { name: 'rpm_setpoint', label: 'RPM setpoint', unit: 'rpm', kind: 'int', min: 0, max: 3000, step: 50 },
        ],
      },
      pump: {
        label: 'Pump',
        thing_id: 'org.acme:pump-01',
        params: [
          { name: 'pump_speed', label: 'Pump speed', unit: '%', kind: 'int', min: 0, max: 100, step: 5 },
        ],
      },
      valve: {
        label: 'Valve',
        thing_id: 'org.acme:valve-01',
        params: [
          { name: 'position', label: 'Position', unit: '%', kind: 'int', min: 0, max: 100, step: 5 },
        ],
      },
      tank: {
        label: 'Tank',
        thing_id: 'org.acme:tank-01',
        params: [
          { name: 'drain_rate', label: 'Drain rate', unit: 'L/min', kind: 'int', min: 0, max: 200, step: 10 },
        ],
      },
    },
  }
}

/** Interactive-agent turn: read state, then set_motor_rpm to overcome the sag. */
export function makeDemoTurn(): ChatTurn {
  return {
    id: 'turn-demo',
    userMessage: 'Line flow is dropping and the tank is draining — what is going on, and can you keep it running?',
    status: 'done',
    steps: [
      {
        kind: 'plan_text',
        text: 'Flow is well below the ~140 baseline and the tank is losing level. Let me pull the full system state before acting.',
      },
      {
        kind: 'tool',
        id: 'tc_state',
        name: 'get_system_state',
        args: {},
        status: 'ok',
        result: {
          motor: { reported: { rpm_setpoint: 1800 }, telemetry: { rpm: 1261, current: 8.31, temp: 70.6 }, status: 'warn' },
          pump: { reported: { pump_speed: 70 }, telemetry: { flow: 98.4, pressure: 2.96, temp: 54.5 }, status: 'ok' },
          valve: { reported: { position: 100 }, telemetry: { flow: 98.4 }, status: 'ok' },
          tank: { reported: { drain_rate: 140 }, telemetry: { level_pct: 22.4, inflow: 98.4, outflow: 140.0 }, status: 'critical' },
        },
        ditto_request: null, // read-only tool
      },
      {
        kind: 'plan_text',
        text: 'Signature is bearing friction: motor current is UP (8.31 A) while rpm is DOWN (1261 vs 1800 setpoint) — not a load problem. That ~30% sag starves pump capacity, so line flow fell to ~98 and the tank is draining. I will compensate by raising the motor setpoint so effective rpm returns to ~1800 and flow to ~140.',
      },
      {
        kind: 'tool',
        id: 'tc_rpm',
        name: 'set_motor_rpm',
        args: { rpm: 2600, reason: 'compensate bearing sag — restore effective rpm ~1800 and line flow to ~140' },
        status: 'ok',
        result: { rpm_setpoint_desired: 2600 },
        ditto_request: {
          method: 'PUT',
          path: '/api/2/things/org.acme:motor-01/features/motor/desiredProperties/rpm_setpoint',
          body: 2600,
        },
        ditto_status: 204,
      },
      {
        kind: 'assistant_text',
        text: 'Raised the motor setpoint to 2600 rpm to overcome the bearing friction — effective rpm should climb back toward 1820 and line flow to ~140 within ~20s. The bearing itself is still degraded (watch motor temp and current), so this compensates rather than fixes it. The tank is critically low, so I would also trim the drain rate to ~110 L/min to rebuild level while flow recovers — say the word and I will apply it.',
      },
    ],
  }
}
