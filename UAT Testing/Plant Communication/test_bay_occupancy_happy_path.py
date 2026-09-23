#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_bay_occupancy_happy_path.py
==================================
Phase 5 of "Plant Communication": does a full, realistic
truck visit -- sent entirely over the broker, using only this folder's own
sender, no dependency on hera-virtual-plant -- show up correctly on Hera's
own Plant Supervision > Bay Monitoring > **Bay Occupancy Schedule** block
chart, with the right per-step checklist progression?

Distinct from Phase 4 (`test_bay_monitoring_actual_vs_broker.py`): that
phase checks the "Injection and Loading Profiles" Actual LINE (the flow
rate curve). This phase checks the Bay Occupancy Schedule BLOCK and its
per-occupation movement checklist -- linked (both come from the same
GetBayMonitoringScreenAsync response, `bays` vs `injectionAndLoadingGraph`
being sibling keys) but a different assertion target, per explicit
direction: "we are not talking anymore of the loading bay filling [chart],
even if it is linked, we are looking at the bay occupancy schedule."

The happy path, in order:
  1. ACS entry pair + operating_mode 1/2/3 (Drop-off start / Vehicle
     approach / Drop-off closed) + RFID_trailer_details -- "until drop-off
     end".
  2. operating_mode 4/5/6 (Ready / Authorized / Filling), then
     fill_rate_meas sustained continuously for 30 minutes (reusing
     `sustain()` from `sustain_signal.py`, same mechanism as Phase 4's
     "previous exercise") -- the filling phase itself.
  3. operating_mode 8/9/10 (Fill done / Wait disconnect / Wait pickup),
     then `pickup()` (mode 11, RFID, mode 12, mode 0) and `exit_()` (ACS
     exit pair) -- "the movement that signs the end of operations at the
     bay".

CONFIRMED, 2026-09-23, from hera-virtual-plant's own source (app.py,
backend/virtual_plant/truck_sim.py, hera_mqtt_test_scenario.txt -- three
independent, mutually-consistent sources): operating_mode 1-6 and 8-12 map
to Drop-off start/Vehicle approach/Drop-off closed/Ready/Authorized/
Filling/Fill done/Wait disconnect/Wait pickup/Pickup created/Vehicle
leaving, in that order; mode 7 is NEVER used anywhere in that repo. This
matches `steps.py` exactly -- no bug there, despite an initial suspicion
that its skip of mode 7 was an off-by-one error.

STILL GENUINELY OPEN, and NOT resolvable by reading source (confirmed by
that same investigation): Hera's own Bay Occupancy Schedule checklist (see
`bay_monitoring_capture.BAY_OCCUPATION_CHECKLIST`, confirmed live from a
real captured response) shows 6 distinct labels for the pickup half
(FILLING DONE, WAITING FOR DISCONNECT, PICK-UP START, DISCONNECTED,
VEHICLE LEAVING, PICK-UP END) but the plant simulator only has 5 mode
values (8-12) for that stretch -- a real numeric mismatch. hera-virtual-
plant's source only tells us what the SIMULATOR sends, not how Hera's own
UI derives each of its 16 checklist labels; one of them is plausibly driven
by something other than `operating_mode` (the `RFID_trailer_details` event
`pickup()` already sends between mode 11 and mode 12 is one candidate).
HAPPY_03 below is deliberately built to answer this empirically: every
signal's OWN send timestamp is logged, then correlated against whichever
checklist slot's `time` field lands closest to it -- turning this run into
the mapping-discovery experiment that source-reading alone couldn't finish.

Also confirmed (same investigation): no condition-waiting between sends is
needed -- hera-virtual-plant's own two lifecycle drivers (Streamlit "Send
All" button, and the backend TruckSimulator) both just use a fixed delay
(1.0s and 1.5s respectively) between steps, never waiting for an ack. This
test uses 1.5s (`STEP_DELAY_S`), matching the backend driver.

