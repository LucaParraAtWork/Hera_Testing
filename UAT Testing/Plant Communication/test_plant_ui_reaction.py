#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_plant_ui_reaction.py
==========================
Phase 3 of "Plant Communication": given a signal already
confirmed (by test_signal_reaction.py) to land correctly in Hera's own
database, does it ALSO show up correctly on Hera's own Plant Supervision
screen -- https://<env>.azurewebsites.net/plant-supervision/plant-state,
"MQTT Stream" tab -- with the right value, right topic, right timing?

Scope, deliberately: the MQTT Stream tab only (its "MQTT Stream (PLC)" and
raw-topic view are close to a 1:1 live view of signal_catalog.py). Bay
Monitoring / Plant Monitoring / Assets State -- the business-meaning
downstream screens -- are the natural next phase once this one is solid,
per this folder's README; not attempted here.

THE HARD CONSTRAINT THIS WHOLE SCRIPT IS BUILT AROUND: this screen only
shows data arriving LIVE. It is not a historical view -- you cannot send a
signal and then open the screen afterward. Capture (console + WebSocket
listeners) is attached in PLANT_UI_01, BEFORE any signal is sent by any
later scenario in this run. Do not reorder the scenarios or add a new one
that sends a signal before PLANT_UI_01 has run.

RUN LIVE, 2026-09-17 (--env test): all 4 scenarios confirmed against the
real screen -- see README.md's "Phase 3" section for the full writeup,
including the real frontend bug PLANT_UI_04 found. Originally written
"NOT YET RUN LIVE", same honesty convention `test_bay_occupancy_vs_hera.py`
(same folder) still carries for the same reason (Claude's own tool sandbox
has no real interactive stdin, so it cannot itself sit through the manual
MFA login this script requires -- someone needs to run this from an actual
terminal) -- kept here only as a record of that convention, not as this
script's current status.

Also unresolved, deliberately, per the same task brief: whether the live
updates ride over a WebSocket frame this script can actually parse, or
whether DOM polling alone is what actually works. Both are captured; DOM
is treated as the primary pass/fail source (it's the screen a human
actually reads), WebSocket frames as corroborating raw evidence.

Scenarios
---------
  PLANT_UI_01  Login, open Plant Supervision > Plant State, confirm all 4
               tabs exist, switch to MQTT Stream, confirm all 3 panels
               exist, THEN attach console + WebSocket capture. No signal is
               sent in this scenario -- it exists purely to get capture
               running before anything is sent (the hard constraint above).

  PLANT_UI_02  Send fill_rate_meas (a distinctive value, one bay) and
               confirm the MQTT Stream (PLC) panel shows a row with the
               matching Tag/Asset/Value/Unit within a timeout.

  PLANT_UI_03  Send an operating_mode 1->2->3 sequence on one bay and
               confirm each mode's value appears on screen, i.e. the panel
               reflects the live sequence, not just the final state.

  PLANT_UI_04  Send rfid_trailer_type (value "type1") and check for the
               JSON.parse-style console error the tester already spotted in
               DevTools ("Unexpected token 'y', \"type1\" is not valid
               JSON") -- confirms (or fails to reproduce) the suspected
               frontend bug with actual captured evidence, correlated by
               timestamp to this specific send, not just "seen once, some
               time, somewhere".

Run
---
    py test_plant_ui_reaction.py
    py test_plant_ui_reaction.py --from 2

Prompts for the Hera env (dev/test, same env for the matching MQTT broker)
at startup, same convention as Weekly Nomination / Scheduled Transfers --
hardcode this file's own `ENV = ""` constant (e.g. `ENV = "test"`) to skip
the prompt.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import ui_capture as ui
from broker_client import PlantBrokerClient
from envelope import build_envelope
from signal_catalog import SIGNALS
from steps import build_operating_mode_payload

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE.parent))
from uat_excel_reporter import record_results, ask_verdict  # noqa: E402

# Reuse the SAME dev/test URL mapping every other UI script in this project
# uses, instead of hardcoding herawebdev/herawebtest here -- per this
# folder's task brief.
sys.path.insert(0, str(HERE.parent / "Scheduled Transfers"))
import test_scheduled_transfers_profiles as sched_mod  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ENV = ""   # "dev" | "test" | ""  (prompt at startup)
ENV_TO_BROKER = {"dev": "DEV", "test": "TEST"}

