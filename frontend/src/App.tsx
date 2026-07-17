import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useStore } from './store'
import { connectWs, disconnectWs } from './lib/ws'
import { getConfig, getObservations, getTelemetry } from './lib/api'
import { makeDemoObservations, makeDemoTelemetry, makeDemoTurn } from './lib/demo'
import TelemetryChart from './components/TelemetryChart'
import ObservationLog from './components/ObservationLog'
import ChatPanel from './components/ChatPanel'
import SettingsModal from './components/SettingsModal'

const PUMP_ID = 'org.acme:pump-01'

export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false)

  useEffect(() => {
    const demo = new URLSearchParams(location.search).get('demo') === '1'
    const store = useStore.getState()

    // Demo flag seeds the store only (for UI work while the backend is absent).
    // It never blocks live data: the WS still connects and REST backfill still
    // runs below — real frames overwrite/extend the seed when a backend exists.
    if (demo) {
      store.seed({
        telemetry: makeDemoTelemetry(),
        observations: makeDemoObservations(),
        turns: [makeDemoTurn()],
        dittoConnected: true,
      })
    }

    connectWs()

    // Backfill on load (best-effort; graceful when the backend is offline).
    void getTelemetry(10)
      .then((points) => {
        if (points.length) useStore.getState().backfillTelemetry(points)
      })
      .catch(() => {})
    void getObservations()
      .then((obs) => {
        if (obs.length) useStore.getState().backfillObservations(obs)
      })
      .catch(() => {})
    void getConfig()
      .then((c) => useStore.getState().setConfig(c))
      .catch(() => {})

    return () => disconnectWs()
  }, [])

  return (
    <div className="flex min-h-dvh flex-col lg:h-dvh lg:overflow-hidden">
      <Header pumpId={PUMP_ID} onOpenSettings={() => setSettingsOpen(true)} />

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.92fr)_minmax(0,1.12fr)]">
        <Panel
          title="Live telemetry"
          subtitle="reported twin state · ~1 Hz"
          icon={<IconWave />}
          accentClass="text-flow"
        >
          <TelemetryChart />
        </Panel>

        <Panel
          title="Sleeper agent"
          subtitle="passive observer — flags, never acts"
          icon={<IconEye />}
          accentClass="text-sev-warn"
          headerRight={<WatchingChip />}
        >
          <ObservationLog />
        </Panel>

        <Panel
          title="Interactive agent"
          subtitle="reads & acts · shows every Ditto call"
          icon={<IconBolt />}
          accentClass="text-accent"
        >
          <ChatPanel />
        </Panel>
      </main>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  )
}

function Header({ pumpId, onOpenSettings }: { pumpId: string; onOpenSettings: () => void }) {
  const wsConnected = useStore((s) => s.wsConnected)
  const dittoConnected = useStore((s) => s.dittoConnected)

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-hair bg-surface/80 px-4 backdrop-blur">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/12 text-accent">
        <IconBolt />
      </div>
      <div className="flex items-baseline gap-2.5">
        <h1 className="text-[15px] font-semibold tracking-tight text-ink">Pump Digital Twin</h1>
        <span className="hidden text-[12px] text-muted sm:inline">Mission Control</span>
      </div>
      <span className="ml-1 rounded-md border border-hair bg-surface-2 px-2 py-1 font-mono text-[11px] text-ink-2">
        {pumpId}
      </span>

      <div className="ml-auto flex items-center gap-2">
        <StatusDot label="Ditto" connected={dittoConnected} />
        <StatusDot label="WS" connected={wsConnected} showReconnect />
        <button
          type="button"
          onClick={onOpenSettings}
          className="ml-1 grid h-9 w-9 place-items-center rounded-lg border border-hair bg-surface-2 text-muted transition-colors hover:text-ink"
          aria-label="Settings"
        >
          <IconGear />
        </button>
      </div>
    </header>
  )
}

function StatusDot({
  label,
  connected,
  showReconnect,
}: {
  label: string
  connected: boolean
  showReconnect?: boolean
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-hair bg-surface-2 px-2.5 py-1.5">
      <span className="relative flex h-2 w-2">
        {connected && (
          <span className="dt-pulse absolute inline-flex h-full w-full rounded-full bg-sev-recovered opacity-60" />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${
            connected ? 'bg-sev-recovered' : 'bg-sev-critical'
          }`}
        />
      </span>
      <span className="text-[11px] font-medium text-ink-2">{label}</span>
      {!connected && showReconnect && <span className="text-[10.5px] text-muted">reconnecting…</span>}
    </div>
  )
}

function Panel({
  title,
  subtitle,
  icon,
  accentClass,
  headerRight,
  children,
}: {
  title: string
  subtitle: string
  icon: ReactNode
  accentClass: string
  headerRight?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="flex h-[78vh] min-h-0 flex-col overflow-hidden rounded-xl border border-hair bg-surface lg:h-auto">
      <header className="flex shrink-0 items-center gap-2.5 border-b border-hair px-4 py-3">
        <span className={`shrink-0 ${accentClass}`}>{icon}</span>
        <div className="min-w-0">
          <h2 className="text-[13px] font-semibold leading-tight text-ink">{title}</h2>
          <p className="truncate text-[11px] text-muted">{subtitle}</p>
        </div>
        {headerRight && <div className="ml-auto">{headerRight}</div>}
      </header>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </section>
  )
}

function WatchingChip() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-hair bg-surface-2 px-2 py-1 text-[10.5px] font-medium text-muted">
      <span className="dt-pulse inline-block h-1.5 w-1.5 rounded-full bg-sev-warn" />
      watching
    </span>
  )
}

// ---- icons ----
function IconBolt() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.9">
      <path d="M13 3 4 14h7l-1 7 9-11h-7l1-7Z" strokeLinejoin="round" />
    </svg>
  )
}
function IconWave() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.9">
      <path d="M3 12c2 0 2-4 4-4s2 8 4 8 2-10 4-10 2 6 4 6h2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
function IconEye() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.9">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="2.5" />
    </svg>
  )
}
function IconGear() {
  return (
    <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="12" cy="12" r="3" />
      <path
        d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"
        strokeLinecap="round"
      />
    </svg>
  )
}