Three scenarios, run against ONE continuous, realistic visit (same
truck/plate throughout -- do not reorder or skip):

  HAPPY_01  Entry ACS + dropoff (modes 1-3 + RFID). Asserts the first 5
            checklist slots (MOVEMENT REQUESTED x2, DROP-OFF
            START/VEHICLE APPROACH/DROP-OFF END) show status "Success" --
            a real, confirmed assertion (modes 1-3 mapping is settled).

  HAPPY_02  Modes 4/5/6, then fill_rate_meas sustained for 30 minutes.
            Asserts READY/AUTHORIZED/FILLING show "Success" -- also
            confirmed mapping. This scenario takes ~30 minutes to run.

  HAPPY_03  Modes 8/9/10, pickup() (mode 11, RFID, mode 12, mode 0),
            exit_() (ACS exit pair). NOT asserted PASS/FAIL against a
            guessed mapping (see above) -- instead reports, for each of
            the remaining checklist slots, its status/time and the closest
            matching send by timestamp, to finally settle which raw signal
            produces which UI label. Also reports the occupation's overall
            `status` field once presumably complete (not yet confirmed
            what value that becomes).

STATUS, 2026-09-23: run live once (env=dev, bay 2, plate 1TRL167).
HAPPY_01/02 both PASS. HAPPY_03's discovery is now RESOLVED, confirmed by
cross-checking the returned data against a screenshot of the real screen
(times matched exactly, UTC+2): mode 8 -> FILLING DONE, mode 9 -> NO
observable effect on this checklist at all ("WAITING FOR DISCONNECT" stayed
unfilled the whole run, shown as "-" on screen -- not a bug, the confirmed
answer to the earlier "6 labels vs 5 modes" mismatch), mode 10 -> PICK-UP
START, mode 11(+RFID) -> DISCONNECTED, mode 12 -> VEHICLE LEAVING, final
mode 0 -> PICK-UP END. Full table now lives in
`bay_monitoring_capture.BAY_OCCUPATION_CHECKLIST`'s own comment -- read
that first rather than re-deriving. The occupation's overall `status`
field became "Success" despite the one permanently-empty slot. HAPPY_03
is still deliberately left as an observation/report scenario (not
converted to an assertion) since this is only one confirmed run so far.

Run
---
    py test_bay_occupancy_happy_path.py
    py test_bay_occupancy_happy_path.py --from 3

Prompts for the Hera env (dev/test) and the loading bay (1-4) at startup,
same convention as Weekly Nomination / Scheduled Transfers -- hardcode this
file's own `ENV = ""` / `BAY = 0` constants (e.g. `ENV = "test"`,
`BAY = 1`) to skip either prompt.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import steps as S
import ui_capture as ui
from bay_monitoring_capture import (
    BAY_OCCUPATION_CHECKLIST,
    bay_label, capture_bay_monitoring, find_bay, find_occupation, movement_at, poll_until,
    resolve_bay,
)
from broker_client import PlantBrokerClient
from signal_catalog import get as get_signal
from sustain_signal import sustain

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE.parent))
from uat_excel_reporter import record_results, ask_verdict  # noqa: E402

sys.path.insert(0, str(HERE.parent / "Scheduled Transfers"))
import test_scheduled_transfers_profiles as sched_mod  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ENV = ""   # "dev" | "test" | ""  (prompt at startup)
BAY = 0    # 1-4, or 0 to prompt
ENV_TO_BROKER = {"dev": "DEV", "test": "TEST"}

# Confirmed 2026-09-23 from hera-virtual-plant's own two lifecycle drivers
# (Streamlit "Send All" default 1.0s, backend TruckSimulator default 1.5s) --
# neither waits for an ack/condition, both use a plain fixed delay. Using
# the backend driver's value as the more "production-realistic" one.
STEP_DELAY_S = 1.5
FILLING_DURATION_MIN = 30
FILL_SEND_INTERVAL_S = 7.5  # same reasoning as Phase 4 -- see sustain_signal.py
FILL_VALUE_GS = 40.0
SETTLE_FIRST_WAIT_S = 20.0    # quick check first -- catches the common fast case without waiting needlessly
SETTLE_MAX_WAIT_S = 300.0     # 5 min -- confirmed necessary 2026-09-23: watching the real screen live
                               # sometimes showed a real, several-minute delay before a just-sent
                               # signal's effect appeared, more than the original flat 30s wait
                               # accounted for. _get_occupation() below polls up to this long.
