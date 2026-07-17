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
import type { TelemetryPoint } from '../types'
import { fixed, hms, toEpoch } from '../lib/format'

const COLORS = {
  temp: 'var(--color-temp)',
  pressure: 'var(--color-pressure)',
  flow: 'var(--color-flow)',
  grid: 'var(--color-grid)',
  axis: 'var(--color-axis)',
  muted: 'var(--color-muted)',
}

interface Row {
  t: number
  temperature: number
  pressure: number
  flow_rate: number
}

interface Threshold {
  y: number
  color: string
  label: string
}

export default function TelemetryChart() {
  const telemetry = useStore((s) => s.telemetry)
  const wsConnected = useStore((s) => s.wsConnected)

  const rows: Row[] = useMemo(
    () =>
      telemetry.map((p) => ({
        t: toEpoch(p.ts),
        temperature: p.temperature,
        pressure: p.pressure,
        flow_rate: p.flow_rate,
      })),
    [telemetry],
  )

  const domain: [number, number] | undefined = useMemo(() => {
    if (rows.length === 0) return undefined
    return [rows[0].t, rows[rows.length - 1].t]
  }, [rows])

  const latest: TelemetryPoint | undefined = telemetry[telemetry.length - 1]
  const empty = rows.length === 0

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Actuator convergence readout — reported vs desired */}
      <ActuatorRow latest={latest} />

      {/* Small multiples sharing the time axis */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 px-3 pb-3">
        {empty ? (
          <EmptyChart wsConnected={wsConnected} />
        ) : (
          <>
            <Mini
              label="Temperature"
              unit="°C"
              color={COLORS.temp}
              dataKey="temperature"
              rows={rows}
              domain={domain}
              yDomain={[40, 100]}
              yTicks={[40, 60, 80, 100]}
              precision={1}
              current={latest?.temperature}
              thresholds={[
                { y: 85, color: 'var(--color-sev-warn)', label: 'warn 85' },
                { y: 95, color: 'var(--color-sev-critical)', label: 'crit 95' },
              ]}
            />
            <Mini
              label="Pressure"
              unit="bar"
              color={COLORS.pressure}
              dataKey="pressure"
              rows={rows}
              domain={domain}
              yDomain={[1, 7]}
              yTicks={[1, 3, 5, 7]}
              precision={2}
              current={latest?.pressure}
              thresholds={[{ y: 3.0, color: 'var(--color-sev-warn)', label: 'min 3.0' }]}
            />
            <Mini
              label="Flow rate"
              unit="L/min"
              color={COLORS.flow}
              dataKey="flow_rate"
              rows={rows}
              domain={domain}
              yDomain={[0, 200]}
              yTicks={[0, 100, 200]}
              precision={1}
              current={latest?.flow_rate}
              showXAxis
            />
          </>
        )}
      </div>
    </div>
  )
}

