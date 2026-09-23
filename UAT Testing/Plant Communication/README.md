# Plant Communication

Every automated check involving Hera's plant-side MQTT communication, in
one folder (consolidated 2026-09-23 from three separate "Plant
Communication - *" folders -- **Signal Testing**, **testing**, and
**sending** -- into this single one, per explicit direction to make the
UAT suite's layout clear and consistent with the rest of it, e.g. Weekly
Nomination's single flat folder holding both its import and UI scripts).

Two genuinely different concerns live here, both about Hera's plant-side
screens but asking opposite questions:

- **Part A -- Dispatch checks** (`test_dispatch_vs_hera_profiles.py`,
  `test_bay_occupancy_vs_hera.py`): did Hera's own nightly dispatch job run,
  and does what it dispatched match what Hera's Nominations/Profiles and
  Bay Occupancy screens actually show? This never touches the MQTT broker
  at all -- it's a DB (dispatched tables) vs. UI comparison.
- **Part B -- Signal-to-screen checks** (everything else, Phases 1-5): WE
  send realistic plant signals over the broker ourselves (no dependency on
  `hera-virtual-plant` -- that simulator won't exist long-term for this
  testing effort, only the broker will) and confirm Hera reacts correctly
  at every layer: does it land in Hera's own database (Phase 1-2), show up
  on the MQTT Stream screen (Phase 3), the Bay Monitoring Actual chart
  (Phase 4), and the Bay Occupancy Schedule (Phase 5)?

There's also `dashboard/` -- a manual Streamlit monitoring tool (not a
test), see its own section at the bottom.

Every `test_*.py` script here follows this project's shared convention
(numbered scenarios, `uat_excel_reporter.ask_verdict()`, a final summary,
a `record_results()` write into the shared Excel report) and is now wired
into `run_all_tests.py`'s suite, in this order: Part A's 2 scripts, then
Part B's `test_signal_reaction.py`, the 2 edge-case scripts,
`test_plant_ui_reaction.py`, `test_bay_monitoring_actual_vs_broker.py`, and
`test_bay_occupancy_happy_path.py` last (the two longest-running ones,
~30-40 min each, are last on purpose).

## Part A: Dispatch checks

Does Hera's own nightly dispatch Azure Function actually run, and does
what it dispatched match Hera's own screens? Two independent comparisons,
same pattern each time: confirm the function ran (Application Insights),
pull the relevant dispatched DB rows, capture the matching Hera screen via
its own API response, compare.

- **`test_dispatch_vs_hera_profiles.py`** (DISP_CMP_01-03) -- Nominations
  > Profiles screen (`ProfilesScreen/GetProfilesScreenAsync`) vs.
  dispatched profile `TimeSerie` rows, per bay, in g/s.
