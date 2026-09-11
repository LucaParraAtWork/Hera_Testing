#!/usr/bin/env python3
"""
test_yearly_ui.py – Hera Yearly Nomination UI Interaction Tests
=================================================================
Dedicated, layered-script replacement for the yearly-nomination UI flows
that only existed as unconditional-PASS, screenshot-only steps inside the
legacy hera_import_and_profiles.py monolith (its steps 21-23). This script
follows the same pattern as "Weekly Nomination/test_weekly_ui.py":
SCENARIOS list, live API capture, real assertions, _ask() confirmation,
results CSV.

The legacy script was used as the reference for *how to drive the UI*
(button texts, selectors: "Validate Year", "View History", "chart" tab,
select#yearly-offtaker, the card-value edit interaction) — but unlike the
legacy version, every scenario here actually asserts something from the
captured GetYearlyNominationScreenAsync API response instead of always
logging PASS.

Scenarios covered (Excel test case they close the gap on):
  UI_01     Access/view uploaded yearly nomination      → Nom_YN_05
  UI_02     Yearly chart view                           → Nom_YN_07
  UI_03     Edit history breakdown and submit new version → Nom_YN_06

This script does NOT import CSV files — that is already covered by
test_yearly_import.py (Nom_YN_01/03/04 and the NEG_* cases). Prerequisite:
a yearly nomination for TARGET_OFFTAKER/TARGET_YEAR must already exist in
Hera (e.g. run test_yearly_import.py first, or reuse existing test data) —
this script only reads/edits it through the UI.

Run order note: UI_01 and UI_02 are read-only and run against whatever
version is currently active; UI_03 mutates the data (submits a new
version), so it runs last — this is why the execution order is 01/02/03
while the Excel IDs are 05/07/06.

Run:
    python "Yearly Nomination/test_yearly_ui.py"
    python "Yearly Nomination/test_yearly_ui.py" --from 2
"""

# ==========================================================================
# ENVIRONMENT — "dev" | "test" | "" (ask at startup)
# ==========================================================================
ENV = ""

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

START_FROM = 1

# ==========================================================================
# TARGET — reuses the same fixture as Nom_YN_03 in test_yearly_import.py
# ==========================================================================
TARGET_YEAR     = 2028
TARGET_OFFTAKER = "Messer Belgium NV"
BASELINE_CSV    = "Messer_Nomination_Year2028.csv"

# ==========================================================================
# TIMING
# ==========================================================================
WAIT_S = 0.3
WAIT_M = 0.8
WAIT_L = 1.8
SLOWMO = 0

# ==========================================================================
# SCENARIOS
# ==========================================================================
SCENARIOS = [
    {"n": 1, "id": "UI_01", "group": "view", "excel_id": "Nom_YN_05",
     "name": "Access/view uploaded yearly nomination",
     "desc": ("Select offtaker + year, click Validate Year, open View History, select a "
              "version. Assert the captured API response has a non-null current version "
              "and non-empty version history / breakdowns. Note whether a download/export "
              "control exists (informational only — Excel says 'if available')."),
     "expect": "PASS"},

    {"n": 2, "id": "UI_02", "group": "chart", "excel_id": "Nom_YN_07",
     "name": "Yearly chart view",
     "desc": ("Select offtaker + year, click the 'chart' tab. Assert a chart element "
              "actually renders (non-zero bounding box), and cross-check the captured "
              "monthlyBreakdowns/weeklyBreakdowns against the imported CSV values."),
     "expect": "PASS"},

    {"n": 3, "id": "UI_03", "group": "edit", "excel_id": "Nom_YN_06",
     "name": "Edit history breakdown and submit new version",
     "desc": ("Open View History, select the current version, reduce Jan and W01 by 2, "
              "Submit new version, reopen View History. Assert: new version number = "
              "old + 1, new entry status = 'New', previous version still listed."),
     "expect": "PASS"},
]

# ==========================================================================
# Internals
# ==========================================================================
import argparse, csv as _csv, json, re, sys, time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE         = Path(__file__).parent
BASELINE_CSV_PATH = HERE / BASELINE_CSV
SEP          = "  " + "─" * 70

# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
def _ws(): time.sleep(WAIT_S)
def _wm(): time.sleep(WAIT_M)
def _wl(): time.sleep(WAIT_L)

def _quiet(page, ms=8000):
    try: page.wait_for_load_state("networkidle", timeout=ms)
    except Exception: pass

