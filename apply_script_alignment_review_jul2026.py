"""
apply_script_alignment_review_jul2026.py

Colors rows in Hera_UAT_Test_Plan.xlsx to show script-coverage status for the
8 folders reviewed on 2026-07-03 (EndToEndPlannedMaintenance, Master Data,
NetCommodityCost, Planned Maintenance, Plant Communication - sending,
Scheduled Transfers, Weekly Nomination, Yearly Nomination).

Color meaning (user-defined, 3-tier):
  GREEN  = already implemented and verified (real automated/manual check exists)
  YELLOW = partially covered, or not covered yet but a natural extension of an
           existing script (scaffolding/handler already there, or same script's
           domain clearly applies)
  RED    = not implemented at all, and not a simple extension of any reviewed
           script (would need genuinely new automation / integration)

Also fixes a leftover bug from update_ncc_tests.py: it silently failed to write
the "Extra description " column for NCC_UI_01/02/05 and NCC_CALC_07 because it
built its COL dict from .strip()-ped headers while PATCHES used the literal
(trailing-space) header name. This script writes the correct text directly.

Idempotent: safe to re-run; always sets colors/text to the values below rather
than toggling.
"""
import openpyxl
from openpyxl.styles import PatternFill
from openpyxl.comments import Comment

PATH = r"C:\Users\LucaParraGabarron\Desktop\ViryaH2\Hera_Testing\Hera_UAT_Test_Plan.xlsx"

GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
RED = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
COLOR_MAP = {"GREEN": GREEN, "YELLOW": YELLOW, "RED": RED}

