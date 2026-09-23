#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_bay_monitoring_actual_vs_broker.py
=========================================
Phase 4 of "Plant Communication": does the value we send
over the broker actually show up correctly on Hera's own Plant Supervision >
**Bay Monitoring** screen's "Injection and Loading Profiles" chart -- the
"Actual" line, one 15-minute bucket at a time -- for a given loading bay?

This grew out of a manual exploration on 2026-09-21 (`sustain_signal.py`,
same folder) that already found real problems: a single short burst only
ever lands in one 15-min bucket and can still come back `null`; a bucket
covering a longer, continuous send came back looking DILUTED, apparently
averaged together with hera-virtual-plant's own automatic ~30s zero-tick in
DEV. That exploration sent signals and logged them, but never closed the
loop by pulling the screen's own data back and comparing -- this script is
that missing other half, turned into a repeatable, env-agnostic test.

Why this can be simpler than Phase 3 (`test_plant_ui_reaction.py`,
MQTT Stream tab): that screen is LIVE-ONLY (must attach capture before
sending). Bay Monitoring's Actual chart is inherently historical/bucketed --
you can send first, then load the screen afterward. No pre-attached capture
needed, just a network-response listener at capture time (same pattern as
`test_bay_occupancy_vs_hera.py`'s own BayOccupancyScreen capture, same
folder).

