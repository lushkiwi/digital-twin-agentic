import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useStore } from '../store'
import { streamChat } from '../lib/api'
import type { ChatTurn, Step } from '../types'
import ToolCallStep from './ToolCallStep'

export default function ChatPanel() {
  const turns = useStore((s) => s.turns)
  const streaming = useStore((s) => s.streaming)
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // keep pinned to newest as the stream grows
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns, streaming])

  async function send() {
    const message = draft.trim()
    if (!message || streaming) return
    setDraft('')

    const store = useStore.getState()
    const history = store.buildHistory()
    const turnId = store.startTurn(message)

    try {
      await streamChat(message, history, (ev) => {
        useStore.getState().applyChatEvent(turnId, ev)
      })
      useStore.getState().finishTurn(turnId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      useStore.getState().failTurn(turnId, `Request failed: ${msg}`)
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send()
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="scroll-thin min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {turns.length === 0 ? (
          <EmptyState />
        ) : (
          turns.map((t) => <TurnView key={t.id} turn={t} />)
        )}
      </div>

      {/* composer */}
      <div className="border-t border-hair px-3 py-3">
        <div
          className={`flex items-end gap-2 rounded-xl border bg-elevated px-3 py-2 transition-colors ${
            streaming ? 'border-hair opacity-70' : 'border-hair-2 focus-within:border-accent/60'
          }`}
        >
          <textarea
            ref={taRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={streaming}
            rows={1}
            placeholder={streaming ? 'Agent is working…' : 'Ask the agent to inspect or act on the system…'}
            className="scroll-thin max-h-32 min-h-[24px] flex-1 resize-none bg-transparent text-[13px] leading-relaxed text-ink placeholder:text-muted focus:outline-none disabled:cursor-not-allowed"
          />
          <button
            type="button"
            onClick={() => void send()}
            disabled={streaming || draft.trim().length === 0}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent text-white transition-opacity hover:bg-accent-2 disabled:cursor-not-allowed disabled:opacity-30"
            aria-label="Send"
          >
            {streaming ? (
              <span className="dt-spin inline-block h-3.5 w-3.5 rounded-full border-2 border-white/60 border-t-transparent" />
            ) : (
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
        </div>
        <div className="mt-1.5 px-1 text-[11px] text-muted">
          <kbd className="font-sans">Enter</kbd> to send · <kbd className="font-sans">Shift</kbd>+
          <kbd className="font-sans">Enter</kbd> for newline
        </div>
      </div>
    </div>
  )
}

function TurnView({ turn }: { turn: ChatTurn }) {
  return (
    <div className="space-y-2.5">
      {/* user message */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-[13px] leading-relaxed text-white">
          {turn.userMessage}
        </div>
      </div>

      {/* assistant steps, in arrival order */}
      <div className="space-y-2">
        {turn.steps.map((step, i) => (
          <StepView key={i} step={step} />
        ))}
        {turn.status === 'streaming' && turn.steps.length === 0 && (
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <span className="dt-spin inline-block h-3 w-3 rounded-full border-2 border-muted border-t-transparent" />
            thinking…
          </div>
        )}
      </div>
    </div>
  )
}

function StepView({ step }: { step: Step }) {
  switch (step.kind) {
    case 'plan_text':
    case 'assistant_text':
      return (
        <p
          className={`dt-fade-in whitespace-pre-wrap text-[13px] leading-relaxed ${
            step.kind === 'assistant_text' ? 'text-ink' : 'text-ink-2'
          }`}
        >
          {step.text}
        </p>
      )
    case 'error':
      return (
        <div className="dt-fade-in flex items-start gap-2 rounded-lg border border-sev-critical/30 bg-sev-critical/10 px-3 py-2 text-[12.5px] text-sev-critical">
          <svg viewBox="0 0 24 24" className="mt-0.5 h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 8v5M12 16.5v.5" strokeLinecap="round" />
            <circle cx="12" cy="12" r="9" />
          </svg>
          <span>{step.message}</span>
        </div>
      )
    case 'tool':
      return <ToolCallStep step={step} />
  }
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="grid h-11 w-11 place-items-center rounded-xl border border-hair bg-surface-2 text-accent">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path
            d="M4 5h16v11H8l-4 4V5Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p className="mt-3 text-[13px] font-medium text-ink-2">Interactive agent ready</p>
      <p className="mt-1 max-w-[15rem] text-[12px] leading-relaxed text-muted">
        It can read the twin and act on it — each tool call shows the exact JSON sent to Ditto.
      </p>
      <p className="mt-3 text-[11.5px] text-muted">Try: “line flow is dropping and the tank is draining — stabilize it”</p>
    </div>
  )
}