function ActuatorRow({ latest }: { latest: TelemetryPoint | undefined }) {
  const speedR = latest?.pump_speed_reported
  const speedD = latest?.pump_speed_desired
  const valveR = latest?.valve_state_reported
  const valveD = latest?.valve_state_desired
  const speedConverging = speedR != null && speedD != null && speedR !== speedD
  const valveConverging = valveR != null && valveD != null && valveR !== valveD

  return (
    <div className="grid grid-cols-2 gap-2 px-3 pb-3 pt-1">
      <div className="rounded-lg border border-hair bg-surface-2 px-3 py-2">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">Pump speed</div>
        <div className="mt-0.5 flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums text-ink">{fmtInt(speedR)}</span>
          <span className="text-[11px] text-muted">%</span>
          {speedConverging ? (
            <span className="ml-auto inline-flex items-center gap-1 text-[12px] font-medium tabular-nums text-accent">
              <Arrow /> {speedD}
              <span className="text-[10px] font-normal text-muted">target</span>
            </span>
          ) : (
            <span className="ml-auto text-[11px] text-muted">
              {speedR != null ? 'at target' : ''}
            </span>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-hair bg-surface-2 px-3 py-2">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">Valve</div>
        <div className="mt-0.5 flex items-baseline gap-2">
          <span className="text-2xl font-semibold capitalize text-ink">{valveR ?? '—'}</span>
          {valveConverging ? (
            <span className="ml-auto inline-flex items-center gap-1 text-[12px] font-medium capitalize text-accent">
              <Arrow /> {valveD}
              <span className="text-[10px] font-normal normal-case text-muted">target</span>
            </span>
          ) : (
            <span className="ml-auto text-[11px] text-muted">{valveR ? 'at target' : ''}</span>
          )}
        </div>
      </div>
    </div>
  )
}

function Mini(props: {
  label: string
  unit: string
  color: string
  dataKey: keyof Row
  rows: Row[]
  domain: [number, number] | undefined
  yDomain: [number, number]
  yTicks: number[]
  precision: number
  current: number | undefined
  thresholds?: Threshold[]
  showXAxis?: boolean
}) {
  const { label, unit, color, dataKey, rows, domain, yDomain, yTicks, precision, current, thresholds, showXAxis } =
    props

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-hair bg-chart">
      <div className="flex items-baseline justify-between px-3 pt-2">
        <div className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
          <span className="text-[11px] font-medium text-ink-2">{label}</span>
        </div>
        <div className="flex items-baseline gap-1">
          <span className="text-[15px] font-semibold tabular-nums text-ink">{fixed(current, precision)}</span>
          <span className="text-[10px] text-muted">{unit}</span>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 12, bottom: showXAxis ? 2 : 4, left: 0 }}>
            <CartesianGrid stroke={COLORS.grid} strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              domain={domain ?? ['dataMin', 'dataMax']}
              scale="time"
              hide={!showXAxis}
              tickFormatter={(v: number) => hms(v)}
              tick={{ fill: COLORS.muted, fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: COLORS.axis }}
              minTickGap={44}
              height={16}
            />
            <YAxis
              domain={yDomain}
              ticks={yTicks}
              width={34}
              tick={{ fill: COLORS.muted, fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => String(v)}
            />
            {thresholds?.map((th) => (
              <ReferenceLine
                key={th.label}
                y={th.y}
                stroke={th.color}
                strokeDasharray="4 4"
                strokeOpacity={0.7}
                strokeWidth={1}
                label={{
                  value: th.label,
                  position: 'insideTopRight',
                  fill: th.color,
                  fontSize: 9,
                  fontWeight: 600,
                }}
              />
            ))}
            <Tooltip
              cursor={{ stroke: COLORS.axis, strokeWidth: 1 }}
              content={(p: TooltipProps<number, string>) => (
                <ChartTooltip {...p} unit={unit} precision={precision} label={label} />
              )}
            />
            <Line
              type="monotone"
              dataKey={dataKey as string}
              stroke={color}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: color, stroke: 'var(--color-chart)', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function ChartTooltip(
  props: TooltipProps<number, string> & { unit: string; precision: number; label: string },
) {
  const { active, payload, unit, precision, label } = props
  if (!active || !payload || payload.length === 0) return null
  const point = payload[0]
  const t = point.payload?.t as number | undefined
  const value = typeof point.value === 'number' ? point.value : undefined
  return (
    <div className="rounded-lg border border-hair-2 bg-elevated px-2.5 py-1.5 shadow-xl">
      <div className="font-mono text-[10px] tabular-nums text-muted">{t ? hms(t) : ''}</div>
      <div className="mt-0.5 flex items-baseline gap-1.5 text-[12px]">
        <span className="text-ink-2">{label}</span>
        <span className="font-semibold tabular-nums text-ink">{fixed(value, precision)}</span>
        <span className="text-[10px] text-muted">{unit}</span>
      </div>
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
