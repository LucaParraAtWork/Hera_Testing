#!/usr/bin/env python3
"""
test_bay_occupancy_vs_hera.py
================================
Compares HERA's own Bay Occupancy screen against what was ACTUALLY
dispatched to the plant -- read directly from heraplantdatabasedev's
dbo.DispatchedBayOccupationSessions (see check_bay_occupancy.py). This is
the occupancy-side counterpart to test_dispatch_vs_hera_profiles.py.

Bay matching (the hard part of that other script) is NOT a problem here:
HERA's BayOccupancyScreen response gives each bay an "id" field that is
the EXACT SAME GUID as the DB's LoadingBayId (case differs only) --
confirmed live on 2026-09-16 against a real capture:
    DB   LoadingBayId = 5348EDBD-26E5-4E49-A948-2D9DAF3D0E47
    HERA bay.id        = 5348edbd-26e5-4e49-a948-2d9daf3d0e47  (label "Loading Bay 1")
No segment-timing heuristics needed -- just match on this id, case-insensitive.

Also confirmed live: HERA's bayOccupations use type strings "DropOff",
"Filling", "PickUp" (NOT "Drop"/"Pick" as an older comment elsewhere in
this project assumed) -- these map onto the DB row's three window pairs:
    DB StartDrop/EndDrop              <-> HERA type "DropOff"
    DB StartFillingWindow/EndFillingWindow <-> HERA type "Filling"
    DB StartPickup/EndPickup          <-> HERA type "PickUp"

Scenarios
---------
  OCC_CMP_01  Capture both sides for the most recent dispatch batch:
                - Query the DB for every DispatchedBayOccupationSessions row
                  sharing the latest [Created] timestamp (see
                  check_bay_occupancy.py -- same "whole batch" semantics).
                - Compute the ISO week (Europe/Brussels local time) of the
                  earliest session and navigate HERA's Bay Occupancy screen
                  there, capturing BayOccupancyScreen/GetBayOccupancyScreenAsync.

  OCC_CMP_02  Compare, per session:
                - Find the HERA bay by id == LoadingBayId (case-insensitive).
                - Within that bay, verify a DropOff/Filling/PickUp occupation
                  exists whose from/till matches the DB's corresponding
                  window (tolerance: WINDOW_TOL_MIN).

Auth: SQL Server authentication for the DB side (see .env.example). HERA
login is the usual manual/MFA browser flow, exactly like every other
script in this project.

NOT YET RUN LIVE.

Run
---
    py "test_bay_occupancy_vs_hera.py"
    py "test_bay_occupancy_vs_hera.py" --from 2
"""
import argparse
import csv as csv_mod
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results, ask_verdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Scheduled Transfers"))
import test_scheduled_transfers_profiles as sched_mod  # noqa: E402

from db_common import add_common_db_args, connect_from_args
from check_bay_occupancy import fetch_latest_batch, TABLE_DEFAULT as OCC_TABLE_DEFAULT
import function_check


def _goto_resilient(page, url, wait_until="domcontentloaded", retries=2):
    """page.goto() wrapped against the OAuth-redirect race this app hits
    right after login -- see test_dispatch_vs_hera_profiles.py's copy of
    this same helper for the full explanation."""
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until=wait_until)
            return
        except Exception as exc:
            if "interrupted by another navigation" not in str(exc) or attempt == retries:
                raise
            print(f"  [WARN] Navigation interrupted by auth redirect, retrying "
                  f"({attempt + 1}/{retries})…")
            sched_mod._wl()
            sched_mod._quiet(page)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent
LOCAL_TZ = ZoneInfo("Europe/Brussels")

# Same dev/test coupling as test_dispatch_vs_hera_profiles.py -- both names
# confirmed live on 2026-09-16 (connection + query succeeded against both).
# Unlike the profile table, DispatchedBayOccupationSessions' schema has NOT
# been independently verified identical between dev/test -- if this table
# also turns out to be renamed in test, add a *_TABLE_BY_ENV map here the
# same way PROFILE_TABLE_BY_ENV handles it in the other script.
DB_NAME_BY_ENV = {
    "dev": "heraplantdatabasedev",
    "test": "heraplantdatabasetest",
}

WINDOW_TOL_MIN = 5  # both sides come from the same underlying data -- a
                     # tight tolerance is enough, unlike the profile
                     # segment-matching's 30 min (which has to absorb
                     # HERA's own graph-rendering step-boundary shift).

DB_TO_HERA_WINDOW = [
    ("StartDrop", "EndDrop", "DropOff"),
    ("StartFillingWindow", "EndFillingWindow", "Filling"),
    ("StartPickup", "EndPickup", "PickUp"),
]

ENV = ""

