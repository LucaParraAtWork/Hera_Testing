#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_signal_reaction.py
========================
Does Hera correctly capture, in its own database, every plant signal this
project's fake-plant simulator can send? Same terminal look AND same
verdict flow as the rest of this project's "test_*.py" scripts (numbered
scenario blocks, a Steps section, a YOUR VERDICT prompt via
uat_excel_reporter.ask_verdict(), a final PASS/FAIL summary, and a
record_results() write into the shared UAT Excel report) -- confirmed by
reading how Master Data/test_master_data.py and
NetCommodityCost/test_ncc_costs_breakdown.py do it: EVERY scenario there
goes through ask_verdict(), including pure data-comparison ones with no UI
involved at all (e.g. NCC's Python-vs-API check) -- AUTO_VALIDATION_ENABLED
is False project-wide, so the auto-detected result is always shown only as
an observation, never silently applied. A human sets the final verdict,
even when the check itself is fully deterministic. This script previously
skipped that (reasoning "deterministic == no judgement needed") -- that was
the wrong call for this project's convention; fixed here.

`ask_verdict()`'s Excel-linked Steps section will report "no matching row"
for these scenario IDs since they aren't in Hera_UAT_Test_Plan.xlsx yet --
expected, not an error; add rows there later if these should become part of
the formal plan.

Scope: database-level only, same as every script in this folder -- whether
the right thing then shows up on a Hera screen is a separate, not-yet-built
phase.

Everything this script needs lives in this same folder (the fake-plant
sender: signal_catalog.py, steps.py, broker_client.py, config.py,
envelope.py; the DB-check helpers: db_check.py) except two things pulled in
via _paths.py / sys.path:
  - ../Plant Communication - testing/db_common.py  (credentialed DB access
    -- one shared .env, not duplicated here)
  - ../uat_excel_reporter.py (ask_verdict / record_results -- the
    project-wide verdict + reporting convention, one level up in UAT Testing/)
  - The full operating_mode -> Movements.EventName mapping and the "one
    known gap" finding, both confirmed in earlier ad-hoc runs this session
    and now asserted here instead of just observed -- see
    PLANT_SIGNALS_CATALOG.md sections 6 and 8 for the narrative writeup.

Scenarios
---------
  SIG_REACT_01  Every catalog signal reaches PlantSignals (or Movements for
                the ACS activity signal) -- the "every signal -> a DB write"
                check. `oks_stat_blending_line` is tracked separately, not
                counted against the auto-detected result: it's a known,
                confirmed gap in heraplantdatabasetest specifically (see
                PLANT_SIGNALS_CATALOG.md §8).

  SIG_REACT_02  The full operating_mode 0-12 lifecycle produces the CORRECT
                Movements.EventName for every mode sent -- asserted against
                signal_catalog.OPERATING_MODE_EVENT_NAME, not just "a row
                showed up".

  SIG_REACT_03  A full ACS entry pair (mvt_requested + mvt_completed) with
                known truck_id/driver_id/trailer_id/bay produces a Movements
                row whose OWN COLUMNS match what was sent -- not just that a
                row with the right EventName exists somewhere.

  SIG_REACT_04  Scalar/structured VALUES are captured correctly, not just
                presence: fill_rate_meas's numeric value, flow_rate_mass's
                value AND unit (a regression guard for the kg/h vs m3(n)/h
                mismatch found earlier), and RFID_trailer_details' own
                fields (license_plate, trailer_id).

NOT YET COVERED, deliberately -- see PLANT_SIGNALS_CATALOG.md §8 and this
folder's README "What's still open": whether raw telemetry actually flows
into Hera's higher-level derived tables (LoadingSessions,
GridInjectionSessions) -- the mechanism isn't understood well enough yet
(likely needs a full Transfers-backed lifecycle, not standalone messages)
to write a reliable assertion without guessing.

Run
---
    py test_signal_reaction.py
    py test_signal_reaction.py --env DEV
    py test_signal_reaction.py --database heraplantdatabasedev
    py test_signal_reaction.py --from 3
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import _paths  # noqa: F401
import db_check
from broker_client import PlantBrokerClient
from config import ENV_CHOICES, DEFAULT_ENV
from envelope import build_envelope
from signal_catalog import SIGNALS, OPERATING_MODE_EVENT_NAME
from steps import build_acs_event_payload, build_rfid_trailer_details_payload
import steps as S

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results, ask_verdict  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent

