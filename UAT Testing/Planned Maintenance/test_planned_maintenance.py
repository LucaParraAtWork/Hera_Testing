#!/usr/bin/env python3
"""
test_planned_maintenance.py – Hera Planned Maintenance Test Suite
=================================================================
Covers all UAT test cases for the Planned Maintenance screen:

  Creation
    MAINT_CR_01  Create short maintenance (future, ~2 h window)                → PASS
    MAINT_CR_02  Create long  maintenance (future, 3-day window)               → PASS
    MAINT_CR_03  Attempt to create maintenance with dates in the past          → FAIL

  Editing (click orange block)
    MAINT_ED_01  Change asset on existing block, re-open to verify persistence → PASS
    MAINT_ED_02  Change end date to further-future, verify accepted            → PASS
    MAINT_ED_03  Change dates to the past on existing block                    → FAIL

  Deletion (click orange block)
    MAINT_DEL_01 Click trash icon in Update Maintenance dialog → block removed → PASS

  Profile-curve impact  (Plant Nominal Capacity curve above the Gantt)
    MAINT_PR_01  Electrolyser Stack (Stack 1/2) in maintenance → curve = 0 kW  → PASS
    MAINT_PR_02  Only High Pressure Compressor (HPC)     → curve = 12 500 kW  → PASS
    MAINT_PR_03  Only Low  Pressure Compressor (LPC)     → curve = 12 500 kW  → PASS
    MAINT_PR_04  No maintenance in checked window        → curve = 25 000 kW  → PASS

Run:
    python "Planned Maintenance/test_planned_maintenance.py"

A browser opens; log in with MFA if prompted, then press Enter.
The script cycles through every scenario, auto-detects the result, and asks you
to confirm.  A summary table is printed at the end and saved to test_results.csv.

Notes
-----
- Test data (maintenance records) are left in the system.  Each run uses weeks
  that are 2 weeks (creation/edit) and 4 weeks (profile tests) ahead of today,
  so repeating the test within the same calendar week will operate on the same
  future weeks.
- The profile tests create one maintenance per day (Mon–Fri of week_b) so each
  day acts as an isolated sub-scenario.
- UTC offset for Brussels summer time (CEST) is hard-coded as +2 h.  Adjust
  UTC_OFFSET_H if running the test in winter (CET = +1 h).
"""

# =============================================================================
# ENVIRONMENT
# =============================================================================
ENV = ""          # "dev" | "test" | "" → prompt at start-up

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

# =============================================================================
# RESUME — set to N to skip scenarios 1 … N-1
# =============================================================================
START_FROM = 7

# =============================================================================
# TIMING / DISPLAY
# =============================================================================
WAIT_S  = 0.3
WAIT_M  = 0.7
WAIT_L  = 1.5
SLOWMO  = 0           # Playwright slow-motion ms (0 = full speed; 200 = visible)

# Brussels CEST offset (summer = +2, winter CET = +1)
UTC_OFFSET_H = 2

# =============================================================================
# TEST CASES
# expect: "PASS" = app should accept the action
#         "FAIL" = app should reject / show a validation error
# =============================================================================
TEST_CASES = [
    # ── Creation ──────────────────────────────────────────────────────────────
    {"id": "MAINT_CR_01", "desc": "Create short maintenance (~2 h) in the future",         "expect": "PASS"},
    {"id": "MAINT_CR_02", "desc": "Create long  maintenance (3 days) in the future",       "expect": "PASS"},
    {"id": "MAINT_CR_03", "desc": "Attempt to create maintenance in the past",             "expect": "FAIL"},
    # ── Edit (orange block) ───────────────────────────────────────────────────
    {"id": "MAINT_ED_01", "desc": "Click orange block – change asset, verify persistence", "expect": "PASS"},
    {"id": "MAINT_ED_02", "desc": "Click orange block – extend end date (further future)", "expect": "PASS"},
    {"id": "MAINT_ED_03", "desc": "Click orange block – change dates to the past",         "expect": "FAIL"},
    # ── Delete ───────────────────────────────────────────────────────────────
    {"id": "MAINT_DEL_01", "desc": "Click orange block – delete maintenance via trash icon", "expect": "PASS"},
    # ── Profile-curve impact ──────────────────────────────────────────────────
    {"id": "MAINT_PR_01", "desc": "Electrolyser Stack in maintenance → 0 kW",              "expect": "PASS"},
    {"id": "MAINT_PR_02", "desc": "Only High Pressure Compressor → 12 500 kW",            "expect": "PASS"},
    {"id": "MAINT_PR_03", "desc": "Only Low  Pressure Compressor → 12 500 kW",            "expect": "PASS"},
    {"id": "MAINT_PR_04", "desc": "No maintenance in window → 25 000 kW",                 "expect": "PASS"},
]

# =============================================================================
# SHARED RUN STATE  (populated by scenarios, read by later scenarios)
# =============================================================================
_STATE: dict = {}

# =============================================================================
# IMPORTS
# =============================================================================
import argparse, csv as _csv, json, random, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results

HERE = Path(__file__).parent


# =============================================================================
# TEST-WEEK DATES  (computed once at import time)
# =============================================================================
def _monday_of_week(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)

_today_monday  = _monday_of_week(datetime.now())
_week_a_monday = _today_monday  + timedelta(weeks=2)   # creation/edit tests
_week_b_monday = _today_monday  + timedelta(weeks=4)   # profile tests
_past_monday   = _today_monday  - timedelta(weeks=2)   # "past" negative tests

def _iso_week(dt: datetime) -> int:
    return dt.isocalendar()[1]

def _fmt(dt: datetime) -> str:
    """Format datetime for <input type='datetime-local'>."""
    return dt.strftime("%Y-%m-%dT%H:%M")

def _local_to_utc_key(local_str: str) -> str:
    """Convert local datetime string (Brussels summer) to UTC key as returned by the API."""
    local = datetime.fromisoformat(local_str)
    utc   = local - timedelta(hours=UTC_OFFSET_H)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# TIMING HELPERS
# =============================================================================
def _ws(): time.sleep(WAIT_S)
def _wm(): time.sleep(WAIT_M)
def _wl(): time.sleep(WAIT_L)

def _quiet(page, timeout_ms: int = 8000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


# =============================================================================
# NAVIGATION
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


def _current_week_on_page(page) -> int | None:
    """Read the ISO week number shown in the date-picker."""
    try:
        txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000)
        m = re.search(r"Week\s+(\d{1,2})", txt, re.I)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _goto_week(page, target_week: int, max_clicks: int = 60):
    """Navigate the date-picker to the requested ISO week."""
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception:
        pass
    _wm()
    for _ in range(max_clicks):
        wk = _current_week_on_page(page)
        if wk == target_week:
            _quiet(page)
            return
        if wk is None:
            _wm(); continue
        diff = target_week - wk
        go_next = (diff > 0 and diff <= 26) or (diff < -26)
        if go_next:
            page.locator("div.date-picker-next > button").first.click()
        else:
            page.locator("div.date-picker-previous > button").first.click()
        _ws()
    raise RuntimeError(f"Could not reach week {target_week}")


def _goto_maintenance(page, base_url: str, target_week: int | None, shots_dir: Path):
    """Navigate to Planned Maintenance and optionally jump to a specific week."""
    url = f"{base_url}/schedules/maintenance"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass
    _wm(); _quiet(page)
    if target_week is not None:
        _goto_week(page, target_week)
    _wm()
    page.screenshot(path=str(shots_dir / "01_landing.png"))


# =============================================================================
# UI HELPERS
# =============================================================================
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


_MODAL_SELECTORS = [
    # HTML5 native dialog
    "dialog[open]",
    # ARIA roles
    "[role='dialog']", "[role='alertdialog']",
    # Angular Material / CDK
    ".mat-dialog-container", ".mat-mdc-dialog-container",
    ".cdk-overlay-pane", ".cdk-overlay-container",
    ".mat-bottom-sheet-container",
    # Bootstrap / generic
    ".modal.show", ".modal-content", ".modal-dialog",
    # Angular components (Hera-specific guesses)
    "app-confirmation-dialog",
    "app-maintenance-modal", "app-edit-maintenance", "app-add-maintenance",
    "app-maintenance-form", "app-maintenance-detail", "app-maintenance-edit",
    "app-maintenance-dialog", "app-maintenance-panel",
    # Generic overlay / popover / drawer
    "[class*='overlay']:not(script)",
    "[class*='popover']:not(script)",
    "[class*='drawer']:not(script)",
    "[class*='panel']:not(script)",
    "[class*='sidebar']:not(script)",
    "[class*='dialog']:not(script)",
    "[class*='modal']:not(script)",
    # Inline form that appears on click (not a traditional dialog)
    "app-maintenance form",
    "app-maintenance input[type='datetime-local']",
]