SCENARIOS = [
    {"n": 1, "id": "OCC_CMP_01", "expect": "PASS",
     "name": "Verify the dispatch Azure Function ran, on time, successfully",
     "desc": ("Query Application Insights for the last invocation of "
              f"{function_check.DEFAULT_FUNCTION_NAME}, checking it succeeded "
              "and ran within tolerance of the expected ~22:00 local trigger "
              "time. Separate Azure AD login required (not the HERA/SQL ones). "
              "NOTE: assumes bay-occupancy dispatch is part of this same "
              "function's run -- not independently confirmed, see "
              "function_check.py's docstring.")},
    {"n": 2, "id": "OCC_CMP_02", "expect": "PASS",
     "name": "Capture dispatched DB occupancy batch + matching HERA Bay Occupancy screen",
     "desc": ("Query heraplantdatabasedev for every DispatchedBayOccupationSessions "
              "row in the latest dispatch batch, then navigate HERA's Bay Occupancy "
              "screen to that batch's week and capture it.")},
    {"n": 3, "id": "OCC_CMP_03", "expect": "PASS",
     "name": "Compare HERA Bay Occupancy vs dispatched DB sessions, per session",
     "desc": ("Match each DB session to its HERA bay via the shared bay-id GUID, "
              "then verify DropOff/Filling/PickUp windows match "
              f"(tolerance: {WINDOW_TOL_MIN} min).")},
]

_STATE: dict = {}


def _ask(scenario, auto, reason):
    return ask_verdict(
        tc_id=scenario["id"], title=scenario["name"], expect=scenario["expect"],
        auto=auto, reason=reason, index=scenario.get("n"), total=len(SCENARIOS),
        excel_id=scenario["id"],
    )


# --------------------------------------------------------------------------
# DB side
# --------------------------------------------------------------------------
def _load_db_side(args, table: str) -> list[dict]:
    conn = connect_from_args(args)
    if conn is None:
        raise RuntimeError("Could not connect to the database (see message above).")
    rows = fetch_latest_batch(conn, table)
    if not rows:
        raise RuntimeError(f"No rows found in [dbo].[{table}].")
    return rows


# --------------------------------------------------------------------------
# HERA side (capture)
# --------------------------------------------------------------------------
def _capture_hera_occupancy(page, base_url, week, year, shots_dir):
    """Returns list of {"id": lowercase-guid-str, "label": str, "occupations": [...]}."""
    captured: dict = {}

    def _handler(response):
        try:
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            if "BayOccupancyScreen/GetBayOccupancyScreenAsync" in response.url:
                captured["occupancy"] = response.json()
                print("  [CAPTURE] occupancy ← .../GetBayOccupancyScreenAsync")
        except Exception:
            pass

    page.on("response", _handler)
    try:
        _goto_resilient(page, f"{base_url}/schedules/occupancy")
        sched_mod._wm(); sched_mod._quiet(page)
        page.screenshot(path=str(shots_dir / "01_occupancy_landed.png"))

        print(f"  Navigating to week {week}/{year}…")
        sched_mod._goto_week_year(page, week, year)
        sched_mod._quiet(page); page.wait_for_timeout(2000)
        page.screenshot(path=str(shots_dir / "02_occupancy_week.png"), full_page=True)

        if "occupancy" not in captured:
            raise RuntimeError("BayOccupancyScreen API not captured")

        (shots_dir / "hera_occupancy_raw.json").write_text(
            json.dumps(captured["occupancy"], indent=2), encoding="utf-8")

        body = sched_mod._inner(captured["occupancy"])
        bays = []
        for bay in body.get("bays", []):
            bid = (bay.get("id") or "").lower()
            if not bid:
                continue
            bays.append({
                "id": bid,
                "label": bay.get("label") or bay.get("name") or "",
                "occupations": bay.get("bayOccupations", []),
            })
        return bays
    finally:
        try: page.remove_listener("response", _handler)
        except Exception: pass


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _close(a: datetime, b: datetime, tol_min=WINDOW_TOL_MIN) -> bool:
    return abs((a - b).total_seconds()) <= tol_min * 60


def _find_matching_occupation(occupations, occ_type, from_utc, till_utc, tol_min=WINDOW_TOL_MIN):
    for occ in occupations:
        if occ.get("type") != occ_type:
            continue
        try:
            o_from = _parse_iso(occ["from"])
            o_till = _parse_iso(occ["till"])
        except Exception:
            continue
        if _close(o_from, from_utc, tol_min) and _close(o_till, till_utc, tol_min):
            return occ, o_from, o_till
    return None, None, None


