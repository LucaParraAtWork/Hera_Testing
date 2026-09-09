"""
update_nom_wn_05_07_jul2026.py
Credits the two new scenarios added on 2026-07-10:

  Nom_WN_05 -> test_weekly_import.py (new TEST_CASES entry): imports
  Messer_Nomination_Week26.csv, a fixed week already in the past relative
  to "today" — using the stricter _detect_success_toast_only() detector
  since a deadline block may not raise a loud error, just omit the normal
  success confirmation.

  Nom_WN_07 -> test_weekly_ui.py (new NAV_01 scenario): opens the
  date-picker calendar via the current-week label (not the </> arrows),
  picks a day in an adjacent month, verifies the week updates, then
  returns to the target week and verifies no stale data remains.

Run:
    python update_nom_wn_05_07_jul2026.py
"""
import openpyxl
from openpyxl.styles import Alignment

PATH = r"C:\Users\LucaParraGabarron\Desktop\ViryaH2\Hera_Testing\UAT Testing\Hera_UAT_Test_Plan.xlsx"

WRAP = Alignment(wrap_text=True, vertical="top")

PATCHES = {
    "Nom_WN_05": {
        "Extra description ": (
            "[AUTOMATED 2026-07-10] Covered by test_weekly_import.py — new "
            "TEST_CASES entry importing Messer_Nomination_Week26.csv, a fixed "
            "week (26/2026) already in the past relative to today. Uses the new "
            "_detect_success_toast_only() detector: PASS only if an explicit "
            "success toast is found (meaning Hera wrongly allowed the overwrite); "
            "FAIL otherwise, whether from an explicit rejection message or simply "
            "no success confirmation appearing — matching this row's expected "
            "result ('Rejected or requires explicit approval'). NOT yet run live."
        ),
        "Created via script": "test_weekly_import.py",
    },
    "Nom_WN_07": {
        "Extra description ": (
            "[AUTOMATED 2026-07-10] Covered by test_weekly_ui.py — new NAV_01 "
            "scenario: opens the date-picker calendar via the current-week label "
            "(top right), not the </> arrows already used everywhere else in this "
            "script, picks a day from an adjacent month directly in the visible "
            "6-week grid, verifies the week label/grid updates, then returns to "
            "the target week and verifies it's correctly restored (no stale data). "
            "NOT yet run live — the calendar-popup selectors are a best-effort "
            "guess and may need adjustment on the first real run."
        ),
        "Created via script": "test_weekly_ui.py",
    },
}

wb = openpyxl.load_workbook(PATH)
ws = wb.active

hdr = [str(c.value or "") for c in ws[1]]
COL = {h: i + 1 for i, h in enumerate(hdr)}   # 1-indexed for openpyxl, no .strip()

updated = []
for row in ws.iter_rows(min_row=2):
    tc_id_cell = row[COL["Test case ID"] - 1]
    tc_id = str(tc_id_cell.value or "").strip()
    if tc_id in PATCHES:
        patch = PATCHES[tc_id]
        for col_name, new_val in patch.items():
            col_idx = COL.get(col_name)
            if col_idx:
                cell = row[col_idx - 1]
                cell.value = new_val
                cell.alignment = WRAP
        updated.append(tc_id)
        print(f"  Updated {tc_id}")

if not updated:
    print("  No matching rows found — check Test case ID column.")
else:
    wb.save(PATH)
    print(f"\nSaved {PATH}")
    print(f"Updated {len(updated)} rows: {updated}")