def _any_modal_visible(page, timeout_ms: int = 800) -> bool:
    """Return True if any modal/dialog/overlay is visible after a block click."""
    for sel in _MODAL_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=timeout_ms):
                return True
        except Exception:
            pass
    return False


def _open_dialog(page):
    _wm()
    for sel in _MODAL_SELECTORS:
        try:
            modal = page.locator(sel).last
            if modal.is_visible(timeout=800):
                modal.wait_for(state="visible", timeout=12_000)
                _ws()
                return modal
        except Exception:
            pass
    # Last resort – return whatever is last in the DOM for these selectors
    for sel in _MODAL_SELECTORS:
        try:
            modal = page.locator(sel).last
            if modal.count() > 0:
                _ws()
                return modal
        except Exception:
            pass
    raise RuntimeError("No modal/dialog found after click")


def _close_dialog(page, modal):
    for pattern in [r"cancel|close|×|annuler|fermer", r"."]:
        try:
            btn = modal.locator("button", has_text=re.compile(pattern, re.I)).first
            btn.click(timeout=2000); _wm(); return
        except Exception:
            pass
    try:
        page.keyboard.press("Escape"); _wm()
    except Exception:
        pass


_ERROR_KW = [
    "error", "invalid", "required", "must", "cannot", "not allowed",
    "in the past", "past", "400", "500", "warning", "validation",
]


def _detect_action_result(page, modal) -> tuple[str, str]:
    """
    Determine if the last modal action was accepted (PASS) or rejected (FAIL).
    PASS = modal closed without errors / success toast appeared.
    FAIL = modal still open with a validation error, or error toast.
    """
    _wm()
    # Check toasts / alerts
    for sel in [".toast", ".alert", ".snackbar", "[class*='toast']",
                "[class*='error']", "[class*='success']", "[class*='notification']",
                ".mat-snack-bar-container"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                txt = el.inner_text(timeout=600).lower().strip()
                if any(k in txt for k in _ERROR_KW):
                    return "FAIL", f"Toast/alert: \"{txt[:120]}\""
                if any(k in txt for k in ["success", "saved", "created", "updated", "added"]):
                    return "PASS", f"Toast success: \"{txt[:80]}\""
        except Exception:
            pass

    # Check modal still open
    try:
        if modal.is_visible(timeout=600):
            txt = modal.inner_text(timeout=1500).lower()
            for kw in _ERROR_KW:
                if kw in txt:
                    return "FAIL", f"Modal still open – '{kw}' in text"
            # Modal open but no error text: check if Create/Save button disabled
            for btn_name in ("Create", "Save", "Update"):
                try:
                    btn = modal.get_by_role("button", name=re.compile(f"^{btn_name}$", re.I)).first
                    if btn.count() > 0 and btn.get_attribute("disabled") is not None:
                        return "FAIL", f"'{btn_name}' button is disabled (validation)"
                except Exception:
                    pass
            return "FAIL", "Modal still open after action (validation may have blocked it)"
    except Exception:
        pass

    return "PASS", "Action completed – modal closed without errors"


# =============================================================================
# MAINTENANCE-SPECIFIC HELPERS
# =============================================================================
def _get_asset_options(modal) -> list[str]:
    """Return the list of asset names available in the first <select> in the modal."""
    try:
        select = modal.locator("select").first
        opts = select.locator("option").all()
        names = [o.inner_text().strip() for o in opts]
        return [n for n in names if n and n not in ("- Select -", "Select", "")]
    except Exception:
        return []


def _fill_maintenance_modal(page, modal, asset_name: str,
                            start: datetime, end: datetime):
    """
    Fill an Add/Edit Maintenance modal:
      - select asset
      - fill start datetime-local input
      - fill end   datetime-local input
    """
    # Asset select
    try:
        select = modal.locator("select").first
        try:
            select.select_option(label=asset_name, timeout=3000)
        except Exception:
            # Fallback: fuzzy match on option text
            opts = select.locator("option").all()
            match = next((o for o in opts
                          if asset_name.lower() in o.inner_text().lower()), None)
            if match:
                select.select_option(value=match.get_attribute("value"), timeout=3000)
    except Exception:
        # Custom dropdown fallback
        try:
            modal.locator("text=- Select -").first.click(); _ws()
            modal.locator(f"text={asset_name}").last.click()
        except Exception:
            pass
    _ws()

    # Datetime inputs
    start_str = _fmt(start)
    end_str   = _fmt(end)
    for i, val in enumerate([start_str, end_str]):
        try:
            inp = modal.locator("input[type='datetime-local']").nth(i)
            inp.fill(val, timeout=3000)
        except Exception:
            try:
                inp = modal.locator("input").nth(i)
                inp.fill(val, timeout=3000)
            except Exception:
                pass
        _ws()


def _click_create_or_save(page, modal):
    """Click the primary action button in a maintenance modal."""
    for name in ("Create", "Save", "Update"):
        try:
            btn = modal.get_by_role("button", name=re.compile(f"^{name}$", re.I)).first
            if btn.count() > 0 and btn.is_visible(timeout=400):
                _safe_click(page, btn, name)
                try:
                    modal.wait_for(state="detached", timeout=10_000)
                except Exception:
                    pass
                return
        except Exception:
            pass
    # Last resort: any primary/submit button
    try:
        _safe_click(page, modal.locator("button[type='submit']").first, "submit button")
    except Exception:
        pass


def _has_maintenance_for_asset(page, asset_name: str) -> bool:
    """
    Check whether the API response captured for the current maintenance screen
    shows a plannedMaintenance entry for the given asset.
    Uses JavaScript to look at recently captured response data or simply checks
    if a visible orange block exists labelled with the asset.
    Relies on the DOM as a fallback.
    """
    try:
        # Look for asset label near a maintenance block in the DOM
        # Gantt rows often have the asset name + children blocks
        count = page.locator(
            f"[class*='gantt'] *:has-text('{asset_name}')"
        ).count()
        return count > 0
    except Exception:
        return False


# -------------------------------------------------------------------
# Coordinate calculation from HTML landmarks
# -------------------------------------------------------------------
def _compute_block_click_coords(page, asset_name: str,
                                block_start_dt: datetime) -> tuple[float | None, float | None]:
    """
    Calculate the (x, y) pixel coordinates of a maintenance block on the canvas.

    The Gantt chart is a single <canvas> element — individual blocks cannot be
    found via DOM queries.  However the ROW LABELS on the left and the DATE
    COLUMN HEADERS at the top are normal HTML elements.  We read their positions
    to triangulate where the block is painted on the canvas.

    Returns (x, y) or (None, None) if the landmarks cannot be found.
    """
    MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5,  "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

    # ── 1. Row Y from asset label ────────────────────────────────────────────
    try:
        row_y = page.evaluate(
            """
            (name) => {
                for (const el of document.querySelectorAll('*')) {
                    const rect = el.getBoundingClientRect();
                    // Row labels sit in the left column (x < 250) and are leaf-ish
                    if (rect.x > 250 || rect.height > 60 || rect.width > 250) continue;
                    if ((el.textContent || '').trim() === name)
                        return rect.y + rect.height / 2;
                }
                return null;
            }
            """,
            asset_name,
        )
    except Exception:
        row_y = None

    if row_y is None:
        return None, None

    # ── 2. Date column positions from header labels ──────────────────────────
    try:
        raw_headers = page.evaluate("""
            () => {
                const out = [];
                for (const el of document.querySelectorAll('*')) {
                    const txt = (el.textContent || '').trim();
                    if (!/^\\d{1,2}\\s+[A-Za-z]{3}$/.test(txt)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5 || rect.x < 250) continue;
                    out.push({ txt, cx: rect.x + rect.width / 2 });
                }
                return out;
            }
        """)
    except Exception:
        raw_headers = []

    if len(raw_headers) < 2:
        return None, None

    raw_headers.sort(key=lambda h: h["cx"])

    # Parse "13 Jul" → (day, month, cx)
    parsed = []
    for h in raw_headers:
        m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})", h["txt"])
        if m:
            mon = MONTHS.get(m.group(2), 0)
            if mon:
                parsed.append({"day": int(m.group(1)), "mon": mon, "cx": h["cx"]})

    if len(parsed) < 2:
        return None, None

    px_per_day = parsed[1]["cx"] - parsed[0]["cx"]
    if px_per_day <= 0:
        return None, None

    # ── 3. Map block_start_dt → X ────────────────────────────────────────────
    # Find the header for the target day (or use the first header + offset)
    ref = next(
        (p for p in parsed
         if p["day"] == block_start_dt.day and p["mon"] == block_start_dt.month),
        None,
    )
    if ref is None:
        # Extrapolate from first header
        from datetime import date as _date
        ref0 = parsed[0]
        d0   = _date(block_start_dt.year, ref0["mon"], ref0["day"])
        dt   = _date(block_start_dt.year, block_start_dt.month, block_start_dt.day)
        day_offset  = (dt - d0).days
        ref_cx = ref0["cx"] + day_offset * px_per_day
    else:
        ref_cx = ref["cx"]

    # ref_cx is the CENTRE of the target day's column.
    # day starts at  ref_cx - px_per_day/2
    # click at the middle of the first hour of the block
    px_per_hour = px_per_day / 24.0
    day_start_x = ref_cx - px_per_day / 2.0
    block_x     = day_start_x + block_start_dt.hour * px_per_hour + px_per_hour / 2.0

    return block_x, row_y