def _compare_session(db_row: dict, hera_bays_by_id: dict) -> dict:
    bay_id = str(db_row["LoadingBayId"]).lower()
    bay = hera_bays_by_id.get(bay_id)
    result = {
        "session_id": db_row["Id"], "bay_id": bay_id,
        "bay_label": bay["label"] if bay else None,
        "bay_found": bay is not None,
        "windows": [],
    }
    if bay is None:
        return result

    for start_col, end_col, hera_type in DB_TO_HERA_WINDOW:
        db_from = db_row[start_col].replace(tzinfo=timezone.utc)
        db_till = db_row[end_col].replace(tzinfo=timezone.utc)
        occ, h_from, h_till = _find_matching_occupation(
            bay["occupations"], hera_type, db_from, db_till,
        )
        result["windows"].append({
            "type": hera_type, "db_from": db_from, "db_till": db_till,
            "found": occ is not None, "hera_from": h_from, "hera_till": h_till,
        })
    return result


# --------------------------------------------------------------------------
# Scenario implementations
# --------------------------------------------------------------------------
def _do_function_check(args):
    if args.skip_function_check:
        return "SKIP", "Skipped via --skip-function-check."

    resource_id = args.function_resource_id or function_check.get_function_resource_id(args.env)
    if not resource_id:
        return ("SKIP", f"No known Application Insights resource ID for HERA env "
                         f"'{args.env}' (see function_check.py's FUNCTION_RESOURCE_ID_BY_ENV). "
                         f"Pass --function-resource-id explicitly, or --skip-function-check.")
    print(f"  Resource: {resource_id.split('/')[-1]}")
    verdict, message = function_check.check_last_run(resource_id)
    print(f"  {message}")
    return verdict, message


