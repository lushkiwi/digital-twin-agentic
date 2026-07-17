import type {
  ChatEvent,
  ComponentId,
  Config,
  ControlResponse,
  HistoryMessage,
  Observation,
  ParamsResponse,
  SystemResponse,
  TelemetryFrame,
  TestResult,
} from '../types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`${url} → ${res.status}`)
  return (await res.json()) as T
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${url} → ${res.status}`)
  return (await res.json()) as T
}

/**
 * Demo mode (`?demo=1`) mirrors v1: the UI must be fully exercisable with no
 * backend. Reads are seeded into the store directly; the only network-shaped
 * call the UI makes interactively is POST /api/control, so we simulate its
 * success here so the manual-control money-shot (ditto_status 204) is visible.
 */
let DEMO = false
export function setDemoMode(v: boolean): void {
  DEMO = v
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// ---- REST helpers (CONTRACTS §3) ----

export function getHealth(): Promise<{ ok: boolean; ditto_connected: boolean }> {
  return getJson('/api/health')
}

/** Chart backfill on page load — v2 frame data objects, oldest first. */
export async function getTelemetry(minutes = 10): Promise<TelemetryFrame[]> {
  const r = await getJson<{ points: TelemetryFrame[] }>(`/api/telemetry?minutes=${minutes}`)
  return r.points ?? []
}

export async function getObservations(limit = 50): Promise<Observation[]> {
  const r = await getJson<{ observations: Observation[] }>(`/api/observations?limit=${limit}`)
  return r.observations ?? []
}

/** Writable-parameter registry that drives the manual-control editors. */
export function getParams(): Promise<ParamsResponse> {
  return getJson('/api/params')
}

/** Full snapshot (reported + desired + telemetry + status per component). */
export function getSystem(): Promise<SystemResponse> {
  return getJson('/api/system')
}

/**
 * Operator write — POST /api/control/{component}/{param}. Routes through the
 * same executor as LLM writes; success returns ditto_status 204 and emits an
 * operator observation server-side.
 */
export function postControl(
  component: ComponentId,
  param: string,
  value: number,
  reason?: string,
): Promise<ControlResponse> {
  if (DEMO) {
    // Simulate the executor round-trip so the drawer can show the 204 money-shot.
    return sleep(480).then(() => ({
      ok: true,
      ditto_status: 204,
      ditto_request: {
        method: 'PUT',
        path: `/api/2/things/org.acme:${component}-01/features/${component}/desiredProperties/${param}`,
        body: value,
      },
    }))
  }
  return postJson(`/api/control/${component}/${param}`, {
    value,
    reason: reason ?? 'manual operator adjustment',
  })
}

export function getConfig(): Promise<Config> {
  return getJson('/api/config')
}

export function postConfig(body: {
  model: string
  api_key: string | null
  base_url: string | null
}): Promise<Config> {
  return postJson('/api/config', body)
}

export function testConfig(): Promise<TestResult> {
  return postJson('/api/config/test', {})
}

/**
 * Stream the interactive-agent chat.
 *
 * POST /api/chat returns text/event-stream where each event is `data: <json>\n\n`.
 * EventSource can't be used (it's a POST), so we read the ReadableStream and
 * parse SSE frames by hand. `onEvent` fires for every parsed event in order.
 */
export async function streamChat(
  message: string,
  history: HistoryMessage[],
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ message, history }),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`/api/chat → ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const flushFrame = (frame: string) => {
    // A frame may carry multiple `data:` lines — concatenate them (SSE spec).
    const dataLines: string[] = []
    for (const raw of frame.split('\n')) {
      const line = raw.replace(/\r$/, '')
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (dataLines.length === 0) return
    const payload = dataLines.join('\n').trim()
    if (!payload) return // keep-alive / empty
    try {
      onEvent(JSON.parse(payload) as ChatEvent)
    } catch {
      // ignore unparseable frames rather than killing the stream
    }
  }

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, '\n')
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      flushFrame(frame)
    }
  }
  // trailing frame without a terminating blank line
  if (buffer.trim()) flushFrame(buffer)
}
