#!/usr/bin/env python3
"""
test_dispatch_vs_hera_profiles.py
====================================
Compares HERA's own Profiles screen (the forecast it shows the user) against
what was ACTUALLY dispatched to the plant over MQTT -- read directly from
heraplantdatabasedev's dbo.DispatchedTruckFillingFlowRateProfiles, decoded
the same way as check_dispatched_profiles.py. This closes the loop this
session's Azure/SQL investigation was aiming at: not "does the dispatch job
run" (that's check_dispatch_function.py) but "is the content that reaches
the plant the same content HERA itself displays".

Scenarios
---------
  DISP_CMP_01  Capture both sides for the most recently dispatched day:
                 - Query the DB for the latest DispatchedTruckFillingFlowRateProfiles
                   row(s) (one per bay) and decode their [TimeSerie].
                 - Compute that day's ISO week (Europe/Brussels local time)
                   and navigate HERA's own Profiles screen (g/s toggle) to it,
                   capturing the same 'Truck fillings by loading bay' graph
                   test_scheduled_transfers_profiles.py already reads.

  DISP_CMP_02  Compare the two, per bay:
                 - HERA identifies bays by a label ("Loading Bay 1"); the DB
                   identifies them by a raw LoadingBayId GUID. These are
                   matched via asset_lookup.py's authoritative
                   heraassetdatabasedev/...test dbo.Assets table (Name +
                   NameForPlantCommunication, i.e. the DI1-DI4 MQTT
                   identifier) -- confirmed live on 2026-09-16, not a
                   heuristic. Non-zero segment timing is kept only as a
                   defensive FALLBACK for any bay the Assets lookup doesn't
                   cover.
                 - For every matched bay, compare the two point series
                   timestamp-by-timestamp (tolerance: EXACT_TOL_GS).

Auth: SQL Server authentication for the DB side (see .env.example, same as
check_dispatched_profiles.py). HERA login is the usual manual/MFA browser
flow, exactly like every other script in this project.

NOT YET RUN LIVE -- built by reading test_scheduled_transfers_profiles.py's
navigation/capture code and re-using it, but nobody has clicked through this
script's own browser flow yet. Expect to fix selectors/timing on first run,
same caveat as every other new script in this project's history.

Run
---
    py "test_dispatch_vs_hera_profiles.py"
    py "test_dispatch_vs_hera_profiles.py" --from 2
"""
import argparse
import csv as csv_mod
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

# -- Reuse this project's reporting + navigation helpers instead of
#    duplicating them --------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results, ask_verdict, clear_screen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Scheduled Transfers"))
import test_scheduled_transfers_profiles as sched_mod  # noqa: E402  (path set above)

from db_common import add_common_db_args, connect_from_args
from check_dispatched_profiles import decode_timeserie, fetch_latest as fetch_latest_profile_rows
import asset_lookup
from asset_lookup import fetch_loading_bay_map
import function_check

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def _goto_resilient(page, url, wait_until="domcontentloaded", retries=2):
    """page.goto() wrapped against the OAuth-redirect race this app hits
    right after login: pressing Enter can land while the identity
    provider's own '/auth#code=...' redirect is still resolving, and our
    goto() collides with it (Playwright raises "interrupted by another
    navigation"). On that specific failure, just let the page settle and
    retry -- the auth redirect's own embedded return path usually lands
    close to (or exactly on) the URL we wanted anyway.
    """
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


HERE = Path(__file__).parent
LOCAL_TZ = ZoneInfo("Europe/Brussels")

# db_common.DEFAULT_DATABASE always defaults to the "dev" database, but this
# script also asks (via sched_mod._resolve_env()) which HERA ENVIRONMENT
# (dev/test) to log into -- those two choices must follow each other, or a
# "test" HERA session would get silently compared against "dev" data.
# Confirmed live on 2026-09-16: heraplantdatabasetest DOES exist and the
# same login has access to it -- but its schema has DRIFTED from dev's:
# 14 tables vs dev's 15 (missing ConnectionPoints), and the profile table
# itself is named differently (no "FlowRate" in test). Both maps below
# capture that; extend them if further drift is found in other tables.
DB_NAME_BY_ENV = {
    "dev": "heraplantdatabasedev",
    "test": "heraplantdatabasetest",
}
PROFILE_TABLE_BY_ENV = {
    "dev": "DispatchedTruckFillingFlowRateProfiles",
    "test": "DispatchedTruckFillingProfiles",
}