# -------------------------------------------------------------------
# Maintenance block click  –  strategy cascade
# -------------------------------------------------------------------
def _click_maintenance_block_by_name(page, asset_name: str = "",
                                     base_url: str = "",
                                     block_start_dt: datetime | None = None) -> tuple[bool, str]:
    """
    Try every known technique to find a maintenance block and open its edit
    dialog, without ever navigating away from the maintenance screen.

    Strategy 0  – coordinate calculation (row-label Y × date-header X) PRIMARY
    Strategy A  – text / title / aria-label / data-* in chart area (x > 250)
    Strategy B  – CSS class selectors, guarded by bounding-box position check
    Strategy C  – computed orange/amber background colour (JS DOM walk)
    Strategy D  – inline-style orange/amber, guarded by position check
    Strategy E  – cursor:pointer elements deep in the Gantt (not buttons/headers)
    Strategy F  – row-label Y-scan (find asset row Y, then scan across)

    After every strategy that clicks something, the function checks the URL;
    if the page drifted off /schedules/maintenance it navigates back before
    continuing with the next strategy.
    """

    MAINT_PATH = "/schedules/maintenance"

    def _dialog_open():
        return _any_modal_visible(page, timeout_ms=600)

    def _guard_url():
        """Re-navigate to maintenance if a click drifted us off the page."""
        try:
            if MAINT_PATH not in page.url and base_url:
                print(f"  [warn] drifted to {page.url!r} – navigating back")
                page.goto(f"{base_url}{MAINT_PATH}",
                          wait_until="domcontentloaded", timeout=30_000)
                _wm(); _quiet(page)
        except Exception:
            pass

    def _human_click(x: float, y: float):
        """
        Simulate a real human mouse click on the canvas:
          1. Scroll so the target row is roughly centred in the viewport
          2. Move the mouse to (x, y)  ← triggers mousemove / mouseover / hover state
          3. Brief pause (like human hand settling)
          4. mousedown  →  short hold  →  mouseup   (triggers click event)
          5. Wait up to 2 s for Angular change detection + any dialog animation
        Also dispatches pointer-events and a full click sequence directly on the
        canvas element, which some Angular chart libraries require.
        """
        try:
            # 1. Scroll target into view (centre it vertically)
            page.evaluate(
                "([px, py]) => window.scrollTo({top: Math.max(0, py - window.innerHeight/2), "
                "behavior: 'instant'})",
                [x, y],
            )
            time.sleep(0.1)

            # 2. Move mouse → establishes hover / mousemove state on the canvas
            page.mouse.move(x, y)
            time.sleep(0.15)

            # 3. Full press-and-release sequence
            page.mouse.down()
            time.sleep(0.06)
            page.mouse.up()
            time.sleep(1.8)   # Angular needs time to open dialog after canvas click

            # 4. Also dispatch pointer + click events directly on the canvas element,
            #    in case the CDP events didn't bubble correctly through the overlay.
            page.evaluate(
                """([cx, cy]) => {
                    const canvas = document.querySelector(
                        'app-maintenance-graphs canvas, canvas[baseChart], canvas');
                    if (!canvas) return;
                    const seq = [
                        ['pointermove',  {buttons:0, isPrimary:true}],
                        ['mousemove',    {}],
                        ['pointerdown',  {buttons:1, isPrimary:true}],
                        ['mousedown',    {buttons:1}],
                        ['pointerup',    {buttons:0, isPrimary:true}],
                        ['mouseup',      {buttons:0}],
                        ['click',        {buttons:0}],
                    ];
                    for (const [type, extra] of seq) {
                        const cls = type.startsWith('pointer') ? PointerEvent : MouseEvent;
                        canvas.dispatchEvent(new cls(type, {
                            bubbles:true, cancelable:true, view:window,
                            clientX:cx, clientY:cy, screenX:cx, screenY:cy,
                            ...extra
                        }));
                    }
                }""",
                [x, y],
            )
            time.sleep(0.8)
        except Exception:
            pass

    def _try_click(x, y, label):
        _human_click(x, y)
        if _dialog_open():
            return True, label
        _guard_url()
        return False, label

    # ── 0: coordinate calculation (primary – works even on pure canvas) ────────
    if asset_name and block_start_dt:
        try:
            bx, by = _compute_block_click_coords(page, asset_name, block_start_dt)
            if bx is not None and by is not None:
                # Try the exact point, then ±5/10 px on Y to handle label/canvas offset
                for y_off in (0, -5, 5, -10, 10):
                    ok, msg = _try_click(bx, by + y_off,
                                         f"0: coords ({bx:.0f},{by + y_off:.0f}) '{asset_name}'")
                    if ok:
                        return True, msg
        except Exception:
            pass

    # ── A: name in text / title / aria-label / any data-* attribute ──────────
    if asset_name:
        try:
            coords = page.evaluate(
                """
                (name) => {
                    for (const el of document.querySelectorAll('*')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 4 || rect.height < 4) continue;
                        if (rect.x < 250) continue;
                        if (rect.width > window.innerWidth * 0.8) continue;
                        const txt   = (el.textContent || '').trim();
                        const title = el.getAttribute('title') || '';
                        const aria  = el.getAttribute('aria-label') || '';
                        if (txt.includes(name) || title.includes(name) || aria.includes(name))
                            return { x: rect.x + rect.width/2, y: rect.y + rect.height/2 };
                        for (const a of el.attributes)
                            if (a.value.includes(name))
                                return { x: rect.x + rect.width/2, y: rect.y + rect.height/2 };
                    }
                    return null;
                }
                """,
                asset_name,
            )
            if coords:
                ok, msg = _try_click(coords["x"], coords["y"], f"A: by name '{asset_name}'")
                if ok:
                    return True, msg
        except Exception:
            pass

    # ── B: CSS class selectors (position-guarded) ─────────────────────────────
    css_candidates = [
        ".maintenance-event", ".maintenance-block", ".maintenance-item",
        ".planned-maintenance", ".gantt-item", ".gantt-event",
        "[class*='maintenance'][class*='event']",
        "[class*='maintenance'][class*='block']",
        "[class*='maintenance'][class*='item']",
        "app-planned-maintenance-item", "app-maintenance-block",
        "app-maintenance-event", "[class*='gantt'][class*='item']",
        "[class*='gantt'][class*='bar']", "[class*='event-bar']",
        "[class*='task-bar']",           "[class*='chart-bar']",
    ]
    for sel in css_candidates:
        try:
            el = page.locator(sel).first
            if el.count() == 0 or not el.is_visible(timeout=400):
                continue
            bbox = el.bounding_box()
            if not bbox or bbox["x"] < 250:   # skip anything in sidebar / nav column
                continue
            el.click(timeout=2000); _ws()
            if _dialog_open():
                return True, f"B: CSS '{sel}'"
        except Exception:
            pass
        _guard_url()

    # ── C: computed orange/amber background colour (chart area only) ──────────
    try:
        coords = page.evaluate("""
            () => {
                const walk = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_ELEMENT);
                while (walk.nextNode()) {
                    const el   = walk.currentNode;
                    const rect = el.getBoundingClientRect();
                    if (rect.x < 250) continue;
                    if (rect.width < 6 || rect.height < 6 || rect.width > 600) continue;
                    const bg = window.getComputedStyle(el).backgroundColor || '';
                    const m  = bg.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                    if (!m) continue;
                    const [, r, g, b] = m.map(Number);
                    if (r > 200 && g > 75 && g < 210 && b < 80)
                        return { x: rect.x + rect.width/2, y: rect.y + rect.height/2 };
                }
                return null;
            }
        """)
        if coords:
            ok, msg = _try_click(coords["x"], coords["y"],
                                 f"C: orange-bg at ({coords['x']:.0f},{coords['y']:.0f})")
            if ok:
                return True, msg
    except Exception:
        pass

    # ── D: inline-style orange (position-guarded) ─────────────────────────────
    for sel in [
        "[style*='background: #f']",      "[style*='background-color: #f']",
        "[style*='background: rgb(255']",  "[style*='background-color: rgb(255']",
        "[style*='background: orange']",   "[style*='background-color: orange']",
        "[style*='background: #FF']",      "[style*='background-color: #FF']",
    ]:
        try:
            el = page.locator(sel).first
            if el.count() == 0 or not el.is_visible(timeout=400):
                continue
            bbox = el.bounding_box()
            if not bbox or bbox["x"] < 250:
                continue
            el.click(timeout=2000); _ws()
            if _dialog_open():
                return True, f"D: inline-style '{sel}'"
        except Exception:
            pass
        _guard_url()

    # ── E: cursor:pointer deep in the Gantt (exclude header buttons) ──────────
    # Only considers elements that are:
    #   - in the chart area  (x > 250)
    #   - small enough to be a block, not a toolbar button  (h < 60, w < 400)
    #   - below the top navigation bar  (y > 150)
    try:
        coords = page.evaluate("""
            () => {
                for (const el of document.querySelectorAll('*')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.x < 250)  continue;
                    if (rect.y < 150)  continue;   // skip tabs / toolbar area
                    if (rect.width  < 8  || rect.height < 8)  continue;
                    if (rect.width  > 400 || rect.height > 60) continue;  // skip big buttons
                    if (window.getComputedStyle(el).cursor === 'pointer')
                        return { x: rect.x + rect.width/2, y: rect.y + rect.height/2 };
                }
                return null;
            }
        """)
        if coords:
            ok, msg = _try_click(coords["x"], coords["y"],
                                 f"E: cursor:pointer at ({coords['x']:.0f},{coords['y']:.0f})")
            if ok:
                return True, msg
    except Exception:
        pass

    # ── F: row-label Y-scan ───────────────────────────────────────────────────
    if asset_name:
        try:
            row_y = page.evaluate(
                """
                (name) => {
                    for (const el of document.querySelectorAll('*')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.x > 250 || rect.height > 60 || rect.width > 250) continue;
                        const txt = (el.textContent || '').trim();
                        if (txt === name || txt.startsWith(name))
                            return rect.y + rect.height / 2;
                    }
                    return null;
                }
                """,
                asset_name,
            )
            if row_y:
                vw = (page.viewport_size or {}).get("width", 1500)
                # Step of 5 px – a 2-hour block on a 7-day chart is ~15 px wide
                # Try three Y offsets to compensate for label vs canvas row misalignment
                for y_off in (0, -5, 5, -10, 10):
                    for x in range(300, vw - 30, 5):
                        page.mouse.click(x, row_y + y_off); _ws()
                        if _dialog_open():
                            return True, (f"F: row-Y scan for '{asset_name}' "
                                          f"at x={x} y_off={y_off}")
                        _guard_url()
                        if _dialog_open():   # guard may have re-nav'd; stop if open
                            break
        except Exception:
            pass

    # ── G: vertical column scan at the 5th weekday (Friday) ──────────────────
    # The 3-day block (Stack 2, Wed→Fri) is the widest block and always occupies
    # the Friday column.  Scan every 5 px from top to bottom at that X to sweep
    # through every Gantt row – including Stack 2 near the bottom.
    try:
        # Reuse the date-header parsing from _compute_block_click_coords
        MONTHS_G = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                    "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        raw = page.evaluate("""
            () => {
                const out = [];
                for (const el of document.querySelectorAll('*')) {
                    const txt = (el.textContent || '').trim();
                    if (!/^\\d{1,2}\\s+[A-Za-z]{3}$/.test(txt)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5 || rect.x < 250) continue;
                    out.push({ txt, cx: rect.x + rect.width / 2 });
                }
                return out;
            }
        """)
        raw.sort(key=lambda h: h["cx"])
        parsed_g = []
        for h in raw:
            m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})", h["txt"])
            if m:
                mon = MONTHS_G.get(m.group(2), 0)
                if mon:
                    parsed_g.append({"day": int(m.group(1)), "mon": mon, "cx": h["cx"]})

        if len(parsed_g) >= 2:
            px_per_day = parsed_g[1]["cx"] - parsed_g[0]["cx"]
            # Day-5 of the week (Friday, 0-indexed=4): centre = first_header_cx
            # + 3 days offset (if first header is already Mon+1 = Tue).
            # Safer: take the header with the largest cx that is still < vw-50
            # and back-calculate from there.  Use parsed_g[0] as anchor (Mon).
            # Monday cx ≈ parsed_g[0].cx  (first visible day label)
            # Friday cx  = monday_cx + 4 * px_per_day
            friday_cx = parsed_g[0]["cx"] + 4 * px_per_day
            vh = (page.viewport_size or {}).get("height", 900)
            # Scroll to bottom first so Stack 2 is fully in view
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            _ws()
            # Scan top-to-bottom using full human-click sequence (step 15 – slower but thorough)
            for y in range(150, vh - 10, 15):
                _human_click(friday_cx, y)
                if _dialog_open():
                    return True, f"G: vertical scan Friday col x={friday_cx:.0f} y={y}"
                _guard_url()
    except Exception:
        pass

    # ── H: Double-click vertical scan at Friday column ────────────────────────
    try:
        if len(parsed_g) >= 2:
            for y in range(150, vh - 10, 20):
                page.mouse.move(friday_cx, y); time.sleep(0.1)
                page.mouse.dblclick(friday_cx, y); time.sleep(1.5)
                if _dialog_open():
                    return True, f"H: double-click Friday col x={friday_cx:.0f} y={y}"
                _guard_url()
    except Exception:
        pass

    # ── I: Angular component method invocation ────────────────────────────────
    # Access the Angular runtime to call the component's edit method directly,
    # bypassing the canvas click entirely.
    try:
        info = page.evaluate("""
            (assetName) => {
                if (!window.ng) return {error: 'ng not available (not dev mode?)'};
                const roots = [
                    'app-maintenance-graphs',
                    'app-maintenance',
                    'app-schedules',
                ];
                for (const sel of roots) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    try {
                        const comp = ng.getComponent(el);
                        if (!comp) continue;
                        // Collect all method names from the prototype chain
                        const methods = new Set();
                        let proto = Object.getPrototypeOf(comp);
                        while (proto && proto !== Object.prototype) {
                            Object.getOwnPropertyNames(proto).forEach(n => {
                                if (typeof comp[n] === 'function' && n !== 'constructor')
                                    methods.add(n);
                            });
                            proto = Object.getPrototypeOf(proto);
                        }
                        // Try common edit-method names
                        const editNames = [
                            'editItem', 'editMaintenance', 'openEdit', 'onEdit',
                            'openEditDialog', 'editRecord', 'selectBlock',
                            'onBlockClick', 'onBlockSelect', 'openModal',
                            'handleClick', 'handleEdit', 'onItemClick',
                        ];
                        for (const m of editNames) {
                            if (methods.has(m)) {
                                try { comp[m](); } catch(e) {}
                                return {called: m, sel};
                            }
                        }
                        return {sel, available: [...methods].slice(0, 30)};
                    } catch(e) {
                        return {sel, error: String(e)};
                    }
                }
                return {error: 'no component found'};
            }
        """, asset_name)
        if info:
            print(f"  [I] Angular component probe: {info}")
        if info and info.get("called"):
            time.sleep(1.5)
            if _dialog_open():
                return True, f"I: Angular method '{info['called']}' on '{info.get('sel')}'"
    except Exception as e:
        print(f"  [I] Angular probe failed: {e}")

    # ── J: Screenshot diagnostic ──────────────────────────────────────────────
    try:
        debug_path = HERE / "test_artifacts" / "maint_click_debug.png"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(debug_path), full_page=False)
        print(f"  [J] Debug screenshot saved: {debug_path}")
    except Exception:
        pass

    return False, "All strategies exhausted – no maintenance block found"


# -------------------------------------------------------------------
# Profile-data capture via API interception
# -------------------------------------------------------------------
def _capture_maintenance_screen_profile(page, base_url: str, target_week: int,
                                        shots_dir: Path) -> dict:
    """
    Navigate to the planned-maintenance screen for target_week and capture the
    plantCapacityProfile points from the GetMaintenanceScreenAsync API response.
    Returns a dict  { "YYYY-MM-DDTHH:MM:SSZ": value, … } or {} on failure.
    The raw API response is saved to shots_dir/maintenance_data.json for inspection.
    """
    captured: dict = {}
    raw_body: list = []   # holds the last raw response body

    def _on_resp(response):
        if ("GetMaintenanceScreenAsync" in response.url
                and response.status == 200):
            try:
                data = response.json()
                raw_body.clear(); raw_body.append(data)
                # The raw API response has plantCapacityProfile at the top level
                # (no "data" wrapper – that wrapper only exists in DataCapturer files)
                pts = (data["plantCapacityProfile"]
                           ["profileGraphLines"][0]["points"])
                captured.clear(); captured.update(pts)
                print(f"  [CAPTURE] GetMaintenanceScreenAsync → {len(pts)} profile points")
            except Exception as exc:
                print(f"  [CAPTURE] GetMaintenanceScreenAsync parse error: {exc}")

    page.on("response", _on_resp)
    url = f"{base_url}/schedules/maintenance"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass
    _wm(); _quiet(page)
    _goto_week(page, target_week)
    _wl(); _quiet(page)
    page.screenshot(path=str(shots_dir / "profile_capture.png"), full_page=True)
    page.remove_listener("response", _on_resp)

    if raw_body:
        (shots_dir / "maintenance_data.json").write_text(
            json.dumps(raw_body[0], indent=2), encoding="utf-8")

    return captured


def _check_profile_values(points: dict,
                          start_local: str, end_local: str,
                          expected: int) -> tuple[bool, str]:
    """
    Verify that all profile data-points inside [start_local, end_local] equal
    expected.  start/end_local are 'YYYY-MM-DDTHH:MM' in Brussels local time.
    """
    if not points:
        return False, "No profile points were captured from the API"

    start_utc = _local_to_utc_key(start_local)
    end_utc   = _local_to_utc_key(end_local)

    in_window = {k: v for k, v in points.items() if start_utc <= k <= end_utc}
    if not in_window:
        # Show a sample of available keys to help debugging
        sample_keys = sorted(points.keys())[:5]
        return False, (
            f"No points between {start_utc} and {end_utc}. "
            f"Sample keys: {sample_keys}"
        )

    wrong = {k: v for k, v in in_window.items() if v != expected}
    if wrong:
        # Summarise what was actually found (distinct values + count)
        from collections import Counter
        actual_counts = Counter(in_window.values())
        actual_summary = ", ".join(
            f"{v:,} kW × {n}" for v, n in sorted(actual_counts.items()))
        window_str = f"{start_utc} → {end_utc}"
        return False, (
            f"{len(wrong)}/{len(in_window)} points ≠ {expected:,} kW "
            f"in window [{window_str}]. "
            f"Actual values: {actual_summary}."
        )

    return True, f"All {len(in_window)} points in window [{start_utc} → {end_utc}] = {expected:,} kW ✓"


# =============================================================================
# SCENARIO IMPLEMENTATIONS
# =============================================================================

def _run_MAINT_CR_01(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Create a short (2 h) future maintenance window."""
    week_a = _iso_week(_week_a_monday)
    test_day   = _week_a_monday + timedelta(days=1)   # Tuesday of week A
    start_dt   = test_day.replace(hour=8,  minute=0)
    end_dt     = test_day.replace(hour=10, minute=0)

    _goto_maintenance(page, base_url, week_a, shots_dir)

    # Open Add Maintenance modal
    try:
        btn = page.get_by_role("button", name=re.compile("^Add Maintenance$", re.I))
        _safe_click(page, btn, "Add Maintenance")
    except Exception:
        _safe_click(page, page.locator("button", has_text=re.compile(r"add.*maintenance", re.I)).first,
                    "Add Maintenance fallback")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Read and store available asset list (used by later tests too)
    assets = _get_asset_options(modal)
    if assets:
        _STATE["available_assets"] = assets

    # Fixed asset for creation – keeps edit tests deterministic (no random block hunt)
    chosen = "Stack 1"
    _STATE["cr01_asset"]    = chosen
    _STATE["cr01_start"]    = _fmt(start_dt)
    _STATE["cr01_end"]      = _fmt(end_dt)
    _STATE["cr01_start_dt"] = start_dt          # datetime object for coord calc
    _STATE["cr01_week"]     = week_a
    _STATE["cr01_shot_dir"] = str(shots_dir)

    _fill_maintenance_modal(page, modal, chosen, start_dt, end_dt)
    page.screenshot(path=str(shots_dir / "03_filled.png"))

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_create.png"))

    auto, reason = _detect_action_result(page, modal)
    if auto == "PASS":
        reason = (f"Created {chosen} maintenance "
                  f"{_fmt(start_dt)} → {_fmt(end_dt)} (week {week_a})")
    return auto, reason


