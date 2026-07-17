# Interface Contracts (single source of truth)

Every component codes against this document. If something here is ambiguous, the
implementation must match the JSON examples exactly — field names are load-bearing.

All env vars are defined in `.env.example` at repo root. Python code loads them via
`python-dotenv` (repo-root `.env`) with the defaults shown there.

## 1. Ditto access

- Base URL `http://localhost:8080`, HTTP basic auth `ditto:ditto`, thing `org.acme:pump-01`.
- Initial thing model: see `scripts/thing.json`. Features:
  - `telemetry.properties`: `temperature` (°C, float), `pressure` (bar, float), `flow_rate` (L/min, float), `ts` (ISO8601 UTC string).
  - `pump.properties` (reported): `pump_speed` (int 0–100), `valve_state` (`"open"|"closed"`).
  - `pump.desiredProperties` (targets): same two keys.

Endpoints used:

| Purpose | Call |
|---|---|
| Sim → reported telemetry (1 Hz) | `PUT /api/2/things/org.acme:pump-01/features/telemetry/properties` (full JSON object) |
| Sim → reported actuator state | `PUT /api/2/things/org.acme:pump-01/features/pump/properties` (full JSON object) |
| Agent → desired write | `PUT /api/2/things/org.acme:pump-01/features/pump/desiredProperties/pump_speed` body `50` (bare JSON value; also `.../valve_state` body `"closed"`) |
| Read whole twin | `GET /api/2/things/org.acme:pump-01` |
| Change stream | `GET /api/2/things?ids=org.acme:pump-01&fields=thingId,features` with header `Accept: text/event-stream` |

SSE notes: each `data:` line is a JSON **partial** of the thing containing only changed
fields (e.g. `{"thingId":"org.acme:pump-01","features":{"telemetry":{"properties":{...}}}}`).
Consumers MUST keep a locally cached thing (seeded by a full GET on connect) and
**deep-merge** each event into it, then act on the merged state. Empty `data:` lines are
keep-alives — ignore. Reconnect with exponential backoff (1s → 30s cap) and re-GET the
full thing after every reconnect.

## 2. Device simulator (`device-sim/`)

Physics (tick every `TELEMETRY_INTERVAL_S`, default 1.0s; all rates per second):

- Targets at current reported `speed` (valve open): `temp_target = 40 + 0.4*speed`,
  `pressure_target = 2.0 + 0.05*speed`, `flow_target = 2.0*speed`.
  Valve closed: `flow_target = 0`, `pressure_target = 2.0 + 0.05*speed` (isolated, holds).
- First-order dynamics: `T += (temp_target - T)/20 * dt`, `P += (pressure_target - P)/8 * dt`,
  `F += (flow_target - F)/3 * dt`. Add small deterministic ripple:
  `+0.15*sin(2π*t/7)` on temperature, `+0.05*sin(2π*t/5)` on pressure (t = seconds since start).
  No randomness — demo must be reproducible.
- **Fault `overheat`**: adds `+1.0*(speed/60)` °C/s to temperature while active.
  (First-order equilibrium with the fault: `T_eq = temp_target + TEMP_TAU * drift`. At
  speed 80: T_eq ≈ 98.7°C — crosses the 85°C warn line ~13s after injection and the 95°C
  critical line ~40s in. Reducing speed to 35 gives T_eq ≈ 65.7°C, so the corrective
  action genuinely resolves the anomaly even while the fault stays active.)
- **Fault `leak`**: while valve is open, pressure target becomes 1.5 (decays via same
  first-order dynamics). Closing the valve isolates the leak: normal closed-valve physics apply.
- Convergence to desired: reported `pump_speed` slews toward desired at **5 units/s**;
  `valve_state` flips to desired after a 2s actuation delay.
- Sim is the ONLY writer of reported state; it never touches desiredProperties.

Desired-state watch: SSE per §1 filtered to `features/pump/desiredProperties`, plus a
2s polling fallback (`GET .../features/pump/desiredProperties`) that activates whenever
SSE is down. Ditto being unreachable must never crash the sim — keep simulating, retry I/O.

Debug API (FastAPI on `SIM_DEBUG_PORT` 9001, entrypoint `device-sim/sim.py` exposing `app`):

- `POST /fault/{mode}` where mode ∈ `overheat|leak|clear` → `{"fault": "overheat"}` (or `null` after clear)
- `GET /state` → full internal state: `{"t": float, "fault": str|null, "reported": {...}, "desired": {...}, "telemetry": {...}, "ditto_connected": bool}`

## 3. Backend (`backend/`, FastAPI on port 8000)

### WebSocket `/ws` (backend → UI fan-out)

One connection per browser tab; server broadcasts JSON text frames:

