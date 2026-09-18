# Plant → Hera Signal Catalog

Everything `hera-virtual-plant` is capable of publishing outbound (Plant → Hera: TLM
telemetry + EVT activity/event). This is a documentation-only deliverable — no test
code, and no judgement on whether the simulator's own values/logic are "correct".
CMD topics (Hera → Plant) are explicitly **out of scope** — those already have test
tooling in `UAT Testing/Plant Communication - testing/`.

**Source repo:** `C:\Users\LucaParraGabarron\Desktop\ViryaH2\hera-virtual-plant`
(read 2026-09-17). Two independent apps in this repo both publish plant signals, plus
one standalone script — see §0 before using this catalog to build tests.

---

## 0. Important: there are THREE independent publishers, not one

| Publisher | Entry point | Status | Broker cfg |
|---|---|---|---|
| **Legacy Streamlit dashboard** | `app.py` (+ `mqtt_client.py` receive, `mqtt_publisher.py` send) | **The one testers actually run today** per project context | `config.py` (DEV/TEST) |
| **New FastAPI + React app** | `backend/virtual_plant/main.py` → `mqtt_bridge.py` (+ `plant_sim.py`, `truck_sim.py`, `scenario.py`) | Newer, more automated (ticking plant model, scriptable scenario API), not confirmed as the tester's daily driver | same `config.py` (DEV/TEST), one active env per running process |
| **`random_plc_publisher.py`** | standalone CLI script at repo root | Independent of both apps above; own env-var driven broker profile (DEV/TST/PRE_PROD, **different creds/hosts than `config.py`'s DEV/TEST**) | hardcoded `BROKER_PROFILES` dict in the script itself |

**Why this matters for test design:**
- The Streamlit app and the FastAPI backend both publish to the **same topic strings**
  for `DI{n}/TLM/fill_rate_meas` and (Streamlit's Send tab) `INJ/TLM/flow_meas` — if
  both are pointed at the same broker/env at once, they will race and overwrite each
  other's retained values. A test harness must know which one (if either, or neither —
  see `random_plc_publisher.py`) is the system under test for a given run.
- Same topic, different **retain** semantics depending on which tool published last:
  the FastAPI backend's automatic tick and the Streamlit "Send Profiles" tab both
  publish `fill_rate_meas`/`flow_meas`/`operating_mode` with `retain=True`, but the
  Streamlit "Truck Scenario" tab and the FastAPI backend's truck/ACS events publish
  the *same* `operating_mode`/`RFID_trailer_details`/`fill_rate_meas` topics with
  `retain=False`. See the Retain column per row below — verified from code, not
  assumed consistent.
- `random_plc_publisher.py`'s own doc comments (`RANDOM_PLC_PUBLISHER.md`, and inline
  comments in the script) reference `src/plant_sim_ui/ui/pid_state.py`,
  `scripts/random_plc_publisher.py`, `config/plc_config.yaml` — **none of these paths
  exist in this repo** (verified: no `src/`, `scripts/`, or `config/` directories at
  the repo root). This strongly suggests the script/doc were copied from a different,
  external plant-PLC codebase and only partially adapted. Its claims about "drives the
  UI gauge" almost certainly describe *that* other system, not `hera-virtual-plant`'s
  own Streamlit or React frontends — neither of which contains any code reading
  `RE1`–`RE4` `active_power`. Treat §5 accordingly: real topics that *can* be published
  to the broker, but not integrated with anything else in this repo.
- The `.md` doc for that script also disagrees with the script's own code on the topic
  string itself (`rectifierN_active_power` in the doc vs. `active_power` in
  `SPEC` in the `.py`) — the **`.py` is ground truth**, used below.

All three publishers share the same JSON envelope shape (see §1), with one confirmed
inconsistency in the timestamp field's format (flagged in §4).

---

## 1. Common payload envelope

Every signal below (all three publishers) uses the same JSON envelope, built by
`build_envelope()` / `build_envelope_bytes()` (three near-identical copies exist —
`backend/virtual_plant/envelope.py`, `mqtt_publisher.py`, `random_plc_publisher.py`):

```json
{"ts": "<ISO-8601 UTC timestamp>", "value": "<value>", "type": "<type tag>", "unit": "<unit>"}
```

- **`value` is (almost) always a JSON string**, even for numbers — e.g. a float `42.5`
  is serialized as `"42.5"` (trailing zeros stripped, so `"42.5"` not `"42.500000"`).
  Nested objects/arrays (RFID trailer details, ACS event data) are `json.dumps`'d and
  then embedded as *that string* inside `value`, i.e. double-encoded JSON — a consumer
  must `json.loads(payload)["value"]` and then `json.loads(...)` again for object
  payloads. Exception: `random_plc_publisher.py`'s envelope keeps `value` as a
  **native JSON number**, not a string (see its own `build_envelope`, distinct from
  the other two) — a real, code-confirmed shape difference between that script and the
  two main apps.