def _run_MAINT_CR_02(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Create a long (3-day) future maintenance window."""
    week_a = _iso_week(_week_a_monday)
    start_dt  = (_week_a_monday + timedelta(days=2)).replace(hour=0,  minute=0)
    end_dt    = (_week_a_monday + timedelta(days=4)).replace(hour=23, minute=59)

    _goto_maintenance(page, base_url, week_a, shots_dir)

    try:
        btn = page.get_by_role("button", name=re.compile("^Add Maintenance$", re.I))
        _safe_click(page, btn, "Add Maintenance")
    except Exception:
        _safe_click(page, page.locator("button", has_text=re.compile(r"add.*maintenance", re.I)).first,
                    "Add Maintenance fallback")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Fixed asset – deterministic, different from CR_01's Stack 1
    chosen = "Stack 2"
    _STATE["cr02_asset"]    = chosen
    _STATE["cr02_start"]    = _fmt(start_dt)
    _STATE["cr02_end"]      = _fmt(end_dt)
    _STATE["cr02_start_dt"] = start_dt
    _STATE["cr02_week"]     = week_a

    _fill_maintenance_modal(page, modal, chosen, start_dt, end_dt)
    page.screenshot(path=str(shots_dir / "03_filled.png"))

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_create.png"))

    auto, reason = _detect_action_result(page, modal)
    if auto == "PASS":
        reason = (f"Created 3-day {chosen} maintenance "
                  f"{_fmt(start_dt)} → {_fmt(end_dt)} (week {week_a})")
    return auto, reason


def _run_MAINT_CR_03(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Attempt to create maintenance in the past – app should reject this."""
    # Use a week safely in the past
    past_day   = _past_monday + timedelta(days=2)   # Wednesday 2 weeks ago
    start_dt   = past_day.replace(hour=8,  minute=0)
    end_dt     = past_day.replace(hour=10, minute=0)

    # Navigate to the past week (UI may or may not allow it)
    _goto_maintenance(page, base_url, None, shots_dir)

    try:
        btn = page.get_by_role("button", name=re.compile("^Add Maintenance$", re.I))
        _safe_click(page, btn, "Add Maintenance")
    except Exception:
        _safe_click(page, page.locator("button", has_text=re.compile(r"add.*maintenance", re.I)).first,
                    "Add Maintenance fallback")
    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    assets = _STATE.get("available_assets") or _get_asset_options(modal)
    chosen = assets[0] if assets else "Stack 1"

    _fill_maintenance_modal(page, modal, chosen, start_dt, end_dt)
    page.screenshot(path=str(shots_dir / "03_past_dates_filled.png"))

    # Check if Create button is already disabled before clicking
    pre_disabled = False
    for btn_name in ("Create", "Save", "Update"):
        try:
            btn = modal.get_by_role("button", name=re.compile(f"^{btn_name}$", re.I)).first
            if btn.count() > 0 and btn.get_attribute("disabled") is not None:
                pre_disabled = True
                break
        except Exception:
            pass

    if pre_disabled:
        _close_dialog(page, modal)
        _quiet(page)
        page.screenshot(path=str(shots_dir / "04_button_disabled.png"))
        return "FAIL", "Create button disabled for past dates (correct)"

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_attempt.png"))

    auto, reason = _detect_action_result(page, modal)
    # For this test: FAIL (app rejected) is the expected correct behaviour
    if auto == "FAIL":
        return "FAIL", f"App correctly rejected past-date maintenance: {reason}"
    return "PASS", f"App accepted past-date maintenance (unexpected): {reason}"


# ── Edit scenarios ─────────────────────────────────────────────────────────────

def _wait_for_user_block_click(page, shots_dir: Path, hint: str = "") -> bool:
    """
    Pause and ask the user to click the orange maintenance block in the browser.
    Returns True once a modal/dialog is detected open, False if the user skips.
    """
    page.screenshot(path=str(shots_dir / "02_before_click.png"))
    print()
    print("  ┌──────────────────────────────────────────────────────────────┐")
    print("  │  ACTION REQUIRED  –  click the orange maintenance block      │")
    if hint:
        print(f"  │  {hint:<60}  │")
    print("  │  The edit dialog should open in the browser window.          │")
    print("  │  Press Enter here once the dialog is open.                   │")
    print("  │  Type 's' + Enter to skip this scenario.                     │")
    print("  └──────────────────────────────────────────────────────────────┘")
    try:
        raw = input("  > ").strip().lower()
    except EOFError:
        raw = ""
    if raw == "s":
        return False
    _quiet(page)
    return True


def _run_MAINT_ED_01(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Click orange block → change asset → re-open to verify persistence."""
    week_a = _STATE.get("cr02_week") or _STATE.get("cr01_week") or _iso_week(_week_a_monday)

    _goto_maintenance(page, base_url, week_a, shots_dir)

    opened = _wait_for_user_block_click(
        page, shots_dir,
        hint="Click the Stack 2 orange block (wide, Wed–Fri row at the bottom)")
    if not opened:
        return "?", "Skipped – user did not click the block"

    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_edit_modal_open.png"))

    # Current asset in modal
    try:
        current_asset = modal.locator("select").first.evaluate(
            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''"
        )
    except Exception:
        current_asset = ""

    # Choose a different asset
    all_assets = _get_asset_options(modal)
    hpc_lpc = {"High Pressure Compressor", "Low Pressure Compressor",
               "High Pressure Valve", "Low Pressure Valve",
               "Loading Bay 1", "Loading Bay 2", "Loading Bay 3", "Loading Bay 4"}
    candidates = [a for a in all_assets
                  if a != current_asset and a not in hpc_lpc] or all_assets
    new_asset = random.choice(candidates) if candidates else current_asset
    _STATE["ed01_original_asset"] = current_asset
    _STATE["ed01_new_asset"]      = new_asset

    # Fill modal with new asset (keep existing dates)
    try:
        start_input = modal.locator("input[type='datetime-local']").nth(0).input_value()
        end_input   = modal.locator("input[type='datetime-local']").nth(1).input_value()
    except Exception:
        start_input = _STATE.get("cr01_start", _fmt(_week_a_monday + timedelta(days=1, hours=8)))
        end_input   = _STATE.get("cr01_end",   _fmt(_week_a_monday + timedelta(days=1, hours=10)))

    # Change only the asset
    try:
        select = modal.locator("select").first
        try:
            select.select_option(label=new_asset, timeout=3000)
        except Exception:
            opts = select.locator("option").all()
            match = next((o for o in opts
                          if new_asset.lower() in o.inner_text().lower()), None)
            if match:
                select.select_option(value=match.get_attribute("value"), timeout=3000)
    except Exception:
        pass
    _ws()
    page.screenshot(path=str(shots_dir / "03_asset_changed.png"))

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    auto, reason = _detect_action_result(page, modal)
    if auto != "PASS":
        return auto, reason

    # Persistence check: ask user to re-open the block to verify the new asset name persisted
    _wl()
    _goto_maintenance(page, base_url, week_a, shots_dir)
    print()
    print("  ┌──────────────────────────────────────────────────────────────┐")
    print("  │  PERSISTENCE CHECK  –  click the same block again           │")
    print(f"  │  (it may now be labelled: {new_asset:<36})│")
    print("  │  Press Enter once the dialog is open.                        │")
    print("  │  Type 's' + Enter to skip the verification.                  │")
    print("  └──────────────────────────────────────────────────────────────┘")
    try:
        raw2 = input("  > ").strip().lower()
    except EOFError:
        raw2 = ""
    if raw2 == "s":
        return "PASS", f"Asset changed from '{current_asset}' → '{new_asset}' (persistence check skipped)"
    _quiet(page)

    modal2 = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "05_reopen_verify.png"))
    try:
        persisted = modal2.locator("select").first.evaluate(
            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''"
        )
    except Exception:
        persisted = ""
    _close_dialog(page, modal2)
    _quiet(page)

    if new_asset.lower() in persisted.lower() or persisted.lower() in new_asset.lower():
        return "PASS", (f"Asset changed from '{current_asset}' → '{new_asset}' "
                        f"and persisted (verified: '{persisted}')")
    return "FAIL", (f"Asset change not persisted: expected '{new_asset}', "
                    f"found '{persisted}' after re-open")


def _run_MAINT_ED_02(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Click orange block → push end date further into the future."""
    week_a = _STATE.get("cr02_week") or _STATE.get("cr01_week") or _iso_week(_week_a_monday)
    # After ED_01 the block may have a new asset name; fall back to Stack 2
    target = _STATE.get("ed01_new_asset") or _STATE.get("cr02_asset", "Stack 2")

    _goto_maintenance(page, base_url, week_a, shots_dir)

    opened = _wait_for_user_block_click(
        page, shots_dir,
        hint=f"Click the orange block for {target} (Wed–Fri row)")
    if not opened:
        return "?", "Skipped – user did not click the block"

    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_edit_modal_open.png"))

    # Read current end date and extend by 1 day
    try:
        end_val = modal.locator("input[type='datetime-local']").nth(1).input_value()
        curr_end = datetime.fromisoformat(end_val) if end_val else None
    except Exception:
        curr_end = None

    new_end = (curr_end + timedelta(days=1)) if curr_end else (
        _week_a_monday + timedelta(days=3, hours=10)
    )
    _STATE["ed02_new_end"] = _fmt(new_end)

    try:
        inp = modal.locator("input[type='datetime-local']").nth(1)
        inp.fill(_fmt(new_end), timeout=3000)
    except Exception:
        try:
            inp = modal.locator("input").nth(1)
            inp.fill(_fmt(new_end), timeout=3000)
        except Exception:
            pass
    _ws()
    page.screenshot(path=str(shots_dir / "03_end_date_extended.png"))

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_save.png"))

    auto, reason = _detect_action_result(page, modal)
    if auto != "PASS":
        return auto, reason

    # Persistence check: re-open the same block and verify end date survived the save
    _wl()
    _goto_maintenance(page, base_url, week_a, shots_dir)
    print()
    print("  ┌──────────────────────────────────────────────────────────────┐")
    print("  │  PERSISTENCE CHECK  –  click the same block again           │")
    print(f"  │  Expected end date: {_fmt(new_end):<41}│")
    print("  │  Press Enter once the dialog is open.                        │")
    print("  │  Type 's' + Enter to skip the verification.                  │")
    print("  └──────────────────────────────────────────────────────────────┘")
    try:
        raw2 = input("  > ").strip().lower()
    except EOFError:
        raw2 = ""
    if raw2 == "s":
        return "PASS", f"End date extended to {_fmt(new_end)} (persistence check skipped)"
    _quiet(page)

    modal2 = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "05_reopen_verify.png"))
    try:
        persisted_end = modal2.locator("input[type='datetime-local']").nth(1).input_value()
    except Exception:
        persisted_end = ""
    _close_dialog(page, modal2)
    _quiet(page)

    expected_fmt = _fmt(new_end)
    if persisted_end and persisted_end.startswith(expected_fmt[:16]):
        return "PASS", (f"End date extended to {expected_fmt} "
                        f"and persisted (verified: '{persisted_end}')")
    return "FAIL", (f"End date not persisted: expected '{expected_fmt}', "
                    f"found '{persisted_end}' after re-open")