def _do_capture(page, base_url, args, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Querying {args.database}…")
    db_rows = _load_db_side(args, OCC_TABLE_DEFAULT)
    earliest_drop = min(r["StartDrop"] for r in db_rows).replace(tzinfo=timezone.utc)
    target_local_date = earliest_drop.astimezone(LOCAL_TZ).date()
    iso = target_local_date.isocalendar()
    week, year = iso.week, iso.year
    print(f"  Batch Created={db_rows[0]['Created']}  -- {len(db_rows)} session(s), "
          f"earliest drop (local): {target_local_date}  ->  ISO week {week}/{year}")

    (shots_dir / "dispatched_db_occupancy.json").write_text(
        json.dumps(
            [{k: (v.isoformat() if isinstance(v, datetime) else str(v))
              for k, v in r.items()} for r in db_rows],
            indent=2,
        ),
        encoding="utf-8",
    )

    hera_bays = _capture_hera_occupancy(page, base_url, week, year, shots_dir)
    print(f"  {len(hera_bays)} bay(s) captured from HERA Bay Occupancy screen.")

    _STATE["db_rows"] = db_rows
    _STATE["hera_bays"] = hera_bays

    if not db_rows or not hera_bays:
        return "FAIL", "One side captured nothing -- see above."
    return "PASS", f"{len(db_rows)} DB session(s), {len(hera_bays)} HERA bay(s) for week {week}/{year}."


def _do_compare(shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)

    if "db_rows" not in _STATE:
        return "FAIL", "OCC_CMP_02 did not run / produced no data to compare."

    hera_bays_by_id = {b["id"]: b for b in _STATE["hera_bays"]}

    print(f"\n  {'Session':<10} {'Bay':<16} {'DropOff':^10} {'Filling':^10} {'PickUp':^10}")
    print(f"  {'-'*10} {'-'*16} {'-'*10} {'-'*10} {'-'*10}")

    rows_out = []
    any_fail = False
    for db_row in _STATE["db_rows"]:
        cmp = _compare_session(db_row, hera_bays_by_id)
        sess_short = str(cmp["session_id"])[:8] + "…"
        if not cmp["bay_found"]:
            print(f"  {sess_short:<10} {'NOT FOUND':<16}  !! bay id {cmp['bay_id']} has no "
                  f"matching HERA bay")
            any_fail = True
            rows_out.append({"session_id": cmp["session_id"], "bay_label": None,
                              "DropOff": "N/A", "Filling": "N/A", "PickUp": "N/A"})
            continue

        marks = {}
        for w in cmp["windows"]:
            marks[w["type"]] = "OK" if w["found"] else "MISSING"
            if not w["found"]:
                any_fail = True
        print(f"  {sess_short:<10} {cmp['bay_label']:<16} "
              f"{marks['DropOff']:^10} {marks['Filling']:^10} {marks['PickUp']:^10}")
        for w in cmp["windows"]:
            if not w["found"]:
                print(f"      MISSING {w['type']}: DB says {w['db_from'].isoformat()} -> "
                      f"{w['db_till'].isoformat()} (±{WINDOW_TOL_MIN} min) -- no matching "
                      f"HERA occupation of that type/window in bay '{cmp['bay_label']}'")
        rows_out.append({
            "session_id": cmp["session_id"], "bay_label": cmp["bay_label"],
            "DropOff": marks["DropOff"], "Filling": marks["Filling"], "PickUp": marks["PickUp"],
        })

    with (shots_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=["session_id", "bay_label", "DropOff", "Filling", "PickUp"])
        w.writeheader()
        w.writerows(rows_out)

    n = len(_STATE["db_rows"])
    if any_fail:
        return "FAIL", f"{n} session(s) checked, at least one window/bay mismatch -- see detail above."
    return "PASS", f"All {n} session(s): bay id matched HERA and DropOff/Filling/PickUp windows all found."


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    _STATE.clear()

    parser = argparse.ArgumentParser(
        description="HERA Bay Occupancy vs dispatched DB sessions -- per-session comparison")
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                         help="Resume from scenario N (1-based).")
    parser.add_argument("--excel", default=None, metavar="PATH",
                         help="Report file to write Pass/Fail into. Default: create a "
                              "new timestamped copy under UAT Testing/Reports/.")
    parser.add_argument("--function-resource-id", default=None,
                         help="Application Insights resource ID for OCC_CMP_01's Azure "
                              "Function check. Default: auto-selected per HERA environment "
                              "(only 'dev' is known -- see function_check.py).")
    parser.add_argument("--skip-function-check", action="store_true",
                         help="Skip OCC_CMP_01 (avoids the separate Azure AD login prompt).")
    add_common_db_args(parser)
    args, _ = parser.parse_known_args()
    start_from = max(1, args.start_from)
    database_explicit = "--database" in sys.argv

    env = ENV.strip().lower()
    if env not in sched_mod.ENVIRONMENTS:
        env, base_url = sched_mod._resolve_env()
    else:
        base_url = sched_mod.ENVIRONMENTS[env]

    if not database_explicit:
        inferred_db = DB_NAME_BY_ENV.get(env)
        if inferred_db:
            args.database = inferred_db
        else:
            print(f"  [WARN] No known database mapping for HERA env '{env}' -- "
                  f"keeping default '{args.database}'. Pass --database explicitly if wrong.")
    args.env = env

    artifacts = HERE / "test_artifacts_occupancy_vs_hera"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    db_note = "" if database_explicit else "  (auto-selected for this env, override with --database)"
    print(f"\n{'='*64}")
    print("  HERA Bay Occupancy vs Dispatched DB Sessions")
    print(f"  Environment   : {env}  ({base_url})")
    print(f"  Database      : {args.database}{db_note}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*64}\n")

    results: list[dict] = []

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir), headless=False, slow_mo=0,
            viewport={"width": 1500, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("  Navigating to Hera…")
        try:
            page.goto(f"{base_url}/schedules/occupancy", wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA if prompted.                       │")
        print("  │  Press Enter once you see the Hera app.             │")
        print("  └─────────────────────────────────────────────────────┘")
        input("  > ")
        sched_mod._wl()  # give the identity provider's own /auth redirect time
        sched_mod._quiet(page)  # to finish landing before we navigate ourselves
        print("  Login confirmed.\n")

        for sc in SCENARIOS:
            if sc["n"] < start_from:
                continue

            shots_dir = run_dir / f"{sc['n']:02d}_{sc['id']}"
            # clear_screen() deliberately NOT used while this script is new/
            # unverified -- keep ERROR tracebacks visible across scenarios.
            print(f"\n{'═'*64}")
            print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['name']}")
            print(f"  {sc['desc']}")

            try:
                if sc["id"] == "OCC_CMP_01":
                    auto, reason = _do_function_check(args)
                elif sc["id"] == "OCC_CMP_02":
                    auto, reason = _do_capture(page, base_url, args, shots_dir)
                elif sc["id"] == "OCC_CMP_03":
                    auto, reason = _do_compare(shots_dir)
                else:
                    auto, reason = "?", "Unknown scenario ID"

                result, notes = _ask(sc, auto, reason)
            except Exception as exc:
                import traceback
                print(f"  ERROR: {exc}")
                traceback.print_exc()
                auto, result, notes = "ERROR", "ERROR", str(exc)

            results.append({**sc, "result": result, "notes": notes})

        ctx.close()

    print(f"\n{'='*64}")
    print(f"  SUMMARY  –  run {run_id}")
    print(f"{'='*64}")
    ok = fail = err = 0
    for r in results:
        match = r["expect"] == "?" or r["result"] == r["expect"]
        flag  = "   " if match else "!! "
        if   r["result"] == "ERROR": err  += 1
        elif r["result"] == "PASS":  ok   += 1
        else:                        fail += 1
        print(f"  {flag}{r['n']:>2}. {r['id']:<12} exp={r['expect']} "
              f"got={r['result']}  {r['name']}")
    print(f"\n  PASS: {ok}  FAIL: {fail}  ERROR: {err}")

    out = run_dir / "test_results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=["n", "id", "name", "expect", "result", "notes", "desc"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"\n  Results    : {out}")
    print(f"  Artifacts  : {run_dir}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_bay_occupancy_vs_hera.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
