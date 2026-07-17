import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TooltipProps } from 'recharts'
import { useStore } from '../store'
import type { ComponentId, ParamsResponse, TelemetryFrame } from '../types'
import { fixed, hms } from '../lib/format'
import {
  COMPONENT_CHARTS,
  OVERVIEW_CHARTS,
  buildRows,
  timeDomain,
  type ChartDef,
  type ChartRow,
} from '../lib/series'

const CHROME = {
  grid: 'var(--color-grid)',
  axis: 'var(--color-axis)',
  muted: 'var(--color-muted)',
}

export default function TelemetryChart() {
  const telemetry = useStore((s) => s.telemetry)
  const selected = useStore((s) => s.selectedComponent)
  const params = useStore((s) => s.params)
  const wsConnected = useStore((s) => s.wsConnected)

  const charts = selected ? COMPONENT_CHARTS[selected] : OVERVIEW_CHARTS
  const domain = useMemo(() => timeDomain(telemetry), [telemetry])
  const latest: TelemetryFrame | undefined = telemetry[telemetry.length - 1]
  const empty = telemetry.length === 0

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {selected ? (
        <ActuatorRow component={selected} latest={latest} params={params} />
      ) : (
        <OverviewLabel />
      )}

      <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 pb-3">
        {empty ? (
          <EmptyChart wsConnected={wsConnected} />
        ) : (
          charts.map((c, i) => (
            <Mini
              key={c.id}
              chart={c}
              telemetry={telemetry}
              domain={domain}
              showXAxis={i === charts.length - 1}
            />
          ))
        )}
      </div>
    </div>
  )
}

function OverviewLabel() {
  return (
    <div className="flex items-center gap-2 px-4 pb-1.5 pt-2">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
        System overview
      </span>
      <span className="text-[11px] text-muted/70">· select a node to focus</span>
    </div>
  )
}

/**
 * A single small-multiple. Renders one or more lines against a shared time axis,
 * with optional §3.5 threshold reference lines. Exported so the FocusDrawer can
 * reuse the exact same trend pattern.
 */
