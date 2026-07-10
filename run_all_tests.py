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
  8. Plant Communication - sending – no test script here (app.py/api_client.py/
                                      mqtt_client.py/config.py are the live
                                      sending service itself, not test code)
                                      → reported and skipped

Each script stays fully interactive on its own (MFA login, environment
choice, pass/fail confirmation prompts) — this runner just launches them
one after another in the same terminal, inheriting stdin/stdout, so using
any one of them by hand doesn't change. It does NOT interpret a script's
internal scenario verdicts (those live in each script's own
test_results.csv) — it only tracks whether the process ran to completion
or crashed, and prints a final summary of that.

Run:
    python run_all_tests.py
    python run_all_tests.py --from "Master Data"        # resume from a folder
    python run_all_tests.py --only "NetCommodityCost"   # just one folder
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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
    ("Plant Communication - sending", []),
]


def _banner(title: str, width: int = 70) -> None:
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def _run_script(folder: str, script: str) -> str:
    path = ROOT / folder / script
    if not path.exists():
        print(f"  [MISSING] {path}")
        return "MISSING"

    _banner(f"{folder}  ->  {script}")
    result = subprocess.run([sys.executable, str(path)])
    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    print(f"\n  [{status}] {folder} / {script}")
    return status


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
    args = parser.parse_args()

    suite = _select_suite(args)

    _banner("HERA MASTER UAT TEST RUNNER")
    print("  Folders to run, in order:")
    for name, scripts in suite:
        label = " -> ".join(scripts) if scripts else "(no test script — will be skipped)"
        print(f"    - {name}: {label}")

    results: list[tuple[str, str, str]] = []
    start = time.time()

    for folder, scripts in suite:
        if not scripts:
            _banner(f"{folder}  ->  (no automated test script)")
            print("  This folder holds the live sending service "
                  "(app.py/api_client.py/mqtt_client.py/config.py), not test code.")
            print("  Skipping.")
            results.append((folder, "(none)", "SKIPPED"))
            continue
        for script in scripts:
            status = _run_script(folder, script)
            results.append((folder, script, status))

    elapsed_min = (time.time() - start) / 60

    _banner("SUMMARY")
    for folder, script, status in results:
        print(f"  {status:10s}  {folder:32s} {script}")
    print(f"\n  Total wall time: {elapsed_min:.1f} min")
    print("  Note: this only reflects whether each script ran to completion —")
    print("  actual pass/fail verdicts live in each folder's own test_results.csv.")


if __name__ == "__main__":
    main()
