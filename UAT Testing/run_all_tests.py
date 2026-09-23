#!/usr/bin/env python3
"""
run_all_tests.py – Hera Master UAT Test Runner
===============================================
Launches every folder's test script(s) in one go, in the order the UAT
suite is organized, so the whole thing can be run from a single command
instead of opening each folder separately.

Folder order (as requested) and the script(s) run in each, in the order
that makes sense within a folder — import/setup scripts before the UI
scripts that depend on their data:

  1. Weekly Nomination            – test_weekly_import.py, then test_weekly_ui.py
  2. Yearly Nomination             – test_yearly_import.py, then test_yearly_ui.py
                                      (test_yearly_ui.py assumes a yearly
                                      nomination already imported by
                                      test_yearly_import.py — see that
                                      script's own docstring)
  3. Master Data                   – test_master_data.py
  4. Planned Maintenance           – test_planned_maintenance.py
  5. Scheduled Transfers           – test_scheduled_transfers_profiles.py
  6. NetCommodityCost              – test_ncc_costs_breakdown.py
  7. EndToEndPlannedMaintenance    – test_e2e_maintenance_profiles.py
  8. Plant Communication           – 8 scripts (see below); dashboard/ (the
                                      Streamlit live monitoring tool, not a
                                      test) is skipped, same reasoning as
                                      the old "Plant Communication -
                                      sending" folder's empty entry before
                                      this suite consolidated 3 separate
                                      "Plant Communication - *" folders into
                                      one on 2026-09-23.

Plant Communication's 8 scripts, in dependency order (DB-level checks
before the UI-level checks that build on them), with their own distinct
auth requirements -- expect several separate login prompts across this
folder, not just one:

  8a. test_dispatch_vs_hera_profiles.py   – did the dispatch Azure Function
  8b. test_bay_occupancy_vs_hera.py         run, and does its output match
                                             Hera's own screens? (Azure AD
                                             login + SQL .env + Hera login)
  8c. test_signal_reaction.py              – do broker signals land
                                             correctly in Hera's own DB?
                                             (SQL .env only, no Hera login)
  8d. test_edge_case_skipped_step.py       – exploratory DB observation
  8e. test_edge_case_duplicates.py           harnesses, not pass/fail (SQL
                                             .env only)
  8f. test_plant_ui_reaction.py            – do signals show up correctly
                                             on Hera's MQTT Stream screen?
                                             (Hera login only)
  8g. test_bay_monitoring_actual_vs_broker.py – does the Bay Monitoring
                                             Actual chart match what was
                                             sent? (Hera login; ~30-40 min,
                                             includes a 5-min settle-retry)
  8h. test_bay_occupancy_happy_path.py     – does a full truck visit show
                                             up correctly on the Bay
                                             Occupancy Schedule? (Hera
                                             login; ~35-40 min, includes a
                                             30-min sustained fill)

Folder 8's last two scripts alone add up to well over an hour -- use
--skip on them (see below) for a quick sanity pass through the rest of the
suite, and run them separately when you have time for the full wait.

Each script stays fully interactive on its own (MFA login, environment
choice, pass/fail confirmation prompts) — this runner just launches them
one after another in the same terminal, inheriting stdin/stdout, so using
any one of them by hand doesn't change. It does NOT interpret a script's
internal scenario verdicts (those live in each script's own
test_results.csv) — it only tracks whether the process ran to completion
or crashed, and prints a final summary of that.

Mid-run, at any test case's verdict prompt (P/F/S/I), typing N instead
jumps straight to the next script in the suite: the current test case and
every remaining one in that script are recorded as SKIP (with a note that
it was jumped, not actually run) and the script exits normally, so this
runner moves straight on. Handy for e.g. bailing out of Weekly Nomination
Import partway through to get to Weekly Nomination UI sooner.

Every script also reports its Pass/Fail confirmations into an Excel report
(see uat_excel_reporter.py). By default this runner creates ONE new
timestamped report under Reports/ before anything starts, and passes
"--excel <that path>" to every script it launches, so a full suite run
lands in a single consolidated report instead of nine separate ones. Pass
--excel yourself to keep adding to an existing report instead.

Nothing else is required by hand for a full run, including across a week
rollover between two runs: this runner calls Weekly Nomination's own
ensure_next_week() itself, once, before anything starts, so every script's
CSV fixtures are already dated for the current target week. The one script
that needs an explicit --csv (test_e2e_maintenance_profiles.py, which has
no default of its own) gets it auto-filled from that same target week (see
_extra_args_for()) -- so double-clicking Run_All_Tests.bat is enough on
its own; nothing needs editing between runs.

Run:
    python run_all_tests.py
    python run_all_tests.py --from "Master Data"        # resume from a folder
    python run_all_tests.py --only "NetCommodityCost"   # just one folder
    python run_all_tests.py --skip test_weekly_import.py  # run everything except this script
    python run_all_tests.py --excel "Reports\\Hera_UAT_Test_Plan_20260910-090000.xlsx"
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from uat_excel_reporter import new_versioned_copy

ROOT = Path(__file__).resolve().parent

# Reused for the one script (test_e2e_maintenance_profiles.py) that needs a
# --csv pair rather than just --excel -- see _extra_args_for(). Keeps the
# whole suite runnable end-to-end with nothing but --excel for every other
# script, and no manual CSV bookkeeping across a week rollover.
sys.path.insert(0, str(ROOT / "Weekly Nomination"))
from _weekly_csv_sync import ensure_next_week

# ---------------------------------------------------------------------------
# Folder order + the script(s) to run in each, in the order they should run.
# An empty list means "no automated test script in this folder" — reported
# and skipped rather than treated as an error.
# ---------------------------------------------------------------------------
SUITE: list[tuple[str, list[str]]] = [
    ("Weekly Nomination",             ["test_weekly_import.py", "test_weekly_ui.py"]),
    ("Yearly Nomination",             ["test_yearly_import.py", "test_yearly_ui.py"]),
    ("Master Data",                   ["test_master_data.py"]),
    ("Planned Maintenance",           ["test_planned_maintenance.py"]),
    ("Scheduled Transfers",           ["test_scheduled_transfers_profiles.py"]),
    ("NetCommodityCost",              ["test_ncc_costs_breakdown.py"]),
    ("EndToEndPlannedMaintenance",    ["test_e2e_maintenance_profiles.py"]),
    ("Plant Communication",           ["test_dispatch_vs_hera_profiles.py",
                                        "test_bay_occupancy_vs_hera.py",
                                        "test_signal_reaction.py",
                                        "test_edge_case_skipped_step.py",
                                        "test_edge_case_duplicates.py",
                                        "test_plant_ui_reaction.py",
                                        "test_bay_monitoring_actual_vs_broker.py",
                                        "test_bay_occupancy_happy_path.py"]),
]


def _banner(title: str, width: int = 70) -> None:
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def _extra_args_for(script: str, target_week: int) -> list[str]:
    """Per-script extra CLI args needed beyond --excel, so every script in
    the suite can run unattended from a single double-click regardless of
    which week it currently is.

    test_e2e_maintenance_profiles.py is the one script in the suite that
    requires --csv (it has no default of its own, unlike every other
    script here) -- point it at the same Weekly Nomination CSV pair the
    rest of the suite already keeps rolled onto the current target week
    (see ensure_next_week(), called once at the top of main()). The script
    itself resolves bare filenames like these against Weekly Nomination/
    (see its own docstring's "CSV resolution" search order).
    """
    if script == "test_e2e_maintenance_profiles.py":
        return ["--csv",
                f"Messer_Nomination_Week{target_week}.csv",
                f"Virya_Nomination_Week{target_week}.csv"]
    return []


def _run_script(folder: str, script: str, excel_path: Path,
                 extra_args: list[str] | None = None) -> str:
    path = ROOT / folder / script
    if not path.exists():
        print(f"  [MISSING] {path}")
        return "MISSING"

    _banner(f"{folder}  ->  {script}")
    cmd = [sys.executable, str(path), "--excel", str(excel_path), *(extra_args or [])]
    result = subprocess.run(cmd)
    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    print(f"\n  [{status}] {folder} / {script}")
    return status


def _apply_skip(suite: list[tuple[str, list[str]]],
                 skip_names: list[str]) -> tuple[list[tuple[str, list[str]]], set[str]]:
    """Remove any script whose filename matches --skip (case-insensitive).

    Returns the filtered suite plus the set of folder names that originally
    had script(s) but ended up with none purely because every one of them
    was skipped -- so the runner can report that distinctly from a folder
    that never had an automated script to begin with.
    """
    if not skip_names:
        return suite, set()
    skip_set = {s.lower() for s in skip_names}
    filtered: list[tuple[str, list[str]]] = []
    emptied_by_skip: set[str] = set()
    for folder, scripts in suite:
        kept = [s for s in scripts if s.lower() not in skip_set]
        if scripts and not kept:
            emptied_by_skip.add(folder)
        filtered.append((folder, kept))
    return filtered, emptied_by_skip


def _select_suite(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    if args.only_folder:
        match = [f for f in SUITE if f[0].lower() == args.only_folder.lower()]
        if not match:
            print(f"No folder named '{args.only_folder}' in the suite.")
            sys.exit(1)
        return match

    if args.from_folder:
        names = [f[0].lower() for f in SUITE]
        target = args.from_folder.lower()
        if target not in names:
            print(f"No folder named '{args.from_folder}' in the suite.")
            sys.exit(1)
        return SUITE[names.index(target):]

    return SUITE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run every Hera UAT test folder's script(s), in order.")
    parser.add_argument("--from", dest="from_folder", default=None,
                         help="Resume from this folder name (skip everything before it)")
    parser.add_argument("--only", dest="only_folder", default=None,
                         help="Run only this one folder")
    parser.add_argument("--skip", action="append", default=[], metavar="SCRIPT",
                         help="Skip a specific script by filename (e.g. "
                              "--skip test_weekly_import.py). Can be given "
                              "multiple times to skip more than one script.")
    parser.add_argument("--excel", default=None, metavar="PATH",
                         help="Existing report file to accumulate results into. "
                              "Default: create one new timestamped report under "
                              "Reports/ and use it for every script in this run.")
    args = parser.parse_args()

    suite = _select_suite(args)
    suite, emptied_by_skip = _apply_skip(suite, args.skip)

    excel_path = Path(args.excel) if args.excel else new_versioned_copy()

    # Keep the Weekly Nomination CSV family dated for the current target
    # week BEFORE anything runs, and reuse that same week number for any
    # script needing an explicit --csv (see _extra_args_for) -- so a full
    # run works unattended across a week rollover with nothing to edit.
    print("  Checking weekly nomination CSVs are dated for next week…")
    target_week, target_year, _, _ = ensure_next_week()

    _banner("HERA MASTER UAT TEST RUNNER")
    print(f"  Excel report : {excel_path}")
    print(f"  Target week  : {target_week}/{target_year}")
    print("  Folders to run, in order:")
    for name, scripts in suite:
        if scripts:
            parts = []
            for s in scripts:
                extra = _extra_args_for(s, target_week)
                parts.append(f"{s} ({' '.join(extra)})" if extra else s)
            label = " -> ".join(parts)
        elif name in emptied_by_skip:
            label = "(every script skipped via --skip)"
        else:
            label = "(no test script — will be skipped)"
        print(f"    - {name}: {label}")

    results: list[tuple[str, str, str]] = []
    start = time.time()

    for folder, scripts in suite:
        if not scripts:
            _banner(f"{folder}  ->  (no automated test script)")
            if folder in emptied_by_skip:
                print("  Every script in this folder was skipped via --skip.")
            else:
                print("  This folder has no automated test script registered in SUITE.")
            print("  Skipping.")
            results.append((folder, "(none)", "SKIPPED"))
            continue
        for script in scripts:
            status = _run_script(folder, script, excel_path,
                                  extra_args=_extra_args_for(script, target_week))
            results.append((folder, script, status))

    elapsed_min = (time.time() - start) / 60

    _banner("SUMMARY")
    for folder, script, status in results:
        print(f"  {status:10s}  {folder:32s} {script}")
    print(f"\n  Total wall time: {elapsed_min:.1f} min")
    print(f"  Excel report   : {excel_path}")
    print("  Note: this only reflects whether each script ran to completion —")
    print("  actual pass/fail verdicts live in each folder's own test_results.csv")
    print("  and in the Excel report above.")


if __name__ == "__main__":
    main()
