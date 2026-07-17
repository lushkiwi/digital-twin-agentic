import type { KeyboardEvent, ReactNode } from 'react'
import { useStore } from '../store'
import type { ComponentId, Status } from '../types'

/**
 * Hand-authored SCADA schematic (viewBox 800×280, no layout library).
 * Motor → Pump → Valve → Tank, left to right. Each node shows its key live
 * value and a status ring colored by `frame.components[c].status`. The two
 * pipe edges animate (dash-offset) at a speed set by the live line flow; the
 * motor→pump shaft is drawn as a mechanical coupling, styled apart from pipes.
 * Whole node is clickable → selects that component (drives the FocusDrawer).
 */

const STATUS_COLOR: Record<Status, string> = {
  ok: 'var(--color-sev-recovered)',
  warn: 'var(--color-sev-warn)',
  critical: 'var(--color-sev-critical)',
}

const CY = 120
const R = 44

// Node centers, left → right.
const X = { motor: 104, pump: 312, valve: 512, tank: 700 }
const TANK = { w: 92, h: 108, top: CY - 54, bottom: CY + 54 }

export default function SystemSchematic() {
  const latest = useStore((s) => s.telemetry[s.telemetry.length - 1])
  const selected = useStore((s) => s.selectedComponent)
  const setSelected = useStore((s) => s.setSelectedComponent)

  const c = latest?.components
  const pipe1Flow = c?.pump.flow ?? 0 // pump → valve (line flow)
  const pipe2Flow = c?.valve.flow ?? 0 // valve → tank

  const toggle = (id: ComponentId) => setSelected(selected === id ? null : id)

  return (
    <div className="relative shrink-0 px-3 pt-2">
      {selected && (
        <button
          type="button"
          onClick={() => setSelected(null)}
          className="absolute right-4 top-3 z-10 inline-flex items-center gap-1 rounded-full border border-hair-2 bg-elevated/90 px-2 py-1 text-[10.5px] font-medium text-muted backdrop-blur transition-colors hover:text-ink"
          aria-label="Clear selection"
        >
          <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
          </svg>
          deselect
        </button>
      )}

      <svg
        viewBox="0 0 800 280"
        width="100%"
        className="h-[248px] w-full select-none"
        preserveAspectRatio="xMidYMid meet"
        role="group"
        aria-label="System schematic: motor, pump, valve, tank"
      >
        <defs>
          <clipPath id="tank-clip">
            <rect
              x={X.tank - TANK.w / 2 + 4}
              y={TANK.top + 4}
              width={TANK.w - 8}
              height={TANK.h - 8}
              rx={7}
            />
          </clipPath>
        </defs>

        {/* Edges (drawn first, under the nodes) */}
        <Shaft x1={X.motor + R} x2={X.pump - R} rpm={c?.motor.rpm ?? 0} />
        <Pipe x1={X.pump + R} x2={X.valve - 34} flow={pipe1Flow} />
        <Pipe x1={X.valve + 34} x2={X.tank - TANK.w / 2} flow={pipe2Flow} />

        {/* Nodes */}
        <MotorNode
          selected={selected === 'motor'}
          status={c?.motor.status ?? 'ok'}
          value={c ? Math.round(c.motor.rpm) : undefined}
          onSelect={() => toggle('motor')}
        />
        <PumpNode
          selected={selected === 'pump'}
          status={c?.pump.status ?? 'ok'}
          value={c ? Math.round(c.pump.flow) : undefined}
          onSelect={() => toggle('pump')}
        />
        <ValveNode
          selected={selected === 'valve'}
          status={c?.valve.status ?? 'ok'}
          value={c ? Math.round(c.valve.position_reported) : undefined}
          onSelect={() => toggle('valve')}
        />
        <TankNode
          selected={selected === 'tank'}
          status={c?.tank.status ?? 'ok'}
          level={c?.tank.level_pct ?? 0}
          hasData={!!c}
          onSelect={() => toggle('tank')}
        />
      </svg>
    </div>
  )
}

// ---- edges ----

