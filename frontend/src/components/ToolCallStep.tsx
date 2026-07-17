import { useState } from 'react'
import type { ReactNode } from 'react'
import type { ToolStep } from '../types'
import { pretty } from '../lib/format'

const WRITE_TOOLS = new Set([
  'set_motor_rpm',
  'set_pump_speed',
  'set_valve_position',
  'set_tank_drain_rate',
  'run_stress_test',
])

function isWrite(step: ToolStep): boolean {
  // A present ditto_request means a mutating call — the demo money-shot.
  return step.ditto_request != null || WRITE_TOOLS.has(step.name)
}

function StatusChip({ step }: { step: ToolStep }) {
  if (step.status === 'running') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-sev-warn/10 px-2 py-0.5 text-[11px] font-medium text-sev-warn">
        <span className="dt-spin inline-block h-2.5 w-2.5 rounded-full border border-sev-warn border-t-transparent" />
        running
      </span>
    )
  }
  if (step.status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-sev-critical/12 px-2 py-0.5 text-[11px] font-medium text-sev-critical">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-sev-critical" />
        failed
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-sev-recovered/12 px-2 py-0.5 text-[11px] font-medium text-sev-recovered">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-sev-recovered" />
      ok
    </span>
  )
}

export default function ToolCallStep({ step }: { step: ToolStep }) {
  const write = isWrite(step)
  // Write tools default expanded so the ditto_request is visible (money-shot).
  const [open, setOpen] = useState(write)

  return (
    <div className="dt-fade-in overflow-hidden rounded-lg border border-hair bg-surface-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-elevated/60"
      >
        <svg
          viewBox="0 0 24 24"
          className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
        >
          <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="shrink-0 text-muted">
          <ToolIcon />
        </span>
        <code className="font-mono text-[13px] text-ink">{step.name}</code>
        {write && (
          <span className="rounded bg-accent/10 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-accent">
            write
          </span>
        )}
        <span className="ml-auto">
          <StatusChip step={step} />
        </span>
      </button>

      {open && (
        <div className="space-y-2.5 border-t border-hair px-3 py-3">
          {/* args */}
          <Block label="args">
            <pre className="scroll-thin overflow-x-auto font-mono text-[12px] leading-relaxed text-ink-2">
              {pretty(step.args)}
            </pre>
          </Block>

          {/* ditto_request — the money-shot for write tools */}
          {step.ditto_request && (
            <div className="overflow-hidden rounded-md border border-accent/30 bg-accent/[0.06]">
              <div className="flex items-center gap-2 border-b border-accent/20 px-2.5 py-1.5">
                <span className="font-mono text-[11px] font-semibold tracking-wide text-accent">
                  → Ditto
                </span>
                <span className="rounded bg-accent/15 px-1.5 py-px font-mono text-[10px] font-semibold text-accent">
                  {step.ditto_request.method}
                </span>
                {typeof step.ditto_status === 'number' && (
                  <span className="ml-auto rounded bg-sev-recovered/12 px-1.5 py-px font-mono text-[10px] font-semibold tabular-nums text-sev-recovered">
                    {step.ditto_status}
                  </span>
                )}
              </div>
              <pre className="scroll-thin overflow-x-auto px-2.5 py-2 font-mono text-[12px] leading-relaxed text-ink-2">
                <span className="text-muted">{step.ditto_request.path}</span>
                {'\n'}
                {pretty(step.ditto_request.body)}
              </pre>
            </div>
          )}

          {/* result (read tools & confirmations) */}
          {step.result !== undefined && step.status !== 'running' && (
            <Block label="result">
              <pre className="scroll-thin max-h-48 overflow-auto font-mono text-[12px] leading-relaxed text-ink-2">
                {pretty(step.result)}
              </pre>
            </Block>
          )}
        </div>
      )}
    </div>
  )
}

function Block({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
        {label}
      </div>
      {children}
    </div>
  )
}

function ToolIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        d="M14.7 6.3a4 4 0 0 0-5.4 5.4l-6 6 2 2 6-6a4 4 0 0 0 5.4-5.4l-2.5 2.5-2-2 2.5-2.5Z"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
