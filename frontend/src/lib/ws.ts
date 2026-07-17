import { useStore } from '../store'
import type { WsFrame } from '../types'

/**
 * Single WebSocket to /ws (backend fan-out, CONTRACTS §3).
 * - ws(s) scheme derived from location (works behind the Vite proxy).
 * - Auto-reconnect with exponential backoff (1s → 30s cap).
 * - Dispatches frames by `type` into the store.
 */

let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let backoff = 1000
const MAX_BACKOFF = 30000
let stopped = false

function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws`
}

function dispatch(frame: WsFrame) {
  const store = useStore.getState()
  switch (frame.type) {
    case 'telemetry':
      store.pushTelemetry(frame.data)
      break
    case 'observation':
      store.pushObservation(frame.data)
      break
    case 'status':
      store.setDittoConnected(!!frame.data?.ditto_connected)
      break
    default:
      break
  }
}

function scheduleReconnect() {
  if (stopped) return
  if (reconnectTimer) return
  const delay = backoff
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    open()
  }, delay)
  backoff = Math.min(backoff * 2, MAX_BACKOFF)
}

function open() {
  if (stopped) return
  try {
    socket = new WebSocket(wsUrl())
  } catch {
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    backoff = 1000
    useStore.getState().setWsConnected(true)
  }

  socket.onmessage = (ev) => {
    try {
      dispatch(JSON.parse(ev.data) as WsFrame)
    } catch {
      // ignore malformed frames
    }
  }

  socket.onerror = () => {
    // onclose will follow and handle reconnect
  }

  socket.onclose = () => {
    useStore.getState().setWsConnected(false)
    // Ditto status is only meaningful while connected to the backend.
    useStore.getState().setDittoConnected(false)
    socket = null
    scheduleReconnect()
  }
}

/** Start the connection (idempotent). */
export function connectWs() {
  stopped = false
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }
  open()
}

/** Tear down (used on unmount / HMR dispose). */
export function disconnectWs() {
  stopped = true
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (socket) {
    socket.onclose = null
    socket.close()
    socket = null
  }
  backoff = 1000
}
