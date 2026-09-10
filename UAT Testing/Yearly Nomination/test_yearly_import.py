#!/usr/bin/env python3
"""
test_yearly_import.py – Hera Yearly Nomination Import Test Suite
=================================================================
Run from the repo root or from the 'Yearly Nomination' folder:

    python "Yearly Nomination/test_yearly_import.py"

A browser window opens; log in with MFA when prompted, then press Enter
in the terminal. The script cycles through every test CSV, waits 5 s for
Hera to process each one, auto-detects the result, and asks you to confirm.
A summary table is printed at the end and saved to test_results.csv.
"""

# ===========================================================================
# ENVIRONMENT SELECTION
# Set ENV to "dev" or "test" to pick the target environment,
# or leave it as "" to be asked interactively each time you run the script.
# ===========================================================================
ENV = ""   # "dev"  |  "test"  |  ""  (prompt at startup)

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",  # <-- update if the test URL differs
}

# ===========================================================================
# TEST CASES
# Files are resolved relative to this script's folder.
# expect:  "PASS" = Hera should accept the import and update the yearly view
#          "FAIL" = Hera should reject the file with an error message
#          "?"    = behaviour unknown – observe and document
# ===========================================================================
TEST_CASES = [
    {
        "id":       "Nom_YN_01",
        "file":     "Virya_Nomination_Year2028.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "PASS",
        "desc":     "Happy path – Virya Year 2028 (175/month, 40/week)",
    },
    {
        "id":       "Nom_YN_03",
        "file":     "Messer_Nomination_Year2028.csv",
        "offtaker": "Messer Belgium NV",
        "expect":   "PASS",
        "desc":     "Happy path – Messer Year 2028 (180/month, 42/week)",
    },
    {
        "id":       "Nom_YN_04",
        "file":     "Virya_Nomination_Year2028_V2.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "PASS",
        "desc":     "Second import overwrites – Virya Year 2028 V2 (170/month, 38/week)",
    },
    {
        "id":       "Nom_YN_NEG_03",
        "file":     "Virya_Nomination_Year2028_NEG03_MonthOverCap.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Month 1 = 201 (exceeds per-offtaker monthly cap of 200)",
    },
    {
        "id":       "Nom_YN_NEG_04",
        "file":     "Virya_Nomination_Year2028_NEG04_WeekOverCap.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Week 1 = 51 (exceeds per-offtaker weekly cap of 50)",
    },
    {
        "id":       "Nom_YN_NEG_05",
        "file":     "Virya_Nomination_Year2028_NEG05_NegativeCount.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Month 1 = -1 (negative trailer count)",
    },
    {
        "id":       "Nom_YN_NEG_06",
        "file":     "Virya_Nomination_Year2028_NEG06_DecimalCount.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Month 1 = 10.5 (decimal trailer count)",
    },
    {
        "id":       "Nom_YN_NEG_07",
        "file":     "Virya_Nomination_Year2028_NEG07_MissingMonth.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Month 3 row missing from CSV",
    },
    {
        "id":       "Nom_YN_NEG_08",
        "file":     "Virya_Nomination_Year2028_NEG08_MissingWeek.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Week 26 row missing from CSV",
    },
    {
        "id":       "Nom_YN_NEG_09",
        "file":     "Virya_Nomination_Year2028_NEG09_InvalidType.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Row 1 Type = 'Quarter' (invalid period type)",
    },
    {
        "id":       "Nom_YN_NEG_10",
        "file":     "Virya_Nomination_Year2028_NEG10_InvalidPeriod.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Month 13 appended (invalid period number)",
    },
    {
        "id":       "Nom_YN_NEG_12",
        "file":     "Virya_Nomination_Year2028_NEG12_HeaderOnly.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "CSV contains header row only – no data",
    },
    {
        "id":       "Nom_YN_NEG_14",
        "file":     "Virya_Nomination_Year2028_NEG14_DuplicatePeriod.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Month 5 appears twice (duplicate period row)",
    },
    {
        "id":       "Nom_YN_NEG_15",
        "file":     "Virya_Nomination_Year2028_NEG15_WrongHeaders.csv",
        "offtaker": "Virya Energy NV",
        "expect":   "FAIL",
        "desc":     "Header 'Trailers' instead of 'Number of trailers'",
    },
]