export function Mini({
  chart,
  telemetry,
  domain,
  showXAxis,
  compact,
}: {
  chart: ChartDef
  telemetry: TelemetryFrame[]
  domain: [number, number] | undefined
  showXAxis?: boolean
  compact?: boolean
}) {
  const rows: ChartRow[] = useMemo(() => buildRows(telemetry, chart), [telemetry, chart])
  const last = rows[rows.length - 1]
  const primary = chart.lines[0]
  const current = last ? last[primary.key] : undefined
  const multi = chart.lines.length > 1

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-hair bg-chart">
      <div className="flex items-baseline justify-between px-3 pt-2">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: primary.color }} />
          <span className="text-[11px] font-medium text-ink-2">{chart.label}</span>
          {multi && (
            <span className="flex items-center gap-2 pl-1">
              {chart.lines.map((l) => (
                <span key={l.key} className="flex items-center gap-1">
                  <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: l.color }} />
                  <span className="text-[10px] text-muted">{l.label ?? l.key}</span>
                </span>
              ))}
            </span>
          )}
        </div>
        {!multi && (
          <div className="flex items-baseline gap-1">
            <span className="text-[15px] font-semibold tabular-nums text-ink">
              {fixed(current, chart.precision)}
            </span>
            <span className="text-[10px] text-muted">{chart.unit}</span>
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1" style={compact ? { minHeight: 62 } : undefined}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 12, bottom: showXAxis ? 2 : 4, left: 0 }}>
            <CartesianGrid stroke={CHROME.grid} strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              domain={domain ?? ['dataMin', 'dataMax']}
              scale="time"
              hide={!showXAxis}
              tickFormatter={(v: number) => hms(v)}
              tick={{ fill: CHROME.muted, fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: CHROME.axis }}
              minTickGap={44}
              height={16}
            />
            <YAxis
              domain={chart.yDomain}
              ticks={chart.yTicks}
              width={34}
              tick={{ fill: CHROME.muted, fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => String(v)}
            />
            {chart.thresholds?.map((t) => (
              <ReferenceLine
                key={t.label}
                y={t.y}
                stroke={t.color}
                strokeDasharray="4 4"
                strokeOpacity={0.7}
                strokeWidth={1}
                label={{
                  value: t.label,
                  position: 'insideTopRight',
                  fill: t.color,
                  fontSize: 9,
                  fontWeight: 600,
                }}
              />
            ))}
            <Tooltip
              cursor={{ stroke: CHROME.axis, strokeWidth: 1 }}
              content={(p: TooltipProps<number, string>) => (
                <ChartTooltip {...p} chart={chart} />
              )}
            />
            {chart.lines.map((l) => (
              <Line
                key={l.key}
                type="monotone"
                dataKey={l.key}
                stroke={l.color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: l.color, stroke: 'var(--color-chart)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function ChartTooltip(props: TooltipProps<number, string> & { chart: ChartDef }) {
  const { active, payload, chart } = props
  if (!active || !payload || payload.length === 0) return null
  const t = payload[0]?.payload?.t as number | undefined
  return (
    <div className="rounded-lg border border-hair-2 bg-elevated px-2.5 py-1.5 shadow-xl">
      <div className="font-mono text-[10px] tabular-nums text-muted">{t ? hms(t) : ''}</div>
      {chart.lines.map((l) => {
        const p = payload.find((x) => x.dataKey === l.key)
        const value = typeof p?.value === 'number' ? p.value : undefined
        return (
          <div key={l.key} className="mt-0.5 flex items-baseline gap-1.5 text-[12px]">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: l.color }} />
            <span className="text-ink-2">{l.label ?? chart.label}</span>
            <span className="font-semibold tabular-nums text-ink">{fixed(value, chart.precision)}</span>
            <span className="text-[10px] text-muted">{chart.unit}</span>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Actuator convergence readout for the selected component — its writable param(s)
 * reported vs desired. Generalized from the v1 pump/valve version. Exported so the
 * FocusDrawer reuses the same reported↔desired row treatment.
 */
export function ActuatorRow({
  component,
  latest,
  params,
}: {
  component: ComponentId
  latest: TelemetryFrame | undefined
  params: ParamsResponse | null
}) {
  const specs = params?.components[component]?.params ?? []
  const comp = latest?.components[component] as unknown as Record<string, number> | undefined

  if (specs.length === 0) return <div className="px-3 pt-2" />

  return (
    <div className="grid grid-cols-1 gap-2 px-3 pb-3 pt-2">
      {specs.map((spec) => {
        const reported = comp?.[`${spec.name}_reported`]
        const desired = comp?.[`${spec.name}_desired`]
        const converging = reported != null && desired != null && reported !== desired
        return (
          <div key={spec.name} className="rounded-lg border border-hair bg-surface-2 px-3 py-2">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">
              {spec.label}
            </div>
            <div className="mt-0.5 flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums text-ink">{fmtInt(reported)}</span>
              <span className="text-[11px] text-muted">{spec.unit}</span>
              {converging ? (
                <span className="ml-auto inline-flex items-center gap-1 text-[12px] font-medium tabular-nums text-accent">
                  <Arrow /> {desired}
                  <span className="text-[10px] font-normal text-muted">target</span>
                </span>
              ) : (
                <span className="ml-auto text-[11px] text-muted">
                  {reported != null ? 'at target' : ''}
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function EmptyChart({ wsConnected }: { wsConnected: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-hair text-center">
      <svg viewBox="0 0 24 24" className="h-7 w-7 text-muted" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M3 3v18h18" strokeLinecap="round" />
        <path d="M7 14l3-3 3 3 4-6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="mt-2 text-[13px] font-medium text-ink-2">No telemetry yet</p>
      <p className="mt-1 text-[12px] text-muted">
        {wsConnected ? 'Waiting for the first frame…' : 'Backend offline — awaiting connection'}
      </p>
    </div>
  )
}

function Arrow() {
  return (
    <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.2">
      <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function fmtInt(n: number | undefined): string {
  return n == null || Number.isNaN(n) ? '—' : String(Math.round(n))
}