# Known, confirmed gap -- see PLANT_SIGNALS_CATALOG.md §8. Tracked and
# reported, but excluded from SIG_REACT_01's auto-detected result.
KNOWN_GAPS = {"oks_stat_blending_line"}

BAY_FOR_GENERIC_CHECKS = 1
BAY_FOR_OPERATING_MODE = 2
BAY_FOR_ACS_FIELDS = 3
BAY_FOR_VALUE_CHECKS = 4
BATCH_SETTLE_S = 20.0
CLOCK_SKEW_BUFFER_S = 5.0
STEP_DELAY_S = 0.4

# Bursting all ~19 generic signals within a couple of seconds (an earlier
# 0.15s pacing) repeatedly produced a "cutoff" pattern in send order that
# got WORSE across successive fast runs -- Hera's ingestion building up a
# backlog under a burst, not a per-signal problem. Fixed by pacing sends;
# also more realistic (a real plant wouldn't fire 19 telemetry topics
# inside 3 seconds either).
SEND_PACING_S = 1.5

DB_NAME_BY_ENV = {"DEV": "heraplantdatabasedev", "TEST": "heraplantdatabasetest"}

SCENARIOS = [
    {"n": 1, "id": "SIG_REACT_01", "expect": "PASS",
     "name": "Every catalog signal reaches the database",
     "desc": (f"Send all {len(SIGNALS)} signals from signal_catalog.py once each "
              "(bay-scoped ones on DI1), then verify each one produced the expected "
              "row: PlantSignals.Tag=<tag> for TLM signals, Movements.EventName for "
              "the ACS activity signal. 'oks_stat_blending_line' is a known, tracked "
              "exception (see PLANT_SIGNALS_CATALOG.md §8) -- excluded from the "
              "auto-detected result.")},
    {"n": 2, "id": "SIG_REACT_02", "expect": "PASS",
     "name": "operating_mode 0-12 lifecycle -> correct Movements.EventName",
     "desc": ("Play the full 0-12 bay lifecycle (entry -> drop-off -> filling -> "
              "pickup -> exit) on one bay, then assert each mode produced the "
              "EXACT EventName from signal_catalog.OPERATING_MODE_EVENT_NAME -- "
              "not just that some row appeared.")},
    {"n": 3, "id": "SIG_REACT_03", "expect": "PASS",
     "name": "ACS entry pair -> Movements row fields match what was sent",
     "desc": ("Send a full mvt_requested + mvt_completed pair with known "
              "truck_id/driver_id/trailer_id/drop bay, then assert the resulting "
              "Movements row's DriverId/TruckId/TrailerId/DropOffLoadingBayName/"
              "AcsCorrelationId all match exactly -- not just that a mvt_completed "
              "row exists somewhere.")},
    {"n": 4, "id": "SIG_REACT_04", "expect": "PASS",
     "name": "Signal VALUES are captured correctly, not just presence",
     "desc": ("Send fill_rate_meas, flow_rate_mass and RFID_trailer_details with "
              "known values, then assert PlantSignals.Value/Unit (and the RFID "
              "object's own fields) match exactly what was sent. Includes a "
              "regression guard for flow_rate_mass's unit (m3(n)/h, not the "
              "simulator's originally-wrong kg/h).")},
]


@dataclass
class Row:
    label: str
    ok: bool
    detail: str


def _now_utc() -> datetime:
    # Naive UTC on purpose -- SQL Server's datetime2 columns here are naive;
    # see db_check.py / this folder's other scripts for the same note.
    return datetime.utcnow()


def _print_table(rows: list[Row]) -> None:
    w = max((len(r.label) for r in rows), default=20)
    w = max(w, 20)
    for r in rows:
        mark = "PASS" if r.ok else "FAIL"
        print(f"    [{mark}]  {r.label:<{w}}  {r.detail}")