# ===========================================================================
# TARGET — all test CSVs are for this nomination year
# ===========================================================================
TARGET_YEAR = 2028   # the year selector value inside the import modal
                     # and the year to navigate to after upload

# ===========================================================================
# TIMING — increase if Hera responds slowly on your network
# ===========================================================================
WAIT_S  = 0.3
WAIT_M  = 0.7
WAIT_L  = 1.5
SLOWMO  = 0     # Playwright action delay in ms (0 = full speed; 200 = visible)

# ===========================================================================
# Internal helpers
# ===========================================================================
import argparse, re, sys, time, csv as _csv
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results


HERE = Path(__file__).parent  # folder containing this script and all CSVs


def _resolve_env() -> tuple[str, str]:
    env = ENV.strip().lower()
    if env not in ENVIRONMENTS:
        print("\nSelect environment:")
        for i, key in enumerate(ENVIRONMENTS, 1):
            print(f"  {i}. {key}  ({ENVIRONMENTS[key]})")
        while True:
            choice = input("Enter 1 or 2: ").strip()
            if choice == "1":
                env = "dev"; break
            if choice == "2":
                env = "test"; break
    return env, ENVIRONMENTS[env]


def _wait_s():  time.sleep(WAIT_S)
def _wait_m():  time.sleep(WAIT_M)
def _wait_l():  time.sleep(WAIT_L)


def _ui_quiet(page, timeout_ms: int = 8000):
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
            attempt()
            _wait_s()
            return
        except Exception:
            pass
    try:
        h = locator.element_handle(timeout=5000)
        page.evaluate("el => el.click()", h)
        _wait_s()
    except Exception as e:
        raise RuntimeError(f"Cannot click {what}: {e}")


def _open_modal(page):
    _wait_m()
    modal = page.locator("dialog[open]").last
    modal.wait_for(state="visible", timeout=10_000)
    _wait_s()
    return modal


def _goto_year(page, target_year: int, max_clicks: int = 20):
    """Navigate the yearly date picker to target_year."""
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception:
        pass
    _wait_m()
    for _ in range(max_clicks):
        try:
            txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000).strip()
            yr_m = re.search(r"\b(20\d{2})\b", txt)
            curr = int(yr_m.group(1)) if yr_m else None
        except Exception:
            curr = None
        if curr == target_year:
            return
        if curr is None:
            _wait_m()
            continue
        btn = ("div.date-picker-next > button" if curr < target_year
               else "div.date-picker-previous > button")
        page.locator(btn).first.click()
        _wait_s()


def _select_robust(page, modal, selectors: list[str], label: str):
    """Try each CSS selector in turn until one accepts label."""
    for sel in selectors:
        try:
            modal.locator(sel).select_option(label=label, timeout=3000)
            _wait_s()
            return
        except Exception:
            pass
    raise RuntimeError(f"Could not select '{label}' in any of {selectors}")


