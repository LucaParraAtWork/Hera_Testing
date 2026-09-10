#!/usr/bin/env python3
"""
uat_excel_reporter.py – live Pass/Fail reporting into a Hera UAT report file
==============================================================================
Every test script's own _ask()-style prompt already asks the tester to
confirm Pass/Fail (and optional notes) in the console, but that confirmation
used to only land in a per-run test_results.csv under each folder's
test_artifacts*/ — never in an Excel file anyone could actually review.

record_results() closes that gap: given a list of (excel_id, result, notes)
tuples, it finds the matching "Test case ID" row — searched across every
sheet in the workbook (currently "Covered by Scripts", "Covered Manually",
"Missing from Scripts") since the same ID can live in any of them — and
writes the tester name, status (PASS/FAIL/SKIP/?) and a timestamped comment.
Nothing else on the row (title, steps, "Created via script", ...) is ever
touched.

Which Excel file gets written
------------------------------
The master template Hera_UAT_Test_Plan.xlsx is never written to directly.
Every one of the 9 test scripts takes an "--excel PATH" argument:

  --excel not given   -> a fresh timestamped COPY of the master template is
                         created under UAT Testing/Reports/ (e.g.
                         Reports/Hera_UAT_Test_Plan_20260910-143000.xlsx) and
                         the path is printed. This is the "new report per
                         run" behaviour — the master file is never mutated.
  --excel PATH given  -> results are written into that existing report file
                         instead, so several scripts (or several runs of the
                         same script) can accumulate into one consolidated
                         report for a single test cycle. Copy the path
                         printed by the first script's run and pass it to
                         every later one in the same cycle.

If that report file is open in Excel at save time (a PermissionError), the
results are appended to _pending_excel_updates.csv (tagged with which file
they were meant for) instead of being lost. Run this after closing Excel:

    py uat_excel_reporter.py --flush

To create a new versioned report file without running any test:

    py uat_excel_reporter.py --new
"""
from __future__ import annotations

import csv
import getpass
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
except ImportError:
    openpyxl = None

HERE = Path(__file__).resolve().parent
TEMPLATE_PATH = HERE / "Hera_UAT_Test_Plan.xlsx"
REPORTS_DIR = HERE / "Reports"
PENDING_CSV = HERE / "_pending_excel_updates.csv"

ID_COL = "Test case ID"
TESTER_COL = "tester"
STATUS_COL = "status"
COMMENT_COL = "comment"


def _default_tester() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def new_versioned_copy(template_path: Path | None = None) -> Path:
    """Copy the master template into Reports/ with a timestamped name."""
    template_path = template_path or TEMPLATE_PATH
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = REPORTS_DIR / f"{template_path.stem}_{stamp}{template_path.suffix}"
    shutil.copyfile(template_path, dest)
    print(f"  [Excel report] New report created: {dest}")
    print(f"  [Excel report] Pass --excel \"{dest}\" to later scripts to keep "
          f"adding results to this same file.")
    return dest


def _header_index(ws, name: str) -> int | None:
    for cell in ws[1]:
        if cell.value is not None and str(cell.value).strip() == name:
            return cell.column
    return None


def _build_id_index(wb) -> dict[str, tuple[str, int]]:
    """Map Test case ID -> (sheet_name, row_number), searching every sheet."""
    index: dict[str, tuple[str, int]] = {}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        col = _header_index(ws, ID_COL)
        if col is None:
            continue
        for row in range(2, ws.max_row + 1):
            val = ws.cell(row=row, column=col).value
            if val:
                index[str(val).strip()] = (sheet_name, row)
    return index


def _save_pending(rows, tester: str, source: str, xlsx_path: Path) -> None:
    file_exists = PENDING_CSV.exists()
    with PENDING_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["excel_id", "result", "notes", "tester", "source",
                        "xlsx_path", "saved_at"])
        stamp = datetime.now().isoformat(timespec="seconds")
        for excel_id, result, notes in rows:
            w.writerow([excel_id, result, notes, tester, source, str(xlsx_path), stamp])