def _run_MAINT_ED_03(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Click orange block → set dates to the past → app should reject."""
    week_a = _STATE.get("cr02_week") or _STATE.get("cr01_week") or _iso_week(_week_a_monday)
    target = _STATE.get("ed01_new_asset") or _STATE.get("cr02_asset", "Stack 2")

    _goto_maintenance(page, base_url, week_a, shots_dir)

    opened = _wait_for_user_block_click(
        page, shots_dir,
        hint=f"Click the orange block for {target} (Wed–Fri row)")
    if not opened:
        return "?", "Skipped – user did not click the block"

    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_edit_modal_open.png"))

    past_start = _past_monday + timedelta(days=1, hours=8)
    past_end   = _past_monday + timedelta(days=1, hours=10)

    for i, dt in enumerate([past_start, past_end]):
        try:
            inp = modal.locator("input[type='datetime-local']").nth(i)
            inp.fill(_fmt(dt), timeout=3000)
        except Exception:
            try:
                inp = modal.locator("input").nth(i)
                inp.fill(_fmt(dt), timeout=3000)
            except Exception:
                pass
        _ws()

    page.screenshot(path=str(shots_dir / "03_past_dates_filled.png"))

    # Check if Save disabled before clicking
    pre_disabled = False
    for btn_name in ("Create", "Save", "Update"):
        try:
            btn = modal.get_by_role("button", name=re.compile(f"^{btn_name}$", re.I)).first
            if btn.count() > 0 and btn.get_attribute("disabled") is not None:
                pre_disabled = True; break
        except Exception:
            pass

    if pre_disabled:
        _close_dialog(page, modal)
        _quiet(page)
        page.screenshot(path=str(shots_dir / "04_button_disabled.png"))
        return "FAIL", "Save button disabled for past dates (correct)"

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / "04_after_attempt.png"))

    auto, reason = _detect_action_result(page, modal)
    if auto == "FAIL":
        return "FAIL", f"App correctly rejected past-date edit: {reason}"
    return "PASS", f"App accepted past-date edit (unexpected): {reason}"


# ── Profile curve scenarios ────────────────────────────────────────────────────

def _create_profile_maintenance(page, base_url: str, shots_dir: Path,
                                asset: str, day_offset: int,
                                hour_start: int = 8, hour_end: int = 16) -> tuple[str, str]:
    """
    Create a maintenance for *asset* on a specific day within week_b.
    day_offset 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri.
    Returns (start_local, end_local) strings for profile verification.
    """
    week_b   = _iso_week(_week_b_monday)
    test_day = _week_b_monday + timedelta(days=day_offset)
    start_dt = test_day.replace(hour=hour_start, minute=0)
    end_dt   = test_day.replace(hour=hour_end,   minute=0)

    _goto_maintenance(page, base_url, week_b, shots_dir)

    try:
        btn = page.get_by_role("button", name=re.compile("^Add Maintenance$", re.I))
        _safe_click(page, btn, "Add Maintenance")
    except Exception:
        _safe_click(page, page.locator("button", has_text=re.compile(r"add.*maintenance", re.I)).first,
                    "Add Maintenance fallback")
    modal = _open_dialog(page)
    shots_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shots_dir / f"create_modal_{asset.replace(' ','_')}.png"))

    _fill_maintenance_modal(page, modal, asset, start_dt, end_dt)
    page.screenshot(path=str(shots_dir / f"create_filled_{asset.replace(' ','_')}.png"))

    _click_create_or_save(page, modal)
    _quiet(page)
    page.screenshot(path=str(shots_dir / f"create_done_{asset.replace(' ','_')}.png"))

    return _fmt(start_dt), _fmt(end_dt)


def _run_MAINT_DEL_01(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """
    Open an existing orange block → click the trash icon → verify the block is gone.
    The user clicks the block manually; the script clicks the delete button and
    confirms deletion, then re-navigates to check the block no longer appears.
    """
    week_a = _STATE.get("cr02_week") or _STATE.get("cr01_week") or _iso_week(_week_a_monday)

    _goto_maintenance(page, base_url, week_a, shots_dir)
    page.screenshot(path=str(shots_dir / "01_before_delete.png"))

    opened = _wait_for_user_block_click(
        page, shots_dir,
        hint="Click any orange maintenance block you want to delete")
    if not opened:
        return "?", "Skipped – user did not click the block"

    modal = _open_dialog(page)
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Read the maintenance name so we can confirm it disappeared afterwards
    maint_name = ""
    try:
        maint_name = modal.locator("h2, h3, [class*='title'], [class*='name']").first.inner_text().strip()
    except Exception:
        pass

    # Click the trash / delete icon button (top-right of the modal)
    deleted = False
    for sel in [
        "button[aria-label*='delete' i]",
        "button[aria-label*='remove' i]",
        "button[title*='delete' i]",
        "button[title*='remove' i]",
        "button mat-icon:has-text('delete')",
        "button mat-icon:has-text('delete_forever')",
        "[class*='delete']",
        "[class*='trash']",
        "button:has(mat-icon)",       # last resort: first icon button in modal
    ]:
        try:
            btn = modal.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                deleted = True
                break
        except Exception:
            continue

    if not deleted:
        page.screenshot(path=str(shots_dir / "03_trash_not_found.png"))
        return "FAIL", (
            "Could not find the delete/trash button inside the modal. "
            "Check screenshot 03_trash_not_found.png for the actual dialog layout."
        )

    page.screenshot(path=str(shots_dir / "03_after_trash_click.png"))
    _quiet(page)

    # Handle a possible confirmation dialog ("Are you sure?")
    try:
        confirm_btn = page.locator(
            "button:has-text('Yes'), button:has-text('Confirm'), "
            "button:has-text('Delete'), button:has-text('OK')"
        ).first
        if confirm_btn.count() > 0 and confirm_btn.is_visible(timeout=2000):
            confirm_btn.click(timeout=3000)
            _quiet(page)
    except Exception:
        pass

    page.screenshot(path=str(shots_dir / "04_after_confirm.png"))
    _wl()

    # Verify: re-navigate to the same week — the block should be absent from the API
    captured_after: dict = {}
    raw_after: list = []

    def _on_resp_after(response):
        if "GetMaintenanceScreenAsync" in response.url and response.status == 200:
            try:
                data = response.json()
                raw_after.clear(); raw_after.append(data)
                for graph in data.get("assetGraphs", []):
                    for line in graph.get("assetGraphLines", []):
                        for m in line.get("plannedMaintenances", []):
                            name = m.get("maintenanceName", "")
                            captured_after[name] = True
            except Exception:
                pass

    page.on("response", _on_resp_after)
    _goto_maintenance(page, base_url, week_a, shots_dir)
    _wl(); _quiet(page)
    page.remove_listener("response", _on_resp_after)

    page.screenshot(path=str(shots_dir / "05_after_delete_verify.png"))

    if raw_after and maint_name and maint_name in captured_after:
        return "FAIL", (
            f"Maintenance '{maint_name}' still appears in GetMaintenanceScreenAsync "
            f"after deletion — the delete may not have been saved."
        )

    if not raw_after:
        # API didn't fire; fall back to visual check only
        return "PASS", (
            f"Delete button clicked and modal closed. "
            f"API re-check not available — verify screenshot 05_after_delete_verify.png manually."
        )

    return "PASS", (
        f"Maintenance '{maint_name or '(unnamed)'}' deleted. "
        f"Block no longer found in GetMaintenanceScreenAsync response ✓"
    )


def _run_MAINT_PR_01(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """
    Electrolyser Stack (Stack 1 or Stack 2) in maintenance → profile curve = 0 kW.
    Only Stacks drive the Plant Nominal Capacity to 0; all other asset types leave it unchanged.
    Uses Wednesday of week_b (day_offset=2).
    """
    # Only Electrolyser Stacks reduce nominal capacity to 0 kW
    stack_assets = [
        a for a in (_STATE.get("available_assets") or ["Stack 1", "Stack 2"])
        if "stack" in a.lower()
    ]
    if not stack_assets:
        stack_assets = ["Stack 1", "Stack 2"]

    chosen = random.choice(stack_assets)
    _STATE["pr01_asset"] = chosen
    print(f"  [MAINT_PR_01] Selected Electrolyser Stack asset: {chosen}")

    # Create maintenance on Wednesday of week_b (day_offset=2)
    start_local, end_local = _create_profile_maintenance(
        page, base_url, shots_dir, chosen, day_offset=2)

    # Capture profile from API
    week_b = _iso_week(_week_b_monday)
    points = _capture_maintenance_screen_profile(page, base_url, week_b, shots_dir)

    ok, detail = _check_profile_values(points, start_local, end_local, expected=0)
    if ok:
        return "PASS", (f"Electrolyser Stack '{chosen}' in maintenance: "
                        f"Plant Nominal Capacity = 0 kW ✓ — {detail}")
    # Failure: show the actual values found in the window to aid diagnosis
    return "FAIL", (
        f"Electrolyser Stack '{chosen}' in maintenance (window {start_local}–{end_local}): "
        f"expected Plant Nominal Capacity = 0 kW, but capacity did not drop. "
        f"Detail: {detail}"
    )


def _run_MAINT_PR_02(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Only HPC in maintenance → profile = 12 500 kW. Uses Thursday of week_b."""
    asset = "High Pressure Compressor"
    start_local, end_local = _create_profile_maintenance(
        page, base_url, shots_dir, asset, day_offset=3)

    week_b = _iso_week(_week_b_monday)
    points = _capture_maintenance_screen_profile(page, base_url, week_b, shots_dir)

    ok, detail = _check_profile_values(points, start_local, end_local, expected=12500)
    if ok:
        return "PASS", f"HPC-only maintenance: profile = 12 500 kW ✓ — {detail}"
    return "FAIL", detail


def _run_MAINT_PR_03(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """Only LPC in maintenance → profile = 12 500 kW. Uses Friday of week_b."""
    asset = "Low Pressure Compressor"
    start_local, end_local = _create_profile_maintenance(
        page, base_url, shots_dir, asset, day_offset=4)

    week_b = _iso_week(_week_b_monday)
    points = _capture_maintenance_screen_profile(page, base_url, week_b, shots_dir)

    ok, detail = _check_profile_values(points, start_local, end_local, expected=12500)
    if ok:
        return "PASS", f"LPC-only maintenance: profile = 12 500 kW ✓ — {detail}"
    return "FAIL", detail


def _run_MAINT_PR_04(page, base_url: str, shots_dir: Path) -> tuple[str, str]:
    """
    No maintenance on Monday of week_b → profile = 25 000 kW.
    (Maintenance was added on Wed/Thu/Fri; Monday is untouched.)
    """
    week_b     = _iso_week(_week_b_monday)
    # Monday 10:00–14:00 — should have no maintenance from our earlier tests
    start_local = _fmt(_week_b_monday.replace(hour=10, minute=0))
    end_local   = _fmt(_week_b_monday.replace(hour=14, minute=0))

    points = _capture_maintenance_screen_profile(page, base_url, week_b, shots_dir)

    ok, detail = _check_profile_values(points, start_local, end_local, expected=25000)
    if ok:
        return "PASS", f"Monday (no maintenance): profile = 25 000 kW ✓ — {detail}"
    return "FAIL", detail


# =============================================================================
# DISPATCHER
# =============================================================================
def _run_scenario(page, base_url: str, tc: dict, shots_dir: Path) -> tuple[str, str]:
    shots_dir.mkdir(parents=True, exist_ok=True)
    tc_id = tc["id"]

    if tc_id == "MAINT_CR_01": return _run_MAINT_CR_01(page, base_url, shots_dir)
    if tc_id == "MAINT_CR_02": return _run_MAINT_CR_02(page, base_url, shots_dir)
    if tc_id == "MAINT_CR_03": return _run_MAINT_CR_03(page, base_url, shots_dir)
    if tc_id == "MAINT_ED_01": return _run_MAINT_ED_01(page, base_url, shots_dir)
    if tc_id == "MAINT_ED_02": return _run_MAINT_ED_02(page, base_url, shots_dir)
    if tc_id == "MAINT_ED_03": return _run_MAINT_ED_03(page, base_url, shots_dir)
    if tc_id == "MAINT_DEL_01": return _run_MAINT_DEL_01(page, base_url, shots_dir)
    if tc_id == "MAINT_PR_01": return _run_MAINT_PR_01(page, base_url, shots_dir)
    if tc_id == "MAINT_PR_02": return _run_MAINT_PR_02(page, base_url, shots_dir)
    if tc_id == "MAINT_PR_03": return _run_MAINT_PR_03(page, base_url, shots_dir)
    if tc_id == "MAINT_PR_04": return _run_MAINT_PR_04(page, base_url, shots_dir)

    return "?", f"No implementation for {tc_id}"


# =============================================================================
# PROMPT
# =============================================================================
def _ask(tc_id: str, desc: str, expect: str,
         auto: str = "?", reason: str = "") -> tuple[str, str]:
    label      = {"PASS": "[expected PASS]", "FAIL": "[expected FAIL]",
                  "?": "[unknown]"}.get(expect, "")
    auto_label = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL",
                  "?": "AUTO-?"}.get(auto, auto)
    match_flag = ("" if expect == "?" else
                  (" OK" if auto == expect else " !! MISMATCH"))

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


# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Hera Planned Maintenance Test Suite")
    parser.add_argument(
        "--from", dest="start_from", type=int, default=START_FROM, metavar="N",
        help="Resume from scenario N (1-based).",
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

    print(f"\n{'='*62}")
    print(f"  Hera Planned Maintenance Test Suite")
    print(f"  Environment   : {env_name}  ({base_url})")
    print(f"  Run ID        : {run_id}")
    print(f"  Test cases    : {len(TEST_CASES)}")
    print(f"  Creation week : {_iso_week(_week_a_monday)} "
          f"(starts {_week_a_monday.strftime('%Y-%m-%d')})")
    print(f"  Profile week  : {_iso_week(_week_b_monday)} "
          f"(starts {_week_b_monday.strftime('%Y-%m-%d')})")
    if start_from > 1:
        print(f"  Resuming from scenario {start_from}")
    print(f"{'='*62}\n")
    print("  The browser will open.  Log in with MFA if prompted.")
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

        print("  Navigating to Planned Maintenance…")
        try:
            page.goto(f"{base_url}/schedules/maintenance",
                      wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌──────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA in the browser window if prompted.  │")
        print("  │  Take as long as you need.                           │")
        print("  │  Press Enter here once you see the Hera app.         │")
        print("  └──────────────────────────────────────────────────────┘")
        input("  > ")
        _quiet(page)
        print("  Authentication confirmed.  Starting test loop.\n")

        for idx, tc in enumerate(TEST_CASES, 1):
            if idx < start_from:
                continue

            shots_dir = run_dir / f"{idx:02d}_{tc['id']}"
            print(f"\n{'─'*62}")
            print(f"  [{idx}/{len(TEST_CASES)}]  {tc['id']}  –  {tc['desc']}")
            print(f"  Expect : {tc['expect']}")

            try:
                auto_result, auto_reason = _run_scenario(
                    page, base_url, tc, shots_dir)
                result, notes = _ask(
                    tc["id"], tc["desc"], tc["expect"],
                    auto=auto_result, reason=auto_reason)
            except Exception as exc:
                print(f"  ERROR: {exc}")
                result, notes = "ERROR", str(exc)
                auto_result   = "ERROR"

            if tc["expect"] not in ("?", result):
                print(f"  !! MISMATCH – expected {tc['expect']}, got {result}")

            results.append({**tc, "result": result, "notes": notes})

        ctx.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}")
    print(f"{'='*62}")
    print(f"  {'ID':<16} {'Expect':<7} {'Result':<7} {'Match':<6} Description")
    print("─" * 80)

    pass_n = fail_n = skip_n = mismatch_n = 0
    for r in results:
        match = (r["expect"] == "?" or r["result"] == r["expect"])
        flag  = "  " if match else "!!"
        if r["result"] == "SKIP":   skip_n     += 1
        elif r["result"] == "PASS": pass_n     += 1
        else:                       fail_n     += 1
        if not match:               mismatch_n += 1
        print(f"  {flag} {r['id']:<16} {r['expect']:<7} {r['result']:<7} "
              f"{str(match):<6} {r['desc']}")

    print("─" * 80)
    print(f"  Passed: {pass_n}  |  Failed: {fail_n}  |  Skipped: {skip_n}  "
          f"|  Mismatches: {mismatch_n}\n")

    results_csv = run_dir / "test_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(
            f, fieldnames=["id", "desc", "expect", "result", "notes"])
        w.writeheader()
        for r in results:
            w.writerow(r)

    print(f"  Results saved to : {results_csv}")
    print(f"  Screenshots in   : {run_dir}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_planned_maintenance.py")


if __name__ == "__main__":
    main()
