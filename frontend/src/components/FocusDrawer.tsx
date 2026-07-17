import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useStore } from '../store'
import { postControl } from '../lib/api'
import type { ComponentId, ControlResponse, ParamSpec, Status, TelemetryFrame } from '../types'
import { COMPONENT_CHARTS, timeDomain } from '../lib/series'
import { Mini } from './TelemetryChart'
import { pretty } from '../lib/format'

const STATUS_UI: Record<Status, { label: string; cls: string; dot: string }> = {
  ok: { label: 'ok', cls: 'text-sev-recovered', dot: 'bg-sev-recovered' },
  warn: { label: 'warn', cls: 'text-sev-warn', dot: 'bg-sev-warn' },
  critical: { label: 'critical', cls: 'text-sev-critical', dot: 'bg-sev-critical' },
}

export default function FocusDrawer() {
  const selected = useStore((s) => s.selectedComponent)
  const setSelected = useStore((s) => s.setSelectedComponent)
  const telemetry = useStore((s) => s.telemetry)
  const params = useStore((s) => s.params)

  // Close on Escape whenever the drawer is open.
  useEffect(() => {
    if (!selected) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelected(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, setSelected])

  const domain = useMemo(() => timeDomain(telemetry), [telemetry])

  if (!selected) return null

  const latest: TelemetryFrame | undefined = telemetry[telemetry.length - 1]
  const comp = latest?.components[selected] as unknown as Record<string, number> | undefined
  const status: Status = latest?.components[selected].status ?? 'ok'
  const meta = params?.components[selected]
  const charts = COMPONENT_CHARTS[selected]
  const sui = STATUS_UI[status]

  return (
    <div className="dt-slide-in absolute inset-0 z-20 flex flex-col overflow-hidden rounded-xl border border-hair-2 bg-surface shadow-2xl">
      {/* header */}
      <header className="flex shrink-0 items-center gap-2.5 border-b border-hair px-4 py-3">
        <span className={`inline-flex h-2 w-2 shrink-0 rounded-full ${sui.dot}`} />
        <h3 className="text-[14px] font-semibold capitalize text-ink">{meta?.label ?? selected}</h3>
        {meta && (
          <span className="rounded-md border border-hair bg-surface-2 px-1.5 py-0.5 font-mono text-[10.5px] text-ink-2">
            {meta.thing_id}
          </span>
        )}
        <span className={`ml-1 text-[10px] font-semibold uppercase tracking-wide ${sui.cls}`}>{sui.label}</span>
        <button
          type="button"
          onClick={() => setSelected(null)}
          className="ml-auto grid h-7 w-7 place-items-center rounded-lg text-muted transition-colors hover:bg-elevated hover:text-ink"
          aria-label="Close focus"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {/* mini trends */}
        <Section title="Telemetry">
          <div className="space-y-2">
            {charts.map((chart, i) => (
              <div key={chart.id} className="flex h-[104px] flex-col">
                <Mini chart={chart} telemetry={telemetry} domain={domain} showXAxis={i === charts.length - 1} compact />
              </div>
            ))}
          </div>
        </Section>

        {/* reported vs desired + manual control */}
        {meta && meta.params.length > 0 ? (
          <Section title="Manual control" hint="operator write · routes through the executor">
            <div className="space-y-3">
              {meta.params.map((spec) => (
                <ParamEditor
                  key={spec.name}
                  component={selected}
                  spec={spec}
                  reported={comp?.[`${spec.name}_reported`]}
                  desired={comp?.[`${spec.name}_desired`]}
                />
              ))}
            </div>
          </Section>
        ) : (
          <div className="rounded-lg border border-dashed border-hair px-3 py-4 text-center text-[12px] text-muted">
            No writable parameters for this component.
          </div>
        )}
      </div>
    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 flex items-baseline gap-2 px-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">{title}</span>
        {hint && <span className="text-[10.5px] text-muted/70">{hint}</span>}
      </div>
      {children}
    </section>
  )
}

function clampStep(v: number, spec: ParamSpec): number {
  const clamped = Math.max(spec.min, Math.min(spec.max, v))
  const snapped = Math.round((clamped - spec.min) / spec.step) * spec.step + spec.min
  return Math.max(spec.min, Math.min(spec.max, snapped))
}

function ParamEditor({
  component,
  spec,
  reported,
  desired,
}: {
  component: ComponentId
  spec: ParamSpec
  reported: number | undefined
  desired: number | undefined
}) {
  const initial = desired ?? reported ?? spec.min
  const [value, setValue] = useState<number>(clampStep(initial, spec))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ControlResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const dirty = desired == null || value !== desired

  async function apply() {
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const r = await postControl(component, spec.name, value, 'manual operator adjustment')
      if (r.ok) setResult(r)
      else setError(r.error ?? 'Rejected by executor')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-hair bg-surface-2 px-3 py-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[12px] font-medium text-ink">{spec.label}</span>
        <span className="font-mono text-[10.5px] text-muted">
          {spec.min}–{spec.max} · step {spec.step}
        </span>
      </div>

      {/* reported → desired readout */}
      <div className="mt-1 flex items-center gap-2 text-[11px] text-muted">
        <span>
          reported <span className="font-semibold tabular-nums text-ink-2">{fmt(reported)}</span>
        </span>
        <span className="text-hair-2">·</span>
        <span>
          desired <span className="font-semibold tabular-nums text-ink-2">{fmt(desired)}</span> {spec.unit}
        </span>
      </div>

      {/* slider */}
      <input
        type="range"
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={value}
        disabled={busy}
        onChange={(e) => setValue(clampStep(Number(e.target.value), spec))}
        className="dt-range mt-2.5 w-full"
        aria-label={`${spec.label} slider`}
      />

      {/* stepper + apply */}
      <div className="mt-2 flex items-center gap-2">
        <div className="flex items-center overflow-hidden rounded-lg border border-hair bg-elevated">
          <StepBtn label="decrease" disabled={busy || value <= spec.min} onClick={() => setValue(clampStep(value - spec.step, spec))}>
            −
          </StepBtn>
          <input
            type="number"
            value={value}
            min={spec.min}
            max={spec.max}
            step={spec.step}
            disabled={busy}
            onChange={(e) => setValue(Math.max(spec.min, Math.min(spec.max, Number(e.target.value) || spec.min)))}
            onBlur={() => setValue(clampStep(value, spec))}
            className="w-16 bg-transparent px-2 py-1.5 text-center font-mono text-[13px] tabular-nums text-ink outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
          />
          <StepBtn label="increase" disabled={busy || value >= spec.max} onClick={() => setValue(clampStep(value + spec.step, spec))}>
            +
          </StepBtn>
        </div>
        <span className="text-[11px] text-muted">{spec.unit}</span>
        <button
          type="button"
          onClick={() => void apply()}
          disabled={busy || !dirty}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-accent-2 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy && <span className="dt-spin inline-block h-3 w-3 rounded-full border-2 border-white/60 border-t-transparent" />}
          Apply
        </button>
      </div>

      {/* result */}
      {result?.ok && (
        <div className="dt-fade-in mt-2 overflow-hidden rounded-md border border-sev-recovered/30 bg-sev-recovered/[0.08]">
          <div className="flex items-center gap-2 px-2.5 py-1.5">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-sev-recovered">applied</span>
            {typeof result.ditto_status === 'number' && (
              <span className="rounded bg-sev-recovered/15 px-1.5 py-px font-mono text-[10px] font-semibold tabular-nums text-sev-recovered">
                ditto {result.ditto_status}
              </span>
            )}
            {result.ditto_request && (
              <span className="ml-auto rounded bg-accent/15 px-1.5 py-px font-mono text-[10px] font-semibold text-accent">
                {result.ditto_request.method}
              </span>
            )}
          </div>
          {result.ditto_request && (
            <pre className="scroll-thin overflow-x-auto border-t border-sev-recovered/15 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-ink-2">
              <span className="text-muted">{result.ditto_request.path}</span>
              {'\n'}
              {pretty(result.ditto_request.body)}
            </pre>
          )}
        </div>
      )}
      {error && (
        <div className="dt-fade-in mt-2 rounded-md border border-sev-critical/30 bg-sev-critical/10 px-2.5 py-1.5 text-[11.5px] text-sev-critical">
          {error}
        </div>
      )}
    </div>
  )
}

function StepBtn({
  children,
  onClick,
  disabled,
  label,
}: {
  children: ReactNode
  onClick: () => void
  disabled: boolean
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="grid h-8 w-8 place-items-center text-[16px] font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-30"
    >
      {children}
    </button>
  )
}

function fmt(n: number | undefined): string {
  return n == null || Number.isNaN(n) ? '—' : String(Math.round(n))
}