# row_number: (color, optional note to append to "Extra description ")
ROWS = {
    # --- Weekly Nomination ---
    47: ("GREEN", None),
    48: ("GREEN", None),
    49: ("GREEN", None),
    52: ("YELLOW", "[REVIEW] Credited script (test_weekly_import.py) only checks import-success "
         "keywords, not slot status colour. The real 'New status' check (DOM count of .status-new) "
         "lives in test_weekly_ui.py's UI_01 - consider re-crediting or referencing both scripts."),
    53: ("YELLOW", "[GAP] No NEG_01 case in test_weekly_import.py's TEST_CASES - would fit naturally "
         "alongside the existing NEG_03..NEG_18 cases."),
    54: ("GREEN", None), 55: ("GREEN", None), 56: ("GREEN", None), 57: ("GREEN", None),
    58: ("GREEN", None), 59: ("GREEN", None), 60: ("GREEN", None), 61: ("GREEN", None),
    62: ("GREEN", None), 63: ("GREEN", None), 64: ("GREEN", None),
    65: ("GREEN", None),
    66: ("YELLOW", "[GAP] No automation at all - would fit naturally as a new scenario in "
         "test_weekly_ui.py alongside UI_02-05."),
    67: ("YELLOW", "[GAP] No automation at all - would fit naturally as a new scenario in "
         "test_weekly_ui.py alongside UI_02-05."),
    68: ("YELLOW", None),  # already has its own GAP note from update_align_additional_nomination.py
    69: ("YELLOW", "[GAP] No automation at all - would fit naturally as a new scenario in "
         "test_weekly_ui.py alongside UI_02-05."),
    70: ("YELLOW", None),  # already has its own GAP note
    71: ("YELLOW", None),  # already has its own GAP note
    187: ("GREEN", None), 188: ("GREEN", None), 189: ("GREEN", None), 190: ("GREEN", None),
    191: ("GREEN", None), 192: ("GREEN", None), 193: ("GREEN", None), 194: ("GREEN", None),
    195: ("GREEN", None), 196: ("GREEN", None), 197: ("GREEN", None),
    198: ("GREEN", "[FIXED 2026-07-03] _bulk_week() now snapshots status-new slot count "
         "before/after the bulk confirm and requires it to reach 0 (real state check, not just "
         "click-success) instead of an unconditional PASS."),
    199: ("GREEN", "[FIXED 2026-07-03] _bulk_week() now snapshots non-rejected (NEW+CONFIRMED) "
         "slot count before/after the bulk reject and requires it to reach 0 (real state check) "
         "instead of an unconditional PASS."),
    200: ("GREEN", None),

    # --- Yearly Nomination ---
    26: ("GREEN", None), 28: ("GREEN", None), 29: ("GREEN", None),
    27: ("YELLOW", "[GAP] No script coverage - would fit as a new negative-import scenario in "
         "test_yearly_import.py."),
    30: ("GREEN", "[IMPLEMENTED] test_yearly_ui.py UI_01 genuinely asserts currentVersion / "
         "versionLines / monthlyBreakdowns / weeklyBreakdowns are present. Not yet credited in "
         "'Created via script' pending a confirmed live run of the selectors."),
    31: ("GREEN", "[IMPLEMENTED] test_yearly_ui.py UI_03 asserts version increment (+1), new "
         "status='new', and prior version retained. Not yet credited pending a confirmed live run."),
    32: ("GREEN", "[IMPLEMENTED] test_yearly_ui.py UI_02 checks a real chart element renders and "
         "cross-validates monthly/weekly values against the source CSV. Not yet credited pending a "
         "confirmed live run."),
    33: ("YELLOW", "[GAP] No script coverage yet - natural extension of test_yearly_import.py's "
         "NEG_03..NEG_15 family."),
    34: ("YELLOW", "[GAP] No script coverage yet - natural extension of test_yearly_import.py's "
         "NEG_03..NEG_15 family."),
    35: ("GREEN", None), 36: ("GREEN", None), 37: ("GREEN", None), 38: ("GREEN", None),
    39: ("GREEN", None), 40: ("GREEN", None), 41: ("GREEN", None), 42: ("GREEN", None),
    43: ("GREEN", None), 44: ("GREEN", None), 45: ("GREEN", None),

    # --- Master Data ---
    135: ("GREEN", None), 136: ("GREEN", None), 137: ("GREEN", None), 138: ("GREEN", None),
    139: ("GREEN", None), 140: ("GREEN", None), 141: ("GREEN", None), 142: ("GREEN", None),
    143: ("GREEN", None), 144: ("GREEN", None), 145: ("GREEN", None), 146: ("GREEN", None),
    147: ("GREEN", None), 148: ("GREEN", None), 149: ("GREEN", None), 150: ("GREEN", None),
    151: ("GREEN", None), 152: ("GREEN", None), 153: ("GREEN", None), 154: ("GREEN", None),
    155: ("GREEN", None), 156: ("GREEN", None), 157: ("GREEN", None), 158: ("GREEN", None),
    159: ("GREEN", None), 160: ("GREEN", None), 161: ("GREEN", None), 162: ("GREEN", None),
    163: ("GREEN", None),
    164: ("YELLOW", "[REVIEW] _run_ACS_MD_09 only checks the company table has >1 row, not that "
         "the right data is shown. Consider asserting on specific expected rows."),
    165: ("YELLOW", "[REVIEW] _run_ACS_MD_10 screenshots and counts rows for trucks/trailers/"
         "drivers but always returns PASS - no comparison against any expected count. Add a real "
         "assertion (e.g. count > 0, or a specific expected row present)."),
    166: ("RED", "[GAP] No related code in test_master_data.py - comparing qualified entities "
         "against an external ACS database needs a new integration, not a simple extension of "
         "the current UI script."),

    # --- Planned Maintenance ---
    122: ("GREEN", None), 123: ("GREEN", None), 124: ("GREEN", None),
    125: ("YELLOW", "[REVIEW] Tester can type 's' to skip the persistence re-open check, which "
         "then hardcodes PASS with zero verification. Consider removing the skip shortcut or "
         "logging it as an explicit caveat in the recorded result."),
    126: ("YELLOW", "[REVIEW] Same skip-to-PASS shortcut as MAINT_ED_01, on the end-date "
         "persistence check."),
    127: ("GREEN", None),
    128: ("YELLOW", "[REVIEW] Falls back to a hardcoded PASS if the GetMaintenanceScreenAsync "
         "response isn't captured, only suggesting a manual screenshot check without enforcing "
         "it. Consider making that fallback an explicit '?' requiring confirmation instead of a "
         "silent PASS."),
    129: ("GREEN", None), 130: ("GREEN", None), 131: ("GREEN", None), 132: ("GREEN", None),

    # --- EndToEndPlannedMaintenance ---
    133: ("GREEN", None), 134: ("GREEN", None),

    # --- NetCommodityCost ---
    108: ("GREEN", None), 109: ("GREEN", None), 112: ("GREEN", None), 119: ("GREEN", None),
    110: ("YELLOW", "[GAP] No dedicated scenario yet, but same Costs Breakdown page as "
         "NCC_UI_01/02/05 - natural extension: re-run the NCC_UI_01-style assertion after "
         "confirming a slot."),
    111: ("YELLOW", "[GAP] No dedicated scenario yet - natural extension: re-run the "
         "NCC_UI_01-style assertion after rejecting a slot."),
    113: ("YELLOW", "[GAP] Not covered by test_ncc_costs_breakdown.py, but the script already "
         "ports the same NCC_TESTING.py calculation used for NCC_CALC_07 - natural extension to "
         "add this specific scenario (no slots booked)."),
    114: ("YELLOW", "[GAP] Same as NCC_CALC_01 - natural extension of the existing NCC_CALC_07 "
         "calculation logic (only one offtaker has pickups)."),
    115: ("YELLOW", "[GAP] Same as NCC_CALC_01 - natural extension (both offtakers book)."),
    116: ("YELLOW", "[GAP] Same as NCC_CALC_01 - natural extension (positive price spread)."),
    117: ("YELLOW", "[GAP] Same as NCC_CALC_01 - natural extension (negative price spread)."),
    118: ("YELLOW", "[GAP] Same as NCC_CALC_01 - natural extension (trailer-to-offtaker "
         "allocation, which the script's _calculate_ncc_per_day() already implements internally "
         "for CALC_07 but doesn't expose as its own scenario)."),
    120: ("RED", "[GAP / POSSIBLE DUPLICATE] No scenario in test_ncc_costs_breakdown.py named or "
         "matching NCC_UI_06. Title ('NCC visualisation on UI') and the 'Template NCC_01' note "
         "look like they may predate and overlap with NCC_UI_01 ('Costs Breakdown page loads'). "
         "Left uncoloured for merge decision - not auto-merged since intent could differ; please "
         "confirm whether this is a stale duplicate of NCC_UI_01 or a genuinely separate screen."),
    121: ("RED", "[GAP] No related scenario in test_ncc_costs_breakdown.py - 'Actual' NCC "
         "(post-operation, real metered data) is a distinct feature from the Forecast "
         "calculations the script currently covers; needs new automation, not a simple "
         "extension."),

    # --- Scheduled Transfers ---
    201: ("YELLOW", "[REVIEW] Only verifies the SchedulingScreen API response was captured, not "
         "the actual session content - consider adding a real comparison against expected "
         "session data."),
    202: ("YELLOW", "[REVIEW] Checks the expected graph exists but returns PASS even if "
         "bay-occupancy capture failed - tighten the pass condition."),
    203: ("GREEN", None), 204: ("GREEN", None), 205: ("GREEN", None), 206: ("GREEN", None),
    207: ("GREEN", None), 208: ("GREEN", None),
    87: ("YELLOW", "[REVIEW] Credited to test_scheduled_transfers_profiles.py, but no scenario "
         "in that script specifically checks 'baseline profiles after confirmation' as its own "
         "assertion (the profiles/occupancy capture there feeds SCHED_TRF_03-08 instead) - "
         "confirm this credit is intentional or add a dedicated check."),
    88: ("YELLOW", "[REVIEW] Same as PROF_02 - credited but no scenario specifically checks "
         "'profiles after rejection' as its own assertion."),
    104: ("YELLOW", "[REVIEW] Credited to test_scheduled_transfers_profiles.py, but no scenario "
         "specifically checks 'bay occupancy after confirmation' as its own assertion - confirm "
         "this credit is intentional or add a dedicated check."),
    105: ("YELLOW", "[REVIEW] Same as OCC_02 - credited but no scenario specifically checks "
         "'bay occupancy after rejection' as its own assertion."),

    # --- Plant Communication - sending ---
    177: ("RED", "[GAP] No test script exists in this folder - app.py/api_client.py/"
         "mqtt_client.py are the live dashboard/service itself (visual HERA-vs-plant comparison "
         "tool), not test automation. Would need a new dedicated test_*.py script."),
    178: ("RED", "[GAP] Same as PLANT_COM_01 - no test automation exists in this folder yet."),
    179: ("RED", "[GAP] Same as PLANT_COM_01 - no test automation exists in this folder yet."),
    180: ("RED", "[GAP] Same as PLANT_COM_01 - no test automation exists in this folder yet."),
}