def _detect_import_result(page, modal) -> tuple[str, str]:
    """
    Inspect the page/modal after upload and return (result, reason).
    result: "PASS" | "FAIL" | "?"
    """
    ERROR_KEYWORDS = [
        "error", "exception", "invalid", "failed", "not valid",
        "rejected", "cannot", "unexpected", "warning", "unrecognized",
        "incorrect", "wrong", "bad request", "400", "500",
    ]
    SUCCESS_KEYWORDS = [
        "success", "uploaded", "import complete", "nomination",
    ]

    # 1. Modal still open with error text?
    modal_open = False
    modal_text = ""
    try:
        modal_open = modal.is_visible()
        if modal_open:
            modal_text = modal.inner_text(timeout=2000).lower()
    except Exception:
        pass

    if modal_open and any(k in modal_text for k in ERROR_KEYWORDS):
        for line in modal_text.splitlines():
            line = line.strip()
            if any(k in line for k in ERROR_KEYWORDS) and len(line) > 4:
                return "FAIL", f"Modal: \"{line[:120]}\""
        return "FAIL", "Error text in modal"

    # 2. Toast / alert / snackbar
    for sel in [
        ".toast", ".alert", ".snackbar", ".notification",
        "[class*='toast']", "[class*='snack']", "[class*='alert']",
        "[class*='error']", "[class*='success']", "[class*='message']",
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                txt = el.inner_text(timeout=1000).lower().strip()
                if any(k in txt for k in ERROR_KEYWORDS):
                    return "FAIL", f"Toast: \"{txt[:120]}\""
                if any(k in txt for k in SUCCESS_KEYWORDS):
                    return "PASS", f"Toast: \"{txt[:120]}\""
        except Exception:
            pass

    # 3. Scan page body for error keywords
    try:
        body = page.locator("body").inner_text(timeout=2000).lower()
        for k in ERROR_KEYWORDS:
            idx = body.find(k)
            if idx != -1:
                snippet = body[max(0, idx - 20): idx + 60].strip().replace("\n", " ")
                return "FAIL", f"Page text: \"{snippet[:120]}\""
    except Exception:
        pass

    # 4. Modal closed cleanly → success
    if not modal_open:
        return "PASS", "Modal closed without errors"

    return "?", "Could not determine result automatically"


def _ask(tc_id: str, desc: str, expect: str,
         auto: str = "?", reason: str = "") -> tuple[str, str]:
    label = {"PASS": "[expected PASS]", "FAIL": "[expected FAIL]",
             "?":    "[outcome unknown]"}.get(expect, "")
    auto_label = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL", "?": "AUTO-?"}.get(auto, auto)
    match_flag = "" if expect == "?" else (" OK" if auto == expect else " !! MISMATCH")

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


def _import_yearly_csv(page, yearly_url: str, csv_path: Path,
                       offtaker: str, shots_dir: Path) -> tuple[str, str, str]:
    """
    Open the yearly import modal, fill offtaker + year + file, upload,
    wait 5 s, auto-detect result, dismiss modal, navigate to TARGET_YEAR.

    Returns (screenshot_path, auto_result, auto_reason).
    """
    shots_dir.mkdir(parents=True, exist_ok=True)

    # Navigate to yearly nominations
    page.goto(yearly_url, wait_until="domcontentloaded")
    _wait_m(); _ui_quiet(page)
    page.screenshot(path=str(shots_dir / "01_landing.png"))

    # Open import modal
    try:
        _safe_click(page,
            page.get_by_role("button",
                name=re.compile("^Import Nominations Request$", re.I)), "Import button")
    except Exception:
        _safe_click(page,
            page.locator("text=Import Nominations Request").first, "Import fallback")

    modal = _open_modal(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Select offtaker
    _select_robust(page, modal,
                   ["select#offtaker", "select[name='offtaker']"],
                   offtaker)

    # Select year
    _select_robust(page, modal,
                   ["select#year", "select[name='year']"],
                   str(TARGET_YEAR))

    # Attach file
    try:
        modal.locator("input#nominationFile").set_input_files(str(csv_path))
    except Exception:
        modal.locator("input[type='file']").set_input_files(str(csv_path))
    _wait_s()
    page.screenshot(path=str(shots_dir / "03_ready.png"))

    # Upload — wait 5 s for Hera to process
    _safe_click(page,
        modal.get_by_role("button", name=re.compile("^Upload$", re.I)), "Upload")
    print(f"  Waiting 5 s for Hera to process...")
    time.sleep(5)
    _ui_quiet(page)

    # Screenshot while modal / toasts may still be visible
    page.screenshot(path=str(shots_dir / "04_after_upload_raw.png"))

    # Auto-detect
    auto_result, auto_reason = _detect_import_result(page, modal)

    # Dismiss modal if still open
    try:
        if modal.is_visible():
            close = modal.locator("button",
                has_text=re.compile(r"close|cancel|×", re.I)).first
            _safe_click(page, close, "Close modal")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    _ui_quiet(page)

    # Navigate to TARGET_YEAR so the grid reflects the import
    try:
        _goto_year(page, TARGET_YEAR)
    except Exception:
        pass

    _wait_m()
    after_path = str(shots_dir / "05_grid_after_import.png")
    page.screenshot(path=after_path, full_page=True)
    return after_path, auto_result, auto_reason


# ===========================================================================
# Main
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(description="Hera Yearly Nomination Import Test Suite")
    parser.add_argument(
        "--excel", default=None, metavar="PATH",
        help="Report file to write Pass/Fail into. Default: create a new "
             "timestamped copy under UAT Testing/Reports/.",
    )
    args, _ = parser.parse_known_args()

    env_name, base_url = _resolve_env()
    yearly_url = f"{base_url}/nominations/yearly"

    artifacts = HERE / "test_artifacts"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    print(f"\n{'='*60}")
    print(f"  Hera Yearly Import Test Suite")
    print(f"  Environment : {env_name}  ({base_url})")
    print(f"  Target year : {TARGET_YEAR}")
    print(f"  Run ID      : {run_id}")
    print(f"  Test cases  : {len(TEST_CASES)}")
    print(f"{'='*60}\n")

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir),
            headless=False,
            slow_mo=SLOWMO,
            viewport={"width": 1500, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Initial navigation – triggers MFA if session expired
        print("  Navigating to Hera...")
        try:
            page.goto(yearly_url, wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA in the browser window if prompted. │")
        print("  │  Take as long as you need.                          │")
        print("  │  Press Enter here once you see the Hera app.        │")
        print("  └─────────────────────────────────────────────────────┘")
        input("  > ")
        _ui_quiet(page)
        print("  Authentication confirmed. Starting test loop.\n")

        for idx, tc in enumerate(TEST_CASES, 1):
            csv_path  = HERE / tc["file"]
            shots_dir = run_dir / f"{idx:02d}_{tc['id'].replace('/', '_')}"

            print(f"\n{'─'*60}")
            print(f"  [{idx}/{len(TEST_CASES)}]  {tc['id']}  –  {tc['desc']}")
            print(f"  File     : {tc['file']}")
            print(f"  Offtaker : {tc['offtaker']}")
            print(f"  Expect   : {tc['expect']}")

            if not csv_path.exists():
                print(f"  WARNING: file not found – skipping.")
                results.append({**tc, "result": "SKIP", "notes": "file not found",
                                 "screenshot": ""})
                continue

            try:
                screenshot, auto_result, auto_reason = _import_yearly_csv(
                    page, yearly_url, csv_path, tc["offtaker"], shots_dir)
                print(f"  Screenshot: {screenshot}")
                result, notes = _ask(tc["id"], tc["desc"], tc["expect"],
                                     auto=auto_result, reason=auto_reason)
            except Exception as exc:
                print(f"  ERROR during import: {exc}")
                screenshot = ""
                result, notes = "ERROR", str(exc)

            if tc["expect"] not in ("?", result):
                print(f"  !! MISMATCH – expected {tc['expect']}, got {result}")

            results.append({**tc, "result": result, "notes": notes,
                             "screenshot": screenshot})

        ctx.close()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}")
    print(f"{'='*60}")
    print(f"  {'ID':<20} {'Expect':<7} {'Result':<7} {'Match':<6} Description")
    print("─" * 80)

    pass_n = fail_n = skip_n = mismatch_n = 0
    for r in results:
        match = (r["expect"] == "?" or r["result"] == r["expect"])
        flag  = "  " if match else "!!"
        if r["result"] == "SKIP":   skip_n += 1
        elif r["result"] == "PASS": pass_n += 1
        else:                       fail_n += 1
        if not match: mismatch_n += 1
        print(f"  {flag} {r['id']:<20} {r['expect']:<7} {r['result']:<7} "
              f"{str(match):<6} {r['desc']}")

    print("─" * 80)
    print(f"  Passed: {pass_n}  |  Failed: {fail_n}  |  Skipped: {skip_n}  "
          f"|  Mismatches: {mismatch_n}\n")

    results_csv = run_dir / "test_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=[
            "id", "file", "offtaker", "expect", "result", "match", "notes",
            "screenshot", "desc"])
        w.writeheader()
        for r in results:
            w.writerow({**r,
                        "match": r["expect"] == "?" or r["result"] == r["expect"]})
    print(f"  Results saved to : {results_csv}")
    print(f"  Screenshots in   : {run_dir}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_yearly_import.py")


if __name__ == "__main__":
    main()