def _settle_and_retry(conn, check_once, first_wait: float = 10.0, second_wait: float = 25.0):
    """Run check_once() (a callable that queries the DB and returns
    (satisfied: bool, *payload)) after first_wait; if not satisfied, wait
    second_wait more and check exactly once again, returning whichever
    result came back last.

    Exists because fill_rate_meas and RFID_trailer_details in particular
    have been observed taking 30+ seconds to land (they also update
    LoadingSessions, not just the raw log -- see PLANT_SIGNALS_CATALOG.md
    §8) -- a single fixed wait shorter than that produces a false FAIL that
    has nothing to do with Hera actually being wrong. SIG_REACT_01 already
    used this two-phase pattern inline; SIG_REACT_03/04 send those same
    slow signal types and need the same resilience (a real run against
    SIG_REACT_04 hit exactly this false-FAIL before this helper existed).
    """
    time.sleep(first_wait)
    result = check_once()
    if result[0]:
        return result
    print(f"    Not all data present after {first_wait:.0f}s -- waiting {second_wait:.0f}s more "
          f"(fill_rate_meas / RFID_trailer_details in particular can take this long)...")
    time.sleep(second_wait)
    return check_once()


def _ask(sc: dict, auto: str, reason: str) -> tuple[str, str]:
    return ask_verdict(
        tc_id=sc["id"], title=sc["name"], expect=sc["expect"],
        auto=auto, reason=reason, index=sc["n"], total=len(SCENARIOS),
        excel_id=sc["id"],
    )


# --------------------------------------------------------------------------
# SIG_REACT_01 -- every catalog signal reaches the database
# --------------------------------------------------------------------------
def _send_all_generic(client: PlantBrokerClient) -> list[tuple[str, str, str]]:
    """Send every catalog signal once. Returns [(key, kind, tag_or_event), ...]
    in send order -- kind is 'plant_signal' or 'movement'."""
    planned: list[tuple[str, str, str]] = []
    for key in sorted(SIGNALS.keys()):
        spec = SIGNALS[key]
        if key == "operating_mode":
            continue  # covered by SIG_REACT_02, different bay/window
        if key == "acs_activity":
            payload, _ = build_acs_event_payload("mvt_requested", {"gate": "ENT"})
            client.publish(spec.topic(), payload, retain=spec.retain)
            planned.append((key, "movement", "mvt_requested"))
        elif key == "rfid_trailer_details":
            payload = build_rfid_trailer_details_payload("1TRL285", 1)
            client.publish(spec.topic(BAY_FOR_GENERIC_CHECKS), payload, retain=spec.retain)
            planned.append((key, "plant_signal", spec.tag))
        else:
            bay = BAY_FOR_GENERIC_CHECKS if spec.bay_scoped else None
            topic = spec.topic(bay) if spec.bay_scoped else spec.topic()
            payload = build_envelope(spec.example_value, value_type=spec.value_type, unit=spec.unit)
            client.publish(topic, payload, retain=spec.retain)
            planned.append((key, "plant_signal", spec.tag))
        time.sleep(SEND_PACING_S)
    return planned


def _check_window(conn, planned, window_start) -> dict[str, tuple[bool, int]]:
    window = db_check.dump_window(conn, since=window_start, tables=("PlantSignals", "Movements"))
    out: dict[str, tuple[bool, int]] = {}
    for key, kind, tag_or_event in planned:
        if kind == "plant_signal":
            matches = [r for r in window["PlantSignals"]
                       if r.Tag == tag_or_event and r.Direction == "PlantToSteering"]
        else:
            matches = [r for r in window["Movements"] if r.EventName == tag_or_event]
        out[key] = (len(matches) > 0, len(matches))
    return out