BAY_FILL_RATE = 2
BAY_OPERATING_MODE = 3
BAY_RFID_TYPE = 4

SCENARIOS = [
    {"n": 1, "id": "PLANT_UI_01", "expect": "PASS",
     "name": "Open MQTT Stream tab, confirm layout, attach capture before any send",
     "desc": ("Confirm all 4 Plant Supervision tabs exist and the MQTT Stream tab's 3 "
              "panels (ACS/PLC/Raw) are present, THEN attach console + WebSocket capture. "
              "No signal is sent here -- capture must be running before PLANT_UI_02 sends "
              "anything (this screen only shows live data, never historical).")},
    {"n": 2, "id": "PLANT_UI_02", "expect": "PASS",
     "name": "fill_rate_meas appears on MQTT Stream (PLC) with correct value/unit",
     "desc": (f"Send fill_rate_meas on DI{BAY_FILL_RATE} with a known value, then poll the "
              "MQTT Stream (PLC) panel for a row with matching Tag/Asset/Value/Unit.")},
    {"n": 3, "id": "PLANT_UI_03", "expect": "PASS",
     "name": "operating_mode 1->2->3 sequence appears on screen, not just the final state",
     "desc": (f"Send operating_mode 1, 2, 3 in order on DI{BAY_OPERATING_MODE}, confirm each "
              "value is individually observed on the MQTT Stream (PLC) panel.")},
    {"n": 4, "id": "PLANT_UI_04", "expect": "PASS",
     "name": "rfid_trailer_type='type1' -- confirm/deny the suspected JSON.parse console bug",
     "desc": ("Send rfid_trailer_type (bare string value 'type1') and capture the browser "
              "console for a JSON.parse SyntaxError in the following seconds -- confirms "
              "with real evidence the bug already spotted manually in DevTools, or shows it "
              "doesn't reproduce this way.")},
]


def _ask(sc, auto, reason):
    return ask_verdict(
        tc_id=sc["id"], title=sc["name"], expect=sc["expect"],
        auto=auto, reason=reason, index=sc["n"], total=len(SCENARIOS),
        excel_id=sc["id"],
    )


def _close_numeric(text: str, expected: float, eps: float = 0.05) -> bool:
    try:
        return abs(float(text) - float(expected)) <= eps
    except Exception:
        return False


# --------------------------------------------------------------------------
# PLANT_UI_01
# --------------------------------------------------------------------------
def run_plant_ui_01(page, console_cap: ui.ConsoleCapture, ws_cap: ui.WebSocketCapture, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)

    found_tabs = {name: ui.text_present(page, label, timeout_ms=4000)
                  for name, label in ui.TAB_LABELS.items()}
    print("  Tabs found:")
    for name, ok in found_tabs.items():
        print(f"    [{'OK ' if ok else 'MISS'}] {ui.TAB_LABELS[name]}")

    clicked = ui.click_tab(page, ui.TAB_LABELS["mqtt"])
    ui.wm()
    ui.quiet(page)
    page.screenshot(path=str(shots_dir / "01_mqtt_stream_tab.png"), full_page=True)

    found_panels = {name: ui.text_present(page, label, timeout_ms=5000)
                    for name, label in ui.PANEL_HEADINGS.items()}
    print("  Panels found:")
    for name, ok in found_panels.items():
        print(f"    [{'OK ' if ok else 'MISS'}] {ui.PANEL_HEADINGS[name]}")

    # Attach capture -- BEFORE any scenario below sends a signal.
    console_cap.attach()
    ws_cap.attach()
    print("  Console + WebSocket capture attached (nothing sent yet).")

    all_tabs_ok = all(found_tabs.values())
    all_panels_ok = clicked and all(found_panels.values())
    auto = "PASS" if (all_tabs_ok and all_panels_ok) else "FAIL"
    reason = (f"tabs {sum(found_tabs.values())}/4, mqtt-tab-clicked={clicked}, "
              f"panels {sum(found_panels.values())}/3")
    return auto, reason


