// Types mirror CONTRACTS.md §3 exactly. Field names are load-bearing.
// V2: four-component chained system (motor → pump → valve → tank).

export type ComponentId = 'motor' | 'pump' | 'valve' | 'tank'

/** Per-component health, derived by the backend from active sleeper rule flags. */
export type Status = 'ok' | 'warn' | 'critical'

// ---- Per-component telemetry frame slices (CONTRACTS §3.2, field-for-field) ----

export interface MotorFrame {
  rpm: number
  temp: number // °C
  current: number // A
  rpm_setpoint_reported: number // int 0–3000
  rpm_setpoint_desired: number // int 0–3000
  status: Status
}

export interface PumpFrame {
  flow: number // L/min
  pressure: number // bar
  temp: number // °C
  pump_speed_reported: number // int 0–100
  pump_speed_desired: number // int 0–100
  status: Status
}

export interface ValveFrame {
  flow: number // L/min (post-throttle line flow)
  position_reported: number // int 0–100
  position_desired: number // int 0–100
  status: Status
}

export interface TankFrame {
  level_pct: number // 0–100
  inflow: number // L/min
  outflow: number // L/min
  drain_rate_reported: number // int 0–200
  drain_rate_desired: number // int 0–200
  status: Status
}

export interface FrameComponents {
  motor: MotorFrame
  pump: PumpFrame
  valve: ValveFrame
  tank: TankFrame
}

/** WS "telemetry" frame data + GET /api/telemetry point shape (v2). */
export interface TelemetryFrame {
  ts: string // ISO8601 UTC
  components: FrameComponents
}

export type Severity = 'info' | 'warn' | 'critical' | 'recovered'
export type ObservationSource = 'rule' | 'llm' | 'operator'

/** WS "observation" frame data + GET /api/observations item shape. */
export interface Observation {
  id: string
  ts: string
  severity: Severity
  source: ObservationSource
  component: ComponentId | null // null = system-wide / cross-component
  title: string
  detail: string
}

// ---- Writable parameter registry (GET /api/params, CONTRACTS §3.3) ----

export type ParamKind = 'int'

export interface ParamSpec {
  name: string // e.g. "rpm_setpoint"
  label: string // e.g. "RPM setpoint"
  unit: string // e.g. "rpm"
  kind: ParamKind
  min: number
  max: number
  step: number
}

export interface ComponentParams {
  label: string // e.g. "Motor"
  thing_id: string // e.g. "org.acme:motor-01"
  params: ParamSpec[]
}

export interface ParamsResponse {
  components: Record<ComponentId, ComponentParams>
}

/** POST /api/control/{component}/{param} response (CONTRACTS §3.3). */
export interface ControlResponse {
  ok: boolean
  ditto_status?: number
  ditto_request?: DittoRequest | null
  error?: string
}

/** GET /api/system response (CONTRACTS §3.3). */
export interface SystemComponentState {
  reported: Record<string, unknown>
  desired: Record<string, unknown>
  telemetry: Record<string, unknown>
  status: Status
}

export interface SystemResponse {
  components: Record<ComponentId, SystemComponentState>
  ditto_connected: boolean
}

/** GET/POST /api/config response. */
export interface Config {
  model: string
  api_key_masked: string | null
  base_url: string | null
  presets: string[]
}

/** POST /api/config/test response. */
export interface TestResult {
  ok: boolean
  error: string | null
  latency_ms: number
}

// ---- WebSocket frames (backend → UI) ----
export type WsFrame =
  | { type: 'telemetry'; data: TelemetryFrame }
  | { type: 'observation'; data: Observation }
  | { type: 'status'; data: { ditto_connected: boolean } }

// ---- Chat SSE events (POST /api/chat) ----
export interface DittoRequest {
  method: string
  path: string
  body: unknown
}

export type ChatEvent =
  | { type: 'plan_text'; text: string }
  | { type: 'tool_call'; id: string; name: string; args: Record<string, unknown> }
  | {
      type: 'tool_result'
      id: string
      ok: boolean
      result?: unknown
      ditto_request?: DittoRequest | null
      ditto_status?: number
    }
  | { type: 'assistant_text'; text: string }
  | { type: 'error'; message: string }
  | { type: 'done' }

// ---- UI-side chat model (per-turn ordered step events) ----
export type ToolStatus = 'running' | 'ok' | 'failed'

export interface ToolStep {
  kind: 'tool'
  id: string
  name: string
  args: Record<string, unknown>
  status: ToolStatus
  result?: unknown
  ditto_request?: DittoRequest | null
  ditto_status?: number
}

export type Step =
  | { kind: 'plan_text'; text: string }
  | { kind: 'assistant_text'; text: string }
  | { kind: 'error'; message: string }
  | ToolStep

export type TurnStatus = 'streaming' | 'done' | 'error'

export interface ChatTurn {
  id: string
  userMessage: string
  steps: Step[]
  status: TurnStatus
}

/** History entry for POST /api/chat (finalized turns, text only, last 10). */
export interface HistoryMessage {
  role: 'user' | 'assistant'
  content: string
}