function Pipe({ x1, x2, flow }: { x1: number; x2: number; flow: number }) {
  const width = 2 + 4 * (Math.min(flow, 200) / 200)
  const active = flow >= 2
  const duration = Math.min(4, Math.max(0.4, 98 / Math.max(flow, 1)))
  const mid = (x1 + x2) / 2

  return (
    <g>
      {/* pipe wall */}
      <line x1={x1} y1={CY} x2={x2} y2={CY} stroke="var(--color-hair-2)" strokeWidth={width + 3} strokeLinecap="round" />
      {/* flowing fluid */}
      <line
        x1={x1}
        y1={CY}
        x2={x2}
        y2={CY}
        stroke="var(--color-flow)"
        strokeWidth={width}
        strokeLinecap="round"
        strokeDasharray="8 8"
        opacity={active ? 0.95 : 0.25}
        style={active ? { animation: `dt-flow ${duration}s linear infinite` } : undefined}
      />
      {/* direction chevron near downstream end */}
      <path
        d={`M ${x2 - 10} ${CY - 5} L ${x2 - 4} ${CY} L ${x2 - 10} ${CY + 5}`}
        fill="none"
        stroke="var(--color-flow)"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={active ? 0.9 : 0.3}
      />
      <text x={mid} y={CY - 12} textAnchor="middle" fontSize={10} fill="var(--color-muted)">
        {flow >= 2 ? `${Math.round(flow)} L/min` : 'no flow'}
      </text>
    </g>
  )
}

function Shaft({ x1, x2, rpm }: { x1: number; x2: number; rpm: number }) {
  // Mechanical rotational coupling — deliberately unlike the pipe edges: a grey
  // rail with flanges, a slow rotation dash whose speed tracks rpm.
  const spinning = rpm >= 1
  const duration = Math.min(4, Math.max(0.5, (1.2 * 1800) / Math.max(rpm, 1)))
  const mid = (x1 + x2) / 2
  return (
    <g>
      <line x1={x1} y1={CY} x2={x2} y2={CY} stroke="var(--color-axis)" strokeWidth={6} strokeLinecap="round" />
      <line
        x1={x1 + 3}
        y1={CY}
        x2={x2 - 3}
        y2={CY}
        stroke="var(--color-ink-2)"
        strokeWidth={2}
        strokeDasharray="3 7"
        opacity={0.7}
        style={spinning ? { animation: `dt-flow ${duration}s linear infinite` } : undefined}
      />
      {/* coupling flanges */}
      <line x1={x1 + 4} y1={CY - 7} x2={x1 + 4} y2={CY + 7} stroke="var(--color-muted)" strokeWidth={2} strokeLinecap="round" />
      <line x1={x2 - 4} y1={CY - 7} x2={x2 - 4} y2={CY + 7} stroke="var(--color-muted)" strokeWidth={2} strokeLinecap="round" />
      <text x={mid} y={CY - 12} textAnchor="middle" fontSize={10} fill="var(--color-muted)">
        {rpm >= 1 ? `${Math.round(rpm)} rpm` : 'stopped'}
      </text>
    </g>
  )
}

// ---- node frame (selection outline, hit target, labels) ----