```json
{"type": "telemetry", "data": {"ts": "2026-07-17T10:04:01Z", "temperature": 64.2,
 "pressure": 5.01, "flow_rate": 119.4, "pump_speed_reported": 60,
 "valve_state_reported": "open", "pump_speed_desired": 60, "valve_state_desired": "open"}}
```
```json
{"type": "observation", "data": {"id": "obs-42", "ts": "2026-07-17T10:04:05Z",
 "severity": "warn", "source": "rule", "title": "Temperature above 85°C",
 "detail": "temperature reached 86.4°C while pump_speed=80"}}
```
```json
{"type": "status", "data": {"ditto_connected": true}}
```

Telemetry frames are emitted on every merged Ditto SSE event that touched
`features/telemetry` (~1 Hz), built from the merged twin cache (§1 SSE notes).
`severity` ∈ `info|warn|critical|recovered`. `source` ∈ `rule|llm`.

### `POST /api/chat` (interactive agent)

Request: `{"message": "fix the anomaly", "history": [{"role": "user|assistant", "content": "..."}]}`
(history = prior finalized turns, text only, capped at last 10).

Response: `text/event-stream`; each event is `data: <json>\n\n`:

```
{"type": "plan_text",      "text": "Overheating at speed 80; reducing speed to 35."}
{"type": "tool_call",      "id": "tc_1", "name": "set_pump_speed", "args": {"speed": 35, "reason": "reduce thermal load"}}
{"type": "tool_result",    "id": "tc_1", "ok": true, "result": {...},
                           "ditto_request": {"method": "PUT", "path": "/api/2/things/org.acme:pump-01/features/pump/desiredProperties/pump_speed", "body": 35},
                           "ditto_status": 204}
{"type": "assistant_text", "text": "Done — pump speed lowered to 35. The sleeper will confirm recovery."}
{"type": "error",          "message": "LLM call failed: ..."}
{"type": "done"}
```

`plan_text` = assistant prose emitted *before/between* tool calls; `assistant_text` = final
answer. `ditto_request` is `null` for read-only tools. `error` is followed by `done`; the
stream always ends with `done`.

### REST

- `GET /api/health` → `{"ok": true, "ditto_connected": bool}`
- `GET /api/observations?limit=50` → `{"observations": [<observation data objects, newest last>]}`
- `GET /api/telemetry?minutes=5` → `{"points": [<telemetry data objects, oldest first>]}` (for chart backfill on page load)
- `GET /api/config` → `{"model": "openrouter/anthropic/claude-sonnet-5", "api_key_masked": "sk-or-v1****" | null, "base_url": null, "presets": ["openrouter/anthropic/claude-sonnet-5", "openrouter/anthropic/claude-fable-5", "openrouter/openai/gpt-5.6-sol", "anthropic/claude-fable-5", "openai/gpt-5.6", "ollama/llama3.1"]}`
- `POST /api/config` body `{"model": str, "api_key": str|null, "base_url": str|null}` → same shape as GET. Keys held in process memory only, never logged, never returned unmasked. Empty/null `api_key` keeps the existing one.
- `POST /api/config/test` → `{"ok": bool, "error": str|null, "latency_ms": int}` (1-token completion ping with current config)

### Interactive agent tools

| Tool | Args (Pydantic bounds) | Effect |
|---|---|---|
| `get_twin_state` | — | full thing GET, returns reported + desired |
| `get_telemetry_window` | `minutes: int 1–10` | ring-buffer slice, downsampled to ≤60 points |
| `get_observations` | `limit: int 1–20` | recent sleeper observations |
| `set_pump_speed` | `speed: int 0–100`, `reason: str` | PUT desired pump_speed |
| `set_valve_state` | `state: "open"\|"closed"`, `reason: str` | PUT desired valve_state |
| `run_stress_test` | `profile: "thermal"\|"pressure"`, `duration_s: int 10–120` | background ramp task (snapshot speed → step to 90 → hold → restore); returns `{"status":"started"}` immediately |

Executor is the safety boundary: unknown tool or failed validation returns a structured
error string to the LLM as the tool result — it is never executed. Sleeper has NO tools
(prompt-only reflection); its module must not import any write capability.

## 4. Frontend (`frontend/`, Vite dev server on 5173)

- Vite proxy: `/api` → `http://localhost:8000` and `/ws` → `ws://localhost:8000` (ws: true),
  so the browser sees a single origin.
- On load: `GET /api/telemetry?minutes=10` + `GET /api/observations` to backfill, then
  live via `/ws`. Chat via `POST /api/chat` consuming the SSE stream with
  `fetch` + `ReadableStream` (not `EventSource` — it's a POST).
- Renders the chat step events in order: plan_text as assistant prose, tool_call/tool_result
  as collapsible step cards showing args and the exact `ditto_request` JSON.
