import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { getConfig, postConfig, testConfig } from '../lib/api'
import { useStore } from '../store'
import type { Config, TestResult } from '../types'

const FALLBACK_PRESETS = ['anthropic/claude-fable-5', 'openai/gpt-5.6', 'ollama/llama3.1']

export default function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const config = useStore((s) => s.config)
  const setConfig = useStore((s) => s.setConfig)

  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [presets, setPresets] = useState<string[]>(FALLBACK_PRESETS)
  const [maskedKey, setMaskedKey] = useState<string | null>(null)

  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<TestResult | null>(null)

  // Load fresh config each time the modal opens.
  useEffect(() => {
    if (!open) return
    setSaveError(null)
    setTest(null)
    let cancelled = false
    ;(async () => {
      try {
        const c = await getConfig()
        if (cancelled) return
        hydrate(c)
        setLoadError(null)
        setConfig(c)
      } catch (err) {
        if (cancelled) return
        // fall back to whatever we already have, else defaults
        if (config) hydrate(config)
        setLoadError(err instanceof Error ? err.message : String(err))
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Escape to close
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  function hydrate(c: Config) {
    setModel(c.model ?? '')
    setBaseUrl(c.base_url ?? '')
    setPresets(c.presets?.length ? c.presets : FALLBACK_PRESETS)
    setMaskedKey(c.api_key_masked)
    setApiKey('') // never prefill the secret
  }

  async function onSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const c = await postConfig({
        model: model.trim(),
        api_key: apiKey.trim() ? apiKey.trim() : null, // empty keeps existing
        base_url: baseUrl.trim() ? baseUrl.trim() : null,
      })
      setConfig(c)
      hydrate(c)
      onClose()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function onTest() {
    setTesting(true)
    setTest(null)
    try {
      const r = await testConfig()
      setTest(r)
    } catch (err) {
      setTest({ ok: false, error: err instanceof Error ? err.message : String(err), latency_ms: 0 })
    } finally {
      setTesting(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Agent settings"
        className="dt-fade-in relative w-full max-w-md overflow-hidden rounded-2xl border border-hair-2 bg-surface shadow-2xl"
      >
        <header className="flex items-center justify-between border-b border-hair px-5 py-3.5">
          <div>
            <h2 className="text-[14px] font-semibold text-ink">Agent settings</h2>
            <p className="text-[11.5px] text-muted">Model &amp; credentials for the interactive agent</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-lg text-muted transition-colors hover:bg-elevated hover:text-ink"
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="space-y-4 px-5 py-4">
          {loadError && (
            <Notice tone="warn">Couldn’t reach the backend ({loadError}). Editing local defaults.</Notice>
          )}

          {/* Model preset picker */}
          <Field label="Model preset">
            <div className="flex flex-wrap gap-1.5">
              {presets.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setModel(p)}
                  className={`rounded-lg border px-2.5 py-1.5 font-mono text-[11.5px] transition-colors ${
                    model === p
                      ? 'border-accent/60 bg-accent/10 text-accent'
                      : 'border-hair bg-elevated text-ink-2 hover:border-hair-2'
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </Field>

          {/* Free-text model override */}
          <Field label="Model" hint="Preset or any custom id">
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              spellCheck={false}
              className="w-full rounded-lg border border-hair bg-elevated px-3 py-2 font-mono text-[12.5px] text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60"
              placeholder="provider/model-id"
            />
          </Field>

          {/* API key */}
          <Field label="API key" hint={maskedKey ? 'Leave blank to keep current' : undefined}>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-lg border border-hair bg-elevated px-3 py-2 font-mono text-[12.5px] text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60"
              placeholder={maskedKey ?? 'sk-…'}
            />
          </Field>

          {/* Base URL */}
          <Field label="Base URL" hint="For Ollama / custom endpoints">
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              spellCheck={false}
              className="w-full rounded-lg border border-hair bg-elevated px-3 py-2 font-mono text-[12.5px] text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60"
              placeholder="http://localhost:11434"
            />
          </Field>

          {/* Test result */}
          {test && (
            <div
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[12px] ${
                test.ok
                  ? 'border-sev-recovered/30 bg-sev-recovered/10 text-sev-recovered'
                  : 'border-sev-critical/30 bg-sev-critical/10 text-sev-critical'
              }`}
            >
              {test.ok ? (
                <>
                  <Dot className="bg-sev-recovered" />
                  Connection OK
                  <span className="ml-auto font-mono tabular-nums">{test.latency_ms} ms</span>
                </>
              ) : (
                <>
                  <Dot className="bg-sev-critical" />
                  <span className="truncate">{test.error ?? 'Connection failed'}</span>
                </>
              )}
            </div>
          )}

          {saveError && <Notice tone="critical">Save failed: {saveError}</Notice>}
        </div>

        <footer className="flex items-center gap-2 border-t border-hair px-5 py-3.5">
          <button
            type="button"
            onClick={() => void onTest()}
            disabled={testing}
            className="inline-flex items-center gap-2 rounded-lg border border-hair bg-elevated px-3 py-2 text-[12.5px] font-medium text-ink-2 transition-colors hover:text-ink disabled:opacity-50"
          >
            {testing && <span className="dt-spin inline-block h-3 w-3 rounded-full border-2 border-muted border-t-transparent" />}
            Test connection
          </button>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-3 py-2 text-[12.5px] font-medium text-muted transition-colors hover:text-ink"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void onSave()}
              disabled={saving || model.trim().length === 0}
              className="inline-flex items-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-[12.5px] font-semibold text-white transition-colors hover:bg-accent-2 disabled:opacity-40"
            >
              {saving && <span className="dt-spin inline-block h-3 w-3 rounded-full border-2 border-white/60 border-t-transparent" />}
              Save
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</span>
        {hint && <span className="text-[11px] text-muted/80">{hint}</span>}
      </div>
      {children}
    </label>
  )
}

function Notice({ tone, children }: { tone: 'warn' | 'critical'; children: ReactNode }) {
  const cls =
    tone === 'warn'
      ? 'border-sev-warn/30 bg-sev-warn/10 text-sev-warn'
      : 'border-sev-critical/30 bg-sev-critical/10 text-sev-critical'
  return <div className={`rounded-lg border px-3 py-2 text-[12px] ${cls}`}>{children}</div>
}

function Dot({ className }: { className: string }) {
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${className}`} />
}