def _tick(ok): return "✓" if ok else "✗"

# --------------------------------------------------------------------------
# Environment (same pattern as test_yearly_import.py / test_weekly_ui.py)
# --------------------------------------------------------------------------
def _resolve_env():
    env = ENV.strip().lower()
    if env not in ENVIRONMENTS:
        print("\nSelect environment:")
        for i, k in enumerate(ENVIRONMENTS, 1):
            print(f"  {i}. {k}  ({ENVIRONMENTS[k]})")
        while True:
            c = input("Enter 1 or 2: ").strip()
            if c == "1": env = "dev"; break
            if c == "2": env = "test"; break
    return env, ENVIRONMENTS[env]

# --------------------------------------------------------------------------
# Playwright helpers (ported from test_yearly_import.py)
# --------------------------------------------------------------------------
def _safe_click(page, locator, what="element"):
    for fn in [
        lambda: locator.click(timeout=2000),
        lambda: (locator.scroll_into_view_if_needed(timeout=1500),
                 locator.click(timeout=1500, force=True)),
    ]:
        try: fn(); _ws(); return
        except Exception: pass
    try:
        h = locator.element_handle(timeout=5000)
        page.evaluate("el => el.click()", h); _ws()
    except Exception as e:
        raise RuntimeError(f"Cannot click {what}: {e}")

def _open_modal(page):
    _wm()
    m = page.locator("dialog[open]").last
    m.wait_for(state="visible", timeout=10_000)
    _ws()
    return m

def _close_modal(page, modal):
    try:
        if modal.is_visible():
            close = modal.locator("button", has_text=re.compile(r"close", re.I)).first
            _safe_click(page, close, "Close")
    except Exception:
        try: page.keyboard.press("Escape")
        except Exception: pass
    _quiet(page)

def _select_robust(page, container, selectors, label):
    for sel in selectors:
        try:
            container.locator(sel).select_option(label=label, timeout=3000)
            _ws(); return
        except Exception:
            pass
    raise RuntimeError(f"Could not select '{label}' in any of {selectors}")

def _goto_year(page, target_year: int, max_clicks: int = 20):
    try: page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception: pass
    _wm()
    for _ in range(max_clicks):
        try:
            txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000).strip()
            m = re.search(r"\b(20\d{2})\b", txt)
            curr = int(m.group(1)) if m else None
        except Exception:
            curr = None
        if curr == target_year:
            return
        if curr is None:
            _wm(); continue
        btn = ("div.date-picker-next > button" if curr < target_year
               else "div.date-picker-previous > button")
        page.locator(btn).first.click()
        _ws()

# --------------------------------------------------------------------------
# API response capture (same pattern as Scheduled Transfers / Business Rules)
# --------------------------------------------------------------------------
_CAPTURED: dict = {}

def _make_handler(captures):
    def _h(response):
        try:
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            url = response.url
            for key, pattern in captures:
                if pattern in url:
                    try:
                        _CAPTURED[key] = response.json()
                        print(f"  [CAPTURE] {key} ← …/{url.split('/')[-1].split('?')[0]}")
                    except Exception:
                        pass
        except Exception:
            pass
    return _h

def _inner(body):
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        return body["data"]
    return body