SETTLE_POLL_INTERVAL_S = 30.0

TRUCK_ID = "HAPPY-TRK-01"
DRIVER_ID = "HAPPY-DRV-01"
PLATE = S.RECOGNIZED_LICENSE_PLATES[0]  # "1TRL167"

SCENARIOS = [
    {"n": 1, "id": "HAPPY_01", "expect": "PASS",
     "name": "Entry + drop-off (modes 1-3) reach the Bay Occupancy checklist",
     "desc": (f"Send ACS entry pair + operating_mode 1/2/3 + RFID for {PLATE} on the "
              "target bay, then confirm the first 5 checklist slots (2x MOVEMENT "
              "REQUESTED/COMPLETED, DROP-OFF START, VEHICLE APPROACH, DROP-OFF END) show "
              "status 'Success' on Hera's own Bay Occupancy Schedule.")},
    {"n": 2, "id": "HAPPY_02", "expect": "PASS",
     "name": "Ready/Authorized/Filling reach the checklist; 30-min fill sustained",
     "desc": ("Send operating_mode 4/5/6, then sustain fill_rate_meas for "
              f"{FILLING_DURATION_MIN} minutes (same mechanism as Phase 4's sustained "
              "send), then confirm READY/AUTHORIZED/FILLING show status 'Success'. "
              "Takes ~30 minutes to run.")},
    {"n": 3, "id": "HAPPY_03", "expect": "?",
     "name": "[Discovery, not asserted] Wind-down/pickup/exit -- which mode maps to which label?",
     "desc": ("Send operating_mode 8/9/10, pickup() (mode 11, RFID, mode 12, mode 0), and "
              "exit_() (ACS exit pair), then report -- for each remaining checklist slot -- "
              "its status/time and the closest matching send by timestamp. Settles the "
              "still-open question of which raw signal produces FILLING DONE through "
              "PICK-UP END, which hera-virtual-plant's own source could not answer.")},
]


def _ask(sc, auto, reason):
    return ask_verdict(
        tc_id=sc["id"], title=sc["name"], expect=sc["expect"],
        auto=auto, reason=reason, index=sc["n"], total=len(SCENARIOS),
        excel_id=sc["id"],
    )


def _iso_week_year(dt: datetime) -> tuple[int, int]:
    iso = dt.isocalendar()
    return iso[1], iso[0]


def _send_steps(client: PlantBrokerClient, steps: list, send_times: dict) -> None:
    """Publish `steps` (a list of steps.Step) in order, `STEP_DELAY_S` apart,
    recording each one's own send timestamp (UTC) into `send_times` keyed by
    its `.note` -- used by HAPPY_03 to correlate sends against whichever
    checklist slot lights up closest to that time."""
    for step in steps:
        t = datetime.now(timezone.utc)
        ok = client.publish(step.topic, step.payload, retain=step.retain)
        send_times[step.note] = t
        print(f"  [{t.strftime('%H:%M:%S')}]  {step.note}  ok={ok}")
        time.sleep(STEP_DELAY_S)


def _closest_send(send_times: dict, target_iso: str, tol_s: float = 5.0):
    """Return (note, diff_seconds) for whichever entry in `send_times` is
    closest to `target_iso` (a Hera-reported movement time) within `tol_s`,
    or None if nothing is close enough to be a plausible match."""
    try:
        target = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    best = None
    for note, t in send_times.items():
        diff = abs((t - target).total_seconds())
        if diff <= tol_s and (best is None or diff < best[1]):
            best = (note, diff)
    return best