# Point values are dispatched as plain floats (g/s) -- treat two values as
# equal if within this tolerance rather than requiring bit-exact floats.
EXACT_TOL_GS = 0.01

# Segment-matching tolerance, same convention as SCHED_TRF_05/06's
# MATCH_TOLERANCE_MIN (Hera's own graph rendering can shift a segment's
# apparent start/end by up to one 15-min step vs the raw dispatched data).
SEGMENT_TOL_MIN = 30

ENV = ""

SCENARIOS = [
    {"n": 1, "id": "DISP_CMP_01", "expect": "PASS",
     "name": "Verify the dispatch Azure Function ran, on time, successfully",
     "desc": ("Query Application Insights for the last invocation of "
              f"{function_check.DEFAULT_FUNCTION_NAME}, checking it succeeded "
              "and ran within tolerance of the expected ~22:00 local trigger "
              "time. Separate Azure AD login required (not the HERA/SQL ones).")},
    {"n": 2, "id": "DISP_CMP_02", "expect": "PASS",
     "name": "Capture dispatched DB profile + matching HERA Profiles screen",
     "desc": ("Query heraplantdatabasedev for the latest dispatched "
              "TruckFillingFlowRateProfiles (all bays), decode [TimeSerie], "
              "then navigate HERA's Profiles screen (g/s) to that same "
              "calendar day's week and capture the 'Truck fillings by "
              "loading bay' graph.")},
    {"n": 3, "id": "DISP_CMP_03", "expect": "PASS",
     "name": "Compare HERA Profiles (g/s) vs dispatched DB TimeSerie, per bay",
     "desc": ("Match HERA bay labels to DB LoadingBayId GUIDs via the "
              "authoritative Assets table (see asset_lookup.py), then "
              "compare both series point-by-point for every matched bay. "
              "Idle bays (no activity either side) are reported separately, "
              "not force-matched.")},
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
def _load_db_side(args, table: str) -> tuple[list[dict], datetime, datetime]:
    """Returns (decoded_bays, day_start_utc, day_end_utc).
    decoded_bays: [{"LoadingBayId": str, "points": [(ts_utc, value), ...]}]
    """
    conn = connect_from_args(args)
    if conn is None:
        raise RuntimeError(f"Could not connect to {args.database} (see message above).")

    rows = fetch_latest_profile_rows(conn, table)
    if not rows:
        raise RuntimeError(f"No rows found in [dbo].[{table}].")

    decoded_bays = []
    day_start_utc = day_end_utc = None
    for r in rows:
        try:
            decoded = decode_timeserie(r["TimeSerie"])
        except (ValueError, IndexError) as exc:
            print(f"  [WARN] Could not decode bay {r['LoadingBayId']}: {exc}")
            continue
        decoded_bays.append({"LoadingBayId": r["LoadingBayId"], "points": decoded["points"]})
        if day_start_utc is None:
            day_start_utc = r["Start"].replace(tzinfo=timezone.utc)
            day_end_utc = r["End"].replace(tzinfo=timezone.utc)

    if not decoded_bays:
        raise RuntimeError("Every bay row failed to decode -- nothing to compare.")

    return decoded_bays, day_start_utc, day_end_utc


# --------------------------------------------------------------------------
# HERA side (capture)
# --------------------------------------------------------------------------
def _capture_hera_profiles(page, base_url, week, year, shots_dir):
    """Returns list of {"name": bay_label, "points_raw": {iso_ts: value}}."""
    captured: dict = {}

    def _handler(response):
        try:
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            if "ProfilesScreen/GetProfilesScreenAsync" in response.url:
                captured["profiles"] = response.json()
                print("  [CAPTURE] profiles ← .../GetProfilesScreenAsync")
        except Exception:
            pass

    page.on("response", _handler)
    try:
        _goto_resilient(page, f"{base_url}/schedules/profiles")
        sched_mod._wm(); sched_mod._quiet(page)
        page.screenshot(path=str(shots_dir / "01_profiles_landed.png"))

        print(f"  Navigating to week {week}/{year}…")
        sched_mod._goto_week_year(page, week, year)
        sched_mod._quiet(page)
        page.screenshot(path=str(shots_dir / "02_profiles_week.png"), full_page=True)

        print("  Switching toggle: MW → g/s…")
        try:
            import re
            page.locator("app-toggle button",
                         has_text=re.compile(r"\bMW\b", re.I)).first.click()
            page.wait_for_timeout(3000)
            page.locator("app-toggle button",
                         has_text=re.compile(r"g\s*/\s*s", re.I)).first.click()
            page.wait_for_timeout(3500)
        except Exception as e:
            print(f"  [WARN] Toggle not found: {e}. Waiting for default response…")
            sched_mod._wl(); sched_mod._quiet(page)

        page.screenshot(path=str(shots_dir / "03_profiles_gs.png"), full_page=True)

        if "profiles" not in captured:
            raise RuntimeError("ProfilesScreen API not captured")

        (shots_dir / "hera_profiles_raw.json").write_text(
            json.dumps(captured["profiles"], indent=2), encoding="utf-8")

        inner  = sched_mod._inner(captured["profiles"])
        graphs = inner.get("profileGraphs", [])
        truck  = next((g for g in graphs
                       if g.get("title") == "Truck fillings by loading bay"), None)
        if not truck:
            titles = [g.get("title") for g in graphs]
            raise RuntimeError(f"'Truck fillings by loading bay' not found. Graphs: {titles}")

        lines = []
        for line in truck.get("profileGraphLines", []):
            name = line.get("name")
            if not name:
                continue
            lines.append({"name": name, "points_raw": line.get("points", {})})
        return lines
    finally:
        try: page.remove_listener("response", _handler)
        except Exception: pass


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
def _hera_points_for_window(points_raw: dict, start_utc: datetime, end_utc: datetime):
    """Inclusive on both ends -- the DB's own [Start]..[End] range is
    inclusive (97 points spanning exactly 24h at 15-min steps, confirmed
    live 2026-09-16/17), and HERA's own graph data genuinely has a point at
    that same end-of-day boundary too. An earlier exclusive upper bound
    here (`< end_utc`) was discarding that one legitimate shared boundary
    point from HERA's side only, producing a false "HERA-only" mismatch
    that was actually just this filter being one point too strict."""
    out = []
    for ts_str, v in points_raw.items():
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if start_utc <= ts <= end_utc:
            out.append((ts, float(v)))
    out.sort(key=lambda x: x[0])
    return out


def _segments(points):
    """Contiguous positive-value blocks -- same idea as
    test_scheduled_transfers_profiles.py's _extract_filling_segments,
    generalized to work regardless of step size."""
    segs, current = [], []
    for ts, v in points:
        if v > 0:
            current.append((ts, v))
        else:
            if current:
                segs.append(current); current = []
    if current:
        segs.append(current)
    return [
        {"t_start": s[0][0], "t_end": s[-1][0], "max": max(v for _, v in s), "points": s}
        for s in segs
    ]


def _segments_close(a, b, tol_min=SEGMENT_TOL_MIN):
    tol_s = tol_min * 60
    return (abs((a["t_start"] - b["t_start"]).total_seconds()) <= tol_s
            and abs((a["t_end"] - b["t_end"]).total_seconds()) <= tol_s)


def _match_bays(hera_bays, db_bays, bay_map: dict):
    """Returns (pairs, unmatched_hera, unmatched_db).

    Primary match: HERA bay "name" -> DB LoadingBayId via bay_map, the
    authoritative heraassetdatabasedev/...test dbo.Assets lookup (see
    asset_lookup.py) -- confirmed live, not a heuristic. Falls back to
    non-zero segment-timing overlap only for a HERA bay name the Assets
    lookup doesn't cover (defensive; shouldn't normally trigger, and prints
    a warning if it does since it means the master-data mapping is
    incomplete or stale).
    """
    name_to_guid = {info["name"]: guid for guid, info in bay_map.items()}
    db_by_guid = {d["LoadingBayId"].lower(): d for d in db_bays}

    pairs, used_guids = [], set()
    unmatched_hera, fallback_used = [], []

    for h in hera_bays:
        guid = name_to_guid.get(h["name"])
        match = db_by_guid.get(guid) if guid and guid not in used_guids else None

        if match is None:
            fallback_used.append(h["name"])
            for hseg in h["segments"]:
                for d in db_bays:
                    if d["LoadingBayId"].lower() in used_guids:
                        continue
                    for dseg in d["segments"]:
                        if _segments_close(hseg, dseg):
                            match = d
                            break
                    if match:
                        break
                if match:
                    break

        if match:
            pairs.append((h, match))
            used_guids.add(match["LoadingBayId"].lower())
        else:
            unmatched_hera.append(h)

    unmatched_db = [d for d in db_bays if d["LoadingBayId"].lower() not in used_guids]
    if fallback_used:
        print(f"  [WARN] Assets bay lookup had no entry for: {fallback_used} -- "
              f"used segment-timing fallback instead (check asset_lookup.py's mapping).")
    return pairs, unmatched_hera, unmatched_db


def _compare_series(hera_points, db_points):
    """Returns dict with mismatch details for one matched bay pair."""
    hera_map = {ts: v for ts, v in hera_points}
    db_map   = {ts: v for ts, v in db_points}
    all_ts   = sorted(set(hera_map) | set(db_map))

    missing_in_hera, missing_in_db, value_mismatches = [], [], []
    for ts in all_ts:
        h = hera_map.get(ts)
        d = db_map.get(ts)
        if h is None:
            missing_in_hera.append(ts)
        elif d is None:
            missing_in_db.append(ts)
        elif abs(h - d) > EXACT_TOL_GS:
            value_mismatches.append((ts, h, d))
    return {
        "missing_in_hera": missing_in_hera,
        "missing_in_db": missing_in_db,
        "value_mismatches": value_mismatches,
        "n_common": len(all_ts) - len(missing_in_hera) - len(missing_in_db),
    }


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

    print(f"  Querying {args.database}.dbo.{args.table}…")
    db_bays, day_start_utc, day_end_utc = _load_db_side(args, args.table)
    target_local_date = day_start_utc.astimezone(LOCAL_TZ).date()
    iso = target_local_date.isocalendar()
    week, year = iso.week, iso.year
    print(f"  Dispatched day (local): {target_local_date}  ->  ISO week {week}/{year}")
    print(f"  {len(db_bays)} bay(s) decoded from DB.")

    print(f"  Looking up bay names ({asset_lookup.ASSET_DB_BY_ENV.get(args.env, '?')})…")
    bay_map = fetch_loading_bay_map(args.server, args.user, args.password, args.env)
    for b in db_bays:
        info = bay_map.get(b["LoadingBayId"].lower())
        label = f"{info['name']} ({info['di']})" if info else "UNKNOWN (not in Assets lookup)"
        print(f"    {b['LoadingBayId']}  ->  {label}")

    (shots_dir / "dispatched_db.json").write_text(
        json.dumps(
            [{"LoadingBayId": b["LoadingBayId"],
              "points": [[ts.isoformat(), v] for ts, v in b["points"]]}
             for b in db_bays],
            indent=2,
        ),
        encoding="utf-8",
    )

    hera_lines = _capture_hera_profiles(page, base_url, week, year, shots_dir)
    print(f"  {len(hera_lines)} bay line(s) captured from HERA Profiles screen.")

    _STATE["db_bays"] = db_bays
    _STATE["hera_lines"] = hera_lines
    _STATE["day_start_utc"] = day_start_utc
    _STATE["day_end_utc"] = day_end_utc
    _STATE["target_local_date"] = target_local_date
    _STATE["bay_map"] = bay_map

    if not db_bays or not hera_lines:
        return "FAIL", "One side captured nothing -- see above."
    return ("PASS",
            f"{len(db_bays)} DB bay(s), {len(hera_lines)} HERA bay line(s) "
            f"for {target_local_date}.")


def _do_compare(shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)

    if "db_bays" not in _STATE:
        return "FAIL", "DISP_CMP_02 did not run / produced no data to compare."

    day_start_utc = _STATE["day_start_utc"]
    day_end_utc   = _STATE["day_end_utc"]

    hera_bays = []
    for line in _STATE["hera_lines"]:
        pts = _hera_points_for_window(line["points_raw"], day_start_utc, day_end_utc)
        hera_bays.append({"name": line["name"], "points": pts, "segments": _segments(pts)})

    db_bays = []
    for b in _STATE["db_bays"]:
        db_bays.append({
            "LoadingBayId": b["LoadingBayId"], "points": b["points"],
            "segments": _segments(b["points"]),
        })

    pairs, unmatched_hera, unmatched_db = _match_bays(hera_bays, db_bays, _STATE["bay_map"])

    print(f"\n  {'Bay pair':<45} {'common pts':>10} {'val diffs':>10} "
          f"{'HERA-only':>10} {'DB-only':>10}")
    print(f"  {'-'*45} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    rows_out = []
    any_mismatch = False
    for h, d in pairs:
        cmp = _compare_series(h["points"], d["points"])
        mismatch = bool(cmp["value_mismatches"] or cmp["missing_in_hera"] or cmp["missing_in_db"])
        any_mismatch = any_mismatch or mismatch
        label = f"{h['name']}  <->  {d['LoadingBayId'][:8]}…"
        # "HERA-only" = timestamps HERA has that the DB doesn't (missing_in_db);
        # "DB-only" = timestamps the DB has that HERA doesn't (missing_in_hera).
        print(f"  {label:<45} {cmp['n_common']:>10} {len(cmp['value_mismatches']):>10} "
              f"{len(cmp['missing_in_db']):>10} {len(cmp['missing_in_hera']):>10}"
              f"{'  !! MISMATCH' if mismatch else '  OK'}")
        rows_out.append({
            "hera_bay": h["name"], "db_bay_id": d["LoadingBayId"],
            "common_points": cmp["n_common"],
            "value_mismatches": len(cmp["value_mismatches"]),
            "missing_in_hera": len(cmp["missing_in_hera"]),
            "missing_in_db": len(cmp["missing_in_db"]),
        })
        for ts, h_val, d_val in cmp["value_mismatches"][:5]:
            print(f"      value mismatch @ {ts.isoformat()}: HERA={h_val}  DB={d_val}")

    if unmatched_hera or unmatched_db:
        print()
        for h in unmatched_hera:
            note = "no non-zero segment" if not h["segments"] else "no matching DB segment"
            print(f"  [UNMATCHED - HERA] {h['name']}  ({note})")
        for d in unmatched_db:
            note = "no non-zero segment" if not d["segments"] else "no matching HERA segment"
            print(f"  [UNMATCHED - DB]   {d['LoadingBayId']}  ({note})")

    (shots_dir / "comparison.csv").write_text("", encoding="utf-8")  # placeholder, overwritten below
    with (shots_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=[
            "hera_bay", "db_bay_id", "common_points",
            "value_mismatches", "missing_in_hera", "missing_in_db",
        ])
        w.writeheader()
        w.writerows(rows_out)

    # Bays idle on both sides aren't a failure -- no timing signal to match
    # them by, and both report zero activity, which is itself consistent.
    real_unmatched = [
        h for h in unmatched_hera if h["segments"]
    ] + [
        d for d in unmatched_db if d["segments"]
    ]

    if any_mismatch or real_unmatched:
        return ("FAIL",
                f"{len(pairs)} bay(s) compared, mismatches found. "
                f"{len(real_unmatched)} bay(s) had activity but no counterpart match.")
    if not pairs and not unmatched_hera and not unmatched_db:
        return "FAIL", "Nothing to compare -- captured zero bays on both sides."
    return ("PASS",
            f"{len(pairs)} bay(s) matched and compared with no mismatches. "
            f"{len(unmatched_hera) + len(unmatched_db)} idle bay(s) on both sides "
            f"(not force-matched, see script docstring).")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    _STATE.clear()

    parser = argparse.ArgumentParser(
        description="HERA Profiles vs dispatched DB TimeSerie -- per-bay comparison")
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                         help="Resume from scenario N (1-based).")
    parser.add_argument("--excel", default=None, metavar="PATH",
                         help="Report file to write Pass/Fail into. Default: create a "
                              "new timestamped copy under UAT Testing/Reports/.")
    parser.add_argument("--table", default=None,
                         help="Override the profile table name. Default: auto-selected "
                              "per HERA environment (dev/test schemas differ -- see "
                              "PROFILE_TABLE_BY_ENV).")
    parser.add_argument("--function-resource-id", default=None,
                         help="Application Insights resource ID for DISP_CMP_01's Azure "
                              "Function check. Default: auto-selected per HERA environment "
                              "(only 'dev' is known -- see function_check.py).")
    parser.add_argument("--skip-function-check", action="store_true",
                         help="Skip DISP_CMP_01 (avoids the separate Azure AD login prompt).")
    add_common_db_args(parser)
    args, _ = parser.parse_known_args()
    start_from = max(1, args.start_from)
    database_explicit = "--database" in sys.argv
    table_explicit = "--table" in sys.argv

    env = ENV.strip().lower()
    if env not in sched_mod.ENVIRONMENTS:
        env, base_url = sched_mod._resolve_env()
    else:
        base_url = sched_mod.ENVIRONMENTS[env]

    # Make the DB side follow whichever HERA environment was just chosen,
    # unless the user explicitly overrode --database/--table themselves.
    if not database_explicit:
        inferred_db = DB_NAME_BY_ENV.get(env)
        if inferred_db:
            args.database = inferred_db
        else:
            print(f"  [WARN] No known database mapping for HERA env '{env}' -- "
                  f"keeping default '{args.database}'. Pass --database explicitly if wrong.")
    if not table_explicit:
        inferred_table = PROFILE_TABLE_BY_ENV.get(env)
        if inferred_table:
            args.table = inferred_table
        else:
            args.table = PROFILE_TABLE_BY_ENV["dev"]
            print(f"  [WARN] No known profile-table mapping for HERA env '{env}' -- "
                  f"defaulting to '{args.table}'. Pass --table explicitly if wrong.")
    args.env = env

    artifacts = HERE / "test_artifacts_dispatch_vs_hera"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    db_note    = "" if database_explicit else "  (auto-selected for this env, override with --database)"
    table_note = "" if table_explicit else "  (auto-selected for this env, override with --table)"
    print(f"\n{'='*64}")
    print("  HERA Profiles vs Dispatched DB Profile")
    print(f"  Environment   : {env}  ({base_url})")
    print(f"  Database      : {args.database}{db_note}")
    print(f"  Table         : {args.table}{table_note}")
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
            page.goto(f"{base_url}/schedules/profiles", wait_until="commit", timeout=20_000)
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
            # NOTE: clear_screen() deliberately disabled while this script is
            # still new/unverified -- it was wiping ERROR tracebacks from a
            # previous scenario before they could be read. Re-enable once
            # the script has had a few clean live runs.
            print(f"\n{'═'*64}")
            print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['name']}")
            print(f"  {sc['desc']}")

            try:
                if sc["id"] == "DISP_CMP_01":
                    auto, reason = _do_function_check(args)
                elif sc["id"] == "DISP_CMP_02":
                    auto, reason = _do_capture(page, base_url, args, shots_dir)
                elif sc["id"] == "DISP_CMP_03":
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
                    xlsx_path=args.excel, source="test_dispatch_vs_hera_profiles.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
