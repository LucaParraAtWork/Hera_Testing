#!/usr/bin/env python3
"""
_weekly_csv_sync.py – keeps the Weekly Nomination CSV family on "next week"
=============================================================================
Hera's weekly nomination screen only accepts nominations for the upcoming
week. The baseline test CSVs (Virya/Messer Week35 + the Week36 follow-on,
every V2 override and every NEG_* negative case) were dated to match
whatever "next week" was on the day they were last generated. Left alone,
that date drifts into the past as real time moves on.

Both test_weekly_import.py and test_weekly_ui.py import ensure_next_week()
from this module and call it as the very first thing in main(), before the
browser opens or anything else happens: it reads the baseline file's date,
compares it to the real upcoming ISO week, and — only if they've drifted
apart — shifts every file in FAMILY_FILES by the same day offset so the
whole family moves together and stays internally consistent.

Messer_Nomination_Week26.csv is deliberately excluded: it's a fixed,
already-past week used by the "overwrite after deadline" negative test
(Nom_WN_05) and must never be touched.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent

NORMAL_FMT = "%d/%m/%Y %H:%M"
WRONG_FMT = "%m/%d/%Y %H:%M"  # NEG13 deliberately uses the wrong format

# (filename, Start/End Time format). Order doesn't matter.
FAMILY_FILES: list[tuple[str, str]] = [
    ("Virya_Nomination_Week35.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_V2.csv", NORMAL_FMT),
    ("Messer_Nomination_Week35.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG03_ShiftedTimes.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG05_WrongParitySlot.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG06_InvalidTrailerType.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG07_WrongHeaders.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG08_MaintWithTrailer.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG10_OverlappingSlots.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG13_WrongDateFormat.csv", WRONG_FMT),
    ("Virya_Nomination_Week35_NEG15_TwoDropOff.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG16_EditedValues.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG17_PickUpEmptyPlant.csv", NORMAL_FMT),
    ("Virya_Nomination_Week35_NEG18_WrongPickUpType.csv", NORMAL_FMT),
    ("Virya_Nomination_Week36.csv", NORMAL_FMT),
    ("Virya_Nomination_Week36_V2.csv", NORMAL_FMT),
]

# The file used to detect drift. Must be present.
_BASELINE_FILE = "Virya_Nomination_Week35.csv"
_BASELINE_FMT = NORMAL_FMT


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _next_week_monday(today: date | None = None) -> date:
    """Monday of the ISO week *after* the current one, relative to today."""
    today = today or date.today()
    return _monday_of(today) + timedelta(days=7)


def _first_data_date(path: Path, fmt: str) -> date | None:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        if len(row) > 1 and row[1].strip():
            try:
                return datetime.strptime(row[1].strip(), fmt).date()
            except ValueError:
                continue
    return None


def _shift_file(path: Path, fmt: str, delta: timedelta) -> None:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        for i in (1, 2):  # Start Time, End Time
            if len(row) > i and row[i].strip():
                dt = datetime.strptime(row[i].strip(), fmt)
                row[i] = (dt + delta).strftime(fmt)
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def ensure_next_week(quiet: bool = False) -> tuple[int, int]:
    """Re-date the whole CSV family onto next week if it has drifted.

    Returns (target_week, target_year) — the ISO week/year every test
    script should target from here on, for this run.
    """
    target_monday = _next_week_monday()
    target_year, target_week, _ = target_monday.isocalendar()

    baseline_path = HERE / _BASELINE_FILE
    if not baseline_path.exists():
        raise FileNotFoundError(
            f"Baseline CSV not found, cannot verify freshness: {baseline_path}")

    current_date = _first_data_date(baseline_path, _BASELINE_FMT)
    if current_date is None:
        raise RuntimeError(f"Could not read a baseline date from {baseline_path}")
    current_monday = _monday_of(current_date)

    delta = target_monday - current_monday
    if delta.days == 0:
        if not quiet:
            print(f"  Weekly nomination CSVs already target next week "
                  f"({target_week}/{target_year}) — no changes needed.")
        return target_week, target_year

    if not quiet:
        cur_year, cur_week, _ = current_monday.isocalendar()
        print(f"  Weekly nomination CSVs are stale (baseline was week "
              f"{cur_week}/{cur_year}) — shifting {delta.days:+d} day(s) to "
              f"reach next week ({target_week}/{target_year})...")

    touched = 0
    for name, fmt in FAMILY_FILES:
        path = HERE / name
        if not path.exists():
            continue
        _shift_file(path, fmt, delta)
        touched += 1

    if not quiet:
        print(f"  Done — {touched} file(s) re-dated.")

    return target_week, target_year


if __name__ == "__main__":
    ensure_next_week()
