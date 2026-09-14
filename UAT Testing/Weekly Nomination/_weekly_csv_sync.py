#!/usr/bin/env python3
"""
_weekly_csv_sync.py – keeps the Weekly Nomination CSV family on "next week"
=============================================================================
Hera's weekly nomination screen only accepts nominations for the upcoming
week. The baseline test CSVs (Virya/Messer "base" week + the "base+1" week
follow-on, every V2 override and every NEG_* negative case) must always
target the real upcoming ISO week, and their FILENAMES carry that same week
number (e.g. "Virya_Nomination_Week38.csv" really does contain week-38
dates) — the number is never a stale label, so what a script prints to the
terminal always matches what's actually inside the file.

Both test_weekly_import.py and test_weekly_ui.py import ensure_next_week()
from this module and call it as the very first thing in main(), before the
browser opens or anything else happens: it finds the current base week from
whatever numbered file exists on disk, compares it to the real upcoming ISO
week, and — only if they've drifted apart — shifts every file in
FAMILY_FILES by the same day offset AND renames it to carry the new week
number, so the whole family moves together, stays internally consistent,
and its filenames never lie about their contents.

Messer_Nomination_Week26.csv is deliberately excluded: it's a fixed,
already-past week used by the "overwrite after deadline" negative test
(Nom_WN_05) and must never be touched.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent

NORMAL_FMT = "%d/%m/%Y %H:%M"
WRONG_FMT = "%m/%d/%Y %H:%M"  # NEG13 deliberately uses the wrong format

# (prefix, suffix, week_offset, Start/End Time format). week_offset 0 = the
# base ("target") week; 1 = the following week (used by test_weekly_ui.py's
# setup steps, which need a second, still-untouched week to import).
# filename = f"{prefix}{week_number}{suffix}.csv"
FAMILY_FILES: list[tuple[str, str, int, str]] = [
    ("Virya_Nomination_Week",  "",                          0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_V2",                        0, NORMAL_FMT),
    ("Messer_Nomination_Week", "",                           0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG03_ShiftedTimes",        0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG05_WrongParitySlot",     0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG06_InvalidTrailerType",  0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG07_WrongHeaders",        0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG08_MaintWithTrailer",    0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG10_OverlappingSlots",    0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG13_WrongDateFormat",     0, WRONG_FMT),
    ("Virya_Nomination_Week",  "_NEG15_TwoDropOff",          0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG16_EditedValues",        0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG17_PickUpEmptyPlant",    0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_NEG18_WrongPickUpType",     0, NORMAL_FMT),
    ("Virya_Nomination_Week",  "",                           1, NORMAL_FMT),
    ("Virya_Nomination_Week",  "_V2",                        1, NORMAL_FMT),
]

# The (prefix, suffix) used to discover the current base week on disk, and
# to re-derive its content date for drift detection.
_BASE_PREFIX = "Virya_Nomination_Week"
_BASE_RE = re.compile(rf"^{re.escape(_BASE_PREFIX)}(\d+)\.csv$")
_BASE_FMT = NORMAL_FMT


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


def _discover_base_week() -> tuple[int, Path]:
    """Find the current base week's file: the smallest week number among
    bare "Virya_Nomination_Week<N>.csv" files (the base+1 file, when it
    exists, always carries the next-larger number)."""
    matches = []
    for p in HERE.iterdir():
        m = _BASE_RE.match(p.name)
        if m:
            matches.append((int(m.group(1)), p))
    if not matches:
        raise FileNotFoundError(
            f"No {_BASE_PREFIX}<N>.csv file found in {HERE} — cannot verify freshness.")
    matches.sort(key=lambda t: t[0])
    if len(matches) > 2:
        extra = ", ".join(p.name for _, p in matches[2:])
        print(f"  [WARN] Unexpected extra Week-numbered file(s) found: {extra} "
              f"— ignoring them and using the two lowest-numbered files.")
    return matches[0]


def ensure_next_week(quiet: bool = False) -> tuple[int, int, int, int]:
    """Re-date (and rename) the whole CSV family onto next week if it has drifted.

    Returns (target_week, target_year, next_week, next_week_year) — the ISO
    week/year every test script should target from here on for this run, and
    the following week (used for the "base+1" fixture), both real ISO
    week/year pairs (correctly handling a year rollover, e.g. week 52 -> 1).
    """
    target_monday = _next_week_monday()
    target_year, target_week, _ = target_monday.isocalendar()
    next_monday = target_monday + timedelta(days=7)
    next_year, next_week, _ = next_monday.isocalendar()

    old_week, base_path = _discover_base_week()
    current_date = _first_data_date(base_path, _BASE_FMT)
    if current_date is None:
        raise RuntimeError(f"Could not read a baseline date from {base_path}")
    current_monday = _monday_of(current_date)

    delta = target_monday - current_monday
    if delta.days == 0 and old_week == target_week:
        if not quiet:
            print(f"  Weekly nomination CSVs already target next week "
                  f"({target_week}/{target_year}) — no changes needed.")
        return target_week, target_year, next_week, next_year

    if not quiet:
        cur_year, cur_week, _ = current_monday.isocalendar()
        print(f"  Weekly nomination CSVs are stale (base was week "
              f"{cur_week}/{cur_year}, filenames said 'Week{old_week}') — "
              f"shifting {delta.days:+d} day(s) and renaming to reach next "
              f"week ({target_week}/{target_year})...")

    # Old filenames on disk were never ISO-calendar-safe (they're plain
    # "base_week + offset" integer labels going back to whenever
    # _ensure_week36_csvs() first generated the "+1" file) — replicate that
    # exact naive arithmetic here so we find the files that are actually
    # there, regardless of the CONTENT date (which may already have drifted
    # ahead of the filename, as happens the first time this rename-aware
    # version of the script runs against older files).
    planned = []
    for prefix, suffix, offset, fmt in FAMILY_FILES:
        old_num = old_week + offset
        new_monday = target_monday + timedelta(days=7 * offset)
        new_num = new_monday.isocalendar()[1]

        old_path = HERE / f"{prefix}{old_num}{suffix}.csv"
        new_path = HERE / f"{prefix}{new_num}{suffix}.csv"
        if not old_path.exists():
            continue
        planned.append((old_num, fmt, old_path, new_path))

    # The bare "Virya_Nomination_Week<N>.csv" name is shared by both the
    # base file (offset 0) and the "+1" file (offset 1) -- e.g. shifting
    # base week 38->39 while the existing "+1" file already sits at 39
    # (38+1) collides if the base is renamed first (FileExistsError renaming
    # into a slot the +1 file hasn't vacated yet). Renaming highest
    # old-week-number first guarantees every slot is vacated before
    # something else needs to move into it, since a forward week shift
    # always moves every file to a strictly higher number.
    planned.sort(key=lambda t: t[0], reverse=True)

    touched = 0
    for old_num, fmt, old_path, new_path in planned:
        _shift_file(old_path, fmt, delta)
        if old_path != new_path:
            old_path.rename(new_path)
        touched += 1

    if not quiet:
        print(f"  Done — {touched} file(s) re-dated and renamed.")

    return target_week, target_year, next_week, next_year


if __name__ == "__main__":
    ensure_next_week()