def _get_occupation(page, bay: int, shots_dir: Path, wait_for_indices: list[int] | None = None):
    """Capture the Bay Monitoring screen, retrying via `poll_until()` (up to
    `SETTLE_MAX_WAIT_S` = 5 min) until every checklist slot in
    `wait_for_indices` shows status "Success" -- or, if `wait_for_indices`
    is None, just a single capture with no retry. Returns
    (occupation_dict_or_None, detail_str_if_none); on timeout, returns the
    LAST captured occupation anyway (not None) so the caller can still
    report exactly what state things were actually in, rather than losing
    that information to a bare timeout error."""
    err_holder: dict = {}

    def _check():
        week, year = _iso_week_year(datetime.now())
        body, meta = capture_bay_monitoring(page, sched_mod, week, year, bay, shots_dir)
        if body is None:
            err_holder["err"] = f"GetBayMonitoringScreenAsync not identified (captured: {meta['captured_urls']})"
            return False, None
        bay_entry = find_bay(body, bay)
        if bay_entry is None:
            err_holder["err"] = f"{bay_label(bay)} not found in response bays[] (see raw_responses/)"
            return False, None
        occ = find_occupation(bay_entry, license_plate=PLATE, scheduling_type="Actual")
        if occ is None:
            err_holder["err"] = f"No 'Actual' occupation found for plate {PLATE} on {bay_label(bay)}"
            return False, None
        err_holder["err"] = None
        if wait_for_indices is None:
            return True, occ
        ready = all(movement_at(occ, i)[0] == "Success" for i in wait_for_indices)
        return ready, occ

    if wait_for_indices is None:
        satisfied, occ = _check()
    else:
        print(f"  Waiting for the screen to reflect what was just sent (up to "
              f"{SETTLE_MAX_WAIT_S:.0f}s, checking every {SETTLE_POLL_INTERVAL_S:.0f}s "
              f"after an initial {SETTLE_FIRST_WAIT_S:.0f}s)...")
        satisfied, occ = poll_until(_check, first_wait_s=SETTLE_FIRST_WAIT_S,
                                     max_wait_s=SETTLE_MAX_WAIT_S,
                                     poll_interval_s=SETTLE_POLL_INTERVAL_S)
        if not satisfied and occ is not None:
            print(f"  [WARN] Not all expected slots reached 'Success' within "
                  f"{SETTLE_MAX_WAIT_S:.0f}s -- reporting the last captured state anyway.")

    if occ is None:
        return None, err_holder.get("err", "unknown capture error")
    (shots_dir / "occupation.json").write_text(json.dumps(occ, indent=2), encoding="utf-8")
    return occ, None