function NodeFrame({
  cx,
  name,
  value,
  unit,
  status,
  selected,
  onSelect,
  children,
}: {
  cx: number
  name: string
  value: number | undefined
  unit: string
  status: Status
  selected: boolean
  onSelect: () => void
  children: ReactNode
}) {
  const onKey = (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect()
    }
  }
  return (
    <g
      className="dt-node"
      onClick={onSelect}
      onKeyDown={onKey}
      role="button"
      tabIndex={0}
      aria-label={`${name}${value != null ? `, ${value} ${unit}` : ''}, status ${status}`}
    >
      {/* selection halo */}
      {selected && (
        <rect
          x={cx - 62}
          y={18}
          width={124}
          height={218}
          rx={16}
          fill="var(--color-accent)"
          fillOpacity={0.06}
          stroke="var(--color-accent)"
          strokeWidth={1.5}
        />
      )}
      {/* invisible hit target so the whole cell is clickable */}
      <rect x={cx - 62} y={18} width={124} height={218} fill="transparent" />

      {/* component name */}
      <text
        x={cx}
        y={40}
        textAnchor="middle"
        fontSize={12.5}
        fontWeight={600}
        fill={selected ? 'var(--color-accent)' : 'var(--color-ink-2)'}
        style={{ letterSpacing: '0.02em' }}
      >
        {name}
      </text>

      {/* drawn shape (status ring + body + hint) — hover glow via CSS */}
      <g className="dt-node-shape">{children}</g>

      {/* status dot + key value */}
      <g>
        <circle cx={cx - 30} cy={204} r={3.5} fill={STATUS_COLOR[status]} />
        <text x={cx - 20} y={208} fontSize={9.5} fontWeight={600} fill={STATUS_COLOR[status]} style={{ textTransform: 'uppercase' }}>
          {status}
        </text>
      </g>
      <text x={cx} y={190} textAnchor="middle" fontSize={22} fontWeight={700} fill="var(--color-ink)" style={{ fontVariantNumeric: 'tabular-nums' }}>
        {value != null ? value : '—'}
      </text>
      <text x={cx} y={190} dx={valueOffset(value)} textAnchor="start" fontSize={11} fill="var(--color-muted)">
        {unit}
      </text>
    </g>
  )
}

// nudge the unit label to sit just right of the value number
function valueOffset(value: number | undefined): number {
  const len = value != null ? String(value).length : 1
  return len * 6.5 + 4
}

// ---- nodes ----

function MotorNode({
  selected,
  status,
  value,
  onSelect,
}: {
  selected: boolean
  status: Status
  value: number | undefined
  onSelect: () => void
}) {
  const cx = X.motor
  return (
    <NodeFrame cx={cx} name="Motor" value={value} unit="rpm" status={status} selected={selected} onSelect={onSelect}>
      <circle cx={cx} cy={CY} r={R} fill="var(--color-surface-2)" stroke={STATUS_COLOR[status]} strokeWidth={4} />
      {/* rotor: hub + spokes */}
      <circle cx={cx} cy={CY} r={24} fill="none" stroke="var(--color-hair-2)" strokeWidth={1.5} />
      {[0, 60, 120, 180, 240, 300].map((deg) => {
        const rad = (deg * Math.PI) / 180
        return (
          <line
            key={deg}
            x1={cx + Math.cos(rad) * 7}
            y1={CY + Math.sin(rad) * 7}
            x2={cx + Math.cos(rad) * 22}
            y2={CY + Math.sin(rad) * 22}
            stroke="var(--color-muted)"
            strokeWidth={1.5}
            strokeLinecap="round"
          />
        )
      })}
      <circle cx={cx} cy={CY} r={6} fill="var(--color-ink-2)" />
      {/* terminal box */}
      <rect x={cx - 7} y={CY - R - 8} width={14} height={9} rx={2} fill="var(--color-elevated)" stroke="var(--color-hair-2)" strokeWidth={1} />
    </NodeFrame>
  )
}

function PumpNode({
  selected,
  status,
  value,
  onSelect,
}: {
  selected: boolean
  status: Status
  value: number | undefined
  onSelect: () => void
}) {
  const cx = X.pump
  return (
    <NodeFrame cx={cx} name="Pump" value={value} unit="L/min" status={status} selected={selected} onSelect={onSelect}>
      {/* volute casing */}
      <circle cx={cx} cy={CY} r={R} fill="var(--color-surface-2)" stroke={STATUS_COLOR[status]} strokeWidth={4} />
      {/* tangential outlet stub (top-right) */}
      <path
        d={`M ${cx + 30} ${CY - 30} q 14 -14 20 -2 l -8 10`}
        fill="var(--color-surface-2)"
        stroke={STATUS_COLOR[status]}
        strokeWidth={4}
        strokeLinejoin="round"
      />
      {/* impeller vanes (curved) */}
      {[0, 90, 180, 270].map((deg) => {
        const rad = (deg * Math.PI) / 180
        const ix = cx + Math.cos(rad) * 8
        const iy = CY + Math.sin(rad) * 8
        const ox = cx + Math.cos(rad + 0.9) * 24
        const oy = CY + Math.sin(rad + 0.9) * 24
        return (
          <path
            key={deg}
            d={`M ${ix} ${iy} Q ${cx + Math.cos(rad + 0.4) * 20} ${CY + Math.sin(rad + 0.4) * 20} ${ox} ${oy}`}
            fill="none"
            stroke="var(--color-muted)"
            strokeWidth={1.6}
            strokeLinecap="round"
          />
        )
      })}
      <circle cx={cx} cy={CY} r={5} fill="var(--color-ink-2)" />
    </NodeFrame>
  )
}

