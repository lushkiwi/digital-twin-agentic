/** HH:MM:SS from an ISO timestamp (local time). */
export function hms(ts: string | number): string {
  const d = typeof ts === 'number' ? new Date(ts) : new Date(ts)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** Epoch ms from an ISO timestamp (NaN-safe → 0). */
export function toEpoch(ts: string): number {
  const t = new Date(ts).getTime()
  return Number.isNaN(t) ? 0 : t
}

/** Fixed-decimal number, safe for null/undefined. */
export function fixed(n: number | undefined | null, digits = 1): string {
  if (n == null || Number.isNaN(n)) return '—'
  return n.toFixed(digits)
}

/** Stable, readable JSON block. */
export function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
