import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import type { Observation, Severity } from '../types'
import { hms } from '../lib/format'

const SEV: Record<
  Severity,
  { label: string; text: string; bar: string; tint: string; icon: string }
> = {
  info: {
    label: 'info',
    text: 'text-sev-info',
    bar: 'bg-sev-info',
    tint: 'bg-sev-info/[0.05]',
    icon: 'M12 8v.5M12 11v5',
  },
  warn: {
    label: 'warn',
    text: 'text-sev-warn',
    bar: 'bg-sev-warn',
    tint: 'bg-sev-warn/[0.06]',
    icon: 'M12 8v5M12 16.5v.5',
  },
  critical: {
    label: 'critical',
    text: 'text-sev-critical',
    bar: 'bg-sev-critical',
    tint: 'bg-sev-critical/[0.08]',
    icon: 'M12 8v5M12 16.5v.5',
  },
  recovered: {
    label: 'recovered',
    text: 'text-sev-recovered',
    bar: 'bg-sev-recovered',
    tint: 'bg-sev-recovered/[0.06]',
    icon: 'M8 12.5l3 3 5-6',
  },
}

export default function ObservationLog() {
  const observations = useStore((s) => s.observations)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)
  const count = observations.length

  // Newest-first display (reverse-chronological alert feed).
  const rows = [...observations].reverse()

  // Auto-pin to the newest edge (top). Release when the user scrolls into history.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && pinned) el.scrollTop = 0
  }, [count, pinned])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => setPinned(el.scrollTop <= 16)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  function jumpToLatest() {
    const el = scrollRef.current
    if (el) el.scrollTop = 0
    setPinned(true)
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {rows.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="space-y-1.5">
            {rows.map((o) => (
              <ObservationRow key={o.id} obs={o} />
            ))}
          </ul>
        )}
      </div>

      {!pinned && count > 0 && (
        <button
          type="button"
          onClick={jumpToLatest}
          className="absolute left-1/2 top-2.5 -translate-x-1/2 rounded-full border border-hair-2 bg-elevated/95 px-3 py-1 text-[11px] font-medium text-ink-2 shadow-lg backdrop-blur transition-colors hover:text-ink"
        >
          ↑ Jump to latest
        </button>
      )}
    </div>
  )
}

function ObservationRow({ obs }: { obs: Observation }) {
  const s = SEV[obs.severity]
  return (
    <li
      className={`dt-fade-in relative overflow-hidden rounded-lg border border-hair ${s.tint} pl-3 pr-3 py-2.5`}
    >
      <span className={`absolute left-0 top-0 h-full w-[3px] ${s.bar}`} aria-hidden />
      <div className="flex items-center gap-2">
        <svg viewBox="0 0 24 24" className={`h-3.5 w-3.5 shrink-0 ${s.text}`} fill="none" stroke="currentColor" strokeWidth="2">
          {obs.severity === 'recovered' || obs.severity === 'info' ? (
            <>
              <circle cx="12" cy="12" r="9" />
              <path d={s.icon} strokeLinecap="round" strokeLinejoin="round" />
            </>
          ) : (
            <>
              <path d="M12 3.5 22 20H2L12 3.5Z" strokeLinejoin="round" />
              <path d={s.icon} strokeLinecap="round" />
            </>
          )}
        </svg>
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink">{obs.title}</span>
        <time className="shrink-0 font-mono text-[11px] tabular-nums text-muted">{hms(obs.ts)}</time>
      </div>
      <p className="mt-1 pl-[22px] text-[12px] leading-relaxed text-ink-2">{obs.detail}</p>
      <div className="mt-1.5 flex items-center gap-1.5 pl-[22px]">
        <span className={`text-[10px] font-semibold uppercase tracking-wide ${s.text}`}>{s.label}</span>
        <span className="text-hair-2">·</span>
        <SourceBadge source={obs.source} />
      </div>
    </li>
  )
}

function SourceBadge({ source }: { source: Observation['source'] }) {
  const isLlm = source === 'llm'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px] font-medium ${
        isLlm ? 'bg-accent/10 text-accent' : 'bg-white/[0.05] text-muted'
      }`}
      title={isLlm ? 'Flagged by the LLM sleeper' : 'Flagged by a rule'}
    >
      {isLlm ? (
        <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2" strokeLinecap="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 7h16M4 12h16M4 17h10" strokeLinecap="round" />
        </svg>
      )}
      {source}
    </span>
  )
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="relative grid h-11 w-11 place-items-center rounded-xl border border-hair bg-surface-2 text-muted">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.7">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" strokeLinecap="round" />
        </svg>
        <span className="dt-pulse absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-sev-recovered ring-2 ring-surface" />
      </div>
      <p className="mt-3 text-[13px] font-medium text-ink-2">Watching telemetry — no anomalies yet</p>
      <p className="mt-1 max-w-[16rem] text-[12px] leading-relaxed text-muted">
        The sleeper agent observes passively and flags anomalies here. It never acts.
      </p>
    </div>
  )
}