function ValveNode({
  selected,
  status,
  value,
  onSelect,
}: {
  selected: boolean
  status: Status
  value: number | undefined
  onSelect: () => void
}) {
  const cx = X.valve
  const hw = 34
  const hh = 26
  return (
    <NodeFrame cx={cx} name="Valve" value={value} unit="%" status={status} selected={selected} onSelect={onSelect}>
      {/* bowtie body */}
      <path
        d={`M ${cx - hw} ${CY - hh} L ${cx} ${CY} L ${cx - hw} ${CY + hh} Z`}
        fill="var(--color-surface-2)"
        stroke={STATUS_COLOR[status]}
        strokeWidth={3}
        strokeLinejoin="round"
      />
      <path
        d={`M ${cx + hw} ${CY - hh} L ${cx} ${CY} L ${cx + hw} ${CY + hh} Z`}
        fill="var(--color-surface-2)"
        stroke={STATUS_COLOR[status]}
        strokeWidth={3}
        strokeLinejoin="round"
      />
      {/* stem + handwheel */}
      <line x1={cx} y1={CY} x2={cx} y2={CY - hh - 12} stroke="var(--color-muted)" strokeWidth={2.5} strokeLinecap="round" />
      <line x1={cx - 11} y1={CY - hh - 12} x2={cx + 11} y2={CY - hh - 12} stroke="var(--color-muted)" strokeWidth={2.5} strokeLinecap="round" />
      <circle cx={cx} cy={CY} r={4.5} fill="var(--color-ink-2)" />
    </NodeFrame>
  )
}

function TankNode({
  selected,
  status,
  level,
  hasData,
  onSelect,
}: {
  selected: boolean
  status: Status
  level: number
  hasData: boolean
  onSelect: () => void
}) {
  const cx = X.tank
  const x0 = cx - TANK.w / 2
  const innerBottom = TANK.bottom - 4
  const innerHeight = TANK.h - 8
  const clamped = Math.max(0, Math.min(100, level))
  const fillH = (innerHeight * clamped) / 100
  const fillTop = innerBottom - fillH

  return (
    <NodeFrame cx={cx} name="Tank" value={hasData ? Math.round(level) : undefined} unit="%" status={status} selected={selected} onSelect={onSelect}>
      {/* live level fill (clipped to the rounded interior) */}
      <g clipPath="url(#tank-clip)">
        <rect x={x0 + 4} y={fillTop} width={TANK.w - 8} height={Math.max(0, fillH)} fill="var(--color-level)" fillOpacity={0.24} />
        {hasData && fillH > 0.5 && (
          <line x1={x0 + 4} y1={fillTop} x2={x0 + TANK.w - 4} y2={fillTop} stroke="var(--color-level)" strokeWidth={2} opacity={0.9} />
        )}
      </g>
      {/* tank shell (status ring) */}
      <rect x={x0} y={TANK.top} width={TANK.w} height={TANK.h} rx={10} fill="none" stroke={STATUS_COLOR[status]} strokeWidth={4} />
      {/* drain stub at bottom */}
      <line x1={cx} y1={TANK.bottom} x2={cx} y2={TANK.bottom + 10} stroke="var(--color-hair-2)" strokeWidth={4} strokeLinecap="round" />
    </NodeFrame>
  )
}
