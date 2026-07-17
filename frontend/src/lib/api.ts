import type {
  ChatEvent,
  Config,
  HistoryMessage,
  Observation,
  TelemetryPoint,
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

// ---- REST helpers (CONTRACTS §3) ----

export function getHealth(): Promise<{ ok: boolean; ditto_connected: boolean }> {
  return getJson('/api/health')
}

/** Chart backfill on page load. */
export async function getTelemetry(minutes = 10): Promise<TelemetryPoint[]> {
  const r = await getJson<{ points: TelemetryPoint[] }>(`/api/telemetry?minutes=${minutes}`)
  return r.points ?? []
}

export async function getObservations(limit = 50): Promise<Observation[]> {
  const r = await getJson<{ observations: Observation[] }>(`/api/observations?limit=${limit}`)
  return r.observations ?? []
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
