# Interface Contracts v2 (single source of truth)

Every component codes against this document. If something here is ambiguous, the
implementation must match the JSON examples exactly — field names are load-bearing.
V2 replaces the v1 single-pump model with a four-component chained system.

All env vars are defined in `.env.example` at repo root. Python code loads them via
`python-dotenv` (repo-root `.env`) with the defaults shown there.

## 0. System topology

```
Motor ──rpm──▶ Pump ──capacity──▶ Valve ──throttled flow──▶ Tank ──drain──▶ out
org.acme:motor-01   org.acme:pump-01   org.acme:valve-01     org.acme:tank-01
```

Component ids (used everywhere: WS frames, observations, REST paths, sim API):
`motor | pump | valve | tank`. Thing ids are `${THING_NS}:<component>-01` with
`THING_NS=org.acme`.

## 1. Ditto access

- Base URL `http://localhost:8080`, HTTP basic auth `ditto:ditto`.
- Four things, one per component. Initial models: `scripts/things/<component>-01.json`.
  Each thing has a `telemetry` feature (sim-written, 1 Hz) and one actuator feature
  (named after the component) holding reported `properties` + `desiredProperties`.

| Thing | telemetry properties | actuator feature: properties = desiredProperties |
|---|---|---|
| `org.acme:motor-01` | `rpm` (float), `temp` (°C), `current` (A), `ts` | `motor`: `rpm_setpoint` (int 0–3000, default 1800) |
| `org.acme:pump-01` | `flow` (L/min), `pressure` (bar), `temp` (°C), `ts` | `pump`: `pump_speed` (int 0–100, default 70) |
| `org.acme:valve-01` | `flow` (L/min), `ts` | `valve`: `position` (int 0–100, default 100) |
| `org.acme:tank-01` | `level_pct` (float 0–100), `inflow`, `outflow` (L/min), `ts` | `tank`: `drain_rate` (int 0–200, default 140) |

Endpoints used (per thing; `{tid}` = thing id, `{feat}` = actuator feature name):

| Purpose | Call |
|---|---|
| Sim → telemetry (1 Hz) | `PUT /api/2/things/{tid}/features/telemetry/properties` (full object) |
| Sim → reported actuator | `PUT /api/2/things/{tid}/features/{feat}/properties` (full object) |
| Agent/operator → desired | `PUT /api/2/things/{tid}/features/{feat}/desiredProperties/{prop}` (bare JSON value) |
| Read one thing | `GET /api/2/things/{tid}` |
| Read all four | 4 GETs via `asyncio.gather` |
| Change stream (ALL things, ONE stream) | `GET /api/2/things?ids=org.acme:motor-01,org.acme:pump-01,org.acme:valve-01,org.acme:tank-01&fields=thingId,features` with `Accept: text/event-stream` |

SSE notes (unchanged from v1, now multi-thing): each `data:` line is a JSON partial of
ONE thing containing `thingId` + changed fields. Consumers keep a per-thing cached
thing (`dict[thing_id, thing]`), seed via full GETs on connect, route each event by its
`thingId`, and **deep-merge**. Empty `data:` lines are keep-alives. Events can be missed
during subscription establishment — always re-GET all things after every (re)connect.
Reconnect with exponential backoff (1s → 30s cap); 60s no-data watchdog forces reconnect.

## 2. Device simulator (`device-sim/`, ONE process)

### 2.1 Physics (tick every `TELEMETRY_INTERVAL_S` = 1.0s; rates per second; deterministic, NO randomness)

`SystemPhysics` composes per-component models, ticked in coupling order
motor → pump → valve → tank. First-order relaxation `X += (target − X)/τ · dt`,
actuator slew, sine ripple applied read-only (never fed back into state).

**Motor** — writable `rpm_setpoint` (0–3000, default 1800).
- `rpm` slews toward `rpm_setpoint · (1 − sag)` at 60 rpm/s. `sag` = 0 normally.
- `temp_target = 35 + rpm/90`, τ = 25s. Baseline @1800: 55°C. Ripple `+0.15·sin(2πt/7)`.
- `current_target = 2 + 8·(rpm/1800)·(pump_speed/100) + 8·sag`, τ = 5s. Baseline 7.6 A.
- **Fault `bearing`**: sag ramps 0 → 0.30 at 0.01/s (full in 30s; ramps back down on clear).
  Bearing heat: `temp += 3.0·sag·dt`. At full sag rpm = 1260 so `temp_target` drops to 49;
  equilibrium 49 + 25·0.9 = **71.5°C = warn-only** (> 70, < 85).
  Diagnostic signature: **current UP (8.3 A) while rpm DOWN (1260)** — friction, not load.