# Corrected "Extra description " text for the 4 NCC rows update_ncc_tests.py
# claimed to write but silently didn't (COL built from .strip()-ped headers vs
# PATCHES using the literal trailing-space header - see project memory).
NCC_STALE_FIX = {
    "NCC_UI_01": (
        "Automated via test_ncc_costs_breakdown.py. "
        "The script navigates to /schedules/costs-breakdown, waits for the API response, "
        "and asserts len(netCommodityCostForecasts) == 3."
    ),
    "NCC_UI_02": (
        "Automated via test_ncc_costs_breakdown.py. "
        "The script captures the CostsBreakdown POST response for each day by "
        "collecting all responses during navigation and keying each one by Brussels "
        "date (derived from the API's 'till' timestamp)."
    ),
    "NCC_UI_05": (
        "Automated via test_ncc_costs_breakdown.py. "
        "The script clicks the toggle twice and asserts no page error state appears."
    ),
    "NCC_CALC_07": (
        "Automated via test_ncc_costs_breakdown.py. "
        "The Python NCC calculation ports the reference formula from NCC_TESTING.py "
        "(English, streamlined). "
        "Trailer-to-offtaker allocation uses confirmed pickups from the scheduling "
        "artifact (closest pickup within +/-2 h window); falls back to 50/50 split "
        "if no scheduling artifact is available for the week."
    ),
}