def run_sig_react_01(client: PlantBrokerClient, conn) -> tuple[str, str]:
    batch_since = _now_utc()
    planned = _send_all_generic(client)
    window_start = batch_since - timedelta(seconds=CLOCK_SKEW_BUFFER_S)

    first_wait, second_wait = 10.0, 20.0
    print(f"    Sent {len(planned)} signals; waiting {first_wait:.0f}s, checking, then giving "
          f"anything still missing up to {second_wait:.0f}s more (some signal types are known "
          f"to land slower than others)...")
    time.sleep(first_wait)
    found = _check_window(conn, planned, window_start)

    still_missing = [p for p in planned if not found[p[0]][0]]
    if still_missing:
        print(f"    {len(still_missing)} still missing after {first_wait:.0f}s "
              f"({', '.join(k for k, _, _ in still_missing)}) -- waiting {second_wait:.0f}s more...")
        time.sleep(second_wait)
        found = _check_window(conn, planned, window_start)

    rows: list[Row] = []
    gap_rows: list[Row] = []
    for key, kind, tag_or_event in planned:
        ok, n = found[key]
        detail = f"{n} row(s)" if ok else "no matching row found"
        row = Row(key, ok, detail)
        (gap_rows if key in KNOWN_GAPS else rows).append(row)

    _print_table(sorted(rows, key=lambda r: r.label))
    if gap_rows:
        print("    -- known, tracked exceptions (not counted against the auto-detected result) --")
        for r in gap_rows:
            status = "now landing (!)" if r.ok else "still absent, as expected"
            print(f"    [n/a ]  {r.label:<20}  {status}")

    n_ok = sum(1 for r in rows if r.ok)
    n_total = len(rows)
    auto = "PASS" if n_ok == n_total else "FAIL"
    reason = f"{n_ok}/{n_total} signals confirmed (excluding {len(gap_rows)} known/tracked exception(s))"
    return auto, reason


# --------------------------------------------------------------------------
# SIG_REACT_02 -- operating_mode 0-12 -> correct Movements.EventName
# --------------------------------------------------------------------------
def run_sig_react_02(client: PlantBrokerClient, conn) -> tuple[str, str]:
    bay = BAY_FOR_OPERATING_MODE
    bay_id = f"DI{bay}"
    seq = S.scenario_full_visit(bay=bay, drop_plate="1TRL167", pick_plate="1TRL167",
                                 flowmeter_values_gs=[40.0])
    since_all = _now_utc()

    mode_send_order: list[int] = []
    for step in seq:
        client.publish(step.topic, step.payload, retain=step.retain)
        if "operating_mode=" in step.note:
            mode_send_order.append(int(step.note.split("operating_mode=")[1].split(" ")[0]))
        time.sleep(STEP_DELAY_S)

    print(f"    Sent modes {mode_send_order} on bay {bay_id}; waiting {BATCH_SETTLE_S:.0f}s, "
          f"then checking Movements...")
    time.sleep(BATCH_SETTLE_S)

    window = db_check.dump_window(conn, since=since_all, tables=("Movements",))
    bay_rows = [r for r in window["Movements"]
                if r.DropOffLoadingBayName == bay_id or r.PickupLoadingBayName == bay_id]
    remaining = list(bay_rows)
    rows: list[Row] = []
    for mode in mode_send_order:
        expected = OPERATING_MODE_EVENT_NAME.get(mode, "<no mapping known>")
        hit = next((r for r in remaining if r.EventName == expected), None)
        if hit is not None:
            remaining.remove(hit)
            rows.append(Row(f"mode {mode:>2} -> {expected}", True, f"found at {hit.Received}"))
        else:
            rows.append(Row(f"mode {mode:>2} -> {expected}", False, "NOT FOUND with this EventName"))

    _print_table(rows)
    if remaining:
        print(f"    Note: {len(remaining)} extra Movements row(s) for {bay_id} not "
              f"attributed to a sent mode: {[r.EventName for r in remaining]}")

    n_ok = sum(1 for r in rows if r.ok)
    auto = "PASS" if n_ok == len(rows) else "FAIL"
    reason = f"{n_ok}/{len(rows)} modes produced their confirmed expected EventName"
    return auto, reason


