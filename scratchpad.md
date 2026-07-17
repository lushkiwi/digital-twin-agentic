# Project Scratchpad

Working journal for observations, progress, decisions, and open threads.
Newest entries at the top. Keep entries short; link code/docs instead of duplicating them.

---

## 2026-07-17 — Day 1: full MVP built and demo-rehearsed

### Status: ✅ end-to-end working

All four layers built, integrated, and verified live in one session (three parallel
build agents + orchestration): Ditto infra → device sim → backend (both agents) →
three-panel UI. Full demo loop rehearsed headless AND through the real browser UI.

### Decisions made
- React + Tailwind over Streamlit (live 3-panel updates without rerun jank).
- Custom tool-calling loop on LiteLLM over LangChain (transparent steps, easy debug).
- HTTP-only device↔Ditto transport; MQTT deferred (was: stretch goal).
- OpenRouter as default provider (`openrouter/anthropic/claude-sonnet-5`), presets for
  Fable 5 / GPT-5.6 / Ollama in the settings modal.
- colima (not Docker Desktop) as container runtime on this Mac.
- Ditto 3.5.12 pinned; trimmed compose to 6 services (dropped connectivity,
  things-search, swagger-ui).
- No LLM token streaming — step-level SSE events only (kills cross-provider
  tool-call-delta parsing bugs).
- Safety is structural: sleeper's code path has no write capability at all;
  interactive agent writes go through a Pydantic-validated executor whitelist.

### Bugs found & fixed
1. **Fault physics couldn't reach critical.** Specced overheat drift (0.5·speed/60 °C/s)
   gave equilibrium 85.3°C — barely above warn (85), never reaching critical (95), and
   ~75s to cross. Doubled to 1.0·speed/60: warn ~15s, critical ~40s (measured live,
   matched prediction within 2s). Fix at speed 35-40 lands equilibrium ~66-69°C.
2. **LiteLLM's OpenRouter tool path hard-imports `orjson`** — not pulled in as a dep;
   plain pings worked, tool calls crashed. Added to backend/requirements.txt.
3. **Sleeper LLM reflections silently dead.** `max_tokens=400` made claude-sonnet-5
   return `''`/`[]` on large telemetry payloads; both except-paths swallowed everything
   silently. Raised to 2000, added logging, strengthened SYSTEM_REFLECT to offer
   diagnostic interpretation when rule flags are active. Now produces gems like
   "temperature climbing while speed/flow steady — thermal fault, not load change."

### Observations
- Ditto SSE can miss events during subscription establishment (verified empirically).
  Design absorbs it: re-GET full thing on every (re)connect + sim's 2s poll fallback.
- Ditto desired-property changes DO stream over SSE (earlier negative was the race above).
- First `PUT /api/2/things/{id}` with no policyId auto-creates a permissive policy —
  no hand-written policies needed for local dev.
- The sleeper's reflection tier turned out to be a great demo beat on its own: it posted
  an unprompted post-recovery analysis ("marginal-but-stable equilibrium, not a
  progressive cooling failure") that reads like a junior reliability engineer.

### Live verification highlights (all measured, not assumed)
- Desired write → sim converges at exactly 5 units/s (60→30 over 6s, sampled).
- Chat "set pump speed to 50": get_twin_state → plan → validated PUT (204) → converged.
- Chat "fix the anomaly" at 97°C: read observations → state → trend → ruled out leak
  (pressure/flow normal) → set_pump_speed(40) → recovered observations at 84.1°C.
- Chat "run a thermal stress test for 60 seconds" via the real browser UI: ramp to 90,
  hold, auto-restore to 60 — visible on chart, `{"status":"started"}` tool card rendered.

### Open threads / next steps
- [ ] Rotate the OpenRouter key (it touched chat history during setup).
- [ ] MQTT transport via Mosquitto + Ditto connectivity (deferred stretch goal).
- [ ] Ollama local-model path untested end-to-end (presets wired, no local model pulled).
- [ ] Sleeper "convergence stall" rule exists but hasn't been exercised in a live run.
- [ ] Frontend observation de-dup ignores re-sent ids with updated fields (fine per
      contract; revisit if backend ever mutates observations).
- [ ] Demo dry-run from a fully cold start (`docker compose down -v` → `make demo`)
      before showing it to anyone.
