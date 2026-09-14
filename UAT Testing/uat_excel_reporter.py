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
import os
import shutil
import subprocess
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

# When False (default), a script's own auto-detected PASS/FAIL/etc. is shown
# to the tester purely as a neutral observation -- it never pre-fills the
# verdict prompt's default and Enter alone never accepts it. Flip this back
# to True once auto-detection is trusted enough to suggest a default again;
# every one of the 10 scripts' _ask() wrappers picks this up automatically
# through ask_verdict(), no per-script changes needed.
AUTO_VALIDATION_ENABLED = False

_VERDICT_WIDTH = 74
_VERDICT_KEYS = {
    "p": "PASS", "pass": "PASS",
    "f": "FAIL", "fail": "FAIL",
    "s": "SKIP", "skip": "SKIP",
    "i": "?", "inconclusive": "?",
}


def clear_screen() -> None:
    """Clear the terminal so each test starts on a fresh screen.

    Called at the top of each test iteration (before its pre-run banner),
    never inside ask_verdict() itself -- that keeps the automation output
    for the test currently running (navigation, screenshots, errors)
    visible all the way up to the confirmation prompt for that same test.
    """
    os.system("cls" if os.name == "nt" else "clear")


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


def get_test_case_info(excel_id: str, template_path: Path | None = None) -> dict | None:
    """Look up a Test case ID's authored title/steps/expected result.

    Always reads the master template (never a --excel report copy) so the
    printed steps are the canonical, up-to-date UAT wording regardless of
    which report file this run happens to be writing results into.
    """
    if openpyxl is None:
        return None
    path = template_path or TEMPLATE_PATH
    if not path.exists():
        return None
    wb = openpyxl.load_workbook(path, data_only=True)
    hit = _build_id_index(wb).get(excel_id)
    if hit is None:
        return None
    sheet_name, row = hit
    ws = wb[sheet_name]

    def _get(col_name: str):
        col = _header_index(ws, col_name)
        return ws.cell(row=row, column=col).value if col else None

    return {
        "title": _get("Test case title"),
        "steps": _get("Input with test step by step"),
        "expected": _get("Expected results"),
    }


def print_test_case_info(excel_id: str, template_path: Path | None = None) -> None:
    """Print the authored title/steps/expected result for excel_id, if found."""
    info = get_test_case_info(excel_id, template_path)
    if not info:
        return
    print(f"  ┌─ UAT steps ({excel_id}) " + "─" * max(0, 40 - len(excel_id)))
    if info.get("title"):
        print(f"  │ {info['title']}")
    if info.get("steps"):
        for line in str(info["steps"]).splitlines():
            print(f"  │ {line}")
    if info.get("expected"):
        print(f"  │ Expected: {info['expected']}")
    print("  └" + "─" * 50)


def _open_csv_preview(csv_path):
    """Open csv_path in Notepad (a plain preview, spawned as its own process
    so it can be closed on its own without touching any other window) so the
    tester can see exactly what was uploaded while deciding the verdict.
    Returns the Popen handle, or None if it couldn't be opened -- never
    fatal to the verdict prompt itself.
    """
    if os.name != "nt" or not csv_path:
        return None
    path = Path(csv_path)
    if not path.exists():
        return None
    try:
        return subprocess.Popen(["notepad.exe", str(path)])
    except Exception:
        return None


def _close_csv_preview(proc) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:
        pass


def ask_verdict(tc_id: str, title: str | None, expect: str, *,
                 auto: str | None = None, reason: str | None = None,
                 index: int | None = None, total: int | None = None,
                 excel_id: str | list[str] | None = None,
                 csv_path: str | Path | None = None) -> tuple[str, str]:
    """Print one uniform confirmation block and return (result, notes).

    This is the single shared prompt every test script's own _ask() wrapper
    delegates to, so all 10 scripts read identically in the terminal. It
    reads AUTO_VALIDATION_ENABLED itself -- when False (default) the
    auto-detected result is shown only as a neutral "Detected" observation
    and never becomes the Enter-key default; when True it's shown as the
    suggested default instead. Either way the underlying auto-detection
    code in each script is untouched, so flipping the flag later needs no
    changes anywhere else.

    excel_id may be a single ID or a list (some scenarios are credited
    against more than one row in the Excel) -- steps are printed for every
    id that has a matching row.

    csv_path: for CSV import scenarios, the file that was uploaded -- opened
    in Notepad right as the verdict prompt appears, and closed again right
    after a verdict is recorded, so the tester can see exactly what was
    imported without having to go find the file themselves.
    """
    excel_ids = excel_id if isinstance(excel_id, list) else [excel_id or tc_id]
    w = _VERDICT_WIDTH

    print("\n" + "=" * w)
    if index and total:
        print(f" TEST {index} / {total}   —   {tc_id}")
    else:
        print(f" {tc_id}")
    if title:
        print(f" {title}")
    print("-" * w)

    exp_label = expect if expect in ("PASS", "FAIL") else "UNKNOWN / TBD"
    print(f" Expected result : {exp_label}")

    print(" Steps           :")
    found_any = False
    for eid in excel_ids:
        if get_test_case_info(eid):
            print_test_case_info(eid)
            found_any = True
    if not found_any:
        print("   (no matching row found in the Excel for this ID -- steps not shown)")

    default = None
    if auto is not None:
        detected_label = auto if auto in ("PASS", "FAIL", "SKIP") else "?"
        detail = f" -- {reason}" if reason else ""
        print(f" Detected        : {detected_label}{detail}")
        if AUTO_VALIDATION_ENABLED:
            print("                    (auto-validation ON -- shown as the suggested default)")
            if auto in ("PASS", "FAIL"):
                default = auto
        else:
            print("                    (auto-validation OFF -- this is an observation only,")
            print("                     not a suggested verdict)")
    print("-" * w)

    if default:
        prompt = f" >>> YOUR VERDICT (P=Pass / F=Fail / S=Skip / I=Inconclusive / Enter={default}): "
    else:
        prompt = " >>> YOUR VERDICT (P=Pass / F=Fail / S=Skip / I=Inconclusive): "

    csv_proc = _open_csv_preview(csv_path)
    if csv_proc is not None:
        print(f" (Opened {Path(csv_path).name} in Notepad for reference -- "
              f"it will close once you answer.)")

    result = None
    notes = ""
    try:
        while result is None:
            try:
                raw = input(prompt).strip().lower()
            except EOFError:
                result = "?"
                notes = "Auto-recorded as Inconclusive: no interactive input available (EOF)."
                print(f"\n {notes}")
                break
            if not raw and default:
                result = default
            elif raw in _VERDICT_KEYS:
                result = _VERDICT_KEYS[raw]
            else:
                hint = " (or press Enter to accept the default)" if default else ""
                print(f"     Please enter P, F, S, or I{hint}.")

        if not notes:
            try:
                notes = input("     Notes (optional): ").strip()
            except EOFError:
                notes = notes or ""
    finally:
        _close_csv_preview(csv_proc)

    print("=" * w)
    print(f" -> Recorded: {result}" + (f"   ({notes})" if notes else ""))
    print("=" * w)
    return result, notes


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