- **`test_bay_occupancy_vs_hera.py`** (OCC_CMP_01-03) -- Bay Occupancy
  screen (`BayOccupancyScreen/GetBayOccupancyScreenAsync` -- the DISPATCH
  side's own occupancy view, with types `"DropOff"`/`"Filling"`/`"PickUp"`;
  a DIFFERENT screen and vocabulary than Part B's Bay Monitoring tab, see
  Phase 5 below -- don't conflate the two) vs. dispatched
  `DispatchedBayOccupationSessions` rows.

Shared helpers: `function_check.py` (the Application Insights "did the
function run" check, reusable version of the standalone
`check_dispatch_function.py`, TC-DISP-001/002), `db_common.py` (SQL Server
connection, `.env`-based credentials), `asset_lookup.py` (bay-name
lookups), `check_dispatched_profiles.py` / `check_dispatched_rectifier_power.py`
/ `check_bay_occupancy.py` (standalone DB-query CLIs, also imported by the
test scripts above). Auth: Azure AD interactive login (separate from
everything else in this folder) for the function check, SQL `.env` creds
for the DB queries, and the usual Hera/Playwright login for the screen
capture -- three distinct logins per script run.

## Part B: Signal-to-screen checks

Everything below this point sends signals ourselves and confirms Hera's
reaction, layered as five phases, each building on the last being
confirmed correct. Step 1 was sending realistic plant signals on demand
(the sender below). Step 2 confirmed Hera actually captures them correctly
in its own database (`test_signal_reaction.py`). Phases 3-5 check three
different downstream screens against the same kind of signals.

## Layout

```
--- Part A: dispatch checks ---
function_check.py                       -- reusable "did the dispatch function run" check
check_dispatch_function.py              -- same check, standalone CLI (TC-DISP-001/002)
db_common.py                            -- SQL Server connection/credentials (.env-based)
asset_lookup.py                         -- bay-name lookups
check_dispatched_profiles.py            -- dispatched profile TimeSerie rows (CLI + imported)
check_dispatched_rectifier_power.py     -- dispatched rectifier power rows (CLI)
check_bay_occupancy.py                  -- dispatched bay-occupancy sessions (CLI + imported)
test_dispatch_vs_hera_profiles.py       -- Part A test: dispatched profiles vs Hera's screen
test_bay_occupancy_vs_hera.py           -- Part A test: dispatched occupancy vs Hera's screen

--- Part B: signal-to-screen checks ---
config.py             -- broker credentials (DEV / TEST / TEST_BIS)
envelope.py            -- the {ts,value,type,unit} envelope builder
broker_client.py        -- thin publish-only MQTT wrapper (this IS the plant)
signal_catalog.py        -- registry of every known signal (topic, unit, evidence, ...)
steps.py                  -- confirmed-order truck-visit building blocks + scenarios
send_signal.py             -- CLI: send ONE signal
run_scenario.py              -- CLI: play a full truck-visit scenario
list_signals.py                -- CLI: print the catalog
requirements.txt

db_check.py                     -- DB query/verification helpers (no polling loops --
                                    see its module docstring for why; built on db_common.py
                                    above, same folder, no cross-folder path setup needed)
test_signal_reaction.py          -- Phase 1-2: THE automated DB-level test to run (see below)
test_edge_case_skipped_step.py    -- exploratory: what happens when a step is missing?
test_edge_case_duplicates.py       -- exploratory: what happens when a signal repeats?

ui_capture.py                        -- Phase 3: Playwright helpers for Hera's own
                                          Plant Supervision screen (login, tab/panel
                                          navigation, DOM grid reading, console +
                                          WebSocket capture)
test_plant_ui_reaction.py             -- Phase 3: THE UI-level test to run (see below) --
                                          run live 2026-09-17, confirmed working

sustain_signal.py                    -- Phase 4/5 sender: resend one constant signal at a
                                          fixed interval across multiple 15-min buckets
                                          (also importable -- see test_bay_monitoring_
                                          actual_vs_broker.py / test_bay_occupancy_happy_path.py)
bay_monitoring_capture.py             -- Phase 4/5: Playwright helpers for the Bay
                                          Monitoring tab (bay selector, week nav reused
                                          from Scheduled Transfers, response capture,
                                          Bay Occupancy checklist parsing)
test_bay_monitoring_actual_vs_broker.py -- Phase 4: THE test to run (see below) --
                                          run live once, 3 real fixes applied since
test_bay_occupancy_happy_path.py        -- Phase 5: THE test to run (see below) --
                                          run live twice, both PASS, mapping confirmed

--- dashboard/ (manual tool, not a test -- see its own section) ---
```

## Quick start

`requirements.txt` covers only the MQTT/broker side (`paho-mqtt`,
`certifi`) -- every `test_*.py` script in this folder additionally needs
`playwright` (`py -m playwright install chromium` too) and `openpyxl` for
the shared verdict/reporting convention, neither captured in a requirements
file yet. Part A additionally needs `pyodbc` (SQL Server) and
`azure-identity`/`azure-monitor-query` (the dispatch-function check).

```bash
py -m pip install -r requirements.txt

# see everything the sender can send
py list_signals.py

# send one signal
py send_signal.py operating_mode --bay 1 --value 3

# preview / actually run a full truck-visit scenario
py run_scenario.py full_visit --bay 3 --dry-run
py run_scenario.py full_visit --bay 3

# the real test: does Hera correctly capture what's sent?
py test_signal_reaction.py
```

### A note on env/bay prompts

Every `test_*.py` script in this folder (2026-09-23, converted from
argparse `--env`/`--bay` flags) prompts interactively for whatever it needs
-- broker env (DEV/TEST/TEST_BIS), Hera env (dev/test), loading bay (1-4)
-- the first time it needs it, same convention as Weekly Nomination /
Scheduled Transfers: a module-level `ENV = ""` (and `BAY = 0` where
relevant) constant near the top of the file. Leave it blank to get a
numbered menu at startup; hardcode e.g. `ENV = "test"` / `BAY = 1` in the
file to skip the prompt on every subsequent run. There is deliberately no
`--env`/`--bay` CLI flag anymore, for consistency with the rest of the UAT
suite (the sender CLIs -- `send_signal.py`, `run_scenario.py`,
`sustain_signal.py` -- are a different kind of tool and keep their own
`--env`/`--bay` flags, since they're one-shot utilities, not full
`test_*.py` scripts with a verdict flow).

## The test to run: `test_signal_reaction.py`

```
py test_signal_reaction.py
py test_signal_reaction.py --from 3
```

Prompts for the broker environment (DEV/TEST/TEST_BIS) at startup, same
convention every `test_*.py` script in this folder now uses (see "A note
on env/bay prompts" below) -- hardcode the file's own `ENV = ""` constant
to skip it on repeat runs.

Same terminal conventions and verdict flow as every other `test_*.py` in
this project (confirmed by reading `Master Data/test_master_data.py` and
`NetCommodityCost/test_ncc_costs_breakdown.py`): numbered scenario blocks,
an auto-detected result shown as an *observation* (not applied
automatically -- `AUTO_VALIDATION_ENABLED` is `False` project-wide), a
`>>> YOUR VERDICT (P/F/S/I)` prompt via `uat_excel_reporter.ask_verdict()`,
a final summary, and a `record_results()` write into the shared UAT Excel
report. The Steps section will report "no matching row" for these
scenario IDs since they aren't in `Hera_UAT_Test_Plan.xlsx` yet -- expected,
add rows there later if these should join the formal plan.

Four scenarios:

- **SIG_REACT_01** -- every catalog signal reaches its expected row
  (`PlantSignals.Tag=...`, or `Movements.EventName` for the ACS activity
  signal). `oks_stat_blending_line` is tracked but excluded from the
  auto-detected result -- a known, confirmed gap (catalog §8).
- **SIG_REACT_02** -- the full 0-12 `operating_mode` lifecycle produces the
  *exact* expected `Movements.EventName` per mode
  (`signal_catalog.OPERATING_MODE_EVENT_NAME`), not just "a row appeared".
- **SIG_REACT_03** -- a full ACS entry pair with known truck/driver/trailer/
  bay produces a `Movements` row whose *own columns* match what was sent.
- **SIG_REACT_04** -- signal *values* are captured correctly, not just
  presence: `fill_rate_meas`'s number, `flow_rate_mass`'s value **and
  unit** (a regression guard -- see below), and `RFID_trailer_details`'
  own fields.

**Not yet covered, deliberately:** whether raw telemetry flows into Hera's
higher-level derived tables (`LoadingSessions`, `GridInjectionSessions`) --
the mechanism (likely needs a full `Transfers`-backed lifecycle, not
standalone messages) isn't understood well enough yet to assert reliably.

### A real DB-latency finding baked into how these scenarios wait

Bursting many signals within a couple of seconds reliably produced a
"cutoff" pattern in send order -- Hera's ingestion building up a backlog
under a burst, not a per-signal problem. Two mitigations, both load-bearing:
**pace sends** (`SEND_PACING_S` in SIG_REACT_01, ~1.5s apart -- also just
more realistic plant behaviour) and **use `_settle_and_retry()`** for any
check involving `fill_rate_meas` or `RFID_trailer_details` specifically --
both have been observed taking 30+ seconds to land (they also update
`LoadingSessions`, not just the raw log). A real run against an earlier,
non-resilient version of SIG_REACT_04 hit exactly this as a false FAIL --
the data had landed, just a few seconds after the script's fixed wait had
already given up. If you add a new scenario that sends either of those two
signal types, use `_settle_and_retry()`, not a flat `time.sleep()`.

## The signal catalog and its evidence levels

`signal_catalog.py` merges two sources: a full read of `hera-virtual-plant`'s
own code (exact topics/envelopes/trigger sequences), and a direct read-only
cross-check against `heraplantdatabasetest`/`dev`'s `PlantSignals` table,
which turned up ~14 real signal types the simulator can't produce at all.
Every signal is tagged:

| Evidence | Meaning |
|---|---|
| `code` | Confirmed working shape from `hera-virtual-plant`'s own source |
| `db` | Topic/tag/unit confirmed from a real row; envelope shape inferred by analogy |
| `db-topic-only` | Topic confirmed present, no real sample value captured -- `example_value` is a documented guess |

Run `py list_signals.py` to see every signal with its evidence level. Full
narrative writeup: `PLANT_SIGNALS_CATALOG.md` at the repo root.

## Confirmed findings so far

- **Frontend bug, confirmed live, non-fatal (2026-09-17,
  `test_plant_ui_reaction.py` PLANT_UI_04):** sending
  `RFID_trailer_type="type1"` throws
  `SyntaxError: Unexpected token, "type1" is not valid JSON` in the
  browser console on the MQTT Stream screen -- **but the value still
  renders correctly** on the Raw panel (`Payload="type1"`, confirmed both
  visually and by the script's own DOM check), so the exception is being
  caught and the raw string used as a fallback, not actually breaking the
  display. Stack trace: a shared `r.getValue()` value-formatter
  (`chunk-FO6DEE4N.js`) calls `JSON.parse()` unconditionally on the raw
  MQTT value field, with no upfront check for whether it's actually JSON
  -- works cleanly for structured signals (`RFID_trailer_details`, ACS
  events) but throws-and-falls-back on any bare-string value. Real finding
  (unnecessary `JSON.parse` attempts + noisy `console.error` on a path
  clearly meant to also handle plain strings), just not a data-loss bug.
  Reasonable expectation (not yet independently re-tested per signal) is
  this affects every other `value_type="str"` signal in the catalog too:
  `connected`/`disconnected`/`filling`/`fill_requested`/
  `main_setpoint_active`/`system_in_ESD`/`pwr_online`/
  `rfid_trailer_licence_plate`/`rfid_system_failure`/
  `oks_stat_blending_line`. Worth flagging to the dev team as-is.
- **Full `operating_mode` (0-12) → `Movements.EventName` mapping**, not
  guessable from label wording (e.g. mode 11 "Pickup created" →
  `DISCONNECTED`) -- `signal_catalog.OPERATING_MODE_EVENT_NAME`.
- **`flow_rate_mass`'s real unit is `m3(n)/h`**, not the simulator's
  original `kg/h` -- SIG_REACT_04 guards against this regressing.
- **Hera does not validate sequence order** (a skipped intermediate
  `operating_mode` just never appears) **and does not require an ACS entry**
  before a drop-off sequence.
- **Hera does not deduplicate.** The same `operating_mode` sent twice
  creates two `Movements` rows; an ACS `mvt_completed` sent twice with the
  same `correlation_id` creates two rows sharing that id -- worth flagging
  to the team as a real risk (a plant retransmission would double-write).
- **19/20 catalogued signals confirmed reaching `heraplantdatabasetest`**;
  `oks_stat_blending_line` is the one exception (consistent with
  `GridBlendingStatusSessions` also being empty there -- `dev` has real
  data, so likely an environment-specific gate).

Full detail: `PLANT_SIGNALS_CATALOG.md` §6-§8.

## Exploratory scripts (not routine tests)

`test_edge_case_skipped_step.py` and `test_edge_case_duplicates.py` are
observation harnesses, not pass/fail tests -- there's no written spec for
what Hera *should* do when a step is missing or a signal repeats, so they
dump exactly what happened (Movements/PlantSignals rows in the affected
window) rather than asserting an expectation nobody's confirmed. Findings
so far are already folded into `PLANT_SIGNALS_CATALOG.md` §8 and this
README's "Confirmed findings" section above; re-run them to extend the
scenario coverage (currently 3 skipped-step + 3 duplicate scenarios,
deliberately curated, not exhaustive).

## Phase 3: does it show up on Hera's own screen? (`test_plant_ui_reaction.py`)

**Status: first full live run completed 2026-09-17, `--env test`.** All 4
scenarios executed against the real screen; PLANT_UI_01-03 confirmed
`fill_rate_meas`, the `operating_mode` 1->2->3 sequence, and general tab/
panel layout all show up correctly on the MQTT Stream (Raw) panel.
PLANT_UI_04 **confirmed the suspected frontend bug with real evidence** --
see "Confirmed findings" below. Everything DB-level above is solid;
this phase checks the next layer -- Hera's own Plant Supervision screen
(`/plant-supervision/plant-state`), specifically the **MQTT Stream** tab,
which is close to a 1:1 live view of this folder's own signal catalog (an
"MQTT Stream (ACS)" panel, an "MQTT Stream (PLC)" panel with columns
TimeStamp/Asset/Type/Tag/Value/Unit, and an "MQTT Stream (Raw)" panel with
Topic/Payload). The other three tabs (Bay Monitoring, Plant Monitoring,
Assets State) are presumably the business-meaning *derived* screens --
the natural next target once this one is confirmed solid, not attempted
yet.

### The one hard constraint this phase is built around

This screen only shows data arriving **live** -- it is not a
historical/persisted view. Sending a signal and then opening the screen
afterward will never show it. The only valid sequence is: open the
browser, log in, navigate to the tab, **start capturing**, *then* send the
signal, then wait for the live update. `test_plant_ui_reaction.py`'s
PLANT_UI_01 scenario exists purely to get capture (console errors +
WebSocket frames) attached before any later scenario sends anything --
don't reorder scenarios or add a new signal-sending one ahead of it.

### Why Claude can't run this itself

Every scenario needs a real, interactive MFA login (same as every other UI
script in this project) and, ultimately, a human's own eyes on the
resulting `ask_verdict()` prompt. Claude's own tool sandbox has no real
interactive stdin -- an `input()` call here returns EOF immediately instead
of actually waiting -- so it cannot itself sit through the login step.
**Someone needs to run this from a real terminal** -- true of every
Playwright script in this folder (`test_dispatch_vs_hera_profiles.py`,
`test_bay_occupancy_vs_hera.py`, `test_bay_monitoring_actual_vs_broker.py`,
`test_bay_occupancy_happy_path.py` included), not just this one.

```bash
py test_plant_ui_reaction.py
```

Prompts for the Hera env (dev/test) at startup -- see "A note on env/bay
prompts" above.

### What's confirmed vs. still guessed

- **Confirmed live, 2026-09-17 (first real run, `--env test`):** login,
  tab/panel navigation and PLANT_UI_01 all worked as designed. The real DOM
  behind each MQTT Stream panel is an Angular `div.message-group` /
  `div.grid-table` / `div.messages` / `div.message` structure (no
  `<table>`, no ARIA roles) -- confirmed via DevTools and now what
  `ui_capture.read_grid_after_heading()`'s "Attempt 0" matches; see its
  own docstring for the exact shape. It's an **append-only live message
  log** (each new MQTT message becomes a new row) rather than a
  single current-value cell per tag. `fill_rate_meas` sent via the broker
  showed up on both the Raw panel (Topic + Payload) and the PLC panel
  (Tag/Asset/Value/Unit) within about a second. Matching now checks the
  **Raw panel** first (exact Topic + Payload) as the primary source of
  truth, per direct tester feedback on the first run, with the PLC panel
  checked opportunistically afterward for the Unit column specifically
  (Raw doesn't show units). Also confirmed from the tester's own
  screenshots: the screen's URL, its 4 tabs, the MQTT Stream tab's 3
  panels and the PLC panel's 6 columns; two real JS console errors
  (`SyntaxError` from `JSON.parse`, one with the literal token `"type1"`
  in it -- almost certainly `RFID_trailer_type`'s bare-string value being
  passed to `JSON.parse()` instead of handled as plain text); and real
  Unit-column values for `RFID_signal_strength` (`dBm`, now applied to
  `signal_catalog.py`) and an odd literal `"0"`/`"integer"` shown for
  `main_setpoint_active`/`online`/`system_in_ESD`/`oks_stat_blending_line`
  (flagged in the catalog as a possible UI display quirk, not adopted as a
  real unit -- still needs live confirmation).
- **A real, not-yet-explained finding from that same first run:**
  WebSocket frame capture (`ui_capture.WebSocketCapture`) saw **zero**
  frames mentioning `fill_rate_meas`, even though the DOM updated
  correctly and near-instantly, and even though DevTools separately shows
  `[SignalR] New data for 'OnPlantPassedSignal'` console logs. Only one
  data point so far -- possible explanations, none confirmed: this
  deployment's SignalR connection negotiated down to a non-WebSocket
  transport (long-polling / Server-Sent Events, both invisible to
  `page.on("websocket")`), or the update this script's Playwright page
  attached to isn't the same connection carrying the data. **DOM is
  therefore the only reliable source of truth confirmed so far**, not
  merely the "primary" one as originally planned -- don't rely on
  WebSocket capture for a pass/fail decision; it's kept only in case a
  slower-updating signal type shows different behaviour.
- **Still open / not yet confirmed:** the exact 4 tab labels' clickability
  beyond the MQTT Stream tab already exercised (`role="tab"` assumed,
  falls back to a plain text click -- untested against Bay Monitoring /
  Plant Monitoring / Assets State so far); the full PLANT_UI_02/03/04 run
  end-to-end with a human verdict (only PLANT_UI_01 has a recorded PASS
  from a real run so far, and PLANT_UI_02 was re-run against the fixed
  selector but not yet confirmed end-to-end).

## Phase 4: does it show up correctly on Bay Monitoring's Actual chart? (`test_bay_monitoring_actual_vs_broker.py`)

**Status: built 2026-09-23, run live once (env=dev) -- endpoint confirmed,
response-shape guess was wrong and is now fixed, second run pending.**
Closes the loop on a manual
exploration from 2026-09-21 (`sustain_signal.py`): that session sent a
constant signal continuously across a wall-clock window and logged it, but
never pulled Hera's own Bay Monitoring screen back to compare -- and along
the way found real problems worth re-testing properly: a bucket can come
back `null`, and a bucket can come back DILUTED (looking averaged together
with hera-virtual-plant's own automatic ~30s zero-tick, seen in DEV).

Unlike Phase 3's MQTT Stream tab, Bay Monitoring's "Injection and Loading
Profiles" Actual line is historical/bucketed (15-min slots for a selectable
week), not live-only -- so there's no "capture must be attached before
sending" constraint here; you can send first and pull the screen afterward.

Two scenarios:

- **BAYMON_ACT_01** -- layout only (tab, both chart headings, week nav, bay
  selector). No signal sent.
- **BAYMON_ACT_02** -- the real, **formula-agnostic** assertion: sends one
  constant, known value every 8s (the same DEV-zero-tick-outrunning pacing
  `sustain_signal.py` already found necessary) across exactly two full,
  wall-clock-aligned 15-min buckets. Because the input never changes within
  a bucket, any sane aggregation (average/last-write/first-write) should
  reproduce it -- this scenario doesn't need to know Hera's real formula to
  make a fair comparison, unlike a partial-bucket send. A bucket that comes
  back `null` or comes back present-but-diluted (< 50% of the sent value) is
  reported as its own distinct outcome, not folded into a generic mismatch.
- **BAYMON_ACT_03** -- discovery only, deliberately never asserted
  PASS/FAIL: sends two different values, first half / second half of ONE
  isolated bucket, and reports which candidate aggregation (first-half,
  second-half/last-write, average, sum) the observed value is closest to.
  Same "observation harness" convention as this folder's
  `test_edge_case_*.py` scripts -- no spec confirms Hera's real formula.

**2026-09-23, first live run -- endpoint confirmed, response shape
CORRECTED from real evidence.** `GetBayMonitoringScreenAsync` was the
right endpoint (matched on the first try). But the response shape guess in
`extract_actual_points()` was wrong -- BAYMON_ACT_02 auto-detected SKIP
("no Actual points could be extracted") -- and the real body, captured to
`raw_responses/` exactly as designed for this situation, showed the actual
shape:

```
body["injectionAndLoadingGraph"]["profileGraphLines"] = [
    {"name": "Loading Bay 1", "subName": "Forecasted", "points": {...}, ...},
    {"name": "Loading Bay 1", "subName": "Actual",     "points": {...}, ...},
    {"name": "Loading Bay 2", "subName": "Forecasted", "points": {...}, ...},
    ...
    {"name": "Injection", "subName": "Actual Injection", "points": {...}, ...},
]
```

A **flat** list (not nested per bay), matched by `name` + `subName`. Each
line's `points` is a dict keyed by an exact ISO-8601 **UTC** timestamp
(15-min spaced, e.g. `"2026-09-20T22:00:00Z"`) -> a numeric value or
`null`. `null` is the DEFAULT/baseline state for a bucket nothing has been
received for yet, confirmed from the pre-test response (every bay's
"Actual" line was all-`null` before any signal was sent) -- not
automatically evidence of a bug on its own. `extract_actual_points()` and a
new `lookup_bucket_value()` (exact-key lookup with a small clock-skew
fallback) in `bay_monitoring_capture.py` now match this confirmed shape.

Remaining unconfirmed: the bay-selector dropdown's DOM (`select_bay()`) --
not yet exercised for real, since the default bay shown already happened to
be the one under test on this run.

**2026-09-23, live run in progress -- a real timing finding, already fixed.**
A first live run of BAYMON_ACT_02 (bucket 09:30-10:00 local) showed the
09:30 bucket looking correct but 09:45 not -- while the run was still
mid-flight, so not yet conclusive on its own, but it prompted a real fix:
the original 8s send interval does NOT divide a 900s (15-min) bucket
evenly (900/8 = 112.5), so a send only ever lands NEAR a later quarter-hour
boundary, off by up to ~4s -- worth fixing if the screen keys its 15-min
"Actual" points by exact timestamp. Switched the default interval to
**7.5s** (900/7.5 = 120 exactly, 1800/7.5 = 240 exactly) in both
`sustain_signal.py` and this test. That alone wasn't enough, though: the
same live log showed real clock drift under the OLD relative
`sleep(interval_s)` scheduling (a 9s gap appeared where 8s was expected, by
send #68, from ordinary per-publish overhead accumulating) -- so `sustain()`
now schedules against absolute target times (`start_at + k*interval_s`)
instead, which can't drift regardless of per-send overhead. Both fixes are
in `sustain_signal.py`. Combined with the response-shape fix just above,
BAYMON_ACT_02 hasn't yet been re-run end-to-end with all three fixes in
place (timing x2 + parsing) -- that's the next live run to do.

**2026-09-23, also fixed (same finding as Phase 5's happy-path test):** the
flat post-send wait wasn't always enough -- watching the real screen live
sometimes showed a real, several-minute delay before a just-sent value
appeared. `BAYMON_ACT_02/03` now use `_wait_for_bucket()`, which polls (via
`bay_monitoring_capture.poll_until()`) for up to **5 minutes**,
re-capturing the screen every 30s, instead of one fixed sleep + one
capture.

**Which environment:** run against **both** DEV and TEST. DEV is where the
2026-09-21 zero-tick dilution was actually observed; TEST uses a completely
separate broker cluster and is presumed (not yet confirmed) to be free of
that interference, since nothing else is known to publish there
automatically. Running both is the only way to confirm that assumption
rather than build the routine test around an untested one.

```bash
py test_bay_monitoring_actual_vs_broker.py
```

Prompts for the Hera env (dev/test) and loading bay (1-4) at startup, so
running it twice -- once accepting "dev", once accepting "test" -- is how
you cover both environments. See "A note on env/bay prompts" above.

## Phase 5: full happy-path visit vs. the Bay Occupancy Schedule (`test_bay_occupancy_happy_path.py`)

**Status: built 2026-09-23, run live once (env=dev, bay 2, plate 1TRL167) --
HAPPY_01/02 both PASS, HAPPY_03's discovery question RESOLVED (see below).**
Distinct target from Phase 4:
that phase checks the "Injection and Loading Profiles" Actual LINE; this one
checks the **Bay Occupancy Schedule block** and its per-occupation movement
checklist -- linked (same `GetBayMonitoringScreenAsync` response, `bays` and
`injectionAndLoadingGraph` are sibling keys) but a different assertion
target, per explicit direction.

Sends one full, realistic truck visit over the broker (this folder's own
sender only, no hera-virtual-plant dependency): ACS entry + drop-off (modes
1-3) -> Ready/Authorized/Filling (modes 4-6) + a 30-minute sustained
`fill_rate_meas` (same mechanism as Phase 4) -> Fill done/Wait disconnect/
Wait pickup (modes 8-10) -> `pickup()` (mode 11, RFID, mode 12, mode 0) ->
`exit_()` (ACS exit pair). Three scenarios: **HAPPY_01** (asserts the first
5 checklist slots), **HAPPY_02** (asserts READY/AUTHORIZED/FILLING, after
the 30-min fill), **HAPPY_03** (discovery only -- see below).

**A real question this triggered, investigated before writing any code
(2026-09-23):** an initial read of `steps.py` looked suspicious -- it skips
sending `operating_mode=7` entirely, going 6 -> 8, with comments guessing
labels for 8-12. Had an agent read hera-virtual-plant's own source
end-to-end (`app.py`, `backend/virtual_plant/truck_sim.py`,
`hera_mqtt_test_scenario.txt` -- three independent, mutually-consistent
files) to check this against ground truth rather than guessing further.
**Confirmed: `steps.py` was already correct.** Modes 1-6 and 8-12 map
exactly to Drop-off start/Vehicle approach/Drop-off closed/Ready/
Authorized/Filling/Fill done/Wait disconnect/Wait pickup/Pickup created/
Vehicle leaving; mode 7 is never used anywhere in that repo. No fix needed.

**A real mismatch the same investigation surfaced, and confirmed couldn't
be settled by reading source alone -- NOW RESOLVED by the first live run:**
Hera's own Bay Occupancy checklist (`bay_monitoring_capture.
BAY_OCCUPATION_CHECKLIST`) shows **6** distinct labels for the pickup half
but the plant simulator only has **5** mode values (8-12) covering that
stretch. `HAPPY_03` was built to answer this empirically (log every send's
own timestamp, correlate against whichever checklist slot's `time` lands
closest -- `_closest_send()`), and it worked: the confirmed answer is that
**`operating_mode=9` produces no observable effect on this checklist at
all** -- "WAITING FOR DISCONNECT" stayed unfilled the entire run, shown as
"-" on the real screen, exactly matching the correlation script's `status
None / time None` result. It isn't a missing 6th signal; that slot simply
never triggers with this signal set. Independently cross-checked against a
screenshot of the real screen (UTC console timestamps vs. local UTC+2
screen times matched exactly). Full confirmed mapping:

| mode | UI label |
|---|---|
| 8 | FILLING DONE |
| 9 | *(no observable effect)* |
| 10 | PICK-UP START |
| 11 (+RFID) | DISCONNECTED |
| 12 | VEHICLE LEAVING |
| 0 (final) | PICK-UP END |

Also confirmed: the occupation's overall `status` field became `"Success"`
despite the one permanently-empty slot -- Hera doesn't require every
checklist slot filled for the occupation to count as complete. This table
now lives as the authoritative comment on `BAY_OCCUPATION_CHECKLIST` itself
-- read it there, don't re-derive. `HAPPY_03` is deliberately still left as
an observation/report scenario rather than converted to an assertion,
since this is only one confirmed run so far.

**2026-09-23, second live run -- two real fixes, both landed:**
1. **Timing.** Watching the real screen live during that first run showed
   everything eventually working, but sometimes with a real, several-minute
   delay before a just-sent signal's effect appeared -- longer than the
   original flat 30s wait accounted for. `_get_occupation()` now polls (via
   the new shared `bay_monitoring_capture.poll_until()`) for up to
   **5 minutes**, re-capturing every 30s, instead of one fixed sleep + one
   capture. Same fix applied to Phase 4's `BAYMON_ACT_02/03`
   (`_wait_for_bucket()`), since the same latency applies there.
2. **A real, confirmed bug this surfaced:** `movement_status()` looks up a
   checklist slot by label text, but "MOVEMENT REQUESTED"/"MOVEMENT
   COMPLETED" each appear TWICE (entry + exit ACS). The first live run's
   `HAPPY_03` silently re-reported the ENTRY pair's already-"Success"
   status while believing it was confirming the EXIT pair -- harmless in
   that run (it just showed stale timestamps with no send correlation) but
   a real defect. Fixed by adding `movement_at(occupation, index)`
   (position-based, matching `BAY_OCCUPATION_CHECKLIST`'s own order) and
   switching every scenario to use it instead of label-text lookup.

Also confirmed by the same investigation (worth keeping in mind for
`STEP_DELAY_S`): hera-virtual-plant's own two lifecycle drivers (Streamlit
"Send All" button, default 1.0s; the backend `TruckSimulator`, default
1.5s) both use a plain fixed delay between steps -- neither waits for an
ack/condition. This test uses 1.5s, matching the backend driver. Also spot-
checked: `signal_catalog.py`'s `operating_mode`/`acs_activity` topic
strings already match hera-virtual-plant's current canonical topics
(`.../operating_mode/0.8.3`, `.../activity/0.8.3`) -- not the stale legacy
form (`...V0.5.3`) the same investigation found in an old reference doc.

```bash
py test_bay_occupancy_happy_path.py
```

Prompts for the Hera env (dev/test) and loading bay (1-4) at startup. See
"A note on env/bay prompts" above.

### What's still open (all phases)

- Phase 3's MQTT Stream checks are solid (run live twice); Phase 4/5 (Bay
  Monitoring, Bay Occupancy Schedule) are built and partially/fully
  confirmed live, per their own sections above. Plant Monitoring / Assets
  State (the remaining two Plant Supervision tabs) are still untouched --
  the natural next targets, closing the "does raw telemetry actually flow
  into Hera's derived state correctly" gap flagged in
  `PLANT_SIGNALS_CATALOG.md` §8.
- A live run is also the free opportunity to finish the Unit-column
  cross-check against every remaining `db-topic-only` signal in
  `signal_catalog.py` (only `RFID_signal_strength` has been corrected so
  far, from a screenshot -- `main_setpoint_active`, `system_in_ESD`,
  `online`, `oks_stat_blending_line`, `connected`, `disconnected`,
  `filling`, `fill_requested`, `fill_rate_trailer_max`,
  `fill_rate_calc_max`, `hpc_pressure_max`, `RFID_trailer_licence_plate`,
  `RFID_system_failure` are all still guesses).
- Widen the DB-level skipped-step / duplicate scenarios beyond the 3+3
  covered now, if the findings so far warrant it.
- Whether raw telemetry flows into `LoadingSessions`/`GridInjectionSessions`
  -- needs more research before it can be a reliable assertion.
- Confirming the exact wire envelope shape for the `db-topic-only` signals
  against a real raw MQTT capture, if one becomes available.
- Part A (dispatch checks) has no equivalent "what's still open" writeup
  yet -- it predates this README (came from the old "Plant Communication -
  testing" folder, which never had its own). Worth a proper pass if this
  folder gets more attention.

## dashboard/ (manual tool, not a test)

A Streamlit app that subscribes to the broker's `HOF/+/+/CMD/#` topics and
visualises command profiles for Electrolyser/Rectifier/Loading Bay, live.
Registered in `run_all_tests.py`'s suite with an empty script list (it has
no automated test -- it's a manual monitoring tool a tester runs
separately, `streamlit run dashboard/app.py`). Has its own `config.py`
(broker creds) and `requirements.txt`, independent of everything above --
see `dashboard/README.md` for how to run it. Moved here 2026-09-23 from
the old separate "Plant Communication - sending" folder, as a subfolder
rather than flat among the test scripts, since it's a genuinely different
kind of thing (a live tool, not something with a pass/fail verdict).