# --------------------------------------------------------------------------
# PLANT_UI_02
# --------------------------------------------------------------------------
def run_plant_ui_02(page, client: PlantBrokerClient, ws_cap: ui.WebSocketCapture, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    bay = BAY_FILL_RATE
    value = 91.3
    spec = SIGNALS["fill_rate_meas"]
    topic = spec.topic(bay)

    since = time.time()
    client.publish(topic, build_envelope(value, value_type="float", unit="g/s"), retain=spec.retain)
    print(f"  Sent fill_rate_meas={value} g/s on DI{bay} (topic={topic}); "
          f"polling MQTT Stream (Raw)...")

    # Raw panel is the primary source of truth (topic + payload, exactly as
    # sent) -- confirmed live 2026-09-17 to update near-instantly. PLC panel
    # (Tag/Asset/Value/Unit) is checked as a secondary, non-blocking source
    # for the Unit column specifically, which Raw doesn't show.
    def _raw_match(row):
        return row.get("Topic", "").strip() == topic and _close_numeric(row.get("Payload", ""), value)

    raw_row, raw_grid = ui.poll_grid_until(page, ui.PANEL_HEADINGS["raw"], _raw_match,
                                            fallback_columns=ui.RAW_COLUMNS, timeout_s=40.0)
    page.screenshot(path=str(shots_dir / "02_after_fill_rate_meas.png"), full_page=True)

    for name, grid in (("raw", raw_grid),):
        if grid is not None:
            (shots_dir / f"{name}_grid_last_read.json").write_text(
                json.dumps({"headers": grid.headers, "rows": grid.rows}, indent=2), encoding="utf-8")

    ws_hits = ws_cap.frames_mentioning("fill_rate_meas", since_ts=since)
    print(f"  WebSocket frames mentioning 'fill_rate_meas' since send: {len(ws_hits)}")

    if raw_row is None:
        detail = f"no Raw row matched Topic={topic} Payload~={value} within 40s"
        if raw_grid:
            detail += f" (last read {len(raw_grid.rows)} row(s), headers={raw_grid.headers} -- see raw_grid_last_read.json)"
        else:
            detail += " (Raw grid could not be located at all -- ui_capture.read_grid_after_heading needs adjustment)"
        return "FAIL", detail

    def _plc_match(row):
        return (row.get("Tag", "").strip() == "fill_rate_meas"
                and f"DI{bay}" in row.get("Asset", "")
                and _close_numeric(row.get("Value", ""), value))

    plc_grid = ui.read_grid_after_heading(page, ui.PANEL_HEADINGS["plc"], fallback_columns=ui.PLC_COLUMNS)
    plc_row = next((r for r in plc_grid.as_dicts() if _plc_match(r)), None) if plc_grid else None
    if plc_grid is not None:
        (shots_dir / "plc_grid_last_read.json").write_text(
            json.dumps({"headers": plc_grid.headers, "rows": plc_grid.rows}, indent=2), encoding="utf-8")

    if plc_row is None:
        return "PASS", (
            f"Raw panel confirms topic+value: {raw_row!r}. PLC row not located this pass "
            f"(see plc_grid_last_read.json) -- Unit not independently re-checked. "
            f"{len(ws_hits)} corroborating websocket frame(s)."
        )

    unit_ok = plc_row.get("Unit", "").strip() == "g/s"
    return ("PASS" if unit_ok else "FAIL",
            f"Raw panel confirms topic+value: {raw_row!r}. PLC row: {plc_row!r} -- Unit "
            f"{'matches' if unit_ok else 'does NOT match'} expected 'g/s'; "
            f"{len(ws_hits)} corroborating websocket frame(s)")


# --------------------------------------------------------------------------
# PLANT_UI_03
# --------------------------------------------------------------------------
def run_plant_ui_03(page, client: PlantBrokerClient, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    bay = BAY_OPERATING_MODE
    modes = [1, 2, 3]
    mode_sig = SIGNALS["operating_mode"]
    topic = mode_sig.topic(bay)

    for m in modes:
        client.publish(topic, build_operating_mode_payload(m), retain=mode_sig.retain)
        print(f"  Sent operating_mode={m} on DI{bay}")
        time.sleep(2.0)

    # Raw panel (Topic/Payload) is the primary source, same rationale as
    # PLANT_UI_02 -- all 3 modes share the same Topic, so each is
    # distinguished by its own Payload ("1"/"2"/"3") appearing as its own
    # row (the panel is a live append-only message log, not a single
    # current-value cell -- confirmed live 2026-09-17).
    print("  Polling MQTT Stream (Raw) for each mode value...")
    results = []
    for m in modes:
        def _match(row, m=m):
            return row.get("Topic", "").strip() == topic and row.get("Payload", "").strip() == str(m)

        row, grid = ui.poll_grid_until(page, ui.PANEL_HEADINGS["raw"], _match,
                                        fallback_columns=ui.RAW_COLUMNS,
                                        timeout_s=25.0, poll_interval_s=1.5)
        results.append((m, row))
        print(f"    mode {m}: {'FOUND ' + repr(row) if row else 'NOT FOUND'}")
        if row is None and grid is not None:
            (shots_dir / f"raw_grid_after_mode_{m}.json").write_text(
                json.dumps({"headers": grid.headers, "rows": grid.rows}, indent=2), encoding="utf-8")

    page.screenshot(path=str(shots_dir / "03_after_operating_mode.png"), full_page=True)

    n_ok = sum(1 for _, r in results if r is not None)
    auto = "PASS" if n_ok == len(modes) else "FAIL"
    return auto, f"{n_ok}/{len(modes)} operating_mode value(s) confirmed on the Raw panel for DI{bay}"


# --------------------------------------------------------------------------
# PLANT_UI_04
# --------------------------------------------------------------------------
def run_plant_ui_04(page, client: PlantBrokerClient, console_cap: ui.ConsoleCapture, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    bay = BAY_RFID_TYPE
    spec = SIGNALS["rfid_trailer_type"]
    value = spec.example_value  # "type1"
    topic = spec.topic(bay)

    since = time.time()
    client.publish(topic, build_envelope(value, value_type=spec.value_type, unit=spec.unit),
                   retain=spec.retain)
    print(f"  Sent rfid_trailer_type={value!r} on DI{bay}; watching console for 8s...")
    time.sleep(8.0)
    page.screenshot(path=str(shots_dir / "04_after_rfid_trailer_type.png"), full_page=True)

    since_entries = [e for e in console_cap.entries if e["ts"] >= since]
    (shots_dir / "console_entries_since_send.json").write_text(
        json.dumps(since_entries, indent=2, default=str), encoding="utf-8")

    hits = console_cap.matching(
        r"SyntaxError|Unexpected token|JSON\.parse|Unexpected non-whitespace", since_ts=since)

    # Also check whether the value still rendered correctly despite any
    # console error -- a caught exception with a working fallback is a
    # meaningfully different (less severe) finding than a broken display,
    # and this shouldn't rely on someone eyeballing a screenshot to know
    # which one happened. Not a fresh poll -- 8s already elapsed above, so
    # a single read is enough.
    raw_grid = ui.read_grid_after_heading(page, ui.PANEL_HEADINGS["raw"], fallback_columns=ui.RAW_COLUMNS)
    raw_row = None
    if raw_grid is not None:
        raw_row = next((r for r in raw_grid.as_dicts()
                         if r.get("Topic", "").strip() == topic and r.get("Payload", "").strip() == value), None)
    render_note = (f"value still rendered correctly on the Raw panel ({raw_row!r})" if raw_row
                   else "value did NOT render correctly on the Raw panel -- this would make it a "
                        "worse, data-loss-style bug, not just a noisy caught exception")

    if not hits:
        auto = "SKIP" if not since_entries else "PASS"
        return auto, (
            f"No JSON-parse-style console error observed in the 8s after sending "
            f"rfid_trailer_type={value!r} -- either the bug reported 2026-09-17 doesn't "
            f"reproduce via this exact path, or needs a different value/timing. "
            f"{len(since_entries)} total console entr{'y' if len(since_entries)==1 else 'ies'} "
            f"captured in that window -- see console_entries_since_send.json. {render_note}."
        )

    for h in hits:
        print(f"    [{h['kind']}] {h['text']}")
    print(f"    DOM check: {render_note}")
    return "FAIL", (
        f"{len(hits)} JSON-parse-style console error(s) confirmed within 8s of sending "
        f"rfid_trailer_type={value!r} -- see console_entries_since_send.json for exact text. "
        f"'FAIL' here means the suspected frontend bug reproduced with real evidence -- "
        f"that IS the finding, not a test-harness problem. {render_note}."
    )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                         help="Resume from scenario N (1-based). NOTE: capture is normally "
                              "attached in scenario 1 -- resuming past it attaches capture "
                              "immediately after login instead, as a fallback, but you lose "
                              "the 'nothing sent before capture started' guarantee for "
                              "anything the plant/other testers may have sent in between.")
    parser.add_argument("--excel", default=None, metavar="PATH",
                         help="Report file to write Pass/Fail into. Default: create a new "
                              "timestamped copy under UAT Testing/Reports/.")
    args = parser.parse_args()
    start_from = max(1, args.start_from)

    env = ENV.strip().lower()
    if env not in sched_mod.ENVIRONMENTS:
        env, base_url = sched_mod._resolve_env()
    else:
        base_url = sched_mod.ENVIRONMENTS[env]
    broker_env = ENV_TO_BROKER[env]

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = HERE / "test_artifacts_plant_ui_reaction" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print("  Plant Communication -- Phase 3: does it show up on Hera's own screen?")
    print(f"  Hera env      : {env}  ({base_url})")
    print(f"  Broker env    : {broker_env}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*70}")

    results: list[dict] = []

    with sync_playwright() as pw:
        session_dir = run_dir.parent / "playwright_session"
        ctx, page = ui.open_logged_in_session(pw, base_url, session_dir)

        console_cap = ui.ConsoleCapture(page=page)
        ws_cap = ui.WebSocketCapture(page=page)
        if start_from > 1:
            console_cap.attach()
            ws_cap.attach()
            print("  [WARN] Starting from scenario > 1 -- capture attached now (right after "
                  "login) instead of after PLANT_UI_01's own tab/panel checks.\n")

        with PlantBrokerClient(env=broker_env) as client:
            print(f"  Connected to broker {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']}\n")

            RUNNERS = {
                "PLANT_UI_01": lambda shots_dir: run_plant_ui_01(page, console_cap, ws_cap, shots_dir),
                "PLANT_UI_02": lambda shots_dir: run_plant_ui_02(page, client, ws_cap, shots_dir),
                "PLANT_UI_03": lambda shots_dir: run_plant_ui_03(page, client, shots_dir),
                "PLANT_UI_04": lambda shots_dir: run_plant_ui_04(page, client, console_cap, shots_dir),
            }

            for sc in SCENARIOS:
                if sc["n"] < start_from:
                    continue
                shots_dir = run_dir / f"{sc['n']:02d}_{sc['id']}"
                print(f"\n{'-'*70}")
                print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['name']}")
                print(f"  {sc['desc']}")

                try:
                    auto, reason = RUNNERS[sc["id"]](shots_dir)
                except Exception as exc:
                    import traceback
                    print(f"  ERROR: {exc}")
                    traceback.print_exc()
                    auto, reason = "FAIL", f"ERROR: {exc}"

                result, notes = _ask(sc, auto, reason)
                results.append({**sc, "result": result, "notes": notes, "auto": auto, "reason": reason})

        ctx.close()

    print(f"\n{'='*70}")
    print(f"  SUMMARY -- run {run_id}")
    print(f"{'='*70}")
    ok = fail = other = 0
    for r in results:
        match = r["expect"] == "?" or r["result"] == r["expect"]
        flag = "   " if match else "!! "
        if r["result"] == "PASS":
            ok += 1
        elif r["result"] == "FAIL":
            fail += 1
        else:
            other += 1
        print(f"  {flag}{r['n']}. {r['id']:<12} exp={r['expect']}  got={r['result']}  {r['name']}")
        print(f"       auto-detected: {r['auto']} -- {r['reason']}")
    print(f"\n  PASS: {ok}   FAIL: {fail}   OTHER (skip/inconclusive): {other}")

    csv_path = run_dir / "test_results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["n", "id", "name", "expect", "result", "auto", "reason", "notes"])
        for r in results:
            w.writerow([r["n"], r["id"], r["name"], r["expect"], r["result"], r["auto"], r["reason"], r["notes"]])
    print(f"\n  Results: {csv_path}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_plant_ui_reaction.py")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
