# Plant Communication - Signal Testing

Everything for testing Hera's reaction to plant signals, in one folder:
a standalone "fake plant" that publishes MQTT signals straight to the
broker (no dependency on `hera-virtual-plant` -- that simulator won't exist
long-term for this testing effort, only the broker will), the automated
database-level tests that confirm Hera captures what it's sent correctly,
and (Phase 3, below) a first pass at checking Hera's own Plant Supervision
UI against the same signals.

## Why this exists

The goal is to test **Hera's reaction** to plant signals: does it parse
them correctly, update the right tables, drive the right UI state?
Step 1 was sending realistic plant signals on demand (the sender below).
Step 2, built, is confirming Hera actually captures them correctly in its
own database (`test_signal_reaction.py`). Step 3, started but **not yet
run live** (see its own section below), is checking Hera's own
Plant Supervision UI against what's already confirmed correct in the DB.

## Layout

```
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
                                    see its module docstring for why)
test_signal_reaction.py          -- THE automated DB-level test to run (see below)
test_edge_case_skipped_step.py    -- exploratory: what happens when a step is missing?
test_edge_case_duplicates.py       -- exploratory: what happens when a signal repeats?
_paths.py                           -- wires in the one remaining external dependency
                                        (../Plant Communication - testing/db_common.py)

ui_capture.py                        -- Phase 3: Playwright helpers for Hera's own
                                          Plant Supervision screen (login, tab/panel
                                          navigation, DOM grid reading, console +
                                          WebSocket capture)
test_plant_ui_reaction.py             -- Phase 3: THE UI-level test to run (see below) --
                                          NOT YET RUN LIVE
```

## Quick start

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

## The test to run: `test_signal_reaction.py`

```
py test_signal_reaction.py
py test_signal_reaction.py --env DEV
py test_signal_reaction.py --from 3
```

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

### Why this hasn't been run live yet

Every scenario needs a real, interactive MFA login (same as every other UI
script in this project) and, ultimately, a human's own eyes on the
resulting `ask_verdict()` prompt. Claude's own tool sandbox in this
session has no real interactive stdin -- an `input()` call here returns
EOF immediately instead of actually waiting -- so it cannot itself sit
through the login step. **Someone needs to run this from a real terminal**
the same way `Plant Communication - testing/test_bay_occupancy_vs_hera.py`
(also flagged "NOT YET RUN LIVE") is meant to be run.

```bash
py test_plant_ui_reaction.py            # test env (default)
py test_plant_ui_reaction.py --env dev
```

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

### What's still open (all phases)

- **Phase 3 itself needs its first live run** -- expect to fix selectors in
  `ui_capture.py` against the real DOM/WebSocket traffic, per above.
- Once Phase 3's MQTT Stream checks are solid, extend to Bay Monitoring /
  Plant Monitoring / Assets State -- the derived/business-state screens,
  closing the "does raw telemetry actually flow into Hera's derived state
  correctly" gap flagged in `PLANT_SIGNALS_CATALOG.md` §8.
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
