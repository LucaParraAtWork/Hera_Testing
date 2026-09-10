#!/usr/bin/env python3
"""
test_master_data.py – Hera Master Data (Truck Manager) Test Suite
=================================================================
Covers all UAT test cases for:
  - Transport Company CRUD (ACS_MD_01/02) + validation (ACS_TM_CO_*)
  - Truck CRUD (ACS_MD_03/04) + qualification constraints (ACS_TM_QUAL_01/04)
                               + validation (ACS_TM_TRK_*)
  - Driver CRUD (ACS_MD_05/06) + qualification constraints (ACS_TM_QUAL_03/05)
                                + validation (ACS_TM_DRV_*)
  - Trailer CRUD (ACS_MD_07/08) + qualification constraints (ACS_TM_QUAL_02)
                                 + validation (ACS_TM_TRL_*)
  - List views (ACS_MD_09/10)

Run from the repo root or from the 'Master Data' folder:

    python "Master Data/test_master_data.py"

A browser window opens; log in with MFA if prompted, then press Enter.
The script cycles through every scenario, auto-detects the result, and
asks you to confirm. A summary table is printed at the end and saved to
test_results.csv.
"""

# =============================================================================
# ENVIRONMENT
# =============================================================================
ENV = ""   # "dev"  |  "test"  |  ""  (prompt at startup)

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

# =============================================================================
# RESUME – set to N to skip scenarios 1 .. N-1
# =============================================================================
START_FROM = 28

# =============================================================================
# TEST CASES
# expect: "PASS" = Hera should accept the action
#         "FAIL" = Hera should reject / show a validation error
# =============================================================================
TEST_CASES = [
    # ── Transport Company ─────────────────────────────────────────────────────
    {"id": "ACS_MD_01",      "desc": "Add transport company (happy path)",                           "expect": "PASS"},
    {"id": "ACS_MD_02",      "desc": "Edit transport company – update name / VAT / address",         "expect": "PASS"},
    {"id": "ACS_TM_CO_01",   "desc": "Company – invalid VAT number format",                         "expect": "FAIL"},
    {"id": "ACS_TM_CO_02",   "desc": "Company – save without offtaker mapping (allowed)",           "expect": "PASS"},
    {"id": "ACS_TM_CO_03",   "desc": "Company – empty company name",                                "expect": "FAIL"},
    # ── Truck ─────────────────────────────────────────────────────────────────
    {"id": "ACS_MD_03",      "desc": "Add truck as Pre-Qualified",                                   "expect": "PASS"},
    {"id": "ACS_MD_04",      "desc": "Promote truck Pre-Qualified → Qualified (with date)",          "expect": "PASS"},
    {"id": "ACS_TM_QUAL_01", "desc": "Truck – save as Qualified on create → blocked",               "expect": "PASS"},
    {"id": "ACS_TM_QUAL_04", "desc": "Truck – promote to Qualified without date → blocked",         "expect": "FAIL"},
    {"id": "ACS_TM_TRK_01",  "desc": "Truck – license plate invalid for country B",                 "expect": "FAIL"},
    {"id": "ACS_TM_TRK_02",  "desc": "Truck – country code mismatch with plate",                    "expect": "FAIL"},
    {"id": "ACS_TM_TRK_03",  "desc": "Truck – empty license plate",                                 "expect": "FAIL"},
    {"id": "ACS_TM_TRK_04",  "desc": "Truck – duplicate license plate",                             "expect": "FAIL"},
    {"id": "ACS_TM_TRK_05",  "desc": "Truck – save without transport company",                      "expect": "FAIL"},
    {"id": "ACS_TM_TRK_06",  "desc": "Truck – promote to Qualified with date cleared → blocked",     "expect": "FAIL"},
    # ── Driver ────────────────────────────────────────────────────────────────
    {"id": "ACS_MD_05",      "desc": "Add driver as Pre-Qualified",                                  "expect": "PASS"},
    {"id": "ACS_MD_06",      "desc": "Promote driver Pre-Qualified → Qualified (with dates)",        "expect": "PASS"},
    {"id": "ACS_TM_QUAL_03", "desc": "Driver – save as Qualified on create → blocked",              "expect": "PASS"},
    {"id": "ACS_TM_QUAL_05", "desc": "Driver – promote to Qualified without training date → blocked","expect": "FAIL"},
    {"id": "ACS_TM_DRV_01",  "desc": "Driver – badge ID wrong format or length",                    "expect": "FAIL"},
    {"id": "ACS_TM_DRV_02",  "desc": "Driver – invalid or empty ADR card number",                   "expect": "FAIL"},
    {"id": "ACS_TM_DRV_03",  "desc": "Driver – empty last name or first name",                      "expect": "FAIL"},
    {"id": "ACS_TM_DRV_04",  "desc": "Driver – save without transport company",                     "expect": "FAIL"},
    # ── Trailer ───────────────────────────────────────────────────────────────
    {"id": "ACS_MD_07",      "desc": "Add trailer as Pre-Qualified",                                 "expect": "PASS"},
    {"id": "ACS_MD_08",      "desc": "Promote trailer Pre-Qualified → Qualified (with date)",        "expect": "PASS"},
    {"id": "ACS_TM_QUAL_02", "desc": "Trailer – save as Qualified on create → blocked",             "expect": "PASS"},
    {"id": "ACS_TM_TRL_01",  "desc": "Trailer – license plate invalid for country B",               "expect": "FAIL"},
    {"id": "ACS_TM_TRL_02",  "desc": "Trailer – invalid Global Max Flow Rate (text / negative)",    "expect": "FAIL"},
    {"id": "ACS_TM_TRL_03",  "desc": "Trailer – save without trailer type or offtaker company",     "expect": "FAIL"},
    # ── List views ────────────────────────────────────────────────────────────
    {"id": "ACS_MD_09",      "desc": "Transport company registry list – companies visible",          "expect": "PASS"},
    {"id": "ACS_MD_10",      "desc": "Drivers, trucks, trailers list views visible",                "expect": "PASS"},
]

# =============================================================================
# TIMING
# =============================================================================
WAIT_S = 0.3
WAIT_M = 0.7
WAIT_L = 1.5
SLOWMO = 0   # Playwright action delay in ms (0 = full speed; 200 = visible)

# =============================================================================
# In-run shared state  (company/truck/driver/trailer created during happy path)
# =============================================================================
_STATE: dict = {}