# --------------------------------------------------------------------------
# HAPPY_01 -- entry + drop-off
# --------------------------------------------------------------------------
def run_happy_01(page, client: PlantBrokerClient, bay: int, run_dir: Path, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    send_times: dict = {}

    print(f"  Sending ACS entry + drop-off for {PLATE} on {bay_label(bay)}...")
    _send_steps(client, S.entry(TRUCK_ID, DRIVER_ID, PLATE, bay) + S.dropoff(bay, PLATE), send_times)
    (run_dir / "send_times_happy_01.json").write_text(
        json.dumps({k: v.isoformat() for k, v in send_times.items()}, indent=2), encoding="utf-8")

    expected_idx = range(0, 5)  # 2x ACS entry, DROP-OFF START/VEHICLE APPROACH/DROP-OFF END
    occ, err = _get_occupation(page, bay, shots_dir, wait_for_indices=list(expected_idx))
    if occ is None:
        print(f"  {err}")
        return "SKIP", err

    rows = []
    for idx in expected_idx:
        status, t, label = movement_at(occ, idx)
        rows.append((label, status, t))
        print(f"    [{status}] {label}  ({t})")

    n_ok = sum(1 for _, s, _ in rows if s == "Success")
    auto = "PASS" if n_ok == len(rows) else "FAIL"
    reason = f"{n_ok}/{len(rows)} checklist slot(s) confirmed 'Success': " + \
             "; ".join(f"{label}={status}" for label, status, _ in rows)
    return auto, reason


# --------------------------------------------------------------------------
# HAPPY_02 -- ready/authorized/filling + 30-min sustained fill
# --------------------------------------------------------------------------
def run_happy_02(page, client: PlantBrokerClient, bay: int, run_dir: Path, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    send_times: dict = {}
    mode_sig = get_signal("operating_mode")

    print("  Sending operating_mode 4 (Ready), 5 (Authorized), 6 (Filling)...")
    for m in (4, 5, 6):
        t = datetime.now(timezone.utc)
        ok = client.publish(mode_sig.topic(bay), S.build_operating_mode_payload(m), retain=mode_sig.retain)
        note = f"DI{bay} operating_mode={m}"
        send_times[note] = t
        print(f"  [{t.strftime('%H:%M:%S')}]  {note}  ok={ok}")
        time.sleep(STEP_DELAY_S)

    fill_spec = get_signal("fill_rate_meas")
    start = datetime.now().astimezone()
    end = start + timedelta(minutes=FILLING_DURATION_MIN)
    print(f"  Sustaining fill_rate_meas={FILL_VALUE_GS} g/s for {FILLING_DURATION_MIN} min "
          f"({start.strftime('%H:%M:%S')} -> {end.strftime('%H:%M:%S')})...")
    log_path = run_dir / "sent_log_happy_02_filling.csv"
    with log_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_utc", "ts_local", "value", "ok"])
        sent = sustain(client, fill_spec.topic(bay), fill_spec, FILL_VALUE_GS, start, end,
                        FILL_SEND_INTERVAL_S, csv_writer=w)
    print(f"  Sent {sent} fill_rate_meas messages. Log: {log_path}")

    (run_dir / "send_times_happy_02.json").write_text(
        json.dumps({k: v.isoformat() for k, v in send_times.items()}, indent=2), encoding="utf-8")

    expected_idx = range(5, 8)  # READY, AUTHORIZED, FILLING
    occ, err = _get_occupation(page, bay, shots_dir, wait_for_indices=list(expected_idx))
    if occ is None:
        print(f"  {err}")
        return "SKIP", err

    rows = []
    for idx in expected_idx:
        status, t, label = movement_at(occ, idx)
        rows.append((label, status, t))
        print(f"    [{status}] {label}  ({t})")

    n_ok = sum(1 for _, s, _ in rows if s == "Success")
    auto = "PASS" if n_ok == len(rows) else "FAIL"
    reason = f"{n_ok}/{len(rows)} checklist slot(s) confirmed 'Success' " + \
             f"(after a {FILLING_DURATION_MIN}-min sustained fill, {sent} sends): " + \
             "; ".join(f"{label}={status}" for label, status, _ in rows)
    return auto, reason


# --------------------------------------------------------------------------
# HAPPY_03 -- wind-down + pickup + exit -- discovery, not asserted
# --------------------------------------------------------------------------
def run_happy_03(page, client: PlantBrokerClient, bay: int, run_dir: Path, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    send_times: dict = {}
    mode_sig = get_signal("operating_mode")

    print("  Sending operating_mode 8 (Fill done), 9 (Wait disconnect), 10 (Wait pickup)...")
    for m in (8, 9, 10):
        t = datetime.now(timezone.utc)
        ok = client.publish(mode_sig.topic(bay), S.build_operating_mode_payload(m), retain=mode_sig.retain)
        note = f"DI{bay} operating_mode={m}"
        send_times[note] = t
        print(f"  [{t.strftime('%H:%M:%S')}]  {note}  ok={ok}")
        time.sleep(STEP_DELAY_S)

    print("  Sending pickup() (mode 11, RFID, mode 12, mode 0)...")
    _send_steps(client, S.pickup(bay, PLATE), send_times)

    print("  Sending exit_() (ACS exit pair)...")
    _send_steps(client, S.exit_(TRUCK_ID, DRIVER_ID, bay, pick_bay=bay, pick_trailer_id=PLATE), send_times)

    (run_dir / "send_times_happy_03.json").write_text(
        json.dumps({k: v.isoformat() for k, v in send_times.items()}, indent=2), encoding="utf-8")

    # Confirmed 2026-09-23 (two live runs now): index 9 ("WAITING FOR
    # DISCONNECT") NEVER becomes "Success" with this signal set -- so it's
    # excluded from the wait condition (waiting on it would always time out
    # the full 5 minutes for no reason) but still reported below.
    all_idx = range(8, 16)  # FILLING DONE .. PICK-UP END, then exit ACS x2
    wait_idx = [i for i in all_idx if i != 9]
    occ, err = _get_occupation(page, bay, shots_dir, wait_for_indices=wait_idx)
    if occ is None:
        print(f"  {err}")
        return "SKIP", err

    print("  Checklist state for the confirmed pickup/exit mapping (see "
          "bay_monitoring_capture.BAY_OCCUPATION_CHECKLIST):")
    correlations = []
    for idx in all_idx:
        status, t, label = movement_at(occ, idx)
        match = _closest_send(send_times, t) if (status == "Success" and t) else None
        match_str = f"  <- matches send {match[0]!r} ({match[1]:.1f}s away)" if match else ""
        print(f"    [{status}] {label}  time={t}{match_str}")
        correlations.append({"label": label, "status": status, "time": t,
                              "closest_send": match[0] if match else None,
                              "diff_s": round(match[1], 1) if match else None})

    (shots_dir / "mapping_correlation.json").write_text(
        json.dumps(correlations, indent=2), encoding="utf-8")

    overall_status = occ.get("status")
    print(f"  Occupation's overall 'status' field once wind-down/pickup/exit sent: {overall_status!r}")

    n_ok = sum(1 for i in wait_idx if movement_at(occ, i)[0] == "Success")
    n_matched = sum(1 for c in correlations if c["closest_send"])
    return "SKIP", (f"Observation only (mapping is now confirmed, see BAY_OCCUPATION_CHECKLIST, "
                     f"but this scenario is still left unasserted pending a 3rd confirming run): "
                     f"{n_ok}/{len(wait_idx)} expected slot(s) reached 'Success', {n_matched}/{len(all_idx)} "
                     f"matched to a specific send by timestamp -- see mapping_correlation.json. "
                     f"Occupation status={overall_status!r}.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                         help="Resume from scenario N (1-based). NOTE: HAPPY_02/03 assume "
                              "HAPPY_01's entry/drop-off already happened for the same "
                              "truck/plate on this bay -- resuming past scenario 1 without "
                              "that having actually run will likely find no 'Actual' "
                              "occupation to match against.")
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
    bay = resolve_bay(BAY)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = HERE / "test_artifacts_bay_occupancy_happy_path" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print("  Plant Communication -- Phase 5: Bay Occupancy Schedule happy path")
    print(f"  Hera env      : {env}  ({base_url})")
    print(f"  Broker env    : {broker_env}")
    print(f"  Bay           : {bay_label(bay)}")
    print(f"  Truck/plate   : {TRUCK_ID} / {PLATE}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*70}")

    results: list[dict] = []

    with sync_playwright() as pw:
        session_dir = run_dir.parent / "playwright_session"
        ctx, page = ui.open_logged_in_session(pw, base_url, session_dir)

        with PlantBrokerClient(env=broker_env) as client:
            print(f"  Connected to broker {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']}\n")

            RUNNERS = {
                "HAPPY_01": lambda shots_dir: run_happy_01(page, client, bay, run_dir, shots_dir),
                "HAPPY_02": lambda shots_dir: run_happy_02(page, client, bay, run_dir, shots_dir),
                "HAPPY_03": lambda shots_dir: run_happy_03(page, client, bay, run_dir, shots_dir),
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
        print(f"  {flag}{r['n']}. {r['id']:<10} exp={r['expect']}  got={r['result']}  {r['name']}")
        print(f"       auto-detected: {r['auto']} -- {r['reason']}")
    print(f"\n  PASS: {ok}   FAIL: {fail}   OTHER (skip/inconclusive): {other}")

    csv_path = run_dir / "test_results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["n", "id", "name", "expect", "result", "auto", "reason", "notes"])
        for r in results:
            wr.writerow([r["n"], r["id"], r["name"], r["expect"], r["result"], r["auto"], r["reason"], r["notes"]])
    print(f"\n  Results: {csv_path}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_bay_occupancy_happy_path.py")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
