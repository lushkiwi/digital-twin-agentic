# Digital Twin Agentic Layer — MVP

An agentic AI layer on top of [Eclipse Ditto](https://eclipse.dev/ditto/) that turns a
passive digital twin into an active problem solver:

- **Passive Twin** — Eclipse Ditto manages the *reported* (live) vs *desired* (target)
  state of a four-component chained system (**Motor → Pump → Valve → Tank**, one Ditto
  thing each) with coupled physics; a Python device sim streams telemetry at 1 Hz.
  Faults cascade realistically: a motor bearing fault sags rpm, starves line flow, and
  drains the tank.
- **Interactive schematic** — a live SCADA-style SVG diagram: component nodes with
  status rings, animated flow lines, and a click-to-focus drawer with per-component
  charts and **manual parameter editors** (the LLM is not the only way to act — manual
  writes go through the same validated executor and are audit-logged as *operator*
  observations).
- **Sleeper Agent** — a background observer that watches live telemetry, flags anomalies
  per component, correlates across components (*"flow deficit — root cause upstream:
  motor"*), runs periodic LLM reflection, and **never takes action**.
- **Interactive Agent** — a chat interface that interprets commands like
  *"set motor rpm to 2000"* or *"tank level is falling — find the root cause and fix it"*,
  reads the system state for context, and executes validated writes back to Ditto —
  showing every tool call and the exact JSON payload it sends.
- **Model-agnostic core (BYO subscription)** — bring your own key: OpenRouter, Anthropic,
  OpenAI, or a local Ollama endpoint, hot-swappable at runtime from the settings UI
  (a thin custom tool-calling loop over LiteLLM — no framework lock-in).

## Architecture

```
device sim (:9001 debug) ──PUT reported──▶ Eclipse Ditto (:8080, Docker)
        ▲                                        │
        └────SSE desired-state changes───────────┤
                                                 │ SSE all changes
                                                 ▼
                                    FastAPI backend (:8000)
                                    ├─ Sleeper loop (read-only)
                                    ├─ Interactive agent (LLM → validator → Ditto)
                                    └─ WebSocket fan-out ─▶ React UI (:5173)
```

The Sleeper is read-only *structurally* — its code path contains no write capability.
The Interactive agent's safety lives in a validating executor (tool whitelist + Pydantic
bounds), not in the prompt. See `docs/CONTRACTS.md` for every interface.

## Prerequisites

- Docker (Docker Desktop, OrbStack, or colima) — for the Ditto stack
- Python 3.11+ and Node 20+
- An LLM API key (OpenRouter recommended; Anthropic/OpenAI/local Ollama also work)

## Quick start

```bash
make env        # creates .env from template — add your API key(s)
make venv       # python venv + backend/sim deps
cd frontend && npm install && cd ..

make ditto-up   # start Ditto (waits until the API answers)
make things     # create/reset all four component twins

# three terminals:
make sim        # device simulator
make backend    # agents + API
make frontend   # UI → http://localhost:5173
```

## Demo (3 minutes)

1. Open http://localhost:5173 — live telemetry, calm sleeper log.
2. Tour the schematic; click a node to open its focus drawer (charts + manual controls).
3. `make fault-bearing` → motor rpm sags, the flow line thins, the tank starts draining.
   The sleeper flags the motor, then correlates: *"flow deficit — root cause upstream:
   motor"*; its LLM reflection spots the bearing signature (current up while rpm down).
4. Chat: **"Tank level is falling — find the root cause and fix it"** → the agent reads
   observations + system state + the motor's trend, names the motor as root cause, and
   raises the rpm setpoint to compensate — with the exact Ditto PUT shown in chat.
5. Manual beat: click the tank, trim drain rate 140 → 110, Apply — an *operator*
   observation joins the feed and the level recovers. Rule, LLM, and operator actions
   interleave in one audit trail.
6. Reset any time: `make fault-clear`, `make things` (resets all twins to baseline).

Fault injection: `make fault-bearing | fault-overheat | fault-leak | fault-stuck | fault-clear`,
sim state: `make sim-state`.

## Repo layout

| Path | What it is |
|---|---|
| `docker/` | Trimmed official Ditto docker-compose (pinned 3.5.12) + nginx config |
| `scripts/` | Twin bootstrap (`things/*.json`, `create_things.sh`) |
| `device-sim/` | Coupled system physics (motor/pump/valve/tank), Ditto I/O, fault-injection debug API |
| `backend/` | FastAPI: twin client, SSE ingest, sleeper + interactive agents, WS hub |
| `frontend/` | Vite + React + Tailwind three-panel UI |
| `docs/CONTRACTS.md` | Binding interface contracts between all components |
