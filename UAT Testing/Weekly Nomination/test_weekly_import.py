#!/usr/bin/env python3
"""
test_weekly_import.py – Hera Weekly Nomination Import Test Suite
=================================================================
Run from the repo root or from the 'Weekly Nomination' folder:

    python "Weekly Nomination/test_weekly_import.py"

A browser window opens; log in with MFA if redirected, then the script
cycles through every test CSV and asks you to confirm each result.
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
# expect:  "PASS" = Hera should accept the import and show slots on the grid
#          "FAIL" = Hera should reject the file with an error message
#          "?"    = behaviour unknown – observe and document
# ===========================================================================
TEST_CASES = [
    {
        "id":     "Nom_WN_01/08",
        "file":   "Virya_Nomination_Week35.csv",
        "expect": "PASS",
        "desc":   "Happy path – Virya Week 35 (first import, baseline check)",
    },
    {
        "id":     "Nom_WN_03",
        "file":   "Messer_Nomination_Week35.csv",
        "expect": "PASS",
        "desc":   "Happy path – Messer Week 35",
    },
    {
        "id":     "Nom_WN_04",
        "file":   "Virya_Nomination_Week35_V2.csv",
        "expect": "PASS",
        "desc":   "Overwrite – type 2 trailer on slots 7 / 15 / 35 (V2)",
    },
    {
        "id":     "Nom_WN_05",
        "file":   "Messer_Nomination_Week26.csv",
        "expect": "FAIL",
        "desc":   "Overwrite weekly nomination after deadline – Week 26/2026 is "
                  "already in the past relative to today, so no success "
                  "confirmation should appear",
        # Week 26/2026 is a fixed past week (unlike every other case here,
        # which targets the shared future TARGET_WEEK/TARGET_YEAR).
        "week":   26,
        "year":   2026,
        # Use the stricter detector: for this case "no success toast" IS the
        # expected/correct outcome, even if Hera doesn't raise a loud error.
        "strict_no_success": True,
    },
    {
        "id":     "NEG_03",
        "file":   "Virya_Nomination_Week35_NEG03_ShiftedTimes.csv",
        "expect": "FAIL",
        "desc":   "Slot 1 start/end shifted +1 h (no longer match generated schedule)",
    },
    {
        "id":     "NEG_05",
        "file":   "Virya_Nomination_Week35_NEG05_WrongParitySlot.csv",
        "expect": "FAIL",
        "desc":   "Type 1 on even slot 2 – belongs to Messer, not Virya",
    },
    {
        "id":     "NEG_06",
        "file":   "Virya_Nomination_Week35_NEG06_InvalidTrailerType.csv",
        "expect": "FAIL",
        "desc":   "Invalid trailer type 'Type 99' on slot 3 Drop-Off",
    },
    {
        "id":     "NEG_07",
        "file":   "Virya_Nomination_Week35_NEG07_WrongHeaders.csv",
        "expect": "FAIL",
        "desc":   "Wrong CSV header: 'SlotID' instead of 'PickUpSlotID'",
    },
    {
        "id":     "NEG_08",
        "file":   "Virya_Nomination_Week35_NEG08_MaintWithTrailer.csv",
        "expect": "FAIL",
        "desc":   "Trailer assigned to a Maint. row (power-constrained slot)",
    },
    {
        "id":     "NEG_10",
        "file":   "Virya_Nomination_Week35_NEG10_OverlappingSlots.csv",
        "expect": "FAIL",
        "desc":   "Slot 3 end time overlaps slot 5 start time",
    },
    {
        "id":     "NEG_13",
        "file":   "Virya_Nomination_Week35_NEG13_WrongDateFormat.csv",
        "expect": "FAIL",
        "desc":   "Date format mm/dd/yyyy instead of dd/mm/yyyy",
    },
    {
        "id":     "NEG_15",
        "file":   "Virya_Nomination_Week35_NEG15_TwoDropOff.csv",
        "expect": "FAIL",
        "desc":   "Two consecutive Drop-Offs – plant already occupied at slot 3",
    },
    {
        "id":     "NEG_16",
        "file":   "Virya_Nomination_Week35_NEG16_EditedValues.csv",
        "expect": "?",
        "desc":   "Manually edited values still within valid range – observe behaviour",
    },
    {
        "id":     "NEG_17",
        "file":   "Virya_Nomination_Week35_NEG17_PickUpEmptyPlant.csv",
        "expect": "FAIL",
        "desc":   "Pick-Up on slot 1 with empty plant (no prior Drop-Off)",
    },
    {
        "id":     "NEG_18",
        "file":   "Virya_Nomination_Week35_NEG18_WrongPickUpType.csv",
        "expect": "FAIL",
        "desc":   "Pick-Up type 1 at slot 9 but plant holds type 2 (type mismatch)",
    },
]

# ===========================================================================
# TARGET WEEK — all test CSVs are for this week/year.
# Placeholder values only: main() overwrites both from _weekly_csv_sync.
# ensure_next_week() before the test loop runs, so the suite always targets
# the real upcoming ISO week regardless of when it's run.
# ===========================================================================
TARGET_WEEK = 35
TARGET_YEAR = 2026

# ===========================================================================
# TIMING — increase if Hera responds slowly on your network
# ===========================================================================
WAIT_S  = 0.3   # short pause  (s)
WAIT_M  = 0.7   # medium pause (s)
WAIT_L  = 1.5   # long pause   (s)
SLOWMO  = 0     # Playwright action delay in ms (0 = full speed; 200 = visible)

# ===========================================================================
# Internal helpers
# ===========================================================================
import argparse, re, sys, time, csv as _csv
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from _weekly_csv_sync import ensure_next_week

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results


HERE = Path(__file__).parent  # folder containing this script and all CSVs


def _resolve_env() -> tuple[str, str]:
    """Return (env_name, base_url), prompting if ENV is blank."""
    env = ENV.strip().lower()
    if env not in ENVIRONMENTS:
        print("\nSelect environment:")
        for i, key in enumerate(ENVIRONMENTS, 1):
            print(f"  {i}. {key}  ({ENVIRONMENTS[key]})")
        while True:
            choice = input("Enter 1 or 2: ").strip()
            if choice == "1":
                env = "dev"
                break
            if choice == "2":
                env = "test"
                break
    return env, ENVIRONMENTS[env]


def _derive_offtaker(filename: str) -> str:
    name = filename.lower()
    if "messer" in name:
        return "Messer Belgium NV"
    return "Virya Energy NV"


def _derive_week(filename: str) -> int | None:
    m = re.search(r"[Ww](?:eek)?\s*_?(\d{1,2})", filename)
    return int(m.group(1)) if m else None


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


def _current_week_and_year(page) -> tuple[int | None, int | None]:
    """Read week number and year from the date picker button or visible page text."""
    try:
        _ui_quiet(page, 4000)
        txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000)
        wk_m = re.search(r"Week\s*(\d{1,2})", txt, re.I)
        yr_m = re.search(r"\b(20\d{2})\b", txt)
        wk = int(wk_m.group(1)) if wk_m else None
        yr = int(yr_m.group(1)) if yr_m else None
    except Exception:
        return None, None

    # If year not in the button text, scan visible page content for a year
    if yr is None:
        try:
            body = page.locator("body").inner_text(timeout=3000)
            years = re.findall(r"\b(20\d{2})\b", body)
            if years:
                from collections import Counter
                yr = int(Counter(years).most_common(1)[0][0])
        except Exception:
            pass

    return wk, yr


def _goto_week_year(page, target_week: int, target_year: int, max_clicks: int = 120):
    """Navigate to a specific ISO week of a specific year."""
    if not target_week or not target_year:
        return
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception:
        pass
    _wait_m()

    for _ in range(max_clicks):
        wk, yr = _current_week_and_year(page)
        if wk == target_week and (yr is None or yr == target_year):
            return
        if wk is None:
            _wait_m()
            continue
        # Use year-aware total offset so we never confuse week 20/2026 with week 20/2027
        curr_yr = yr if yr is not None else target_year  # assume same year if unknown
        total_diff = (target_year - curr_yr) * 52 + (target_week - wk)
        if total_diff == 0:
            return
        btn = ("div.date-picker-next > button" if total_diff > 0
               else "div.date-picker-previous > button")
        page.locator(btn).first.click()
        _wait_s()
    raise RuntimeError(f"Could not reach Week {target}")


def _open_modal(page) -> object:
    _wait_m()
    modal = page.locator("dialog[open]").last
    modal.wait_for(state="visible", timeout=10_000)
    _wait_s()
    return modal


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

    # 1. Is the modal still visible? (modal staying open = likely an error inside it)
    modal_open = False
    modal_text = ""
    try:
        modal_open = modal.is_visible()
        if modal_open:
            modal_text = modal.inner_text(timeout=2000).lower()
    except Exception:
        pass

    if modal_open and any(k in modal_text for k in ERROR_KEYWORDS):
        # Extract a short snippet for the reason
        for line in modal_text.splitlines():
            line = line.strip()
            if any(k in line for k in ERROR_KEYWORDS) and len(line) > 4:
                return "FAIL", f"Modal: \"{line[:120]}\""
        return "FAIL", "Error text in modal"

    # 2. Toast / alert / snackbar messages
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

    # 3. Any visible text on the page containing error keywords
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


def _detect_success_toast_only(page, modal) -> tuple[str, str]:
    """Stricter variant for 'should be silently blocked' cases (e.g. importing
    after the nomination deadline has passed): Hera may decline without
    raising a loud error, so the thing to verify is that the normal success
    confirmation is ABSENT, not merely that no error text appeared. Only
    returns PASS if an explicit success toast/keyword is actually observed —
    unlike _detect_import_result, a cleanly-closed modal with nothing found
    is NOT treated as success here."""
    SUCCESS_KEYWORDS = ["success", "uploaded", "import complete"]
    ERROR_KEYWORDS = [
        "error", "exception", "invalid", "failed", "not valid",
        "rejected", "cannot", "unexpected", "warning", "unrecognized",
        "incorrect", "wrong", "bad request", "400", "500", "deadline",
    ]

    try:
        if modal.is_visible():
            modal_text = modal.inner_text(timeout=2000).lower()
            if any(k in modal_text for k in ERROR_KEYWORDS):
                return "FAIL", "Modal shows a rejection/warning message (no success toast) — deadline block confirmed."
    except Exception:
        pass

    for sel in [".toast", ".alert", ".snackbar", ".notification",
                "[class*='toast']", "[class*='snack']", "[class*='alert']",
                "[class*='success']", "[class*='message']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                txt = el.inner_text(timeout=1000).lower().strip()
                if any(k in txt for k in SUCCESS_KEYWORDS):
                    return "PASS", (
                        f"Success toast WAS shown despite the past deadline: "
                        f"\"{txt[:120]}\" — Hera did not block the overwrite.")
                if any(k in txt for k in ERROR_KEYWORDS):
                    return "FAIL", f"Rejection toast: \"{txt[:120]}\" — deadline block confirmed."
        except Exception:
            pass

    return "FAIL", (
        "No success confirmation toast/message appeared — deadline block "
        "confirmed (no explicit error either, but nothing confirms the "
        "overwrite went through)."
    )


def _ask(tc_id: str, desc: str, expect: str,
         auto: str = "?", reason: str = "") -> tuple[str, str]:
    """Show auto-detected result; let the user confirm or override."""
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
        raw = input(f"  Confirm? (P=pass / F=fail / S=skip / I=inconclusive / Enter={default}): ").strip().lower()
        result = ("FAIL" if raw in ("f", "fail", "n", "no")
                  else "PASS" if raw in ("p", "pass", "y", "yes", "o", "oui")
                  else "SKIP" if raw in ("s", "skip")
                  else "?" if raw in ("i", "inconclusive")
                  else default)
        notes = input("  Notes (optional): ").strip()
        return result, notes
    except EOFError:
        return default, reason


def _wait_for_login(page, base_url: str):
    """If redirected to MSAL login, pause until the user authenticates."""
    start = time.time()
    while time.time() - start < 300:
        try:
            url = page.url
        except Exception:
            break
        if "login.microsoftonline.com" not in url:
            return
        time.sleep(1)
    raise RuntimeError("Login timed out (5 min)")


def _import_csv(page, nominations_url: str, csv_path: Path,
                shots_dir: Path, tc_id: str,
                target_week: int = TARGET_WEEK, target_year: int = TARGET_YEAR,
                detect_fn=_detect_import_result) -> tuple[str, str, str]:
    """
    Navigate to the nominations page, open the import modal, upload the file,
    wait for processing, auto-detect the result, and take screenshots.

    Returns (screenshot_path, auto_result, auto_reason).
    auto_result: "PASS" | "FAIL" | "?"
    """
    shots_dir.mkdir(parents=True, exist_ok=True)
    offtaker = _derive_offtaker(csv_path.name)

    # Navigate
    page.goto(nominations_url, wait_until="domcontentloaded")
    _wait_m(); _ui_quiet(page)
    page.screenshot(path=str(shots_dir / "01_landing.png"))

    # Open import modal
    try:
        _safe_click(page,
            page.get_by_role("button",
                name=re.compile("^Import Nominations Request$", re.I)), "Import button")
    except Exception:
        _safe_click(page,
            page.locator("text=Import Nominations Request").first, "Import button (fallback)")

    modal = _open_modal(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Select offtaker
    try:
        modal.locator("select#offtaker").select_option(label=offtaker)
    except Exception:
        modal.locator("select[name='offtaker']").select_option(label=offtaker)
    _wait_s()

    # Attach file
    try:
        modal.locator("input#nominationFile").set_input_files(str(csv_path))
    except Exception:
        modal.locator("input[type='file']").set_input_files(str(csv_path))
    _wait_s()
    page.screenshot(path=str(shots_dir / "03_ready.png"))

    # Upload — then give Hera 5 s to process before inspecting the response
    _safe_click(page,
        modal.get_by_role("button", name=re.compile("^Upload$", re.I)), "Upload")
    print(f"  Waiting 5 s for Hera to process...")
    time.sleep(5)
    _ui_quiet(page)

    # Screenshot immediately after processing (modal may still be open with an error)
    page.screenshot(path=str(shots_dir / "04_after_upload_raw.png"))

    # Auto-detect result while modal / toasts are still visible
    auto_result, auto_reason = detect_fn(page, modal)

    # Dismiss modal if it's still open (close button or Escape)
    try:
        if modal.is_visible():
            close = modal.locator("button", has_text=re.compile(r"close|cancel|×", re.I)).first
            _safe_click(page, close, "Close modal")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    _ui_quiet(page)

    # Navigate to the target week/year to show the grid state
    try:
        _goto_week_year(page, target_week, target_year)
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
    global TARGET_WEEK, TARGET_YEAR

    parser = argparse.ArgumentParser(description="Hera Weekly Nomination Import Test Suite")
    parser.add_argument(
        "--excel", default=None, metavar="PATH",
        help="Report file to write Pass/Fail into. Default: create a new "
             "timestamped copy under UAT Testing/Reports/.",
    )
    args, _ = parser.parse_known_args()

    # Before anything else: make sure the CSV family targets next week, not
    # whatever week it was last generated for.
    print("  Checking weekly nomination CSVs are dated for next week…")
    TARGET_WEEK, TARGET_YEAR = ensure_next_week()

    env_name, base_url = _resolve_env()
    nominations_url = f"{base_url}/nominations/weekly"

    artifacts = HERE / "test_artifacts"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    print(f"\n{'='*60}")
    print(f"  Hera Weekly Import Test Suite")
    print(f"  Environment : {env_name}  ({base_url})")
    print(f"  Run ID      : {run_id}")
    print(f"  Test cases  : {len(TEST_CASES)}")
    print(f"{'='*60}\n")
    print("  The browser will open. Log in with MFA if prompted.")
    print("  After each import you will be asked to confirm the result.\n")

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir),
            headless=False,
            slow_mo=SLOWMO,
            viewport={"width": 1500, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Initial navigation – triggers MFA if not yet authenticated
        print("  Navigating to Hera...")
        try:
            page.goto(nominations_url, wait_until="commit", timeout=20_000)
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
            print(f"  File   : {tc['file']}")
            print(f"  Expect : {tc['expect']}")

            if not csv_path.exists():
                print(f"  WARNING: file not found – skipping.")
                results.append({**tc, "result": "SKIP", "notes": "file not found",
                                 "screenshot": ""})
                continue

            try:
                detect_fn = (_detect_success_toast_only if tc.get("strict_no_success")
                             else _detect_import_result)
                screenshot, auto_result, auto_reason = _import_csv(
                    page, nominations_url, csv_path, shots_dir, tc["id"],
                    target_week=tc.get("week", TARGET_WEEK),
                    target_year=tc.get("year", TARGET_YEAR),
                    detect_fn=detect_fn)
                print(f"  Screenshot saved: {screenshot}")
                result, notes = _ask(tc["id"], tc["desc"], tc["expect"],
                                     auto=auto_result, reason=auto_reason)
            except Exception as exc:
                print(f"  ERROR during import: {exc}")
                screenshot = ""
                result, notes = "ERROR", str(exc)

            # Highlight mismatches immediately
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
    header = f"{'ID':<18} {'Expect':<7} {'Result':<7} {'Match':<6} Description"
    print(header)
    print("─" * 80)

    pass_n = fail_n = skip_n = mismatch_n = 0
    for r in results:
        match = (r["expect"] == "?" or r["result"] == r["expect"])
        flag  = "  " if match else "!!"
        if r["result"] == "SKIP":
            skip_n += 1
        elif r["result"] == "PASS":
            pass_n += 1
        else:
            fail_n += 1
        if not match:
            mismatch_n += 1
        print(f"  {flag} {r['id']:<16} {r['expect']:<7} {r['result']:<7} {str(match):<6} {r['desc']}")

    print("─" * 80)
    print(f"  Passed: {pass_n}  |  Failed: {fail_n}  |  Skipped: {skip_n}  "
          f"|  Mismatches: {mismatch_n}")
    print()

    # Save CSV results
    results_csv = run_dir / "test_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["id","file","expect","result","match","notes","screenshot","desc"])
        w.writeheader()
        for r in results:
            w.writerow({**r, "match": r["expect"] == "?" or r["result"] == r["expect"]})
    print(f"  Results saved to: {results_csv}")
    print(f"  Screenshots in : {run_dir}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_weekly_import.py")


if __name__ == "__main__":
    main()