# --------------------------------------------------------------------------
# SIG_REACT_03 -- ACS entry pair -> Movements row fields match what was sent
# --------------------------------------------------------------------------
def run_sig_react_03(client: PlantBrokerClient, conn) -> tuple[str, str]:
    bay = BAY_FOR_ACS_FIELDS
    bay_id = f"DI{bay}"
    truck_id, driver_id, trailer_id = "ACS-TRK-REACT03", "ACS-DRV-REACT03", "1TRL639"
    corr_id = str(uuid.uuid4())

    acs_topic = SIGNALS["acs_activity"].topic()
    req_payload, _ = build_acs_event_payload("mvt_requested", {"gate": "ENT"}, correlation_id=corr_id)
    completed_data = {
        "gate": "ENT", "driver_id": driver_id, "truck_id": truck_id,
        "trailer_id": trailer_id, "pick_bay_id": "", "drop_bay_id": bay_id,
    }
    done_payload, _ = build_acs_event_payload("mvt_completed", completed_data, correlation_id=corr_id)

    since = _now_utc()
    client.publish(acs_topic, req_payload, retain=False)
    time.sleep(STEP_DELAY_S)
    client.publish(acs_topic, done_payload, retain=False)
    print(f"    Sent ACS entry pair (correlation_id={corr_id[:8]}…, truck={truck_id}, "
          f"driver={driver_id}, trailer={trailer_id}, drop_bay={bay_id})...")

    def _check_once():
        window = db_check.dump_window(conn, since=since, tables=("Movements",))
        hit = next((r for r in window["Movements"]
                    if r.EventName == "mvt_completed"
                    and str(r.AcsCorrelationId).lower() == corr_id.lower()), None)
        return hit is not None, hit

    _, hit = _settle_and_retry(conn, _check_once)

    rows: list[Row] = []
    if hit is None:
        rows.append(Row("mvt_completed row found", False,
                         f"no Movements row with AcsCorrelationId={corr_id}"))
    else:
        rows.append(Row("mvt_completed row found", True, f"found at {hit.Received}"))
        checks = [
            ("DriverId", hit.DriverId, driver_id),
            ("TruckId", hit.TruckId, truck_id),
            ("TrailerId", hit.TrailerId, trailer_id),
            ("DropOffLoadingBayName", hit.DropOffLoadingBayName, bay_id),
        ]
        for field, actual, expected in checks:
            ok = str(actual) == expected
            rows.append(Row(f"{field} == {expected!r}", ok, f"got {actual!r}"))

    _print_table(rows)
    n_ok = sum(1 for r in rows if r.ok)
    auto = "PASS" if n_ok == len(rows) else "FAIL"
    reason = f"{n_ok}/{len(rows)} field check(s) passed"
    return auto, reason