- **Fault `overheat`**: `temp += 1.6·(rpm/1800)·dt` → equilibrium @1800: 55 + 40 = **95°C**
  (crosses warn 70 @~12s, crit 85 @~35s from baseline). Mitigation: setpoint 900 →
  T_eq = 45 + 20 = 65°C < 70 — genuine recovery while fault stays active.

**Pump** — writable `pump_speed` (0–100, default 70), slew 5 units/s.
- `capacity = 200·(rpm/1800)·(pump_speed/100)` L/min. Baseline **140**.
- `flow_target = capacity·(valve_position/100)·leak_factor`, τ = 3s. `leak_factor` = 0.75
  while fault `leak` active, else 1.0.
- `pressure_target = 1.0 + 4.0·(pump_speed/100)·(rpm/1800) + 3.0·(1 − position/100)`, τ = 5s,
  ripple `+0.05·sin(2πt/5)`. While `leak` active and valve open: fixed 1.5.
  Equilibria: baseline **3.8 bar**; leak **1.5** (< warn-low 2.5); deadhead (position 0)
  @speed 70: **6.8** (> warn-high 6.0); @speed 100: **8.0** (> crit-high 7.5).
- `temp_target = 30 + 0.35·pump_speed`, τ = 20s. Baseline 54.5°C.
- **Fault `leak`**: as above (flow ×0.75, pressure → 1.5).

**Valve** — writable `position` (0–100, default 100). Slews 20 %/s (full stroke 5s).
- `flow` telemetry mirrors the line flow (post-throttle).
- **Fault `stuck`**: position frozen at current value; desired ignored while active.

**Tank** — writable `drain_rate` (0–200 L/min, default 140). The only integrator.
- `d(level_pct)/dt = (inflow − outflow)/120` %/s, clamped 0–100 (capacity 200 L).
- `inflow` = line flow; `outflow = drain_rate` (0 when level = 0).
- Baseline: inflow 140 = drain 140 → level holds at 50%.

**Cascade reference timings (bearing fault @ defaults — tests MUST assert these ±20%):**

| t (s) | event |
|---|---|
| ~7 | rpm visibly below setpoint (sag > 5%) |
| ~30 | sag full: rpm 1260, line flow → ~98 L/min, tank net −42 L/min (−0.35 %/s) |
| ~44 | tank level < 40% (from 50%) |
| ~72 | tank level < 30% |
| fix: `rpm_setpoint=2600` | effective rpm 2600·0.70 = 1820 → flow ≈ 141 within ~20s |
| then `drain_rate=110` | net +30 L/min → level recovers > 40% in ~40s |

### 2.2 Debug API (FastAPI on `SIM_DEBUG_PORT` 9001, entrypoint `sim.py` exposing `app`)

- `POST /fault/{component}/{mode}` — valid: `motor: bearing|overheat`, `pump: leak`,
  `valve: stuck`. 404 for unknown component/mode. Returns `{"faults": {"motor": "bearing", "pump": null, "valve": null, "tank": null}}`.
- `POST /fault/clear` — clears ALL faults (exact path kept for Makefile compat). Same return shape.
- `GET /state` → `{"t": float, "faults": {...}, "components": {"motor": {"telemetry": {...}, "reported": {...}, "desired": {...}}, ...}, "ditto_connected": bool}`

I/O: telemetry + reported PUTs for all 4 things each tick via
`asyncio.gather(..., return_exceptions=True)` (~8 PUTs/s total). Desired watch: ONE SSE
stream with all 4 ids (§1) + 2s polling fallback iterating the 4 desired endpoints while
SSE is down. Ditto being down never crashes or stops physics.

## 3. Backend (`backend/`, FastAPI on port 8000)

### 3.1 The param registry (`backend/app/params.py`) — the spine