# =============================================================================
# Imports
# =============================================================================
import argparse, re, sys, time, csv as _csv
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results

HERE = Path(__file__).parent

_ERROR_KW = [
    "error", "invalid", "required", "must", "format", "already exist",
    "duplicate", "cannot", "not valid", "incorrect", "wrong", "400", "500",
    "warning",
]


# =============================================================================
# Helpers
# =============================================================================

def _resolve_env() -> tuple[str, str]:
    env = ENV.strip().lower()
    if env not in ENVIRONMENTS:
        print("\nSelect environment:")
        for i, key in enumerate(ENVIRONMENTS, 1):
            print(f"  {i}. {key}  ({ENVIRONMENTS[key]})")
        while True:
            choice = input("Enter 1 or 2: ").strip()
            if choice == "1": env = "dev"; break
            if choice == "2": env = "test"; break
    return env, ENVIRONMENTS[env]


def _ws():  time.sleep(WAIT_S)
def _wm():  time.sleep(WAIT_M)
def _wl():  time.sleep(WAIT_L)


def _quiet(page, timeout_ms: int = 8000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _safe_click(page, locator, what: str = "element"):
    for attempt in [
        lambda: locator.click(timeout=2000),
        lambda: (locator.scroll_into_view_if_needed(timeout=1500),
                 locator.click(timeout=1500, force=True)),
    ]:
        try:
            attempt(); _ws(); return
        except Exception:
            pass
    try:
        h = locator.element_handle(timeout=5000)
        page.evaluate("el => el.click()", h); _ws()
    except Exception as e:
        raise RuntimeError(f"Cannot click {what}: {e}")


def _goto(page, base_url: str, section: str):
    """Navigate to a truck-manager section and wait for the page to settle."""
    url = f"{base_url}/truck-manager/{section}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass
    _wm(); _quiet(page)


def _open_dialog(page):
    _wm()
    modal = page.locator("dialog[open]").last
    modal.wait_for(state="visible", timeout=10_000)
    _ws()
    return modal


def _close_dialog(page, modal):
    """Dismiss modal via Cancel / Close button, or Escape."""
    try:
        btn = modal.locator(
            "button", has_text=re.compile(r"cancel|close|×|annuler", re.I)
        ).first
        btn.click(timeout=2000); _wm(); return
    except Exception:
        pass
    try:
        page.keyboard.press("Escape"); _wm()
    except Exception:
        pass


def _click_add_btn(page, text_pattern: str):
    btn = page.locator("button", has_text=re.compile(text_pattern, re.I)).first
    _safe_click(page, btn, text_pattern)


def _click_row_to_edit(page, cell_text: str):
    """Click the first cell of the row containing cell_text to open its edit modal."""
    row = page.locator("tr").filter(has_text=cell_text).first
    row.scroll_into_view_if_needed()
    first_cell = row.locator("td").first
    _safe_click(page, first_cell, f"row for '{cell_text}'")
    _ws()
    if page.locator("dialog[open]").count() == 0:
        _safe_click(page, first_cell, f"row for '{cell_text}' (retry)")


def _select_option(locator, text: str = None, index: int = None):
    if text is not None:
        try:
            locator.select_option(label=text, timeout=3000)
        except Exception:
            locator.select_option(value=text, timeout=3000)
    elif index is not None:
        locator.select_option(index=index)
    _ws()


def _type_date(page, modal, date_8d: str, input_nth: int = -1):
    """
    Enter a date into an Angular date input sequentially.
    date_8d must be exactly 8 digits: ddmmyyyy (e.g. '22062026').
    Angular date pickers do not accept .fill() reliably.
    """
    try:
        date_input = modal.locator("input[placeholder*='dd/mm/yyyy']").first
        date_input.click(timeout=3000)
    except Exception:
        modal.locator("input:not([type='hidden'])").nth(input_nth).click(timeout=3000)
    _ws()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    page.keyboard.type(date_8d, delay=100)
    _ws()


def _today_digits() -> str:
    return datetime.now().strftime("%d%m%Y")


def _detect_save_result(page, modal) -> tuple[str, str]:
    """
    Check the page state after clicking Save.
    Returns ("PASS", reason) if the action was accepted,
            ("FAIL", reason) if a validation error was shown.
    """
    _wm()
    # Toast / alert / snackbar
    for sel in [".toast", ".alert", ".snackbar", "[class*='toast']",
                "[class*='error']", "[class*='success']", "[class*='notification']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                txt = el.inner_text(timeout=600).lower().strip()
                if any(k in txt for k in _ERROR_KW):
                    return "FAIL", f"Toast error: \"{txt[:100]}\""
                if any(k in txt for k in ["success", "saved", "created", "updated", "added"]):
                    return "PASS", f"Toast success: \"{txt[:80]}\""
        except Exception:
            pass

    # Modal still open?
    try:
        if modal.is_visible():
            txt = modal.inner_text(timeout=1500).lower()
            for kw in _ERROR_KW:
                if kw in txt:
                    return "FAIL", f"Modal still open – '{kw}' in text"
            return "FAIL", "Modal still open after Save (validation blocked)"
    except Exception:
        pass

    return "PASS", "Modal closed without errors"


def _qualified_option_disabled(modal) -> bool:
    """
    Return True if the 'Qualified' option is absent or disabled across all
    select elements in the modal (meaning the UI correctly blocks it on create).
    """
    selects = modal.locator("select").all()
    for sel in selects:
        opts = sel.locator("option").all()
        for opt in opts:
            if "qualified" in opt.inner_text().strip().lower():
                # Found the option – check if it's disabled
                return opt.get_attribute("disabled") is not None
    return True   # option not present at all → effectively blocked


def _selected_text(locator) -> str:
    """Read the display text of the currently selected option in a <select>."""
    try:
        return locator.evaluate(
            "el => el.options[el.selectedIndex]"
            " ? el.options[el.selectedIndex].text.trim() : ''"
        )
    except Exception:
        return ""


def _reopen_and_check(page, shots_dir, row_text: str, step: str, check_fn) -> tuple[bool, str]:
    """Re-open the edit modal for row_text and verify persistence with check_fn(modal)."""
    _click_row_to_edit(page, row_text)
    try:
        modal = _open_dialog(page)
    except Exception as e:
        return False, f"Could not re-open modal: {e}"
    page.screenshot(path=str(shots_dir / f"{step}_verify.png"))
    try:
        ok, detail = check_fn(modal)
    except Exception as e:
        ok, detail = False, f"Verification error: {e}"
    _close_dialog(page, modal)
    _quiet(page)
    return ok, detail


def _ask(tc_id: str, desc: str, expect: str,
         auto: str = "?", reason: str = "") -> tuple[str, str]:
    label = {"PASS": "[expected PASS]", "FAIL": "[expected FAIL]",
             "?":    "[outcome unknown]"}.get(expect, "")
    auto_label = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL",
                  "?": "AUTO-?"}.get(auto, auto)
    match_flag = ("" if expect == "?"
                  else (" OK" if auto == expect else " !! MISMATCH"))

    print(f"\n  {tc_id} {label}  –  {desc}")
    print(f"  Auto-detected : {auto_label}{match_flag}")
    if reason:
        print(f"  Reason        : {reason}")

    default = auto if auto in ("PASS", "FAIL") else "PASS"
    try:
        raw = input(f"  Confirm? (P=pass / F=fail / Enter={default}): ").strip().lower()
        result = ("FAIL" if raw in ("f", "fail", "n", "no")
                  else "PASS" if raw in ("p", "pass", "y", "yes", "o", "oui")
                  else default)
        notes = input("  Notes (optional): ").strip()
        return result, notes
    except EOFError:
        return default, reason


# =============================================================================
# Scenario implementations
# =============================================================================

def _run_ACS_MD_01(page, base_url, shots_dir):
    """Add transport company – happy path."""
    _goto(page, base_url, "transport-companies")
    page.screenshot(path=str(shots_dir / "01_list.png"))

    _click_add_btn(page, r"Add New Company")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    suffix = int(time.time()) % 1_000_000
    company_name = f"AutoTrans_{suffix}"
    vat          = f"BE0{suffix:09d}"

    modal.locator("input#company-name").fill(company_name)
    modal.locator("input#vat-number").fill(vat)
    modal.locator("input#address").fill("123 Logistics Way, Antwerp 2000")
    _ws()

    # Offtaker mapping – check Messer checkbox (offtaker_1), fall back to first checkbox
    try:
        messer_cb = modal.locator("input#offtaker_1")
        _safe_click(page, messer_cb, "Messer checkbox")
        _wm()
        if not messer_cb.is_checked():
            _safe_click(page, modal.locator("label[for='offtaker_1']"), "Messer label")
            _wm()
    except Exception:
        try:
            _safe_click(page, modal.locator("input[type='checkbox']").first, "first offtaker cb")
            _wm()
        except Exception:
            pass

    page.screenshot(path=str(shots_dir / "03_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    if auto == "PASS":
        _STATE["company_name"] = company_name
        _STATE["company_vat"]  = vat
        print(f"  Company created: {company_name}")
        _goto(page, base_url, "transport-companies")
        def _chk_01(m):
            n = m.locator("input#company-name").input_value()
            v = m.locator("input#vat-number").input_value()
            if n == company_name and v == vat:
                return True, f"Name='{n}', VAT='{v}'"
            return False, f"name got '{n}' exp '{company_name}', vat got '{v}' exp '{vat}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, company_name, "05", _chk_01)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Created + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_02(page, base_url, shots_dir):
    """Edit transport company."""
    _goto(page, base_url, "transport-companies")
    page.screenshot(path=str(shots_dir / "01_list.png"))

    target = _STATE.get("company_name", "")
    if not target:
        rows = page.locator("tr").filter(has_text="AutoTrans_").all()
        if rows:
            target = rows[-1].locator("td").first.inner_text().strip()
    if not target:
        return "?", "No AutoTrans_ company found to edit"

    _click_row_to_edit(page, target)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_edit_open.png"))

    new_name = (target[:-2] + "11") if len(target) >= 2 else (target + "11")
    curr_vat  = modal.locator("input#vat-number").input_value()
    new_vat   = (curr_vat[:-2] + "11") if len(curr_vat) >= 2 else (curr_vat + "11")

    modal.locator("input#company-name").fill(new_name)
    modal.locator("input#vat-number").fill(new_vat)
    modal.locator("input#address").fill("456 Modified Logistics Way, Ghent 9000")
    page.screenshot(path=str(shots_dir / "03_modified.png"))

    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    if auto == "PASS":
        _STATE["company_name"] = new_name
        print(f"  Company updated: {target} → {new_name}")
        _goto(page, base_url, "transport-companies")
        def _chk_02(m):
            n = m.locator("input#company-name").input_value()
            v = m.locator("input#vat-number").input_value()
            if n == new_name and v == new_vat:
                return True, f"Name='{n}', VAT='{v}'"
            return False, f"name got '{n}' exp '{new_name}', vat got '{v}' exp '{new_vat}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, new_name, "05", _chk_02)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Updated + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_03(page, base_url, shots_dir):
    """Add truck as Pre-Qualified."""
    _goto(page, base_url, "trucks")
    page.screenshot(path=str(shots_dir / "01_list.png"))

    _click_add_btn(page, r"Add New Truck")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    suffix      = int(time.time()) % 900 + 100
    truck_plate = f"2ABY{suffix}"

    modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(truck_plate)
    _ws()
    try:
        modal.get_by_label("Country Code").fill("B")
    except Exception:
        modal.locator("input").nth(1).fill("B")
    _ws()
    _select_option(modal.locator("select").first, index=1)          # transport company
    _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    page.screenshot(path=str(shots_dir / "03_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    if auto == "PASS":
        _STATE["truck_plate"] = truck_plate
        print(f"  Truck created: {truck_plate}")
        _goto(page, base_url, "trucks")
        def _chk_03(m):
            p = m.locator("input[placeholder='e.g. 1-ABC-123']").input_value()
            q = _selected_text(m.locator("select").nth(1))
            if p == truck_plate and "pre-qualified" in q.lower():
                return True, f"Plate='{p}', Qual='{q}'"
            return False, f"plate got '{p}' exp '{truck_plate}', qual got '{q}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, truck_plate, "05", _chk_03)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Created + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_04(page, base_url, shots_dir):
    """Promote truck Pre-Qualified → Qualified."""
    _goto(page, base_url, "trucks")

    target = _STATE.get("truck_plate", "")
    if not target:
        rows = page.locator("tr").filter(has_text="2ABY").all()
        if rows:
            target = rows[-1].locator("td").first.inner_text().strip().split()[0]
    if not target:
        return "?", "No Pre-Qualified truck found to promote"

    updated_plate = "1" + target[1:]   # 2ABY123 → 1ABY123

    _click_row_to_edit(page, target)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_edit_open.png"))

    modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(updated_plate)
    _ws()
    _select_option(modal.locator("select").nth(1), text="Qualified")
    _type_date(page, modal, _today_digits(), input_nth=-1)
    page.keyboard.press("Tab"); _ws()

    page.screenshot(path=str(shots_dir / "02_modified.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))

    if auto == "PASS":
        _STATE["truck_plate"]           = updated_plate
        _STATE["qualified_truck_plate"] = updated_plate
        print(f"  Truck promoted: {target} → {updated_plate}")
        _goto(page, base_url, "trucks")
        def _chk_04(m):
            p = m.locator("input[placeholder='e.g. 1-ABC-123']").input_value()
            q = _selected_text(m.locator("select").nth(1))
            if p == updated_plate and "qualified" in q.lower():
                return True, f"Plate='{p}', Qual='{q}'"
            return False, f"plate got '{p}' exp '{updated_plate}', qual got '{q}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, updated_plate, "04", _chk_04)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Promoted + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_05(page, base_url, shots_dir):
    """Add driver as Pre-Qualified."""
    _goto(page, base_url, "drivers")
    page.screenshot(path=str(shots_dir / "01_list.png"))

    _click_add_btn(page, r"Add New Driver")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    suffix    = int(time.time()) % 9000 + 1000
    last_name = f"AutoDriver{suffix}"
    badge_id  = f"000000{suffix}"[:10]
    adr_card  = f"ADR-999{suffix}"

    modal.locator("input").nth(0).fill(last_name)
    modal.locator("input").nth(1).fill("John")
    _ws()
    _select_option(modal.locator("select").nth(0), index=1)           # company
    modal.locator("input[placeholder='e.g. 0123456789']").fill(badge_id)
    _ws()
    modal.locator("input[placeholder='e.g. ADR-9876543']").fill(adr_card)
    _ws()
    _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    page.screenshot(path=str(shots_dir / "03_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    if auto == "PASS":
        _STATE["driver_last_name"] = last_name
        print(f"  Driver created: {last_name}")
        _goto(page, base_url, "drivers")
        def _chk_05(m):
            n = m.locator("input").nth(0).input_value()
            q = _selected_text(m.locator("select").nth(1))
            if n == last_name and "pre-qualified" in q.lower():
                return True, f"LastName='{n}', Qual='{q}'"
            return False, f"last_name got '{n}' exp '{last_name}', qual got '{q}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, last_name, "05", _chk_05)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Created + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_06(page, base_url, shots_dir):
    """Promote driver Pre-Qualified → Qualified."""
    _goto(page, base_url, "drivers")

    target = _STATE.get("driver_last_name", "")
    if not target:
        rows = page.locator("tr").filter(has_text="AutoDriver").all()
        if rows:
            target = rows[-1].locator("td").first.inner_text().strip().split()[0]
    if not target:
        return "?", "No AutoDriver found to promote"

    updated_name = target + "Mod"
    _click_row_to_edit(page, target)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_edit_open.png"))

    modal.locator("input").nth(0).fill(updated_name)
    _ws()
    _select_option(modal.locator("select").nth(1), text="Qualified")

    all_inputs = modal.locator("input:not([type='hidden'])")
    # Date of 1st Qualification
    all_inputs.nth(-2).click(timeout=3000); _ws()
    page.keyboard.press("Home"); page.keyboard.press("ArrowLeft"); page.keyboard.press("ArrowLeft")
    page.keyboard.type(_today_digits(), delay=100); _ws()
    # Date of Last Validated Training
    all_inputs.nth(-1).click(timeout=3000); _ws()
    page.keyboard.press("Home"); page.keyboard.press("ArrowLeft"); page.keyboard.press("ArrowLeft")
    page.keyboard.type(_today_digits(), delay=100); _ws()
    page.keyboard.press("Tab"); _ws()

    page.screenshot(path=str(shots_dir / "02_modified.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))

    if auto == "PASS":
        _STATE["driver_last_name"] = updated_name
        print(f"  Driver promoted: {target} → {updated_name}")
        _goto(page, base_url, "drivers")
        def _chk_06(m):
            n = m.locator("input").nth(0).input_value()
            q = _selected_text(m.locator("select").nth(1))
            if n == updated_name and "qualified" in q.lower():
                return True, f"LastName='{n}', Qual='{q}'"
            return False, f"last_name got '{n}' exp '{updated_name}', qual got '{q}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, updated_name, "04", _chk_06)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Promoted + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_07(page, base_url, shots_dir):
    """Add trailer as Pre-Qualified."""
    _goto(page, base_url, "trailers")
    page.screenshot(path=str(shots_dir / "01_list.png"))

    _click_add_btn(page, r"Add New Trailer")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    suffix        = int(time.time()) % 900 + 100
    trailer_plate = f"3TRL{suffix}"

    modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(trailer_plate)
    _ws()
    try:
        modal.get_by_label("Country Code").fill("B")
    except Exception:
        modal.locator("input").nth(1).fill("B")
    _ws()
    _select_option(modal.locator("select").nth(0), index=1)   # offtaker company
    _select_option(modal.locator("select").nth(1), index=1)   # trailer type
    try:
        modal.locator("input[placeholder='e.g. 32.5']").fill("30.5")
    except Exception:
        modal.locator("input").nth(2).fill("30.5")
    _ws()
    _select_option(modal.locator("select").nth(2), text="Pre-Qualified")

    page.screenshot(path=str(shots_dir / "03_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    if auto == "PASS":
        _STATE["trailer_plate"] = trailer_plate
        print(f"  Trailer created: {trailer_plate}")
        _goto(page, base_url, "trailers")
        def _chk_07(m):
            p = m.locator("input[placeholder='e.g. 1-ABC-123']").input_value()
            q = _selected_text(m.locator("select").nth(2))
            if p == trailer_plate and "pre-qualified" in q.lower():
                return True, f"Plate='{p}', Qual='{q}'"
            return False, f"plate got '{p}' exp '{trailer_plate}', qual got '{q}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, trailer_plate, "05", _chk_07)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Created + verified: {p_detail}"
    return auto, reason


def _run_ACS_MD_08(page, base_url, shots_dir):
    """Promote trailer Pre-Qualified → Qualified."""
    _goto(page, base_url, "trailers")

    target = _STATE.get("trailer_plate", "")
    if not target:
        rows = page.locator("tr").filter(has_text="3TRL").all()
        if rows:
            target = rows[-1].locator("td").first.inner_text().strip().split()[0]
    if not target:
        return "?", "No Pre-Qualified trailer found to promote"

    updated_plate = "1TRL" + target[4:]   # 3TRL123 → 1TRL123

    _click_row_to_edit(page, target)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_edit_open.png"))

    modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(updated_plate)
    _ws()
    _select_option(modal.locator("select").nth(2), text="Qualified")
    _type_date(page, modal, _today_digits(), input_nth=-1)
    page.keyboard.press("Tab"); _ws()

    page.screenshot(path=str(shots_dir / "02_modified.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=10_000)
    except Exception:
        pass
    auto, reason = _detect_save_result(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))

    if auto == "PASS":
        _STATE["trailer_plate"] = updated_plate
        print(f"  Trailer promoted: {target} → {updated_plate}")
        _goto(page, base_url, "trailers")
        def _chk_08(m):
            p = m.locator("input[placeholder='e.g. 1-ABC-123']").input_value()
            q = _selected_text(m.locator("select").nth(2))
            if p == updated_plate and "qualified" in q.lower():
                return True, f"Plate='{p}', Qual='{q}'"
            return False, f"plate got '{p}' exp '{updated_plate}', qual got '{q}'"
        p_ok, p_detail = _reopen_and_check(page, shots_dir, updated_plate, "04", _chk_08)
        if not p_ok:
            auto, reason = "FAIL", f"Persistence check failed: {p_detail}"
        else:
            reason = f"Promoted + verified: {p_detail}"
    return auto, reason


def _run_QUAL_check(page, base_url, shots_dir,
                    section: str, add_btn: str, qual_select_nth: int,
                    fill_fn=None):
    """
    Fill all required fields for a new entity, select 'Qualified', then try to save.
    PASS = the app correctly raised a validation error (blocked creation as Qualified).
    FAIL = the app accepted the save (should have blocked it).
    fill_fn(modal) must populate all required fields except the qual state.
    """
    _goto(page, base_url, section)
    _click_add_btn(page, add_btn)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_modal_open.png"))

    if fill_fn:
        try:
            fill_fn(modal)
        except Exception as e:
            _close_dialog(page, modal)
            return "?", f"Could not fill required fields: {e}"

    _select_option(modal.locator("select").nth(qual_select_nth), text="Qualified")
    page.screenshot(path=str(shots_dir / "02_qualified_selected.png"))

    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))

    if auto == "FAIL":
        _close_dialog(page, modal)
        _quiet(page)
        return "PASS", f"App correctly blocked creation as Qualified: {reason}"
    _quiet(page)
    return "FAIL", "App accepted creation as Qualified – should have raised a validation error"


def _run_QUAL_04(page, base_url, shots_dir):
    """Truck – promote to Qualified without qualification date → should be blocked."""
    _goto(page, base_url, "trucks")

    # Create a dedicated Pre-Qualified truck for this test
    _click_add_btn(page, r"Add New Truck")
    modal = _open_dialog(page)
    suffix     = int(time.time()) % 900 + 100
    temp_plate = f"2NEG{suffix}"
    modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(temp_plate)
    _ws()
    try:
        modal.get_by_label("Country Code").fill("B")
    except Exception:
        modal.locator("input").nth(1).fill("B")
    _ws()
    _select_option(modal.locator("select").first, index=1)
    _select_option(modal.locator("select").nth(1), text="Pre-Qualified")
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=8_000)
    except Exception:
        pass
    _wl(); _quiet(page)
    # Reload the list to ensure the new row is rendered
    _goto(page, base_url, "trucks")

    if page.locator(f"td:has-text('{temp_plate}')").count() == 0:
        return "?", f"Temp truck {temp_plate} not found after creation"

    # Edit: set Qualified but leave date empty
    _click_row_to_edit(page, temp_plate)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_edit_open.png"))

    _select_option(modal.locator("select").nth(1), text="Qualified")
    _ws()
    # Deliberately do NOT fill the qualification date
    page.screenshot(path=str(shots_dir / "02_no_date.png"))

    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))
    if auto != "PASS":
        _close_dialog(page, modal)
    _quiet(page)
    return auto, reason


def _run_QUAL_05(page, base_url, shots_dir):
    """Driver – promote to Qualified without Last Validated Training date → should be blocked."""
    _goto(page, base_url, "drivers")

    # Create a dedicated Pre-Qualified driver for this test
    _click_add_btn(page, r"Add New Driver")
    modal = _open_dialog(page)
    suffix    = int(time.time()) % 9000 + 1000
    temp_name = f"NegDriver{suffix}"
    badge     = f"000000{suffix}"[:10]
    adr       = f"ADR-000{suffix}"
    modal.locator("input").nth(0).fill(temp_name)
    modal.locator("input").nth(1).fill("Test")
    _ws()
    _select_option(modal.locator("select").nth(0), index=1)
    modal.locator("input[placeholder='e.g. 0123456789']").fill(badge)
    modal.locator("input[placeholder='e.g. ADR-9876543']").fill(adr)
    _ws()
    _select_option(modal.locator("select").nth(1), text="Pre-Qualified")
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    try:
        modal.wait_for(state="hidden", timeout=8_000)
    except Exception:
        pass
    _wl(); _quiet(page)
    # Reload the list to ensure the new row is rendered
    _goto(page, base_url, "drivers")

    if page.locator(f"td:has-text('{temp_name}')").count() == 0:
        return "?", f"Temp driver {temp_name} not found after creation"

    # Edit: set Qualified, fill only the 1st qualification date, leave training date empty
    _click_row_to_edit(page, temp_name)
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_edit_open.png"))

    _select_option(modal.locator("select").nth(1), text="Qualified")
    all_inputs = modal.locator("input:not([type='hidden'])")
    all_inputs.nth(-2).click(timeout=3000); _ws()
    page.keyboard.press("Home"); page.keyboard.press("ArrowLeft"); page.keyboard.press("ArrowLeft")
    page.keyboard.type(_today_digits(), delay=100); _ws()
    # Leave Last Validated Training (last input) empty
    page.keyboard.press("Tab"); _ws()

    page.screenshot(path=str(shots_dir / "02_partial_dates.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))
    if auto != "PASS":
        _close_dialog(page, modal)
    _quiet(page)
    return auto, reason


def _run_CO_NEG(page, base_url, shots_dir, tc_id: str):
    """Company validation negative tests (CO_01 / CO_02 / CO_03)."""
    _goto(page, base_url, "transport-companies")
    _click_add_btn(page, r"Add New Company")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_modal_open.png"))

    suffix = int(time.time()) % 1_000_000

    if tc_id == "ACS_TM_CO_01":     # Invalid VAT format
        modal.locator("input#company-name").fill(f"TestCo_{suffix}")
        modal.locator("input#vat-number").fill("INVALID-VAT!")
        modal.locator("input#address").fill("Test Address")
        try:
            _safe_click(page, modal.locator("input[type='checkbox']").first, "offtaker cb"); _wm()
        except Exception:
            pass

    elif tc_id == "ACS_TM_CO_02":   # No offtaker mapping
        modal.locator("input#company-name").fill(f"TestCo_{suffix}")
        modal.locator("input#vat-number").fill(f"BE0{suffix:09d}")
        modal.locator("input#address").fill("Test Address")
        # Deliberately skip all offtaker checkboxes

    elif tc_id == "ACS_TM_CO_03":   # Empty company name
        # Leave name blank
        modal.locator("input#vat-number").fill(f"BE0{suffix:09d}")
        modal.locator("input#address").fill("Test Address")
        try:
            _safe_click(page, modal.locator("input[type='checkbox']").first, "offtaker cb"); _wm()
        except Exception:
            pass

    _ws()
    page.screenshot(path=str(shots_dir / "02_invalid_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))
    _close_dialog(page, modal)
    _quiet(page)
    return auto, reason


def _run_TRK_NEG(page, base_url, shots_dir, tc_id: str):
    """Truck validation negative tests (TRK_01 – TRK_06)."""
    _goto(page, base_url, "trucks")
    page.screenshot(path=str(shots_dir / "01_list.png"))
    suffix = int(time.time()) % 900 + 100

    if tc_id == "ACS_TM_TRK_06":
        # Requires editing an existing truck with an invalid date format
        target = (_STATE.get("truck_plate") or
                  _STATE.get("qualified_truck_plate") or "")
        if not target:
            for row in page.locator("table tr").all()[1:]:
                try:
                    txt = row.locator("td").first.inner_text(timeout=300).strip().split()[0]
                    if txt and len(txt) >= 3:
                        target = txt; break
                except Exception:
                    continue
        if not target:
            return "?", "No truck found for TRK_06 date format test"
        _click_row_to_edit(page, target)
        modal = _open_dialog(page)
        page.screenshot(path=str(shots_dir / "02_edit_open.png"))
        _select_option(modal.locator("select").nth(1), text="Qualified")
        # type="date" prevents invalid format input at browser level; instead
        # clear the date field via JS so Angular sees it as empty/invalid.
        try:
            date_el = modal.locator("#date-of-first-qualification").first
            if date_el.count() == 0:
                date_el = modal.locator("input[type='date']").first
            date_el.evaluate(
                "el => { el.value = '';"
                " el.dispatchEvent(new Event('input',  {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true}));"
                " el.dispatchEvent(new Event('blur',   {bubbles:true})); }"
            )
        except Exception:
            pass
        _ws()
        page.screenshot(path=str(shots_dir / "03_date_cleared.png"))
        _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
        auto, reason = _detect_save_result(page, modal)
        page.screenshot(path=str(shots_dir / "04_after_save.png"))
        _close_dialog(page, modal)
        _quiet(page)
        return auto, reason

    # All other TRK NEG: use Add New Truck modal
    _click_add_btn(page, r"Add New Truck")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    if tc_id == "ACS_TM_TRK_01":    # Plate invalid for country B
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill("INVALID99")
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        _select_option(modal.locator("select").first, index=1)
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_TRK_02":  # Country code mismatch
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(f"2ABY{suffix}")
        try:
            modal.get_by_label("Country Code").fill("NL")   # wrong country for Belgian plate
        except Exception:
            modal.locator("input").nth(1).fill("NL")
        _select_option(modal.locator("select").first, index=1)
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_TRK_03":  # Empty license plate
        # Leave plate field blank
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        _select_option(modal.locator("select").first, index=1)
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_TRK_04":  # Duplicate license plate
        dup = (_STATE.get("qualified_truck_plate") or
               _STATE.get("truck_plate") or "")
        if not dup:
            # Scrape any plate from the list visible behind the modal
            for row in page.locator("table tr").all()[1:]:
                try:
                    txt = row.locator("td").first.inner_text(timeout=300).strip().split()[0]
                    if txt and len(txt) >= 3:
                        dup = txt; break
                except Exception:
                    continue
        if not dup:
            _close_dialog(page, modal)
            return "?", "No truck plate found in the list for duplicate test"
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(dup)
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        _select_option(modal.locator("select").first, index=1)
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_TRK_05":  # No transport company
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(f"2ABY{suffix}")
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        # Skip transport company select
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    _ws()
    page.screenshot(path=str(shots_dir / "03_invalid_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))
    _close_dialog(page, modal)
    _quiet(page)
    return auto, reason


def _run_TRL_NEG(page, base_url, shots_dir, tc_id: str):
    """Trailer validation negative tests (TRL_01 – TRL_03)."""
    _goto(page, base_url, "trailers")
    _click_add_btn(page, r"Add New Trailer")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_modal_open.png"))
    suffix = int(time.time()) % 900 + 100

    if tc_id == "ACS_TM_TRL_01":    # Plate invalid for country B
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill("BADPLATE!")
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        _select_option(modal.locator("select").nth(0), index=1)
        _select_option(modal.locator("select").nth(1), index=1)
        try:
            modal.locator("input[placeholder='e.g. 32.5']").fill("30.5")
        except Exception:
            modal.locator("input").nth(2).fill("30.5")
        _select_option(modal.locator("select").nth(2), text="Pre-Qualified")

    elif tc_id == "ACS_TM_TRL_02":  # Invalid max flow rate
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(f"3TRL{suffix}")
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        _select_option(modal.locator("select").nth(0), index=1)
        _select_option(modal.locator("select").nth(1), index=1)
        try:
            modal.locator("input[placeholder='e.g. 32.5']").fill("-99.9")
        except Exception:
            modal.locator("input").nth(2).fill("-99.9")
        _select_option(modal.locator("select").nth(2), text="Pre-Qualified")

    elif tc_id == "ACS_TM_TRL_03":  # No trailer type or offtaker
        modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(f"3TRL{suffix}")
        try:
            modal.get_by_label("Country Code").fill("B")
        except Exception:
            modal.locator("input").nth(1).fill("B")
        # Skip offtaker and trailer type selects
        try:
            modal.locator("input[placeholder='e.g. 32.5']").fill("30.5")
        except Exception:
            modal.locator("input").nth(2).fill("30.5")
        _select_option(modal.locator("select").nth(2), text="Pre-Qualified")

    _ws()
    page.screenshot(path=str(shots_dir / "02_invalid_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))
    _close_dialog(page, modal)
    _quiet(page)
    return auto, reason


def _run_DRV_NEG(page, base_url, shots_dir, tc_id: str):
    """Driver validation negative tests (DRV_01 – DRV_04)."""
    _goto(page, base_url, "drivers")
    _click_add_btn(page, r"Add New Driver")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "01_modal_open.png"))
    suffix = int(time.time()) % 9000 + 1000

    if tc_id == "ACS_TM_DRV_01":    # Badge ID wrong format
        modal.locator("input").nth(0).fill(f"TestDrv{suffix}")
        modal.locator("input").nth(1).fill("John")
        _select_option(modal.locator("select").nth(0), index=1)
        modal.locator("input[placeholder='e.g. 0123456789']").fill("BAD-ID!")
        modal.locator("input[placeholder='e.g. ADR-9876543']").fill(f"ADR-999{suffix}")
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_DRV_02":  # Empty ADR card number
        modal.locator("input").nth(0).fill(f"TestDrv{suffix}")
        modal.locator("input").nth(1).fill("John")
        _select_option(modal.locator("select").nth(0), index=1)
        modal.locator("input[placeholder='e.g. 0123456789']").fill(f"000000{suffix}"[:10])
        modal.locator("input[placeholder='e.g. ADR-9876543']").fill("")   # blank ADR
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_DRV_03":  # Empty last name
        # Leave last name (nth 0) blank
        modal.locator("input").nth(1).fill("John")
        _select_option(modal.locator("select").nth(0), index=1)
        modal.locator("input[placeholder='e.g. 0123456789']").fill(f"000000{suffix}"[:10])
        modal.locator("input[placeholder='e.g. ADR-9876543']").fill(f"ADR-999{suffix}")
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    elif tc_id == "ACS_TM_DRV_04":  # No transport company
        modal.locator("input").nth(0).fill(f"TestDrv{suffix}")
        modal.locator("input").nth(1).fill("John")
        # Skip transport company select (index 0)
        modal.locator("input[placeholder='e.g. 0123456789']").fill(f"000000{suffix}"[:10])
        modal.locator("input[placeholder='e.g. ADR-9876543']").fill(f"ADR-999{suffix}")
        _select_option(modal.locator("select").nth(1), text="Pre-Qualified")

    _ws()
    page.screenshot(path=str(shots_dir / "02_invalid_filled.png"))
    _safe_click(page, modal.locator("button", has_text="Save").first, "Save")
    auto, reason = _detect_save_result(page, modal)
    page.screenshot(path=str(shots_dir / "03_after_save.png"))
    _close_dialog(page, modal)
    _quiet(page)
    return auto, reason


def _run_ACS_MD_09(page, base_url, shots_dir):
    """Transport company registry list view."""
    _goto(page, base_url, "transport-companies")
    page.screenshot(path=str(shots_dir / "01_list.png"), full_page=True)
    count = page.locator("tr").count()
    if count > 1:
        return "PASS", f"Transport company list visible ({count - 1} entries)"
    return "?", "Table appears empty or was not found"


def _run_ACS_MD_10(page, base_url, shots_dir):
    """Drivers, trucks, trailers list views."""
    summaries = []
    for section, label in [("trucks", "Trucks"), ("trailers", "Trailers"), ("drivers", "Drivers")]:
        _goto(page, base_url, section)
        count = page.locator("tr").count()
        page.screenshot(path=str(shots_dir / f"01_{section}.png"), full_page=True)
        summaries.append(f"{label}: {max(0, count - 1)} rows")
    return "PASS", " | ".join(summaries)


# =============================================================================
# Dispatcher
# =============================================================================
def _run_scenario(page, base_url: str, tc: dict, shots_dir: Path) -> tuple[str, str]:
    shots_dir.mkdir(parents=True, exist_ok=True)
    tc_id = tc["id"]

    if tc_id == "ACS_MD_01":       return _run_ACS_MD_01(page, base_url, shots_dir)
    if tc_id == "ACS_MD_02":       return _run_ACS_MD_02(page, base_url, shots_dir)
    if tc_id == "ACS_MD_03":       return _run_ACS_MD_03(page, base_url, shots_dir)
    if tc_id == "ACS_MD_04":       return _run_ACS_MD_04(page, base_url, shots_dir)
    if tc_id == "ACS_MD_05":       return _run_ACS_MD_05(page, base_url, shots_dir)
    if tc_id == "ACS_MD_06":       return _run_ACS_MD_06(page, base_url, shots_dir)
    if tc_id == "ACS_MD_07":       return _run_ACS_MD_07(page, base_url, shots_dir)
    if tc_id == "ACS_MD_08":       return _run_ACS_MD_08(page, base_url, shots_dir)
    if tc_id == "ACS_TM_QUAL_01":
        def _fill_truck_q(m):
            sfx = int(time.time()) % 900 + 100
            m.locator("input[placeholder='e.g. 1-ABC-123']").fill(f"9QUA{sfx}")
            _ws()
            try:    m.get_by_label("Country Code").fill("B")
            except Exception: m.locator("input").nth(1).fill("B")
            _ws()
            _select_option(m.locator("select").first, index=1)
        return _run_QUAL_check(page, base_url, shots_dir, "trucks", r"Add New Truck",
                               qual_select_nth=1, fill_fn=_fill_truck_q)
    if tc_id == "ACS_TM_QUAL_02":
        def _fill_trailer_q(m):
            sfx = int(time.time()) % 900 + 100
            m.locator("input[placeholder='e.g. 1-ABC-123']").fill(f"9TRL{sfx}")
            _ws()
            try:    m.get_by_label("Country Code").fill("B")
            except Exception: m.locator("input").nth(1).fill("B")
            _ws()
            _select_option(m.locator("select").nth(0), index=1)
            _select_option(m.locator("select").nth(1), index=1)
            try:    m.locator("input[placeholder='e.g. 32.5']").fill("28.0")
            except Exception: m.locator("input").nth(2).fill("28.0")
            _ws()
        return _run_QUAL_check(page, base_url, shots_dir, "trailers", r"Add New Trailer",
                               qual_select_nth=2, fill_fn=_fill_trailer_q)
    if tc_id == "ACS_TM_QUAL_03":
        def _fill_driver_q(m):
            sfx = int(time.time()) % 9000 + 1000
            m.locator("input").nth(0).fill(f"QualDrv{sfx}")
            m.locator("input").nth(1).fill("Test")
            _ws()
            _select_option(m.locator("select").nth(0), index=1)
            m.locator("input[placeholder='e.g. 0123456789']").fill(f"000000{sfx}"[:10])
            _ws()
            m.locator("input[placeholder='e.g. ADR-9876543']").fill(f"ADR-000{sfx}")
            _ws()
        return _run_QUAL_check(page, base_url, shots_dir, "drivers", r"Add New Driver",
                               qual_select_nth=1, fill_fn=_fill_driver_q)
    if tc_id == "ACS_TM_QUAL_04":  return _run_QUAL_04(page, base_url, shots_dir)
    if tc_id == "ACS_TM_QUAL_05":  return _run_QUAL_05(page, base_url, shots_dir)
    if tc_id in ("ACS_TM_CO_01", "ACS_TM_CO_02", "ACS_TM_CO_03"):
        return _run_CO_NEG(page, base_url, shots_dir, tc_id)
    if tc_id in ("ACS_TM_TRK_01", "ACS_TM_TRK_02", "ACS_TM_TRK_03",
                 "ACS_TM_TRK_04", "ACS_TM_TRK_05", "ACS_TM_TRK_06"):
        return _run_TRK_NEG(page, base_url, shots_dir, tc_id)
    if tc_id in ("ACS_TM_TRL_01", "ACS_TM_TRL_02", "ACS_TM_TRL_03"):
        return _run_TRL_NEG(page, base_url, shots_dir, tc_id)
    if tc_id in ("ACS_TM_DRV_01", "ACS_TM_DRV_02", "ACS_TM_DRV_03", "ACS_TM_DRV_04"):
        return _run_DRV_NEG(page, base_url, shots_dir, tc_id)
    if tc_id == "ACS_MD_09":       return _run_ACS_MD_09(page, base_url, shots_dir)
    if tc_id == "ACS_MD_10":       return _run_ACS_MD_10(page, base_url, shots_dir)

    return "?", f"No implementation found for {tc_id}"


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Hera Master Data Test Suite")
    parser.add_argument(
        "--from", dest="start_from", type=int, default=START_FROM, metavar="N",
        help="Resume from scenario N (1-based). Overrides START_FROM in the file.",
    )
    parser.add_argument(
        "--excel", default=None, metavar="PATH",
        help="Report file to write Pass/Fail into. Default: create a new "
             "timestamped copy under UAT Testing/Reports/.",
    )
    args, _ = parser.parse_known_args()
    start_from = max(1, args.start_from)

    env_name, base_url = _resolve_env()

    artifacts = HERE / "test_artifacts"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    print(f"\n{'='*60}")
    print(f"  Hera Master Data Test Suite")
    print(f"  Environment : {env_name}  ({base_url})")
    print(f"  Run ID      : {run_id}")
    print(f"  Test cases  : {len(TEST_CASES)}")
    if start_from > 1:
        print(f"  Resuming from scenario {start_from}")
    print(f"{'='*60}\n")
    print("  The browser will open. Log in with MFA if prompted.")
    print("  After each scenario you will be asked to confirm the result.\n")

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir),
            headless=False,
            slow_mo=SLOWMO,
            viewport={"width": 1500, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("  Navigating to Hera Truck Manager...")
        try:
            page.goto(f"{base_url}/truck-manager/transport-companies",
                      wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA in the browser window if prompted. │")
        print("  │  Take as long as you need.                          │")
        print("  │  Press Enter here once you see the Hera app.        │")
        print("  └─────────────────────────────────────────────────────┘")
        input("  > ")
        _quiet(page)
        print("  Authentication confirmed. Starting test loop.\n")

        for idx, tc in enumerate(TEST_CASES, 1):
            if idx < start_from:
                continue

            shots_dir = run_dir / f"{idx:02d}_{tc['id']}"
            print(f"\n{'─'*60}")
            print(f"  [{idx}/{len(TEST_CASES)}]  {tc['id']}  –  {tc['desc']}")
            print(f"  Expect : {tc['expect']}")

            try:
                auto_result, auto_reason = _run_scenario(page, base_url, tc, shots_dir)
                result, notes = _ask(tc["id"], tc["desc"], tc["expect"],
                                     auto=auto_result, reason=auto_reason)
            except Exception as exc:
                print(f"  ERROR: {exc}")
                result, notes = "ERROR", str(exc)
                auto_result   = "ERROR"

            if tc["expect"] not in ("?", result):
                print(f"  !! MISMATCH – expected {tc['expect']}, got {result}")

            results.append({**tc, "result": result, "notes": notes})

        ctx.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}")
    print(f"{'='*60}")
    print(f"  {'ID':<22} {'Expect':<7} {'Result':<7} {'Match':<6} Description")
    print("─" * 80)

    pass_n = fail_n = skip_n = mismatch_n = 0
    for r in results:
        match = (r["expect"] == "?" or r["result"] == r["expect"])
        flag  = "  " if match else "!!"
        if r["result"] == "SKIP":   skip_n  += 1
        elif r["result"] == "PASS": pass_n  += 1
        else:                       fail_n  += 1
        if not match:               mismatch_n += 1
        print(f"  {flag} {r['id']:<22} {r['expect']:<7} {r['result']:<7} "
              f"{str(match):<6} {r['desc']}")

    print("─" * 80)
    print(f"  Passed: {pass_n}  |  Failed: {fail_n}  |  Skipped: {skip_n}  "
          f"|  Mismatches: {mismatch_n}\n")

    results_csv = run_dir / "test_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["id", "desc", "expect", "result", "notes"])
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"  Results saved to : {results_csv}")
    print(f"  Screenshots in   : {run_dir}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_master_data.py")


if __name__ == "__main__":
    main()
