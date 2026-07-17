# Digital Twin Agentic Layer — MVP

An agentic AI layer on top of [Eclipse Ditto](https://eclipse.dev/ditto/) that turns a
passive digital twin into an active problem solver:

- **Passive Twin** — Eclipse Ditto manages the *reported* (live) vs *desired* (target)
  state of a simulated industrial pump; a Python device sim streams telemetry at 1 Hz.
- **Sleeper Agent** — a background observer that watches live telemetry, flags anomalies
  and drift (fast rules + periodic LLM reflection), and **never takes action**.
- **Interactive Agent** — a chat interface that interprets commands like
  *"change pump speed to 50"*, *"run a thermal stress test"*, or *"fix the anomaly"*,
  reads the twin's state for context, and executes validated writes back to Ditto —
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
make thing      # create/reset the pump twin

# three terminals:
make sim        # device simulator
make backend    # agents + API
make frontend   # UI → http://localhost:5173
```

## Demo (3 minutes)

1. Open http://localhost:5173 — live telemetry, calm sleeper log.
2. Chat: **"set pump speed to 80"** → watch the tool steps + Ditto JSON payload, chart slews to 80.
3. `make fault-overheat` → temperature climbs; the sleeper flags warn → critical,
   plus an LLM reflection. It watches — it never acts.
4. Chat: **"fix the anomaly"** → the agent reads observations + twin state, states a plan,
   lowers pump speed; the chart bends back down; the sleeper posts *recovered*.
5. Reset any time: `make fault-clear`, `make thing` (resets twin to baseline).

Fault injection: `make fault-overheat | fault-leak | fault-clear`, sim state: `make sim-state`.

## Repo layout

| Path | What it is |
|---|---|
| `docker/` | Trimmed official Ditto docker-compose (pinned 3.5.12) + nginx config |
| `scripts/` | Twin bootstrap (`thing.json`, `create_thing.sh`) |
| `device-sim/` | Pump physics, Ditto I/O, fault-injection debug API |
| `backend/` | FastAPI: twin client, SSE ingest, sleeper + interactive agents, WS hub |
| `frontend/` | Vite + React + Tailwind three-panel UI |
| `docs/CONTRACTS.md` | Binding interface contracts between all components |