def record_results(rows, tester: str | None = None,
                    xlsx_path: str | Path | None = None,
                    source: str = "") -> list[str] | None:
    """
    rows: iterable of (excel_id, result, notes) tuples.

    xlsx_path: an existing report file to write into. If None, a brand new
    timestamped copy of the master template is created under Reports/ and
    used instead (see module docstring).

    Returns the list of excel_ids that had no matching "Test case ID" row
    anywhere in the workbook (not an error — just reported so the caller can
    warn), or None if the report could not be opened/saved at all (e.g. open
    in Excel, or an explicit --excel path that doesn't exist) — in that case
    every row was queued to _pending_excel_updates.csv instead (except for a
    missing explicit path, which is a user error and isn't queued).
    """
    rows = list(rows)
    if not rows:
        return []

    if openpyxl is None:
        print("  [Excel report] openpyxl not installed — skipping Excel update.")
        return None

    if xlsx_path is not None:
        path = Path(xlsx_path)
        if not path.exists():
            print(f"  [Excel report] --excel \"{path}\" does not exist — skipping "
                  f"Excel update. Point it at a report file created by an earlier "
                  f"run (or omit --excel to start a brand new one).")
            return None
    else:
        path = new_versioned_copy()

    tester = tester or _default_tester()

    try:
        wb = openpyxl.load_workbook(path)
    except PermissionError:
        _save_pending(rows, tester, source, path)
        print(f"  [Excel report] '{path.name}' is open in another program (Excel?) — "
              f"could not read it. {len(rows)} result(s) queued in "
              f"{PENDING_CSV.name}. Close the file, then run "
              f"'py uat_excel_reporter.py --flush' from UAT Testing/ to apply them.")
        return None

    index = _build_id_index(wb)
    missing: list[str] = []
    updated = 0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    tag = f"[{stamp}" + (f" – {source}]" if source else "]")

    for excel_id, result, notes in rows:
        hit = index.get(excel_id)
        if hit is None:
            missing.append(excel_id)
            continue
        sheet_name, row = hit
        ws = wb[sheet_name]
        status_col = _header_index(ws, STATUS_COL)
        tester_col = _header_index(ws, TESTER_COL)
        comment_col = _header_index(ws, COMMENT_COL)
        if status_col:
            ws.cell(row=row, column=status_col, value=result)
        if tester_col:
            ws.cell(row=row, column=tester_col, value=tester)
        if comment_col:
            ws.cell(row=row, column=comment_col,
                    value=f"{tag} {notes}".strip() if notes else tag)
        updated += 1

    try:
        wb.save(path)
    except PermissionError:
        _save_pending(rows, tester, source, path)
        print(f"  [Excel report] '{path.name}' is open in another program (Excel?) — "
              f"could not save. {len(rows)} result(s) queued in "
              f"{PENDING_CSV.name}. Close the file, then run "
              f"'py uat_excel_reporter.py --flush' from UAT Testing/ to apply them.")
        return None

    print(f"  [Excel report] Updated {updated}/{len(rows)} row(s) in {path} "
          f"(tester: {tester}).")
    if missing:
        print(f"  [Excel report] No 'Test case ID' row found for: {', '.join(missing)}")
    return missing


def flush_pending() -> None:
    """Re-apply queued updates that failed earlier because a workbook was locked.

    Grouped by the target file they were originally meant for (a --flush can
    contain updates queued against several different report files).
    """
    if not PENDING_CSV.exists():
        print("No pending updates.")
        return
    with PENDING_CSV.open(newline="", encoding="utf-8") as f:
        pending = list(csv.DictReader(f))
    if not pending:
        print("No pending updates.")
        return

    by_target: dict[str, list[dict]] = {}
    for r in pending:
        by_target.setdefault(r["xlsx_path"], []).append(r)

    still_pending: list[dict] = []
    for target, group in by_target.items():
        rows = [(r["excel_id"], r["result"], r["notes"]) for r in group]
        tester = group[-1]["tester"]
        missing = record_results(rows, tester=tester, xlsx_path=target, source="flushed")
        if missing is None:
            still_pending.extend(group)

    if still_pending:
        with PENDING_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["excel_id", "result", "notes", "tester", "source",
                        "xlsx_path", "saved_at"])
            for r in still_pending:
                w.writerow([r["excel_id"], r["result"], r["notes"], r["tester"],
                            r["source"], r["xlsx_path"], r["saved_at"]])
        print(f"{len(pending) - len(still_pending)} update(s) applied, "
              f"{len(still_pending)} still pending (target still locked?).")
    else:
        PENDING_CSV.unlink()
        print(f"Applied all {len(pending)} pending update(s) and cleared the queue.")


if __name__ == "__main__":
    if "--flush" in sys.argv:
        flush_pending()
    elif "--new" in sys.argv:
        new_versioned_copy()
    else:
        print(__doc__)
