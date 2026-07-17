// Types mirror CONTRACTS.md §3 exactly. Field names are load-bearing.

export type ValveState = 'open' | 'closed'

/** WS "telemetry" frame data + GET /api/telemetry point shape. */
export interface TelemetryPoint {
  ts: string // ISO8601 UTC
  temperature: number // °C
  pressure: number // bar
  flow_rate: number // L/min
  pump_speed_reported: number // 0–100
  valve_state_reported: ValveState
  pump_speed_desired: number // 0–100
  valve_state_desired: ValveState
}

export type Severity = 'info' | 'warn' | 'critical' | 'recovered'
export type ObservationSource = 'rule' | 'llm'

/** WS "observation" frame data + GET /api/observations item shape. */
export interface Observation {
  id: string
  ts: string
  severity: Severity
  source: ObservationSource
  title: string
  detail: string
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
  | { type: 'telemetry'; data: TelemetryPoint }
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
