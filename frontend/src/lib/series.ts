import type { ComponentId, FrameComponents, TelemetryFrame } from '../types'
import { toEpoch } from './format'

// Series colors reference the CSS tokens in index.css (dataviz categorical
// palette, dark steps). Threshold reference lines use the fixed status tokens
// and come straight from the sleeper thresholds table (CONTRACTS §3.5).
const C = {
  rpm: 'var(--color-rpm)',
  temp: 'var(--color-temp)',
  current: 'var(--color-current)',
  flow: 'var(--color-flow)',
  pressure: 'var(--color-pressure)',
  level: 'var(--color-level)',
  position: 'var(--color-position)',
  inflow: 'var(--color-flow)',
  outflow: 'var(--color-outflow)',
} as const

const WARN = 'var(--color-sev-warn)'
const CRIT = 'var(--color-sev-critical)'

export interface Threshold {
  y: number
  color: string
  label: string
}

const th = (y: number, color: string, label: string): Threshold => ({ y, color, label })

export interface Line {
  key: string
  color: string
  label?: string // direct label for multi-line charts
  get: (c: FrameComponents) => number
}

export interface ChartDef {
  id: string
  label: string
  unit: string
  precision: number
  yDomain: [number, number]
  yTicks: number[]
  thresholds?: Threshold[]
  lines: Line[]
}

/** Default view: system overview — one key series per component, shared axis. */
export const OVERVIEW_CHARTS: ChartDef[] = [
  {
    id: 'motor-rpm',
    label: 'Motor rpm',
    unit: 'rpm',
    precision: 0,
    yDomain: [1000, 2100],
    yTicks: [1000, 1500, 2000],
    lines: [{ key: 'v', color: C.rpm, get: (c) => c.motor.rpm }],
  },
  {
    id: 'line-flow',
    label: 'Line flow',
    unit: 'L/min',
    precision: 1,
    yDomain: [0, 200],
    yTicks: [0, 100, 200],
    lines: [{ key: 'v', color: C.flow, get: (c) => c.pump.flow }],
  },
  {
    id: 'pump-pressure',
    label: 'Pump pressure',
    unit: 'bar',
    precision: 2,
    yDomain: [1, 7],
    yTicks: [1, 3, 5, 7],
    thresholds: [th(2.5, WARN, 'lo 2.5'), th(6.0, WARN, 'hi 6.0')],
    lines: [{ key: 'v', color: C.pressure, get: (c) => c.pump.pressure }],
  },
  {
    id: 'tank-level',
    label: 'Tank level',
    unit: '%',
    precision: 1,
    yDomain: [0, 100],
    yTicks: [0, 50, 100],
    thresholds: [th(40, WARN, 'warn 40'), th(30, CRIT, 'crit 30')],
    lines: [{ key: 'v', color: C.level, get: (c) => c.tank.level_pct }],
  },
]

/** Per-component full series (small multiples) — used on selection + drawer. */
export const COMPONENT_CHARTS: Record<ComponentId, ChartDef[]> = {
  motor: [
    {
      id: 'motor-rpm',
      label: 'RPM',
      unit: 'rpm',
      precision: 0,
      yDomain: [1000, 2100],
      yTicks: [1000, 1500, 2000],
      lines: [{ key: 'v', color: C.rpm, get: (c) => c.motor.rpm }],
    },
    {
      id: 'motor-temp',
      label: 'Temperature',
      unit: '°C',
      precision: 1,
      yDomain: [40, 100],
      yTicks: [40, 60, 80, 100],
      thresholds: [th(70, WARN, 'warn 70'), th(85, CRIT, 'crit 85')],
      lines: [{ key: 'v', color: C.temp, get: (c) => c.motor.temp }],
    },
    {
      id: 'motor-current',
      label: 'Current',
      unit: 'A',
      precision: 2,
      yDomain: [0, 12],
      yTicks: [0, 4, 8, 12],
      lines: [{ key: 'v', color: C.current, get: (c) => c.motor.current }],
    },
  ],
  pump: [
    {
      id: 'pump-flow',
      label: 'Flow',
      unit: 'L/min',
      precision: 1,
      yDomain: [0, 200],
      yTicks: [0, 100, 200],
      lines: [{ key: 'v', color: C.flow, get: (c) => c.pump.flow }],
    },
    {
      id: 'pump-pressure',
      label: 'Pressure',
      unit: 'bar',
      precision: 2,
      yDomain: [1, 8],
      yTicks: [1, 3, 5, 7],
      thresholds: [th(2.5, WARN, 'lo 2.5'), th(6.0, WARN, 'hi 6.0'), th(7.5, CRIT, 'crit 7.5')],
      lines: [{ key: 'v', color: C.pressure, get: (c) => c.pump.pressure }],
    },
    {
      id: 'pump-temp',
      label: 'Temperature',
      unit: '°C',
      precision: 1,
      yDomain: [40, 90],
      yTicks: [40, 60, 80],
      thresholds: [th(75, WARN, 'warn 75')],
      lines: [{ key: 'v', color: C.temp, get: (c) => c.pump.temp }],
    },
  ],
  valve: [
    {
      id: 'valve-position',
      label: 'Position',
      unit: '%',
      precision: 0,
      yDomain: [0, 100],
      yTicks: [0, 50, 100],
      lines: [{ key: 'v', color: C.position, get: (c) => c.valve.position_reported }],
    },
    {
      id: 'valve-flow',
      label: 'Throttled flow',
      unit: 'L/min',
      precision: 1,
      yDomain: [0, 200],
      yTicks: [0, 100, 200],
      lines: [{ key: 'v', color: C.flow, get: (c) => c.valve.flow }],
    },
  ],
  tank: [
    {
      id: 'tank-level',
      label: 'Level',
      unit: '%',
      precision: 1,
      yDomain: [0, 100],
      yTicks: [0, 50, 100],
      thresholds: [th(40, WARN, 'lo 40'), th(30, CRIT, 'crit 30'), th(90, WARN, 'hi 90')],
      lines: [{ key: 'v', color: C.level, get: (c) => c.tank.level_pct }],
    },
    {
      id: 'tank-io',
      label: 'In / out flow',
      unit: 'L/min',
      precision: 1,
      yDomain: [0, 200],
      yTicks: [0, 100, 200],
      lines: [
        { key: 'inflow', color: C.inflow, label: 'in', get: (c) => c.tank.inflow },
        { key: 'outflow', color: C.outflow, label: 'out', get: (c) => c.tank.outflow },
      ],
    },
  ],
}

export type ChartRow = { t: number } & Record<string, number>

/** Project telemetry frames into chart rows for one ChartDef. */
export function buildRows(telemetry: TelemetryFrame[], chart: ChartDef): ChartRow[] {
  return telemetry.map((f) => {
    const row: ChartRow = { t: toEpoch(f.ts) }
    for (const line of chart.lines) row[line.key] = line.get(f.components)
    return row
  })
}

export function timeDomain(telemetry: TelemetryFrame[]): [number, number] | undefined {
  if (telemetry.length === 0) return undefined
  return [toEpoch(telemetry[0].ts), toEpoch(telemetry[telemetry.length - 1].ts)]
}