- `type` is a free-text tag, not a JSON type: seen values are `"float"`, `"str"`,
  `"trailer_object"`, `"acs_event"`.
- `unit` is `"none"` when not applicable (e.g. `operating_mode`, ACS events).
- Topic version suffix `0.8.3` (`MQTT_VERSION` in `backend/virtual_plant/constants.py`)
  is constant across every topic in both current apps.

---

## 2. Automatic signals — FastAPI backend (`backend/virtual_plant/mqtt_bridge.py`)

These fire on their own once the backend is running and MQTT-connected — no tester
action needed beyond starting the app and (for truck events) loading a `db_schedules`
CMD (via real Hera dispatch, or the scenario-injection API in §3).

| Topic | Category | Represents | Payload | Cadence | Retain |
|---|---|---|---|---|---|
| `HOF/PLC/OKS/TLM/flow_rate_mass/0.8.3` | TLM | Gas-network injection flow = H₂ produced − Σ dispenser draw | `{value: <kg/h float-string>, type:"float", unit:"kg/h"}` | Every tick, **30 s** (`TICK_S`); also once immediately on connect/idle (`publish_idle_telemetry`) | **True** (`_publish`, qos 0) |
| `HOF/PLC/OKS/TLM/oks_stat_blending_line/0.8.3` | TLM | Blending-line availability flag | `{value:"1"\|"0", type:"str", unit:"none"}` — value is hardcoded constant `BLENDING_STATUS_VALUE = 1`, never varies in this codebase | Same tick as above, 30 s | **True** |
| `HOF/PLC/DI{1-4}/TLM/fill_rate_meas/0.8.3` | TLM | Actual per-bay dispenser flow | `{value: <g/s float-string>, type:"float", unit:"g/s"}` | Every tick, 30 s; **all 4 bays published every tick regardless of whether they're active**, specifically to overwrite stale retained values from older runs | **True** |
| `HOF/PLC/DI{1-4}/TLM/operating_mode/0.8.3` | TLM | Bay state machine (see §6 for full 0–12 meaning) | `{value:"<mode 0-12 as string>", type:"str", unit:"none"}` | **Not on a timer.** Fires automatically only when a `db_schedules` CMD slot's start time arrives (`TruckSimulator._loop`, polls every 1 s). Sequence published per visit: modes **1, 2, 3** (drop-off) then, later, **11, 12, 0** (pickup) — this automatic path does **not** publish modes 4/5/6/8/9/10 (Ready→Wait-pickup); those exist only in the Streamlit manual scenario tool (§4) | **False** (`_publish_truck`, qos 1) |
| `HOF/PLC/DI{1-4}/TLM/RFID_trailer_details/0.8.3` | TLM | Trailer identity + tank technical spec dropped/picked at a bay | `{value:"<json-string of {trailer_id, license_plate, megc_type, vol, nominal_press, minimal_initial_press, number_of_sections, number_of_filling_flex, number_of_main_valves, global_maximum_flow_ac, maximum_flow_acceptable, maximum_delta_of_press}>", type:"trailer_object", unit:"none"}` | Fires once per drop and once per pick, as part of the same schedule-triggered visit above | **False** |
| `HOF/ACS/ACS/EVT/activity/0.8.3` | EVT | Truck gate movement (entry/exit), `mvt_requested` → `mvt_completed` pairs sharing a `correlation_id` | `{value:"<json-string of {event:'mvt_requested'|'mvt_completed', data:{...}, correlation_id}>", type:"acs_event", unit:"none"}`. `mvt_requested` data = `{gate:"ENT"|"EXT"}` only. `mvt_completed` data = `{gate, driver_id, truck_id, trailer_id, pick_bay_id, drop_bay_id}` | Fires automatically: entry pair when a slot with a drop bay starts; exit pair after the pickup sequence completes (drop-only slots do **not** fire an exit — that's deferred to the later slot that picks the trailer up) | **False** |
| `HOF/ACS/ACS/TLM/heartbeat/0.8.3` | TLM | ACS subsystem liveness counter | `{value:"<incrementing integer as string>", type:"str", unit:"none"}` | Every **10 s** (`HB_INTERVAL_S`) while MQTT connected, independent of the plant tick | **False** (`_publish_heartbeat`, qos 1) |
| `HOF/PLC/PLC/TLM/heartbeat/0.8.3` | TLM | PLC subsystem liveness counter | Same shape as ACS heartbeat, independent counter | Every 10 s, same loop as ACS heartbeat | **False** |

Truck/ACS event ordering and step timing are driven by `TruckSimulator._build_steps()` /
`_run_visit()` — `step_delay_s` (default 1.5 s) between each published message within a
visit. `TruckSimulator.dropoff_handshake_s()` / `pickup_handshake_s()` give the exact
wall-clock span of each phase and are also used by `scenario.py` to time CMD-profile
injection around the truck events (see §3).

**Caveat on `db_schedules` bay/offtaker resolution:** `truck_sim.py`'s `_extract_bays`
and `_extract_offtaker` accept a wide range of possible key names from the inbound
`db_schedules` CMD entry (`drop_bay_id`, `drop_bay`, `entry_bay_id`, `loading_bay_id`,
etc., and a fallback heuristic scanning all keys for "pick"/"drop"-like substrings).
Exactly which key real Hera actually sends is **not verified from this repo alone** —
this is defensive/best-effort parsing, not a documented contract; a test that depends
on a specific inbound key name should confirm it against a real Hera dispatch capture,
not assume it from this parsing code.

---

## 3. Semi-automatic — FastAPI scenario injection API (`/api/scenario/run`, `/api/scenario/preview`)

Not a UI tab (no React frontend code was inspected in this pass — only the backend
API in `main.py`/`scenario.py`); this is a REST endpoint that synthesizes a
`db_schedules` + `fill_rate_profile` + `rectifiers_global_setpoints` CMD sequence and
feeds it through the **same handler** as a real inbound Hera CMD (`bridge._handle_message`).
It does not publish new signal *shapes* — it's a shortcut that ends up triggering
exactly the automatic §2 signals (drop → fill → pick), timed so the fill/power ramps
start only after the drop handshake completes (`SETTLE_AFTER_DROPOFF_S = 2.0` s
buffer). Useful as a "one-click realistic visit" trigger for a test harness instead of
hand-building a `db_schedules` payload — but everything it ultimately publishes is
already covered in §2.

Also exposed for manual/direct signal injection without a full scenario (each is its
own REST endpoint, each one-shot, each `retain=False`, qos 1, same envelope shapes as
§2):
- `POST /api/manual/operating-mode` `{bay, mode}` → publishes one `DI{bay}/TLM/operating_mode` directly, **any mode 0–99** (not validated against the real 0–12 range — a test could intentionally send an out-of-range mode this way)
- `POST /api/manual/rfid-trailer` `{bay, license_plate, trailer_id, tech?}` → one `DI{bay}/TLM/RFID_trailer_details`
- `POST /api/manual/acs-activity` `{event, gate, trailer_id, drop_bay, pick_bay, truck_id, driver_id, correlation_id?}` → one `ACS/EVT/activity` message (either half of the pair, your choice — useful for negative tests like "requested but never completed")

---

## 4. Manual only — legacy Streamlit dashboard (`app.py`)

This is the tool the project context says is **actually used for manual testing
today**. Two relevant sub-tabs under **📥 Plant Receiving**:

### 4a. 📤 Send Profiles tab

Sliders + Publish Once / Start Loop / Stop Loop (loop period 1–300 s, default 5 s).
Uses `mqtt_publisher.py`'s `MqttPublisher.publish_once()`.

| Topic | What the slider represents | Range | Retain |
|---|---|---|---|
| `HOF/PLC/INJ/TLM/flow_meas/0.8.3` | Injection flow measurement | 0–140 g/s | **True** (qos 0) |
| `HOF/PLC/DI{1-4}/TLM/fill_rate_meas/0.8.3` | Per-bay dispenser fill rate | 0–130 g/s each | **True** |

Note: the tab's own docstring (`app.py:1874-1886`) additionally claims a
"⚡ Rectifiers — RE1–RE4 active power [MW]" section, but **no such sliders exist in
the actual rendered code** (`render_send_tab`, lines ~1929–2019 only build a single
"Rectifier Setpoint" `power_sp` slider — which is a **CMD** topic, out of scope here
— followed by Dispensers and Injection). Flagging this as a stale/inaccurate
docstring rather than a real signal; verified by reading the full tab body, not
guessed.

A "🗑 Clear Retained" button elsewhere in the app (Broker Live Feed tab, ~line 2998)
calls `MqttPublisher.clear_retained()`, which publishes an **empty payload with
`retain=True`** (the MQTT-spec way to delete a retained message) to a fixed list of 9
topics (`RETAINED_TLM_TOPICS`): `RE1`–`RE4 active_power`, `INJ flow_meas`,
`DI1`–`DI4 fill_rate_meas`. This is a housekeeping action, not a plant signal — noting
it because a test harness doing repeated runs may need it to avoid stale retained
values from a previous run leaking into a fresh test (especially for `RE1`–`RE4`,
which nothing in this repo's two apps ever actively publishes to *refresh*, only to
*clear* — see §5).

### 4b. 🚛 Truck Scenario tab

The **only tool in this repo that can manually drive a bay through the full 0–12
operating-mode range**, including the "Bay Filling" phase (modes 4/5/6/8/9/10) that
the FastAPI backend's automatic truck sim never touches. Builds an ordered step list
(`build_scenario_steps`), each step a `(topic, payload)` sent individually via
**▶ Send**, or in bulk via **▶▶ Send All** / **⏱ Send All with delay** (configurable
0–30 s, default 1 s, between steps). All publishes go through
`MqttPublisher.publish_raw()` → **qos 1, retain=False**, always, regardless of topic —
this differs from the Send Profiles tab's `retain=True` for the *same*
`fill_rate_meas`/`RFID_trailer_details` topics.

Four selectable scenarios (exact UI labels):

| Scenario (UI label) | Steps published, in order |
|---|---|
| **🚛 Drop-Only Visit** (`entry → drop-off → exit`) | ACS `mvt_requested`(ENT) → ACS `mvt_completed`(ENT) → mode 1 → mode 2 → mode 3 → RFID → ACS `mvt_requested`(EXT) → ACS `mvt_completed`(EXT) |
| **⛽ Bay Filling Sequence** (`modes 4 → 10 + flowmeter`) | mode 4 → mode 5 → mode 6 → *N* `fill_rate_meas` readings (tester-entered, comma-separated g/s values, default `42.5, 43.8, 41.2`) → mode 8 → mode 9 → mode 10 |
| **🔄 Drop + Pick Visit** (`entry → drop-off → pickup → exit`) | entry pair → drop-off (modes 1,2,3 + RFID) → pickup (mode 11 → RFID → mode 12 → mode 0) → exit pair |
| **🗺 Full Happy Path** (`Truck 1 drop-only + Bay Fill + Truck 2 drop+pick`) | Truck 1 entry+drop+exit (its own bay) → Bay Filling on that same bay → Truck 2 entry+drop (different bay) + **pickup from Truck 1's bay** + exit |

Every step's exact topic/payload/"HERA effect" description is visible in the UI table
before sending (`hera_effect` strings in `_entry_steps`/`_dropoff_steps`/etc. are
tester-facing documentation text, not something published over MQTT).

Bay/trailer/offtaker inputs are free-form tester fields (bay 1–4 dropdown, license
plate text input, default plates `1TRL285`/`1TRL378`) — this tab is the natural
scriptable source for a test harness needing precise, full-range, on-demand
operating-mode sequences (e.g. to attempt reproducing the "Rule K" timing bug noted in
memory, which needs mode-11 fired before a *different* truck's entry+drop-off finish
processing — achievable by interleaving two scenario configs' individual step sends).

---

## 5. Standalone — `random_plc_publisher.py`

A CLI script, run separately (`python random_plc_publisher.py`), **not wired into
either app's UI**. Own broker profile selection via `MQTT_PROFILE=DEV|TST|PRE_PROD`
env var (hardcoded creds in the script, different from `config.py`'s DEV/TEST). Emits
independent uniform-random values for **9 fixed topics**, one publish round per tick
(default every 5 s, `PERIOD_S` env var), **no mass-balance or correlation between
them** — e.g. dispenser flow is not constrained to be ≤ produced H₂, by design (per
its own doc, "independent random by design; not mass-balanced").

| Topic | Unit | Default range | Env override |
|---|---|---|---|
| `HOF/PLC/RE1/TLM/active_power/0.8.3` | MW | 0–5.825 | `RE_MIN`/`RE_MAX` (shared across RE1-4) |
| `HOF/PLC/RE2/TLM/active_power/0.8.3` | MW | 0–5.825 | same |
| `HOF/PLC/RE3/TLM/active_power/0.8.3` | MW | 0–5.825 | same |
| `HOF/PLC/RE4/TLM/active_power/0.8.3` | MW | 0–5.825 | same |
| `HOF/PLC/INJ/TLM/flow_meas/0.8.3` | g/s | 0–140 | `INJ_MIN`/`INJ_MAX` |
| `HOF/PLC/DI1/TLM/fill_rate_meas/0.8.3` | g/s | 0–130 | `DI_MIN`/`DI_MAX` (shared across DI1-4) |
| `HOF/PLC/DI2/TLM/fill_rate_meas/0.8.3` | g/s | 0–130 | same |
| `HOF/PLC/DI3/TLM/fill_rate_meas/0.8.3` | g/s | 0–130 | same |
| `HOF/PLC/DI4/TLM/fill_rate_meas/0.8.3` | g/s | 0–130 | same |

Payload: `{ts, value: <native JSON number, NOT a string>, type:"float", unit}` — the
one place this repo's envelope shape actually diverges (see §1). Retain = **True**,
qos = 0, for all 9, every tick, no exceptions.

As flagged in §0, this script's own documentation describes a downstream UI/consumer
that doesn't exist in this repo — verify with the team whether it's still relevant to
`hera-virtual-plant`-based UAT at all, or a leftover from a different project, before
building test tooling around it. `RE1`–`RE4 active_power` topics are **only ever
published by this script** anywhere in the repo — no other code path (Streamlit,
FastAPI backend) publishes them, only references them for the "Clear Retained"
housekeeping action (§4a).

---

## 6. Reference: bay operating-mode state machine (0–12)

Confirmed from `app.py`'s `_OPERATING_MODE_LABELS` (also matches
`hera-virtual-plant/hera_mqtt_test_scenario.txt`'s older/legacy scenario capture,
which uses slightly different topic spelling — `HOF/PLC/DI1/TLM/operating mode`
without version suffix, and `HOF H2 PLANT/ACS/ACS/EVT/activity/V0.5.3` — evidence this
state sequence predates the current `0.8.3` topic scheme but is unchanged in meaning):

| Mode | Label (hera-virtual-plant) | Real Hera `Movements.EventName` | Published automatically (FastAPI, §2)? | Published via manual Truck Scenario (§4b)? |
|---|---|---|---|---|
| 0 | Reset / Pickup closed | `IDLE` | ✅ (end of pickup) | ✅ |
| 1 | Drop-off start | `WAIT_DROP` | ✅ | ✅ |
| 2 | Vehicle approach | `VEH_APPROACH` | ✅ | ✅ |
| 3 | Drop-off closed | `CONNECTED` | ✅ | ✅ |
| 4 | Ready | `READY` | ❌ | ✅ (Bay Filling scenario) |
| 5 | Authorized | `AUTHORIZED` | ❌ | ✅ |
| 6 | Filling | `FILLING` | ❌ | ✅ |
| 8 | Fill done | `FILL_DONE` | ❌ | ✅ |
| 9 | Wait disconnect | `WAIT_DISCON` | ❌ | ✅ |
| 10 | Wait pickup | `WAIT_PICK` | ❌ | ✅ |
| 11 | Pickup created | `DISCONNECTED` | ✅ | ✅ |
| 12 | Vehicle leaving | `VEH_LEAVING` | ✅ | ✅ |

**The `Movements.EventName` column is now fully confirmed** (2026-09-17), live,
by playing the complete 0–12 lifecycle against `heraplantdatabasetest` via the
standalone simulator + test suite in
`UAT Testing/Plant Communication - Signal Testing/` and reading back every
resulting row for that bay in send order — see `test_signal_reaction.py`'s
SIG_REACT_02 (the operating_mode deep-dive). Also confirmed: `Movements.EventName`
is Hera's own internal
naming, unrelated wording-wise to hera-virtual-plant's own labels (e.g. mode 11
"Pickup created" → `DISCONNECTED`, not anything pickup-sounding) — don't assume one
predicts the other.

(Mode 7 does not appear anywhere in either code path — not a typo on my part, it's
simply absent from `_OPERATING_MODE_LABELS` and every step builder.)

A separate, correlation-id-linked ACS `mvt_requested`/`mvt_completed` event pair marks
gate **entry** and **exit** — not 1:1 with a single bay's mode sequence (a truck can
enter, drop at one bay, and a *different* truck's exit later picks up from that bay).

---

## 7. Cross-check against real Hera (`heraplantdatabasetest` / `heraplantdatabasedev`)

**2026-09-17 addendum.** The catalog above documents what `hera-virtual-plant` is
*capable* of publishing. To check whether that's actually *sufficient* — i.e. whether
"every signal that matters" is covered — I queried `dbo.PlantSignals` (a generic raw
log of every inbound/outbound plant message: `Asset`, `Tag`, `Topic`, `Value`, `Unit`,
`DataType`, `Direction`) and `dbo.Movements` directly in both databases, read-only,
via the existing credentialed harness at
`UAT Testing/Plant Communication - testing/db_common.py`. **Answer: no — real Hera's
signal contract is substantially richer than what the simulator can produce.**

### `operating_mode` is captured — confirmed, two places
- `PlantSignals`: raw numeric value logged verbatim (`Tag='operating_mode'`,
  `DataType='Int'`, every value 0–12 present in both DBs).
- `Movements.EventName`: the **same** transitions logged a second time, decoded into
  state labels — `IDLE`, `WAIT_DROP`, `VEH_APPROACH`, `CONNECTED`, `DISCONNECTED`,
  `VEH_LEAVING` — alongside the `mvt_requested`/`mvt_completed` ACS pair, using
  `DropOffLoadingBayName`/`PickupLoadingBayName` instead of the driver/truck/trailer
  columns for these rows. Note these label names don't match `hera-virtual-plant`'s
  own `_OPERATING_MODE_LABELS` wording (e.g. no "Drop-off start"/"Ready"/"Authorized"/
  "Filling"/"Fill done"/"Wait pickup"/"Pickup created" labels appeared) — Hera's own
  internal mapping from mode number to label differs from the simulator's, and/or some
  of these labels (`CONNECTED`/`DISCONNECTED`) come from the separate `connected`/
  `disconnected` topics below, not from `operating_mode` itself. Not fully
  disambiguated from `PlantSignals` alone.

  **2026-09-17, resolved by a live test:** sent a `drop_only` scenario (ACS
  entry, `operating_mode` 1→2→3, RFID, ACS exit) to `heraplantdatabasetest`'s
  broker via the standalone simulator in
  `UAT Testing/Plant Communication - Signal Testing/` and read back the
  resulting `Movements` rows by timestamp order. Confirmed mapping:
  `operating_mode=1` (Drop-off start) → `WAIT_DROP`; `operating_mode=2`
  (Vehicle approach) → `VEH_APPROACH`; `operating_mode=3` (Drop-off closed) →
  `CONNECTED`. Pickup-side modes (11/12/0 → `IDLE`/`VEH_LEAVING`/presumably
  `DISCONNECTED`) were only ever observed passively in the original DB dump,
  not reproduced by a live send yet — same inference, not yet independently
  confirmed the same way.

### Real topics the simulator cannot produce at all

Distinct `Topic` values actually observed in `PlantSignals` (`Direction='PlantToSteering'`)
in **either** database that do not exist anywhere in `hera-virtual-plant`'s source
(confirmed against the full read of `app.py`, `mqtt_bridge.py`, `truck_sim.py`,
`mqtt_publisher.py`, `random_plc_publisher.py` in §2–§5 above):

| Topic pattern | Notes |
|---|---|
| `HOF/PLC/DI{n}/TLM/connected/0.8.3` | per-bay connectivity flag |
| `HOF/PLC/DI{n}/TLM/disconnected/0.8.3` | |
| `HOF/PLC/DI{n}/TLM/RFID_signal_strength/0.8.3` | |
| `HOF/PLC/DI{n}/TLM/RFID_trailer_licence_plate/0.8.3` | **distinct** from `RFID_trailer_details` — a separate, simpler signal the simulator never sends |
| `HOF/PLC/DI{n}/TLM/RFID_trailer_type/0.8.3` | |
| `HOF/PLC/DI1/TLM/RFID_system_failure/0.8.3` | seen once, dev only |
| `HOF/PLC/DI{n}/TLM/filling/0.8.3` | boolean/status, distinct from `fill_rate_meas` |
| `HOF/PLC/DI{n}/TLM/fill_requested/0.8.3` | |
| `HOF/PLC/DI{n}/TLM/fill_rate_trailer_max/0.8.3` | per-trailer ceiling, distinct from the measured rate |
| `HOF/PLC/DI{n}/TLM/fill_rate_calc_max/0.8.3` | |
| `HOF/PLC/DI{n}/TLM/main_setpoint_active/0.8.3` | |
| `HOF/PLC/DI{n}/TLM/system_in_ESD/0.8.3` | Emergency Shutdown status |
| `HOF/PLC/PWR/TLM/online/0.8.3` | a `PWR` asset — not `DI`/`OKS`/`ELY` — not modelled by the simulator at all |
| `HOF/PLC/HPC/TLM/pressure_max/0.8.3` | High-Pressure Compressor, dev only — matches the "compressor" asset named in `Logistic/DatabaseSchema/Documentation/Cartographie_Application.md`'s Asset block, another asset the simulator has no concept of |

That's roughly 14 real, actively-received topics with **zero** coverage from
`hera-virtual-plant` today. Anyone building automated tests purely against the
simulator's publishing capability (§2–§5) will have a structural blind spot here —
these can't be tested against the simulator as it stands; they'd need either the
simulator extended, or tests driven a different way (e.g. replaying captured
`PlantSignals` rows).

### Confirmed discrepancies on topics both sides *do* share

- **`flow_rate_mass` unit mismatch**: `hera-virtual-plant` publishes `unit:"kg/h"`
  (`backend/virtual_plant/mqtt_bridge.py`); real `PlantSignals` rows for the same
  topic carry `unit:"m3(n)/h"` (normal cubic metres/hour) in both DBs, every row
  checked. A real physical-unit difference, not cosmetic — a test comparing simulator
  output against a real-data expectation on this topic must convert or it will be
  silently wrong.
- **`operating_mode` envelope `type` field**: simulator sends `type:"str"` in the
  envelope; real `PlantSignals.DataType` for the same tag is `'Int'`. Consistent with
  the value itself being numeric either way, but the declared type tag differs.
- **`GridBlendingStatusSessions`**: 0 rows in `heraplantdatabasetest`, 1833 rows in
  `heraplantdatabasedev` — the blending-line feature is not exercised at all in the
  test environment right now, independent of the simulator question.

### Heartbeats don't match either

`hera-virtual-plant`'s FastAPI backend publishes `HOF/ACS/ACS/TLM/heartbeat/0.8.3` and
`HOF/PLC/PLC/TLM/heartbeat/0.8.3` every 10 s (§2). **Neither appears anywhere in
either database.** The only heartbeat-related topic found is
`HOF/STL/STL/TLM/heartbeat/0.8.3`, `Direction='SteeringToPlant'` — i.e. sent **by**
Hera itself, not received from the plant. So the simulator's plant→Hera heartbeat
mechanism doesn't correspond to anything Hera's own storage actually reflects; either
Hera doesn't persist a plant-side heartbeat it receives, or the real plant's heartbeat
contract uses topic names the simulator doesn't replicate. Not disambiguated further
in this pass.

### `RE1-4 active_power` confirmed absent

No genuine `PlantSignals` rows for `active_power`/`RE1`–`RE4` in either database — the
only matches were false positives from a case-insensitive substring hit on
`rectifiers_global_power` (a **CMD** topic, `Direction='SteeringToPlant'`, out of
scope). This confirms §5's conclusion from the code alone: nothing in this repo's two
main apps, nor real Hera, ever receives `RE1-4/TLM/active_power` — only
`random_plc_publisher.py` can produce it, and nothing downstream consumes it.

---

## 8. Reaction verification: does every signal actually reach Hera's database?

**2026-09-17, second addendum.** Once the standalone simulator existed (§0 above
describes it as a separate tool now, in
`UAT Testing/Plant Communication - Signal Testing/`), the natural next question
was whether Hera actually *ingests* every one of these ~20 signals, not just whether
the topic name is plausible. Built a dedicated test suite for this — same folder —
see its own README for the full design. Headline result: **19 of the 20 catalogued signals are
confirmed reaching `heraplantdatabasetest`; one (`oks_stat_blending_line`) has never
been observed there at all**, consistently across three independent checks
(two full test runs plus a dedicated unbounded lookback query), which lines up with
`GridBlendingStatusSessions` also being empty in that same database (§7) — most likely
an environment-specific gate on the blending feature in TEST, not a signal-ingestion
bug. `heraplantdatabasedev`, by contrast, has 1833 real `GridBlendingStatusSessions`
rows, so this is worth confirming with the team rather than assuming TEST is simply
broken.

Two test-harness pitfalls surfaced and got fixed along the way, both worth knowing if
extending this suite:
- **Polling a remote DB in a tight loop is noisy.** An early version re-queried the DB
  every 0.5–1s per signal until a timeout; Azure SQL round-trip latency under that
  load varied enough (individual queries occasionally took several seconds) that a
  *different*, wrong-looking set of "misses" appeared on every run, even though every
  signal had actually landed (confirmed by querying each one's latest row directly).
  Fixed by sending everything first, waiting once, then running a single batch query.
- **Clock skew breaks tight per-signal time comparisons.** Comparing each signal's own
  precise send timestamp against Hera's `Received` column looked broken for
  rapid-fire sends a fraction of a second apart — root cause was a small clock offset
  between the test machine and Hera's ingestion server flipping a strict
  `Received >= sent_at` comparison. Fixed by matching against the whole batch window
  (with a skew buffer) instead of an exact per-signal timestamp, since every signal in
  that pass used a distinct `Tag` anyway (no ordering needed).

The `operating_mode` → `Movements.EventName` mapping in §6 was fully confirmed as part
of this same test suite (a dedicated deep-dive playing the complete 0–12 lifecycle).

**2026-09-17, third addendum — turned into a real automated test.**
`UAT Testing/Plant Communication - Signal Testing/test_signal_reaction.py` is
now the script to run for this check, following the same verdict flow as every other
`test_*.py` in this project (a human sets the final PASS/FAIL via
`uat_excel_reporter.ask_verdict()` — the auto-detected result is shown only as an
observation, `AUTO_VALIDATION_ENABLED` is `False` project-wide, same as everywhere
else). Four scenarios: SIG_REACT_01 = every catalog signal reaches its expected row;
SIG_REACT_02 = the full operating_mode 0-12 lifecycle produces the *exact* expected
`Movements.EventName` per mode (against the table in §6, not just "a row appeared");
SIG_REACT_03 = an ACS entry pair's `Movements` row *columns* (DriverId/TruckId/
TrailerId/DropOffLoadingBayName/AcsCorrelationId) match exactly what was sent, not
just that a row exists; SIG_REACT_04 = signal *values* are correct, not just present
(`fill_rate_meas`'s number, `flow_rate_mass`'s value **and unit** — a regression guard
for the kg/h-vs-m3(n)/h fix above — and `RFID_trailer_details`'s own fields). Latest
clean auto-detected run: 18/18 signals + 12/12 modes + 5/5 ACS fields + 6/6 values.

Two real findings surfaced while stabilizing this, both worth remembering for any
future script that sends many MQTT messages to this broker in quick succession:
- **Bursting ~19 signals within a couple of seconds reliably produced a "cutoff"
  pattern in send order** — not a per-signal problem, but Hera's ingestion building up
  a backlog under a burst (every "missing" signal was confirmed to land eventually via
  a direct unbounded query). Fixed by pacing sends 1.5s apart.
- **`fill_rate_meas` and `RFID_trailer_details` specifically can take 30+ seconds to
  land** (both also update `LoadingSessions`, not just the raw log) — a fixed, shorter
  wait produces a false FAIL that has nothing to do with Hera being wrong. Hit this for
  real once SIG_REACT_04 didn't yet have the fix. Fixed with a shared
  `_settle_and_retry()` helper (short check, then one longer retry for stragglers) —
  use it, not a flat `time.sleep()`, for any new check involving these two signal types.

**Next steps, deliberately not done in this pass** (see the test folder's own README
for the same list): widen the "what happens on a skipped step / duplicate signal"
edge-case scenarios beyond the 3+3 curated there now; check Hera's own UI/API against
what's already confirmed landing correctly in the DB.

**2026-09-17, fourth addendum -- Phase 3 (UI-level) started, not yet run live.**
`UAT Testing/Plant Communication - Signal Testing/test_plant_ui_reaction.py` +
`ui_capture.py` check whether a signal sent by the fake-plant sender correctly shows
up on Hera's own Plant Supervision screen (`/plant-supervision/plant-state`, "MQTT
Stream" tab), which is close to a 1:1 live view of this catalog. Built from the
tester's own description/screenshots of the screen, not from reading its real
markup -- selectors will need correcting on the first live run (see that folder's
README for the full "confirmed vs. still guessed" breakdown). Two things already
folded back into the catalog from screenshots: `RFID_signal_strength`'s real unit is
`dBm` (was guessed "none"), and the Unit column shows an odd literal `"0"`
(`main_setpoint_active`/`online`/`system_in_ESD`) or `"integer"`
(`oks_stat_blending_line`) that looks like a UI display quirk rather than a real
unit -- flagged in `signal_catalog.py`, not adopted as fact. A DevTools sighting
(`[SignalR] New data for 'OnPlantPassedSignal'`, plus a `JSON.parse` `SyntaxError`
whose bad token looks like `RFID_trailer_type`'s bare string value `"type1"`) is
what this script's PLANT_UI_04 scenario is built to confirm or deny with captured
evidence, not just note.

**2026-09-17, fifth addendum -- Phase 3's first live run, bug confirmed.** Ran
`test_plant_ui_reaction.py` for real against `herawebtest`. All 4 scenarios executed:
`fill_rate_meas` and an `operating_mode` 1→2→3 sequence both showed up correctly on
the MQTT Stream (Raw) panel within about a second of sending, and PLANT_UI_04
**confirmed the suspected frontend bug -- but as a non-fatal, caught exception**, not
a broken display: sending `RFID_trailer_type="type1"` throws
`SyntaxError: Unexpected token, "type1" is not valid JSON` in the console, yet the
value still renders correctly on the Raw panel (confirmed both visually and by the
script's own DOM check, added right after this nuance surfaced). Stack trace shows a
shared `r.getValue()` value-formatter calling `JSON.parse()` unconditionally on the
raw MQTT value field with no upfront check for whether it's actually JSON, throwing
and falling back to the raw string on any bare-string value. Likely affects every
other bare-string (`value_type="str"`) signal in this catalog, not just this one tag
-- see `signal_catalog.py`'s `rfid_trailer_type` entry and the Signal Testing folder's
README for the full list and detail. Also, while
building this: the real DOM behind the MQTT Stream panels turned out to be an
Angular `div.message-group`/`div.grid-table` structure, not a `<table>` or ARIA grid
as first guessed -- fixed in `ui_capture.py` once the tester shared a DevTools
capture of the real markup.

---

## 9. What I could not fully verify

- The exact key names real Hera sends inside a `db_schedules` CMD entry (bay ids,
  offtaker id, trailer id) — the simulator's parsing (`truck_sim.py::_extract_bays`,
  `_extract_offtaker`, `_extract_trailer`) accepts many possible key spellings
  defensively; I can't confirm from this repo alone which one(s) real Hera actually
  sends on the wire.
- Whether the React frontend (`frontend/src/`) exposes its own UI for the FastAPI
  scenario/manual endpoints in §2/§3, or whether they're currently API-only/unused —
  I read the backend routes and payload builders but did not read the React frontend
  source in this pass, so I can't describe the exact tester-facing button/flow for
  those endpoints the way I could for the Streamlit tabs. If a test needs to trigger
  them the way a human tester would, confirm the frontend flow before assuming the
  raw REST call is what testers actually use.
- Whether `random_plc_publisher.py` is still in active use for anything related to
  this project, given its documentation references a codebase/UI not present here
  (§0, §5) — flagging for the team rather than guessing.
- Real-world payload values are simulator-generated (Gaussian noise, uniform random,
  or tester-typed) — I have not run any of these publishers to capture a live message;
  every payload shape above is read directly from the `build_envelope*()` call sites,
  not observed on the wire.