Single source of truth for every writable parameter: component, param name, tool arg
name, label, unit, kind, bounds/step, thing id, feature, tool name. Derives: LLM write-tool
schemas + Pydantic validators, `POST /api/control` validation, `GET /api/params` payload.
Bounds exist in exactly one place.

| component | param | arg name | bounds (int) | step | tool |
|---|---|---|---|---|---|
| motor | rpm_setpoint | rpm | 0–3000 | 50 | `set_motor_rpm` |
| pump | pump_speed | speed | 0–100 | 5 | `set_pump_speed` |
| valve | position | position | 0–100 | 5 | `set_valve_position` |
| tank | drain_rate | drain_rate | 0–200 | 10 | `set_tank_drain_rate` |

### 3.2 WebSocket `/ws` — telemetry frame v2 (BREAKING vs v1)

Frames are built by a **1 Hz coalescing flusher** (dirty flag set by SSE ingest; NOT one
frame per SSE event). The flusher evaluates sleeper rules FIRST, then attaches per-component
`status` (`ok|warn|critical`, from the sleeper's active rule flags: any critical rule for
that component → `critical`, else any warn → `warn`), then appends to the buffer and
broadcasts:

```json
{"type": "telemetry", "data": {"ts": "2026-07-17T10:04:01Z", "components": {
  "motor": {"rpm": 1800.0, "temp": 55.2, "current": 7.6,
            "rpm_setpoint_reported": 1800, "rpm_setpoint_desired": 1800, "status": "ok"},
  "pump":  {"flow": 140.1, "pressure": 3.81, "temp": 54.5,
            "pump_speed_reported": 70, "pump_speed_desired": 70, "status": "ok"},
  "valve": {"flow": 140.1, "position_reported": 100, "position_desired": 100, "status": "ok"},
  "tank":  {"level_pct": 50.0, "inflow": 140.1, "outflow": 140.0,
            "drain_rate_reported": 140, "drain_rate_desired": 140, "status": "ok"}}}}
```

Observation frames (v1 + `component`; `source` gains `operator`):

```json
{"type": "observation", "data": {"id": "obs-42", "ts": "...", "severity": "warn",
 "source": "rule", "component": "motor", "title": "Motor RPM sag",
 "detail": "rpm 1263 vs setpoint 1800 (30% below) for >20s"}}
```

`component` ∈ `motor|pump|valve|tank|null` (null = system-wide/cross-component).
`severity` ∈ `info|warn|critical|recovered`. `source` ∈ `rule|llm|operator`.
Status frames unchanged: `{"type": "status", "data": {"ditto_connected": bool}}`.

### 3.3 REST

Unchanged from v1: `GET /api/health`, `GET /api/observations?limit=`,
`GET /api/config` / `POST /api/config` / `POST /api/config/test`, `POST /api/chat`
(chat SSE step-event protocol is IDENTICAL to v1: `plan_text|tool_call|tool_result|assistant_text|error|done`,
`ditto_request` shows `{method, path, body}`, null for reads).

Changed/new:

- `GET /api/telemetry?minutes=5` → `{"points": [<v2 frame data objects, oldest first>]}`
- `GET /api/system` (new) → `{"components": {"motor": {"reported": {...}, "desired": {...}, "telemetry": {...}, "status": "ok"}, ...}, "ditto_connected": true}` — same builder the LLM's `get_system_state` uses.
- `GET /api/params` (new) → `{"components": {"motor": {"label": "Motor", "thing_id": "org.acme:motor-01", "params": [{"name": "rpm_setpoint", "label": "RPM setpoint", "unit": "rpm", "kind": "int", "min": 0, "max": 3000, "step": 50}]}, "pump": {...}, "valve": {...}, "tank": {...}}}`
- `POST /api/control/{component}/{param}` (new) — body `{"value": 2600, "reason": "operator trim"}`
  (`reason` optional, defaults to "manual operator adjustment"). 404 unknown component/param.
  Routes through **the same `tools.execute()`** as LLM writes. Success →
  `{"ok": true, "ditto_status": 204, "ditto_request": {...}}` AND emits an observation
  `{severity: "info", source: "operator", component, title: "Operator set <param> → <value>", detail: <reason>}`.
  Validation failure → `{"ok": false, "error": "<executor's structured error>"}` (HTTP 200; never touches Ditto).