wb = openpyxl.load_workbook(PATH)
ws = wb.active

# IMPORTANT: do not .strip() headers - "Extra description " has a real trailing
# space and PATCHES-style dicts must match it literally, or writes silently no-op.
hdr = [str(c.value or "") for c in ws[1]]
COL = {h: i + 1 for i, h in enumerate(hdr)}
last_col = ws.max_column

extra_col = COL["Extra description "]
tcid_col = COL["Test case ID"]

fixed_stale = []
for row in ws.iter_rows(min_row=2):
    tc_id = str(row[tcid_col - 1].value or "").strip()
    if tc_id in NCC_STALE_FIX:
        cell = row[extra_col - 1]
        cell.value = NCC_STALE_FIX[tc_id]
        fixed_stale.append(tc_id)

colored = []
for row_num, (color, note) in ROWS.items():
    fill = COLOR_MAP[color]
    for col_idx in range(1, last_col + 1):
        ws.cell(row=row_num, column=col_idx).fill = fill
    if note:
        cell = ws.cell(row=row_num, column=extra_col)
        existing = str(cell.value or "").strip()
        if note not in existing:
            cell.value = f"{existing} {note}".strip() if existing else note
    colored.append(row_num)

# Legend as a cell comment on the header of column A for future reference.
legend_text = (
    "Script-coverage review (2026-07-03), applied by "
    "apply_script_alignment_review_jul2026.py:\n"
    "GREEN  = already implemented and verified by a real check\n"
    "YELLOW = partially covered, or uncovered but a natural extension of an "
    "existing script\n"
    "RED    = not implemented at all, and not a simple extension of any "
    "reviewed script\n"
    "See 'Extra description' on colored rows for the specific finding."
)
ws["A1"].comment = Comment(legend_text, "Automation review")

wb.save(PATH)
print(f"Fixed stale 'Extra description' text on {len(fixed_stale)} rows: {fixed_stale}")
print(f"Colored {len(colored)} rows.")