# --------------------------------------------------------------------------
# SIG_REACT_04 -- signal VALUES are captured correctly, not just presence
# --------------------------------------------------------------------------
def run_sig_react_04(client: PlantBrokerClient, conn) -> tuple[str, str]:
    bay = BAY_FOR_VALUE_CHECKS
    fill_value = 77.3
    flow_value = 55.25
    plate, trailer_num = "1TRL285", 1

    since = _now_utc()
    fill_spec = SIGNALS["fill_rate_meas"]
    client.publish(fill_spec.topic(bay), build_envelope(fill_value, value_type="float", unit="g/s"),
                   retain=fill_spec.retain)
    time.sleep(STEP_DELAY_S)

    flow_spec = SIGNALS["flow_rate_mass"]
    client.publish(flow_spec.topic(), build_envelope(flow_value, value_type="float", unit=flow_spec.unit),
                   retain=flow_spec.retain)
    time.sleep(STEP_DELAY_S)

    rfid_spec = SIGNALS["rfid_trailer_details"]
    client.publish(rfid_spec.topic(bay), build_rfid_trailer_details_payload(plate, trailer_num),
                   retain=rfid_spec.retain)

    print(f"    Sent fill_rate_meas={fill_value} g/s (DI{bay}), flow_rate_mass={flow_value} "
          f"{flow_spec.unit}, RFID_trailer_details plate={plate}...")

    def _check_once():
        window = db_check.dump_window(conn, since=since, tables=("PlantSignals",))

        def _latest(tag: str):
            matches = [r for r in window["PlantSignals"] if r.Tag == tag and r.Direction == "PlantToSteering"]
            return max(matches, key=lambda r: r.Received) if matches else None

        found = {"fill": _latest("fill_rate_meas"), "flow": _latest("flow_rate_mass"),
                 "rfid": _latest("RFID_trailer_details")}
        return all(found.values()), found

    _, found = _settle_and_retry(conn, _check_once)
    fill_row, flow_row, rfid_row = found["fill"], found["flow"], found["rfid"]

    rows: list[Row] = []

    if fill_row is None:
        rows.append(Row("fill_rate_meas row found", False, "no matching row"))
    else:
        rows.append(Row("fill_rate_meas.Value", str(fill_row.Value) == str(fill_value),
                         f"expected {fill_value!r}, got {fill_row.Value!r}"))
        rows.append(Row("fill_rate_meas.Unit", str(fill_row.Unit) == "g/s",
                         f"expected 'g/s', got {fill_row.Unit!r}"))

    if flow_row is None:
        rows.append(Row("flow_rate_mass row found", False, "no matching row"))
    else:
        rows.append(Row("flow_rate_mass.Value", str(flow_row.Value) == str(flow_value),
                         f"expected {flow_value!r}, got {flow_row.Value!r}"))
        rows.append(Row(f"flow_rate_mass.Unit == {flow_spec.unit!r}", str(flow_row.Unit) == flow_spec.unit,
                         f"expected {flow_spec.unit!r}, got {flow_row.Unit!r} "
                         f"(regression guard: simulator originally sent 'kg/h', real plant uses "
                         f"'m3(n)/h' -- see PLANT_SIGNALS_CATALOG.md §7)"))

    if rfid_row is None:
        rows.append(Row("RFID_trailer_details row found", False, "no matching row"))
    else:
        try:
            obj = json.loads(rfid_row.Value) if isinstance(rfid_row.Value, str) else rfid_row.Value
        except Exception as exc:
            obj = None
            rows.append(Row("RFID_trailer_details.Value parses as JSON", False, str(exc)))
        if obj is not None:
            rows.append(Row("RFID_trailer_details.license_plate", obj.get("license_plate") == plate,
                             f"expected {plate!r}, got {obj.get('license_plate')!r}"))
            rows.append(Row("RFID_trailer_details.trailer_id", obj.get("trailer_id") == str(trailer_num),
                             f"expected {str(trailer_num)!r}, got {obj.get('trailer_id')!r}"))

    _print_table(rows)
    n_ok = sum(1 for r in rows if r.ok)
    auto = "PASS" if n_ok == len(rows) else "FAIL"
    reason = f"{n_ok}/{len(rows)} value check(s) passed"
    return auto, reason


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", choices=ENV_CHOICES, default=DEFAULT_ENV,
                         help="Broker environment to publish to (default: %(default)s)")
    parser.add_argument("--database", default=None,
                         help="Override the DB to check against (default: inferred from --env)")
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                         help="Resume from scenario N (1-based).")
    parser.add_argument("--excel", default=None, metavar="PATH",
                         help="Report file to write Pass/Fail into. Default: create a "
                              "new timestamped copy under UAT Testing/Reports/.")
    args = parser.parse_args()
    start_from = max(1, args.start_from)

    database = args.database or DB_NAME_BY_ENV.get(args.env, "heraplantdatabasetest")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = HERE / "test_artifacts_signal_reaction" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print("  Plant Communication - Signal Testing -- does Hera correctly capture plant signals?")
    print(f"  Broker env    : {args.env}")
    print(f"  Database      : {database}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*70}")

    conn = db_check.connect(database)
    results: list[dict] = []

    RUNNERS = {
        "SIG_REACT_01": run_sig_react_01,
        "SIG_REACT_02": run_sig_react_02,
        "SIG_REACT_03": run_sig_react_03,
        "SIG_REACT_04": run_sig_react_04,
    }

    with PlantBrokerClient(env=args.env) as client:
        print(f"\n  Connected to broker {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']}\n")

        for sc in SCENARIOS:
            if sc["n"] < start_from:
                continue
            print(f"\n{'─'*70}")
            print(f"  Running {sc['id']} -- {sc['name']}...")
            print(f"{'─'*70}")

            try:
                runner = RUNNERS.get(sc["id"])
                if runner is None:
                    auto, reason = "?", "Unknown scenario ID"
                else:
                    auto, reason = runner(client, conn)
            except Exception as exc:
                import traceback
                print(f"    ERROR: {exc}")
                traceback.print_exc()
                auto, reason = "FAIL", f"ERROR: {exc}"

            result, notes = _ask(sc, auto, reason)
            results.append({**sc, "result": result, "notes": notes, "auto": auto, "reason": reason})

    # -- summary ---------------------------------------------------------
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
        print(f"  {flag}{r['n']}. {r['id']:<14} exp={r['expect']}  got={r['result']}  {r['name']}")
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
                    xlsx_path=args.excel, source="test_signal_reaction.py")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