### 3.4 LLM tools (9; write schemas generated from the registry)

| Tool | Args (validated) | Effect |
|---|---|---|
| `get_system_state` | — | all 4 components: reported + desired + latest telemetry + status (replaces v1 `get_twin_state`; keep old name as unadvertised alias) |
| `get_telemetry_window` | `minutes: 1–10`, `component?: motor\|pump\|valve\|tank` | buffer slice ≤60 points, optionally one component only |
| `get_observations` | `limit: 1–20` | recent observations |
| `set_motor_rpm` | `rpm: 0–3000`, `reason` | PUT motor desired `rpm_setpoint` |
| `set_pump_speed` | `speed: 0–100`, `reason` | PUT pump desired `pump_speed` |
| `set_valve_position` | `position: 0–100`, `reason` | PUT valve desired `position` |
| `set_tank_drain_rate` | `drain_rate: 0–200`, `reason` | PUT tank desired `drain_rate` |
| `run_stress_test` | `profile: thermal\|flow`, `duration_s: 10–120` | bg ramp task: `thermal` = pump_speed ladder to 90 (v1 logic); `flow` = motor setpoint ladder to 2700; snapshot → restore |

Executor rules unchanged from v1: whitelist + Pydantic bounds, structured error string on
failure (never executed, never raises), `ToolOutcome{ok, result, ditto_request, ditto_status}`.
Sleeper still has NO tools and imports no write path.

### 3.5 Sleeper rules v2 (thresholds are THE reference — frontend chart thresholds come from here)

Per-component (rule keys prefixed `motor.*` etc., 60s re-fire cooldown, `recovered` on clear):

| Component | Rule | warn | critical |
|---|---|---|---|
| motor | temp high | > 70 | > 85 |
| motor | rpm sag | `rpm < 0.93·setpoint_reported` sustained > grace, where grace = `max(20, |Δsetpoint|/60 + 5)`s since last desired change (slew-aware — no false positive on setpoint ramps) | — |
| pump | pressure low (leak) | < 2.5 | — |
| pump | pressure high (deadhead) | > 6.0 | > 7.5 |
| pump | temp high | > 75 | — |
| valve | stall | `|position_reported − position_desired| > 2` sustained > 15s | — |
| tank | level low | < 40 | < 30 |
| tank | level high | > 90 | > 95 |

Cross-component (component = null): `expected_flow = 200·(setpoint/1800)·(speed_reported/100)·(position_reported/100)`;
fire warn **"Flow deficit — root cause upstream: motor"** when `flow < 0.8·expected AND
rpm < 0.93·setpoint` sustained 5s and `expected > 20`. Variant blaming the valve when the
motor is converged but valve position ≠ desired. LLM reflection unchanged in mechanism
(interval, no tools, JSON array, max 1/cycle) — payload becomes 30 downsampled v2 frames +
active flags + recent 5 observations.

## 4. Frontend (`frontend/`, Vite dev server on 5173)

- Proxy/backfill/WS/chat-stream mechanics unchanged from v1 (fetch+ReadableStream for chat).
- Left panel: **SystemSchematic** (hand-authored SVG, viewBox 800×280, no layout lib) —
  4 nodes (motor circle, pump volute, valve bowtie, tank rect with live level fill) showing
  key value (rpm / flow / position% / level%) + status ring colored by `status`; 3 flow
  edges with `strokeWidth = 2 + 4·(flow/200)` and CSS dash-offset animation whose duration
  scales inversely with flow (static/dim below 2 L/min). Click node → `selectedComponent`.
- **FocusDrawer** (opens on selection): component mini-charts + editors generated from
  `GET /api/params` (int → slider+stepper with Apply; shows reported vs desired), Apply →
  `POST /api/control/...`; in-flight disable; result shows ditto_status.
- Charts below schematic: system overview (motor rpm, line flow, pump pressure, tank level)
  by default; per-component small multiples when a node is selected. Threshold reference
  lines from §3.5 table.
- ObservationLog: component chip per row; `operator` source gets distinct styling.
- `?demo=1` seed regenerates with v2 frames telling the bearing-cascade story (UI work
  must be possible with no backend, as in v1).
