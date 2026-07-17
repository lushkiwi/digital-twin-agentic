import { create } from 'zustand'
import type {
  ChatEvent,
  ChatTurn,
  ComponentId,
  Config,
  HistoryMessage,
  Observation,
  ParamsResponse,
  Step,
  TelemetryFrame,
  ToolStep,
} from './types'

const TELEMETRY_CAP = 600
const HISTORY_CAP = 10

export interface AppState {
  // ---- data ----
  telemetry: TelemetryFrame[] // oldest → newest, capped
  observations: Observation[] // oldest → newest (contract order); UI reverses for display
  turns: ChatTurn[]
  config: Config | null
  params: ParamsResponse | null // writable-parameter registry (GET /api/params)

  // ---- selection (drives schematic ↔ drawer ↔ charts) ----
  selectedComponent: ComponentId | null

  // ---- connection ----
  wsConnected: boolean
  dittoConnected: boolean

  // ---- chat runtime ----
  streaming: boolean

  // ---- telemetry actions ----
  backfillTelemetry: (points: TelemetryFrame[]) => void
  pushTelemetry: (p: TelemetryFrame) => void

  // ---- observation actions ----
  backfillObservations: (obs: Observation[]) => void
  pushObservation: (o: Observation) => void

  // ---- selection ----
  setSelectedComponent: (c: ComponentId | null) => void

  // ---- connection actions ----
  setWsConnected: (v: boolean) => void
  setDittoConnected: (v: boolean) => void

  // ---- config / params ----
  setConfig: (c: Config) => void
  setParams: (p: ParamsResponse) => void

  // ---- chat ----
  startTurn: (userMessage: string) => string
  applyChatEvent: (turnId: string, ev: ChatEvent) => void
  failTurn: (turnId: string, message: string) => void
  finishTurn: (turnId: string) => void
  buildHistory: () => HistoryMessage[]

  // ---- demo seeding (see lib/demo.ts) ----
  seed: (data: {
    telemetry: TelemetryFrame[]
    observations: Observation[]
    turns: ChatTurn[]
    params?: ParamsResponse
    dittoConnected?: boolean
  }) => void
}

function dedupById<T extends { id: string }>(items: T[]): T[] {
  const seen = new Set<string>()
  const out: T[] = []
  for (const it of items) {
    if (seen.has(it.id)) continue
    seen.add(it.id)
    out.push(it)
  }
  return out
}

export const useStore = create<AppState>((set, get) => ({
  telemetry: [],
  observations: [],
  turns: [],
  config: null,
  params: null,
  selectedComponent: null,
  wsConnected: false,
  dittoConnected: false,
  streaming: false,

  backfillTelemetry: (points) =>
    set(() => ({ telemetry: points.slice(-TELEMETRY_CAP) })),

  pushTelemetry: (p) =>
    set((s) => {
      const next = s.telemetry.length >= TELEMETRY_CAP ? s.telemetry.slice(1) : s.telemetry.slice()
      next.push(p)
      return { telemetry: next }
    }),

  backfillObservations: (obs) => set(() => ({ observations: dedupById(obs) })),

  pushObservation: (o) =>
    set((s) => {
      if (s.observations.some((x) => x.id === o.id)) return s
      return { observations: [...s.observations, o] }
    }),

  setSelectedComponent: (c) => set(() => ({ selectedComponent: c })),

  setWsConnected: (v) => set(() => ({ wsConnected: v })),
  setDittoConnected: (v) => set(() => ({ dittoConnected: v })),
  setConfig: (c) => set(() => ({ config: c })),
  setParams: (p) => set(() => ({ params: p })),

  startTurn: (userMessage) => {
    const id = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const turn: ChatTurn = { id, userMessage, steps: [], status: 'streaming' }
    set((s) => ({ turns: [...s.turns, turn], streaming: true }))
    return id
  },

  applyChatEvent: (turnId, ev) =>
    set((s) => ({
      turns: s.turns.map((t) => {
        if (t.id !== turnId) return t
        return { ...t, steps: reduceEvent(t.steps, ev) }
      }),
    })),

  failTurn: (turnId, message) =>
    set((s) => ({
      streaming: false,
      turns: s.turns.map((t) =>
        t.id === turnId
          ? { ...t, status: 'error', steps: [...t.steps, { kind: 'error', message }] }
          : t,
      ),
    })),

  finishTurn: (turnId) =>
    set((s) => ({
      streaming: false,
      turns: s.turns.map((t) =>
        t.id === turnId && t.status === 'streaming' ? { ...t, status: 'done' } : t,
      ),
    })),

  buildHistory: () => {
    const msgs: HistoryMessage[] = []
    for (const t of get().turns) {
      if (t.status === 'streaming') continue // only finalized turns
      msgs.push({ role: 'user', content: t.userMessage })
      // last assistant_text is the finalized answer for the turn
      const answer = [...t.steps].reverse().find((st) => st.kind === 'assistant_text')
      if (answer && answer.kind === 'assistant_text') {
        msgs.push({ role: 'assistant', content: answer.text })
      }
    }
    return msgs.slice(-HISTORY_CAP)
  },

  seed: (data) =>
    set((s) => ({
      telemetry: data.telemetry.slice(-TELEMETRY_CAP),
      observations: dedupById(data.observations),
      turns: data.turns,
      params: data.params ?? s.params,
      dittoConnected: data.dittoConnected ?? false,
    })),
}))

/** Fold one SSE chat event into a turn's ordered step list. */
function reduceEvent(steps: Step[], ev: ChatEvent): Step[] {
  switch (ev.type) {
    case 'plan_text':
      return [...steps, { kind: 'plan_text', text: ev.text }]
    case 'assistant_text':
      return [...steps, { kind: 'assistant_text', text: ev.text }]
    case 'error':
      return [...steps, { kind: 'error', message: ev.message }]
    case 'tool_call': {
      const step: ToolStep = {
        kind: 'tool',
        id: ev.id,
        name: ev.name,
        args: ev.args,
        status: 'running',
      }
      return [...steps, step]
    }
    case 'tool_result':
      return steps.map((st) =>
        st.kind === 'tool' && st.id === ev.id
          ? {
              ...st,
              status: ev.ok ? 'ok' : 'failed',
              result: ev.result,
              ditto_request: ev.ditto_request ?? null,
              ditto_status: ev.ditto_status,
            }
          : st,
      )
    case 'done':
    default:
      return steps
  }
}