Two scenarios, deliberately different in what they can assert:

  BAYMON_ACT_01  Layout only. Login, open Bay Monitoring, confirm both
                 chart headings and the week-navigator are present, and
                 that the bay selector can be operated. No signal sent.

  BAYMON_ACT_02  THE real assertion, and formula-agnostic on purpose: send
                 ONE constant, known value continuously (paced 8s apart, the
                 mitigation `sustain_signal.py` already found necessary to
                 out-run DEV's automatic zero-tick) across exactly TWO full,
                 wall-clock-aligned 15-minute buckets. Because the input is
                 constant for the WHOLE bucket, any sane aggregation
                 (average / last-write / first-write) should reproduce the
                 same value -- this scenario does not need to know Hera's
                 real aggregation formula to make a fair assertion, unlike
                 a partial-bucket send. Both buckets' Actual values are
                 compared to the sent value within a tolerance; a bucket
                 that comes back `null` or comes back present but clearly
                 DILUTED (a fraction of the expected value, matching the
                 2026-09-21 finding) is reported as its own distinct outcome
                 rather than folded into a generic FAIL, since those are
                 different failure modes worth telling apart.

  BAYMON_ACT_03  Bonus / discovery, NOT asserted pass/fail against one
                 assumed formula (no spec confirms which one Hera uses):
                 send two clearly different values back-to-back within a
                 SINGLE isolated bucket (first half / second half) and
                 report which of {first-half, second-half, average, sum}
                 the observed Actual value is closest to. Purely an
                 observation, same convention as this folder's
                 `test_edge_case_*.py` scripts.

STATUS, 2026-09-23: BAYMON_ACT_01/02 have now been run live once (env=dev).
The endpoint guess was CONFIRMED correct
(`GetBayMonitoringScreenAsync`), but the response-shape guess in
`extract_actual_points()` was WRONG and has since been fixed against a real
captured body: `injectionAndLoadingGraph.profileGraphLines` is a FLAT list
of `{name, subName, points}` line objects (one per bay per series, e.g.
`{"name": "Loading Bay 1", "subName": "Actual", "points": {...}}`), not a
nested per-bay structure -- see `bay_monitoring_capture.py`'s docstring for
the confirmed shape. `points` is keyed by an exact ISO-8601 **UTC**
timestamp (15-min spaced) -> value or `null`. The bay-selector dropdown
(`select_bay()`) is still an unconfirmed guess -- not yet exercised because
BAYMON_ACT_01's own capture already found the right response regardless
(the default bay shown was already correct for this run). A second full
run with the shape fix in place hasn't happened yet -- that's the natural
next step, to confirm BAYMON_ACT_02 actually asserts PASS/FAIL correctly
now rather than SKIPping on a parsing failure.

Also fixed, 2026-09-23 (same finding as the Bay Occupancy happy-path
test): the flat `SETTLE_BUFFER_S` (90s) wait before pulling the screen
wasn't always enough -- watching the real screen live sometimes showed a
real, several-minute delay before a just-sent value appeared. Both
BAYMON_ACT_02/03 now use `_wait_for_bucket()`, which polls (re-capturing
each time) for up to 5 minutes until the relevant bucket's Actual value is
non-null, rather than a single fixed sleep + one capture.

Needs a real interactive MFA login each run, which this session's own tool
sandbox cannot sit through (no real stdin) -- someone runs this from an
actual terminal, same as `test_plant_ui_reaction.py` and
`test_bay_occupancy_vs_hera.py` (both same folder).

Run
---
    py test_bay_monitoring_actual_vs_broker.py
    py test_bay_monitoring_actual_vs_broker.py --from 2

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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

import ui_capture as ui
from bay_monitoring_capture import (
    SCHEDULE_HEADING, PROFILE_HEADING, bay_label, resolve_bay,
    capture_bay_monitoring, extract_actual_points, lookup_bucket_value, poll_until,
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
LOCAL_TZ = ZoneInfo("Europe/Brussels")

BUCKET_MIN = 15
SEND_INTERVAL_S = 7.5  # matches sustain_signal.py's default. Chosen (2026-09-23,
                        # over the original 8s) because it divides a 900s/15-min
                        # bucket evenly (120 sends/bucket) -- a boundary-aligned
                        # start therefore lands a send exactly ON every later
                        # quarter-hour mark too, not just near it (900/8=112.5,
                        # off by ~4s). Also still fast enough to out-run DEV's
                        # automatic ~30s zero-tick. Landing on the mark also
                        # needed sustain()'s absolute-target-time scheduling
                        # fix -- see its docstring -- a relative sleep() drifts
                        # under ordinary per-publish overhead regardless of
                        # which interval value is chosen.
SETTLE_FIRST_WAIT_S = 30.0    # quick check first -- catches the common fast case without waiting needlessly
SETTLE_MAX_WAIT_S = 300.0     # 5 min -- confirmed necessary 2026-09-23 (same finding as the happy-path
                               # test): watching the real screen live sometimes showed a real,
                               # several-minute delay before a just-sent value appeared, more than the
                               # original flat 90s wait accounted for.
SETTLE_POLL_INTERVAL_S = 30.0

VALUE_TOL_PCT = 0.03  # 3%: same-signal comparison, no unit/segment ambiguity
DILUTED_THRESHOLD = 0.5  # an Actual reading below this fraction of the sent
                          # value (but not null) is reported as "diluted",
                          # not lumped into a generic mismatch -- matches the
                          # 2026-09-21 finding of an averaged-with-zero-tick
                          # bucket in DEV

SCENARIOS = [
    {"n": 1, "id": "BAYMON_ACT_01", "expect": "PASS",
     "name": "Open Bay Monitoring tab, confirm layout",
     "desc": ("Login, open Plant Supervision > Bay Monitoring, confirm the 'Bay Occupancy "
              "Schedule' and 'Injection and Loading Profiles' headings and the week "
              "navigator are present, and that the bay selector can be operated. "
              "No signal sent.")},
    {"n": 2, "id": "BAYMON_ACT_02", "expect": "PASS",
     "name": "Sustained constant value across 2 full buckets matches Actual",
     "desc": ("Send one constant, known value every 8s across exactly two full, "
              "wall-clock-aligned 15-min buckets, then pull the Bay Monitoring screen "
              "and compare both buckets' Actual value to what was sent (formula-agnostic: "
              "a constant-for-the-whole-bucket input should reproduce under any sane "
              "aggregation). Null or diluted buckets are reported as their own distinct "
              "outcome, not folded into a generic mismatch.")},
    {"n": 3, "id": "BAYMON_ACT_03", "expect": "?",
     "name": "[Discovery, not asserted] Which aggregation formula does a split bucket use?",
     "desc": ("Send two clearly different values, first half / second half of ONE isolated "
              "bucket, then report which candidate formula (first-half, second-half, "
              "average, sum) the observed Actual value is closest to. Observation only -- "
              "no spec confirms Hera's real aggregation, so this is never asserted PASS/FAIL "
              "against one assumed answer.")},
]


def _ask(sc, auto, reason):
    return ask_verdict(
        tc_id=sc["id"], title=sc["name"], expect=sc["expect"],
        auto=auto, reason=reason, index=sc["n"], total=len(SCENARIOS),
        excel_id=sc["id"],
    )


# --------------------------------------------------------------------------
# Bucket-boundary math
# --------------------------------------------------------------------------
def next_bucket_boundary(now: datetime, min_lead_s: float = 90.0) -> datetime:
    """Round `now` up to the next 15-min wall-clock boundary that is at
    least `min_lead_s` away -- gives enough time to finish setting up
    capture/UI state before the first send goes out."""
    now = now.astimezone(LOCAL_TZ)
    candidate = now.replace(second=0, microsecond=0)
    bucket_idx = candidate.minute // BUCKET_MIN + 1
    add_h, minute = divmod(bucket_idx * BUCKET_MIN, 60)
    candidate = (candidate + timedelta(hours=add_h)).replace(minute=minute)
    while (candidate - now).total_seconds() < min_lead_s:
        candidate += timedelta(minutes=BUCKET_MIN)
    return candidate


def _iso_week_year(dt: datetime) -> tuple[int, int]:
    iso = dt.isocalendar()
    return iso[1], iso[0]


def _sent_log_writer(path: Path):
    f = path.open("w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["ts_utc", "ts_local", "value", "ok"])
    return f, w




# --------------------------------------------------------------------------
# BAYMON_ACT_01
# --------------------------------------------------------------------------
def run_baymon_01(page, week: int, year: int, bay: int, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _, meta = capture_bay_monitoring(page, sched_mod, week, year, bay, shots_dir)

    schedule_ok = ui.text_present(page, SCHEDULE_HEADING, timeout_ms=5000)
    profile_ok = ui.text_present(page, PROFILE_HEADING, timeout_ms=5000)
    print(f"  '{SCHEDULE_HEADING}' present: {schedule_ok}")
    print(f"  '{PROFILE_HEADING}' present: {profile_ok}")
    print(f"  Tab clicked: {meta['tab_clicked']}  Bay selected: {meta['bay_selected']}  "
          f"Week nav ok: {meta['week_ok']}")
    print(f"  'baymonitoring' response(s) captured: {len(meta['captured_urls'])}")
    for u in meta["captured_urls"]:
        print(f"    - {u}")

    ok = meta["tab_clicked"] and schedule_ok and profile_ok
    auto = "PASS" if ok else "FAIL"
    reason = (f"tab_clicked={meta['tab_clicked']}, schedule_heading={schedule_ok}, "
              f"profile_heading={profile_ok}, bay_selected={meta['bay_selected']}, "
              f"week_ok={meta['week_ok']}, responses_captured={len(meta['captured_urls'])}")
    if not meta["bay_selected"]:
        reason += " -- bay selector NOT confirmed operable, see select_bay() in bay_monitoring_capture.py"
    return auto, reason


# --------------------------------------------------------------------------
# BAYMON_ACT_02 -- the real, formula-agnostic assertion
# --------------------------------------------------------------------------
def _wait_for_bucket(page, bay: int, week: int, year: int, shots_dir: Path, target_bucket: datetime):
    """Poll (up to SETTLE_MAX_WAIT_S = 5 min) until `target_bucket`'s Actual
    value is non-null, re-capturing the screen each attempt. Confirmed
    necessary, 2026-09-23: watching the real screen live sometimes showed a
    real, several-minute delay before a just-sent value appeared -- longer
    than the original flat 90s wait accounted for (same finding as the Bay
    Occupancy happy-path test). Returns (points_dict_or_None,
    detail_str_if_none) -- on timeout, returns the LAST captured points
    anyway so the caller can still report the real state, not just an error."""
    err_holder: dict = {}

    def _check():
        body, meta = capture_bay_monitoring(page, sched_mod, week, year, bay, shots_dir)
        if body is None:
            err_holder["err"] = f"GetBayMonitoringScreenAsync not identified (captured: {meta['captured_urls']})"
            return False, None
        points = extract_actual_points(body, bay)
        if not points:
            err_holder["err"] = (f"GetBayMonitoringScreenAsync response captured but no 'Actual' line "
                                  f"found for {bay_label(bay)} under injectionAndLoadingGraph.profileGraphLines.")
            return False, None
        err_holder["err"] = None
        _, value = lookup_bucket_value(points, target_bucket)
        return value is not None, points

    print(f"  Waiting for the screen to reflect what was just sent (up to {SETTLE_MAX_WAIT_S:.0f}s, "
          f"checking every {SETTLE_POLL_INTERVAL_S:.0f}s after an initial {SETTLE_FIRST_WAIT_S:.0f}s)...")
    satisfied, points = poll_until(_check, first_wait_s=SETTLE_FIRST_WAIT_S,
                                    max_wait_s=SETTLE_MAX_WAIT_S, poll_interval_s=SETTLE_POLL_INTERVAL_S)
    if points is None:
        return None, err_holder.get("err", "unknown capture error")
    if not satisfied:
        print(f"  [WARN] Target bucket still null/missing after {SETTLE_MAX_WAIT_S:.0f}s -- "
              "reporting the last captured state anyway.")
    return points, None


def run_baymon_02(page, client: PlantBrokerClient, bay: int, run_dir: Path, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    value = 66.6  # matches sustain_signal.py's own default -- far from 0, so
                  # dilution-by-zero-tick is obvious rather than invisible
    spec = get_signal("fill_rate_meas")
    topic = spec.topic(bay)

    bucket1_start = next_bucket_boundary(datetime.now())
    bucket2_start = bucket1_start + timedelta(minutes=BUCKET_MIN)
    window_end = bucket2_start + timedelta(minutes=BUCKET_MIN)

    print(f"  Sending fill_rate_meas={value} g/s on bay {bay} ({topic}) continuously,")
    print(f"  every {SEND_INTERVAL_S}s, across two full buckets:")
    print(f"    bucket 1: {bucket1_start.strftime('%H:%M')} -> {bucket2_start.strftime('%H:%M')}")
    print(f"    bucket 2: {bucket2_start.strftime('%H:%M')} -> {window_end.strftime('%H:%M')}")

    log_path = run_dir / "sent_log_baymon_02.csv"
    f, w = _sent_log_writer(log_path)
    try:
        sent = sustain(client, topic, spec, value, bucket1_start, window_end, SEND_INTERVAL_S,
                        csv_writer=w)
    finally:
        f.close()
    print(f"  Sent {sent} messages. Log: {log_path}")

    week, year = _iso_week_year(bucket1_start)
    points, err = _wait_for_bucket(page, bay, week, year, shots_dir, bucket2_start)
    if points is None:
        print(f"  {err}")
        return "SKIP", err
    (shots_dir / "extracted_actual_points.json").write_text(
        json.dumps(points, indent=2), encoding="utf-8")

    rows = []
    for label, bucket_start in (("bucket 1", bucket1_start), ("bucket 2", bucket2_start)):
        key, actual = lookup_bucket_value(points, bucket_start)
        if key is None:
            rows.append((label, "MISSING",
                         f"no Actual point found near {bucket_start.strftime('%H:%M')} local"))
            continue
        if actual is None:
            rows.append((label, "NULL", f"point {key} exists but value is null -- matches the "
                                         "2026-09-21 null-bucket finding"))
            continue
        try:
            actual_f = float(actual)
        except (TypeError, ValueError):
            rows.append((label, "UNPARSEABLE", f"{key}: value={actual!r} is not numeric"))
            continue
        if actual_f < value * DILUTED_THRESHOLD:
            rows.append((label, "DILUTED", f"{key}: expected ~{value}, got {actual_f} -- less than "
                                            f"{DILUTED_THRESHOLD:.0%} of expected, consistent with "
                                            "averaging against hera-virtual-plant's own automatic "
                                            "zero-tick (2026-09-21 finding)"))
            continue
        within_tol = abs(actual_f - value) <= value * VALUE_TOL_PCT
        rows.append((label, "MATCH" if within_tol else "MISMATCH",
                     f"{key}: expected {value}, got {actual_f} (tol {VALUE_TOL_PCT:.0%})"))

    print("  Results:")
    for label, status, detail in rows:
        print(f"    [{status:<10}] {label}: {detail}")

    n_match = sum(1 for _, s, _ in rows if s == "MATCH")
    auto = "PASS" if n_match == len(rows) else "FAIL"
    reason = f"{n_match}/{len(rows)} bucket(s) matched sent value {value} within {VALUE_TOL_PCT:.0%}; " + \
             "; ".join(f"{label}={status}" for label, status, _ in rows)
    return auto, reason


# --------------------------------------------------------------------------
# BAYMON_ACT_03 -- discovery only, never asserted
# --------------------------------------------------------------------------
def run_baymon_03(page, client: PlantBrokerClient, bay: int, run_dir: Path, shots_dir: Path):
    shots_dir.mkdir(parents=True, exist_ok=True)
    value_a, value_b = 20.0, 90.0
    spec = get_signal("fill_rate_meas")
    topic = spec.topic(bay)

    bucket_start = next_bucket_boundary(datetime.now())
    bucket_mid = bucket_start + timedelta(minutes=BUCKET_MIN / 2)
    bucket_end = bucket_start + timedelta(minutes=BUCKET_MIN)

    print(f"  Bucket {bucket_start.strftime('%H:%M')} -> {bucket_end.strftime('%H:%M')}: "
          f"sending {value_a} for the first half, {value_b} for the second half.")

    log_path = run_dir / "sent_log_baymon_03.csv"
    f, w = _sent_log_writer(log_path)
    try:
        sent_a = sustain(client, topic, spec, value_a, bucket_start, bucket_mid, SEND_INTERVAL_S, csv_writer=w)
        sent_b = sustain(client, topic, spec, value_b, bucket_mid, bucket_end, SEND_INTERVAL_S, csv_writer=w)
    finally:
        f.close()
    print(f"  Sent {sent_a} @ {value_a} then {sent_b} @ {value_b}. Log: {log_path}")

    week, year = _iso_week_year(bucket_start)
    points, err = _wait_for_bucket(page, bay, week, year, shots_dir, bucket_start)
    if points is None:
        return "SKIP", err
    (shots_dir / "extracted_actual_points.json").write_text(json.dumps(points, indent=2), encoding="utf-8")
    key, value_at_bucket = lookup_bucket_value(points, bucket_start)
    if key is None or value_at_bucket is None:
        return "SKIP", f"No usable Actual point found for the bucket (key={key}, value={value_at_bucket})"

    try:
        observed = float(value_at_bucket)
    except (TypeError, ValueError):
        return "SKIP", f"Actual value not numeric: {value_at_bucket!r}"

    candidates = {
        "first-half-only": value_a,
        "second-half-only (last write wins)": value_b,
        "average": (value_a + value_b) / 2,
        "sum": value_a + value_b,
    }
    closest = min(candidates.items(), key=lambda kv: abs(kv[1] - observed))
    print(f"  Observed Actual = {observed}")
    for name, expected in candidates.items():
        print(f"    {'-> ' if name == closest[0] else '   '}{name}: {expected}")

    return "SKIP", (f"Observation only, not asserted: observed Actual={observed}, closest candidate "
                     f"formula = '{closest[0]}' (predicts {closest[1]}). Candidates: "
                     f"{ {k: v for k, v in candidates.items()} }")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                         help="Resume from scenario N (1-based).")
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
    run_dir = HERE / "test_artifacts_bay_monitoring_actual" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print("  Plant Communication -- Phase 4: Bay Monitoring Actual vs. broker")
    print(f"  Hera env      : {env}  ({base_url})")
    print(f"  Broker env    : {broker_env}"
          + ("  [known to carry an automatic ~30s zero-tick, see 2026-09-21 finding]"
             if env == "dev" else ""))
    print(f"  Bay           : {bay_label(bay)}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*70}")

    results: list[dict] = []

    with sync_playwright() as pw:
        session_dir = run_dir.parent / "playwright_session"
        ctx, page = ui.open_logged_in_session(pw, base_url, session_dir)

        with PlantBrokerClient(env=broker_env) as client:
            print(f"  Connected to broker {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']}\n")

            today = datetime.now()
            week, year = _iso_week_year(today.astimezone(LOCAL_TZ))

            RUNNERS = {
                "BAYMON_ACT_01": lambda shots_dir: run_baymon_01(page, week, year, bay, shots_dir),
                "BAYMON_ACT_02": lambda shots_dir: run_baymon_02(page, client, bay, run_dir, shots_dir),
                "BAYMON_ACT_03": lambda shots_dir: run_baymon_03(page, client, bay, run_dir, shots_dir),
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
        print(f"  {flag}{r['n']}. {r['id']:<15} exp={r['expect']}  got={r['result']}  {r['name']}")
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
                    xlsx_path=args.excel, source="test_bay_monitoring_actual_vs_broker.py")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
