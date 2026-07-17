import type { ChatTurn, Observation, TelemetryPoint } from '../types'

/**
 * Deterministic demo dataset for `?demo=1`. It ONLY seeds the store so UI work
 * has realistic content while the backend is absent — it never blocks live WS
 * frames (the socket still connects and overwrites/extends this data).
 *
 * Story: pump running hot at speed 80 (overheat fault) crosses 85°C, the
 * interactive agent drops desired speed to 35, and temperature recovers —
 * exercising every severity and the reported↔desired convergence readout.
 * Physics follow CONTRACTS §2 so the curves look like the real sim.
 */

const N = 60 // ~60 points, 1 Hz
const SPEED_HIGH = 80
const SPEED_LOW = 35
const AGENT_ACTS_AT = 40 // step index where desired speed drops to 35

export function makeDemoTelemetry(now = Date.now()): TelemetryPoint[] {
  const points: TelemetryPoint[] = []

  // seed near the speed-80 operating point
  let T = 71
  let P = 5.9
  let F = 150
  let speedReported = SPEED_HIGH
  let valve: 'open' | 'closed' = 'open'

  for (let i = 0; i < N; i++) {
    const t = i // seconds since start
    const speedDesired = i >= AGENT_ACTS_AT ? SPEED_LOW : SPEED_HIGH

    // reported speed slews toward desired at 5 units/s
    if (speedReported < speedDesired) speedReported = Math.min(speedDesired, speedReported + 5)
    else if (speedReported > speedDesired) speedReported = Math.max(speedDesired, speedReported - 5)

    // targets (valve open)
    const tempTarget = 40 + 0.4 * speedReported
    const pressureTarget = 2.0 + 0.05 * speedReported
    const flowTarget = 2.0 * speedReported

    // first-order dynamics + overheat fault (active throughout the demo)
    const overheat = 0.5 * (speedReported / 60)
    T += (tempTarget - T) / 20 + overheat
    P += (pressureTarget - P) / 8
    F += (flowTarget - F) / 3

    // deterministic ripple
    const tempR = T + 0.15 * Math.sin((2 * Math.PI * t) / 7)
    const pressR = P + 0.05 * Math.sin((2 * Math.PI * t) / 5)

    const ts = new Date(now - (N - 1 - i) * 1000).toISOString()
    points.push({
      ts,
      temperature: round(tempR, 2),
      pressure: round(pressR, 2),
      flow_rate: round(F, 1),
      pump_speed_reported: Math.round(speedReported),
      valve_state_reported: valve,
      pump_speed_desired: speedDesired,
      valve_state_desired: valve,
    })
  }
  return points
}

export function makeDemoObservations(now = Date.now()): Observation[] {
  const at = (secAgo: number) => new Date(now - secAgo * 1000).toISOString()
  return [
    {
      id: 'obs-1',
      ts: at(180),
      severity: 'info',
      source: 'rule',
      title: 'Pump online',
      detail: 'Twin connected · pump_speed=80, valve open. Baseline telemetry nominal.',
    },
    {
      id: 'obs-2',
      ts: at(96),
      severity: 'warn',
      source: 'rule',
      title: 'Temperature above 85°C',
      detail: 'temperature reached 86.4°C while pump_speed=80',
    },
    {
      id: 'obs-3',
      ts: at(70),
      severity: 'critical',
      source: 'llm',
      title: 'Sustained overheat — thermal stress',
      detail:
        'temperature held above 86°C for ~20s at speed 80; bearing thermal limit approaching. Recommend reducing load.',
    },
    {
      id: 'obs-4',
      ts: at(6),
      severity: 'recovered',
      source: 'rule',
      title: 'Temperature back within range',
      detail: 'temperature 68.9°C after desired pump_speed lowered to 35',
    },
  ]
}

export function makeDemoTurn(): ChatTurn {
  return {
    id: 'turn-demo',
    userMessage: 'The pump is overheating — bring it back to a safe temperature.',
    status: 'done',
    steps: [
      {
        kind: 'plan_text',
        text: 'Temperature is climbing past 85°C. Let me confirm the current twin state before acting.',
      },
      {
        kind: 'tool',
        id: 'tc_state',
        name: 'get_twin_state',
        args: {},
        status: 'ok',
        result: {
          reported: { pump_speed: 80, valve_state: 'open' },
          desired: { pump_speed: 80, valve_state: 'open' },
          telemetry: { temperature: 86.8, pressure: 5.98, flow_rate: 159.4 },
        },
        ditto_request: null, // read-only tool
      },
      {
        kind: 'plan_text',
        text: 'Confirmed: pump_speed 80, temperature 86.8°C and rising under the overheat fault. Reducing desired speed to 35 to shed thermal load — equilibrium falls back under 85°C.',
      },
      {
        kind: 'tool',
        id: 'tc_speed',
        name: 'set_pump_speed',
        args: { speed: 35, reason: 'reduce thermal load — overheat above 85°C at speed 80' },
        status: 'ok',
        result: { pump_speed_desired: 35 },
        ditto_request: {
          method: 'PUT',
          path: '/api/2/things/org.acme:pump-01/features/pump/desiredProperties/pump_speed',
          body: 35,
        },
        ditto_status: 204,
      },
      {
        kind: 'assistant_text',
        text: 'Done — desired pump speed set to 35. Reported speed is slewing down at 5 units/s and temperature is already falling. The sleeper agent will confirm recovery once it drops back under 85°C.',
      },
    ],
  }
}

function round(n: number, digits: number): number {
  const f = 10 ** digits
  return Math.round(n * f) / f
}