# --------------------------------------------------------------------------
# CSV baseline parsing — {month/week index: trailer count}
# --------------------------------------------------------------------------
def _parse_yearly_csv(path: Path):
    monthly, weekly = {}, {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(_csv.reader(f))
    for row in rows[1:]:
        if len(row) < 3 or not row[0].strip():
            continue
        kind, period, count = row[0].strip().lower(), row[1].strip(), row[2].strip()
        try:
            period_i, count_i = int(period), int(count)
        except ValueError:
            continue
        if kind == "month":
            monthly[period_i] = count_i
        elif kind == "week":
            weekly[period_i] = count_i
    return monthly, weekly

# --------------------------------------------------------------------------
# User prompt (same pattern as the other scripts)
# --------------------------------------------------------------------------
def _ask(scenario, auto, reason):
    label    = "[expected PASS]" if scenario["expect"] == "PASS" else "[observe]"
    aflag    = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL"}.get(auto, f"AUTO-{auto}")
    mismatch = (scenario["expect"] not in ("?",) and auto not in ("?",) and auto != scenario["expect"])
    print(f"\n{SEP}")
    print(f"  {scenario['id']} {label}  –  {scenario['name']}")
    print(f"  Auto : {aflag}{'  !! MISMATCH' if mismatch else ''}")
    if reason: print(f"  Why  : {reason}")
    default = auto if auto in ("PASS", "FAIL") else "PASS"
    try:
        raw    = input(f"  OK?  (P=pass / F=fail / S=skip / I=inconclusive / Enter={default}): ").strip().lower()
        result = ("FAIL" if raw in ("f", "fail", "n", "no")
                  else "PASS" if raw in ("p", "pass", "y", "yes", "o", "oui")
                  else "SKIP" if raw in ("s", "skip")
                  else "?" if raw in ("i", "inconclusive")
                  else default)
        notes  = input("  Notes (optional): ").strip()
        return result, notes
    except EOFError:
        return default, reason

# ==========================================================================
# UI_01 (Nom_YN_05): Access/view uploaded yearly nomination
# ==========================================================================
def _do_view_access(page, yearly_url, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    handler = _make_handler([("yn_view", "YearlyNominationScreen/GetYearlyNominationScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(yearly_url, wait_until="domcontentloaded")
        _wm(); _quiet(page)

        try:
            _select_robust(page, page, ["select#yearly-offtaker", "select[name='offtaker']"],
                            TARGET_OFFTAKER)
        except Exception as e:
            return "FAIL", f"Could not select offtaker on yearly view: {e}"

        try: _goto_year(page, TARGET_YEAR)
        except Exception: pass
        _quiet(page)
        page.screenshot(path=str(shots_dir / "01_offtaker_year_selected.png"), full_page=True)

        # Validate Year (best-effort — a confirmation popup may or may not appear)
        try:
            validate_btn = page.locator("button", has_text=re.compile("Validate Year", re.I)).first
            _safe_click(page, validate_btn, "Validate Year")
            _wm()
            try:
                popup = _open_modal(page)
                page.screenshot(path=str(shots_dir / "02_validate_popup.png"))
                _safe_click(page,
                    popup.get_by_role("button", name=re.compile("^Confirm$", re.I)), "Confirm")
                _wl(); _quiet(page)
            except Exception:
                pass  # no popup this time — Validate Year may act immediately
        except Exception as e:
            print(f"  [WARN] Validate Year not clicked: {e}")

        page.screenshot(path=str(shots_dir / "03_after_validate.png"), full_page=True)

        # View History
        try:
            history_btn = page.locator("button", has_text=re.compile("View History", re.I)).first
            _safe_click(page, history_btn, "View History")
            modal = _open_modal(page)
        except Exception as e:
            return "FAIL", f"View History did not open: {e}"

        page.screenshot(path=str(shots_dir / "04_history_modal.png"))

        # Select a version row (best-effort; not required for the assertion below)
        try:
            _safe_click(page, modal.locator("tr").last, "last version row")
            _wm()
        except Exception:
            pass

        # Informational: does a download/export control exist? (Excel: "if available")
        has_download = False
        try:
            has_download = modal.locator(
                "button, a", has_text=re.compile(r"download|export", re.I)).count() > 0
        except Exception:
            pass

        page.screenshot(path=str(shots_dir / "05_version_selected.png"))
        _close_modal(page, modal)

        if "yn_view" not in _CAPTURED:
            return "FAIL", "GetYearlyNominationScreenAsync was never captured"

        data = _inner(_CAPTURED["yn_view"])
        version_lines = data.get("versionLines") or []
        monthly = data.get("monthlyBreakdowns") or []
        weekly  = data.get("weeklyBreakdowns") or []

        issues = []
        if data.get("currentVersion") is None:
            issues.append("currentVersion is null")
        if not version_lines:
            issues.append("versionLines is empty")
        if not monthly:
            issues.append("monthlyBreakdowns is empty")
        if not weekly:
            issues.append("weeklyBreakdowns is empty")

        dl_note = "download/export control found" if has_download else "no download/export control found (Excel says 'if available')"

        if issues:
            return "FAIL", f"{'; '.join(issues)}. {dl_note}."
        return ("PASS",
                f"currentVersion={data.get('currentVersion')}, "
                f"{len(version_lines)} version(s) in history, "
                f"{len(monthly)} monthly + {len(weekly)} weekly breakdown block set(s). {dl_note}.")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# ==========================================================================
# UI_02 (Nom_YN_07): Yearly chart view
# ==========================================================================
def _do_chart_view(page, yearly_url, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    expected_monthly, expected_weekly = _parse_yearly_csv(BASELINE_CSV_PATH)

    handler = _make_handler([("yn_chart", "YearlyNominationScreen/GetYearlyNominationScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(yearly_url, wait_until="domcontentloaded")
        _wm(); _quiet(page)

        try:
            _select_robust(page, page, ["select#yearly-offtaker", "select[name='offtaker']"],
                            TARGET_OFFTAKER)
        except Exception as e:
            return "FAIL", f"Could not select offtaker on yearly view: {e}"

        try: _goto_year(page, TARGET_YEAR)
        except Exception: pass
        _quiet(page)
        page.screenshot(path=str(shots_dir / "01_before_chart.png"), full_page=True)

        try:
            chart_tab = page.locator("button", has_text=re.compile(r"^chart$", re.I)).first
            _safe_click(page, chart_tab, "Chart tab")
        except Exception as e:
            return "FAIL", f"'chart' tab button not found: {e}"

        _wl(); _quiet(page)
        page.screenshot(path=str(shots_dir / "02_chart_view.png"), full_page=True)

        # DOM check: some chart-rendering element must actually be visible
        chart_visible = False
        for sel in ["canvas", "svg", "[class*='chart']", "[class*='highcharts']"]:
            try:
                el = page.locator(sel).first
                bb = el.bounding_box(timeout=1500)
                if bb and bb["width"] > 20 and bb["height"] > 20:
                    chart_visible = True
                    break
            except Exception:
                pass

        if not chart_visible:
            return "FAIL", "No chart element (canvas/svg) with a visible bounding box found"

        if "yn_chart" not in _CAPTURED:
            return ("?",
                    "Chart element rendered, but GetYearlyNominationScreenAsync was not "
                    "captured — cannot cross-check data. Manual visual check recommended.")

        data    = _inner(_CAPTURED["yn_chart"])
        monthly = data.get("monthlyBreakdowns") or []
        weekly  = data.get("weeklyBreakdowns") or []

        mismatches = []
        for bset in monthly:
            for block in bset.get("blocks", []):
                idx, val = block.get("index"), block.get("value")
                if idx in expected_monthly and expected_monthly[idx] != val:
                    mismatches.append(f"month {idx}: API={val} CSV={expected_monthly[idx]}")
        for bset in weekly:
            for block in bset.get("blocks", []):
                idx, val = block.get("index"), block.get("value")
                if idx in expected_weekly and expected_weekly[idx] != val:
                    mismatches.append(f"week {idx}: API={val} CSV={expected_weekly[idx]}")

        if mismatches:
            return "FAIL", f"Chart rendered but {len(mismatches)} breakdown mismatch(es): " + \
                   "; ".join(mismatches[:6])
        return ("PASS",
                f"Chart element visible; breakdown data matches imported CSV "
                f"({len(expected_monthly)} month(s), {len(expected_weekly)} week(s) checked)")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# ==========================================================================
# UI_03 (Nom_YN_06): Edit history breakdown and submit new version
# ==========================================================================
def _reduce_card_value(page, label: str, delta: int = -2):
    """Best-effort port of hera_import_and_profiles.py's reduce_card_value."""
    card = page.locator("div.card, .breakdown-item, .item, [class*='card']").filter(
        has_text=re.compile(f"^{label}$", re.I)).first
    card.scroll_into_view_if_needed()

    input_el = card.locator("input[type='number'], input").first
    current_val = None
    val_el = None
    if input_el.count() > 0 and input_el.is_visible():
        m = re.search(r"(\d+)", input_el.input_value())
        if m:
            current_val, val_el = int(m.group(1)), input_el

    if val_el is None:
        for el in card.locator("*:not(:has(*))").all():
            t = (el.inner_text() or "").strip()
            m = re.search(r"(\d+)", t)
            if m:
                current_val, val_el = int(m.group(1)), el
                break

    if val_el is None:
        raise RuntimeError(f"Could not find an editable value for '{label}'")

    new_val = max(0, current_val + delta)
    box = val_el.bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        time.sleep(0.2)
        page.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    else:
        val_el.click(force=True)
    _ws()
    page.keyboard.press("Control+A"); _ws()
    page.keyboard.press("Backspace"); _ws()
    page.keyboard.type(str(new_val)); _ws()
    page.keyboard.press("Enter")
    return current_val, new_val


def _do_edit_submit(page, yearly_url, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)

    handler = _make_handler([("yn_edit", "YearlyNominationScreen/GetYearlyNominationScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(yearly_url, wait_until="domcontentloaded")
        _wm(); _quiet(page)

        try:
            _select_robust(page, page, ["select#yearly-offtaker", "select[name='offtaker']"],
                            TARGET_OFFTAKER)
        except Exception as e:
            return "FAIL", f"Could not select offtaker on yearly view: {e}"

        try: _goto_year(page, TARGET_YEAR)
        except Exception: pass
        _quiet(page)

        try:
            validate_btn = page.locator("button", has_text=re.compile("Validate Year", re.I)).first
            _safe_click(page, validate_btn, "Validate Year")
            _wm()
            try:
                popup = _open_modal(page)
                _safe_click(page,
                    popup.get_by_role("button", name=re.compile("^Confirm$", re.I)), "Confirm")
                _wl(); _quiet(page)
            except Exception:
                pass
        except Exception as e:
            print(f"  [WARN] Validate Year not clicked: {e}")

        # ── Baseline: open View History, read current version list ──────────
        try:
            history_btn = page.locator("button", has_text=re.compile("View History", re.I)).first
            _safe_click(page, history_btn, "View History")
            modal = _open_modal(page)
        except Exception as e:
            return "FAIL", f"View History did not open: {e}"
        page.screenshot(path=str(shots_dir / "01_history_before.png"))

        if "yn_edit" not in _CAPTURED:
            _close_modal(page, modal)
            return "FAIL", "GetYearlyNominationScreenAsync was never captured (baseline)"

        baseline = _inner(_CAPTURED["yn_edit"])
        baseline_lines = baseline.get("versionLines") or []
        if not baseline_lines:
            _close_modal(page, modal)
            return "FAIL", "versionLines is empty — cannot determine current version"
        baseline_highest = max(v.get("version", -1) for v in baseline_lines)

        # Select the current/highest version row
        try:
            _safe_click(page, modal.locator("tr").last, "current version row")
            _wm()
        except Exception as e:
            _close_modal(page, modal)
            return "FAIL", f"Could not select the current version row: {e}"
        page.screenshot(path=str(shots_dir / "02_version_selected.png"))

        # ── Reduce Jan and W01 by 2 ───────────────────────────────────────────
        try:
            jan_before, jan_after = _reduce_card_value(page, "Jan")
            print(f"  Jan: {jan_before} → {jan_after}")
        except Exception as e:
            print(f"  [WARN] Could not edit 'Jan' card: {e}")
        try:
            w01_before, w01_after = _reduce_card_value(page, "W01")
            print(f"  W01: {w01_before} → {w01_after}")
        except Exception as e:
            print(f"  [WARN] Could not edit 'W01' card: {e}")
        page.screenshot(path=str(shots_dir / "03_after_edits.png"))

        # ── Submit new version ────────────────────────────────────────────────
        try:
            submit_btn = page.locator("button", has_text=re.compile("Submit new version", re.I)).first
            _safe_click(page, submit_btn, "Submit new version")
            _wl(); _quiet(page)
        except Exception as e:
            return "FAIL", f"'Submit new version' failed: {e}"
        page.screenshot(path=str(shots_dir / "04_after_submit.png"))

        # ── Reopen View History, capture the post-submit response ───────────
        _CAPTURED.pop("yn_edit", None)
        try:
            history_btn2 = page.locator("button", has_text=re.compile("View History", re.I)).first
            _safe_click(page, history_btn2, "View History (reopen)")
            modal2 = _open_modal(page)
        except Exception as e:
            return "FAIL", f"View History did not reopen after submit: {e}"
        page.screenshot(path=str(shots_dir / "05_history_after.png"))
        _close_modal(page, modal2)

        if "yn_edit" not in _CAPTURED:
            return "FAIL", "GetYearlyNominationScreenAsync was not captured after submit"

        after = _inner(_CAPTURED["yn_edit"])
        after_lines = after.get("versionLines") or []
        if not after_lines:
            return "FAIL", "versionLines is empty after submit"

        after_highest_line = max(after_lines, key=lambda v: v.get("version", -1))
        after_highest = after_highest_line.get("version", -1)
        after_status  = str(after_highest_line.get("status", "")).strip().lower()
        prev_present  = any(v.get("version") == baseline_highest for v in after_lines)

        issues = []
        if after_highest != baseline_highest + 1:
            issues.append(f"new highest version {after_highest} != baseline {baseline_highest}+1")
        if after_status != "new":
            issues.append(f"new entry status = '{after_status}', expected 'New'")
        if not prev_present:
            issues.append(f"previous version {baseline_highest} no longer present in history")

        if issues:
            return "FAIL", "; ".join(issues)
        return ("PASS",
                f"Version {baseline_highest} → {after_highest} (status='{after_highest_line.get('status')}'), "
                f"previous version still present")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# ==========================================================================
# Main
# ==========================================================================
def main():
    _CAPTURED.clear()

    parser = argparse.ArgumentParser(
        description="Hera Yearly Nomination – UI Interaction Tests")
    parser.add_argument(
        "--from", dest="start_from", type=int, default=START_FROM, metavar="N",
        help="Resume from scenario N (1-based).")
    parser.add_argument(
        "--excel", default=None, metavar="PATH",
        help="Report file to write Pass/Fail into. Default: create a new "
             "timestamped copy under UAT Testing/Reports/.")
    args, _ = parser.parse_known_args()
    start_from = max(1, args.start_from)

    env_name, base_url = _resolve_env()
    yearly_url = f"{base_url}/nominations/yearly"

    if not BASELINE_CSV_PATH.exists():
        print(f"  [WARN] {BASELINE_CSV_PATH} not found — UI_02's chart-data "
              f"cross-check will have nothing to compare against.")

    artifacts = HERE / "test_artifacts_ui"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*64}")
    print(f"  Hera Yearly Nomination – UI Interaction Tests")
    print(f"  Environment   : {env_name}  ({base_url})")
    print(f"  Target        : {TARGET_OFFTAKER}, Year {TARGET_YEAR}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"  Prerequisite  : a {TARGET_OFFTAKER} Year {TARGET_YEAR} nomination must already "
          f"exist in Hera (this script does not import — run test_yearly_import.py first "
          f"if needed).")
    print(f"{'='*64}\n")

    results: list[dict] = []

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir), headless=False, slow_mo=SLOWMO,
            viewport={"width": 1500, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("  Navigating to Hera…")
        try:
            page.goto(yearly_url, wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA if prompted.                       │")
        print("  │  Press Enter once you see the Hera app.             │")
        print("  └─────────────────────────────────────────────────────┘")
        input("  > ")
        _quiet(page)
        print("  Login confirmed. Starting scenarios.\n")

        for sc in SCENARIOS:
            if sc["n"] < start_from:
                continue

            shots_dir = run_dir / f"{sc['n']:02d}_{sc['id']}"
            print(f"\n{'═'*64}")
            print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['name']}")
            print(f"  {sc['desc']}")

            try:
                if   sc["id"] == "UI_01":
                    auto, reason = _do_view_access(page, yearly_url, shots_dir)
                elif sc["id"] == "UI_02":
                    auto, reason = _do_chart_view(page, yearly_url, shots_dir)
                elif sc["id"] == "UI_03":
                    auto, reason = _do_edit_submit(page, yearly_url, shots_dir)
                else:
                    auto, reason = "?", "Unknown scenario ID"

                result, notes = _ask(sc, auto, reason)

            except Exception as exc:
                import traceback
                print(f"  ERROR: {exc}")
                traceback.print_exc()
                auto, result, notes = "ERROR", "ERROR", str(exc)

            results.append({**sc, "result": result, "notes": notes})

        ctx.close()

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*64}")
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}")
    print(f"{'='*64}")
    ok = fail = tbd = err = 0
    for r in results:
        match = r["expect"] in ("?",) or r["result"] == r["expect"]
        flag  = "   " if match else "!! "
        if   r["result"] == "ERROR": err  += 1
        elif r["result"] == "PASS":  ok   += 1
        elif r["result"] == "?":     tbd  += 1
        else:                        fail += 1
        print(f"  {flag}{r['n']:>2}. {r['id']:<10} exp={r['expect']:<4} "
              f"got={r['result']:<5}  {r['name']}")
    print(f"\n  PASS: {ok}  FAIL: {fail}  TBD: {tbd}  ERROR: {err}")

    out = run_dir / "test_results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["n", "id", "group", "excel_id", "name",
                                            "expect", "result", "notes", "desc"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"\n  Results    : {out}")
    print(f"  Artifacts  : {run_dir}\n")

    record_results([(r["excel_id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_yearly_ui.py")


if __name__ == "__main__":
    main()
