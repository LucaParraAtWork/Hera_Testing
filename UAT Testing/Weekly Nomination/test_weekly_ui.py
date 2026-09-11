#!/usr/bin/env python3
"""
test_weekly_ui.py – Hera Weekly Nomination UI Interaction Tests
===============================================================
Tests only the UI interactions on the nominations scheduling screen
for Week 36/2026 (Aug 31 - Sep 6).  Profiles and bay occupancy consistency
checks belong in a separate script.

Scenarios covered:
  SETUP_01  Import Week 36 baseline → slots become NEW
  UI_01     View grid, count NEW slots
  UI_02     Open slot modal – change offtaker → Save
  UI_03     Open slot modal – change trailer type (Type 1 → Type 2) → Save
  UI_04     Open slot modal – add extra trailer row → fill → Save
  UI_05     Open slot modal – delete a trailer row → Save
  AN_02     Negative: Drop-Off Type 1 then mismatched Pick-Up Type 2 (Nom_AN_02)
  AN_03     Negative: two consecutive Drop-Offs, no Pick-Up between (Nom_AN_03)
  AN_05     Delete every trailer row in a slot → Save → slot returns empty (Nom_AN_05)
  UI_06     Confirm first NEW slot (individual) (SCHED_CONF_01)
  UI_07     Reject the confirmed slot (individual) (SCHED_REJ_01)
  UI_08     Confirm ALL remaining NEW slots (one by one) (SCHED_CONF_02)
  UI_09     Reject ALL confirmed slots (one by one) (SCHED_REJ_02)
  SETUP_02  Re-import Week 36 (same CSV) → fresh NEW slots for bulk tests
  UI_10     Bulk Confirm Week (SCHED_CONF_03)
  UI_11     Bulk Reject Week (SCHED_REJ_03)
  UI_12     Add a Virya slot via UI (open empty slot, fill modal, save)

Note: UI_06/07/08/09/10/11 double as the automation for the Excel's
"Scheduler" process rows SCHED_CONF_01/02/03 and SCHED_REJ_01/02/03 — those
rows describe the exact same Confirm/Reject-button and Confirm-Week
mechanism on this same screen, just filed under a different Process label.
There is no separate "Scheduler" screen/script in this repo.

State machine:
  SETUP_01  → all slots NEW
  UI_01     → no state change (view)
  UI_02-05  → modal data edits on NEW slots (slots remain NEW)
  AN_02     → no lasting state change beyond the earlier slot's Drop-Off Type 1
              (the later slot's mismatched save is expected to be rejected)
  AN_03     → no state change (the second Drop-Off save is expected to be rejected)
  AN_05     → one NEW slot emptied (all trailer rows deleted, saved as empty)
  UI_06     → first slot CONFIRMED
  UI_07     → that slot REJECTED (rest still NEW)
  UI_08     → remaining NEW → CONFIRMED
  UI_09     → all CONFIRMED → REJECTED
  SETUP_02  → reimport of same CSV; slots become NEW
  UI_10     → all NEW → CONFIRMED
  UI_11     → all CONFIRMED → REJECTED
  UI_12     → one new slot added via UI

Run:
    python "Weekly Nomination/test_weekly_ui.py"
"""

# ==========================================================================
# ENVIRONMENT — "dev" | "test" | "" (ask at startup)
# ==========================================================================
ENV = ""

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

# ==========================================================================
# SCENARIO CONTROL — set to N to skip the first N-1 scenarios
# ==========================================================================
START_FROM = 1

# ==========================================================================
# TARGET
# Placeholder values only: main() overwrites both from _weekly_csv_sync.
# ensure_next_week() before any scenario runs, so this always targets the
# real "week after next week" (Week35 + 7 days) regardless of when it's run.
# ==========================================================================
TARGET_WEEK = 36
TARGET_YEAR = 2026

# ==========================================================================
# TIMING
# ==========================================================================
WAIT_S = 0.3
WAIT_M = 0.8
WAIT_L = 1.8
SLOWMO = 0   # ms between Playwright actions

# ==========================================================================
# SCENARIOS
# ==========================================================================
SCENARIOS = [
    # ── Setup ─────────────────────────────────────────────────────────────
    {"n":  1, "id": "SETUP_01", "group": "setup",
     "name": "Import Week 36 baseline (Virya)",
     "desc": "Import Virya_Nomination_Week36.csv → all Virya slots become NEW",
     "expect": "PASS"},

    # ── Grid view ─────────────────────────────────────────────────────────
    {"n":  2, "id": "UI_01", "group": "view",
     "name": "View nominations grid – count NEW slots",
     "desc": "Navigate to Week 36, count NEW slots, screenshot",
     "expect": "PASS"},

    # ── Modal data modifications ───────────────────────────────────────────
    {"n":  3, "id": "UI_02", "group": "modal_edit",
     "name": "Change offtaker in slot modal → Save",
     "desc": "Open slot[0] modal, switch offtaker dropdown to Messer Belgium NV, Save",
     "expect": "PASS"},

    {"n":  4, "id": "UI_03", "group": "modal_edit",
     "name": "Change trailer type in slot modal → Save",
     "desc": "Open slot[1] modal, change first Trailer Type from Type 1 → Type 2, Save",
     "expect": "PASS"},

    {"n":  5, "id": "UI_04", "group": "modal_edit",
     "name": "Add a trailer row in slot modal → Save",
     "desc": "Open slot[2] modal, click '+ Add Trailer', fill new row (Type 1 / Drop-Off), Save",
     "expect": "PASS"},

    {"n":  6, "id": "UI_05", "group": "modal_edit",
     "name": "Delete a trailer row in slot modal → Save",
     "desc": "Open a slot with ≥2 rows, click the trash button on the last row, Save",
     "expect": "PASS"},

    # ── Negative / validation (Additional Nomination gaps) ──────────────────
    {"n":  7, "id": "AN_02", "group": "negative",
     "name": "Inconsistent trailer types across two slots (Nom_AN_02)",
     "desc": "Save Drop-Off Type 1 on an earlier NEW slot, then try to save a "
             "mismatched Pick-Up Type 2 on a later NEW slot — expect rejection",
     "expect": "FAIL"},

    {"n":  8, "id": "AN_03", "group": "negative",
     "name": "Two consecutive Drop-Offs, no Pick-Up between (Nom_AN_03)",
     "desc": "Add a second trailer row and force it to Drop-Off too (no Pick-Up "
             "between) → attempt Save — expect rejection",
     "expect": "FAIL"},

    {"n":  9, "id": "AN_05", "group": "negative",
     "name": "Delete slot entirely (Nom_AN_05)",
     "desc": "Open a NEW slot, delete every trailer row, Save — slot should "
             "return to a truly empty grid cell",
     "expect": "PASS"},

    # ── Individual confirm / reject ────────────────────────────────────────
    {"n": 10, "id": "UI_06", "group": "individual",
     "name": "Confirm first NEW slot (individual) (SCHED_CONF_01)",
     "desc": "Click first NEW slot, open modal, click Confirm",
     "expect": "PASS"},

    {"n": 11, "id": "UI_07", "group": "individual",
     "name": "Reject the confirmed slot (individual) (SCHED_REJ_01)",
     "desc": "Click the CONFIRMED slot, open modal, click Reject",
     "expect": "PASS"},

    # ── All-slots confirm / reject ─────────────────────────────────────────
    {"n": 12, "id": "UI_08", "group": "all",
     "name": "Confirm ALL remaining NEW slots (one by one) (SCHED_CONF_02)",
     "desc": "Iterate and confirm every NEW slot until none remain",
     "expect": "PASS"},

    {"n": 13, "id": "UI_09", "group": "all",
     "name": "Reject ALL confirmed slots (one by one) (SCHED_REJ_02)",
     "desc": "Iterate and reject every CONFIRMED slot until none remain",
     "expect": "PASS"},

    # ── Reset + bulk ───────────────────────────────────────────────────────
    {"n": 14, "id": "SETUP_02", "group": "setup",
     "name": "Re-import Week 36 (reset to NEW)",
     "desc": "Re-upload the same baseline CSV to create fresh NEW slots for bulk tests",
     "expect": "PASS"},

    {"n": 15, "id": "UI_10", "group": "bulk",
     "name": "Bulk Confirm Week (SCHED_CONF_03)",
     "desc": "Click 'Confirm Week', choose Confirm in popup → all NEW become CONFIRMED",
     "expect": "PASS"},

    {"n": 16, "id": "UI_11", "group": "bulk",
     "name": "Bulk Reject Week (SCHED_REJ_03)",
     "desc": "Click 'Confirm Week', choose Reject in popup → all CONFIRMED become REJECTED",
     "expect": "PASS"},

    # ── Add via UI ─────────────────────────────────────────────────────────
    {"n": 17, "id": "UI_12", "group": "add",
     "name": "Add a Virya slot via UI",
     "desc": "Click empty slot, fill modal (offtaker + DO type 1 + PU type 1), Save",
     "expect": "PASS"},
]

# ==========================================================================
# Internals
# ==========================================================================
import argparse, re, sys, time, csv as _csv
from pathlib import Path
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright
from _weekly_csv_sync import ensure_next_week

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results

# This script's own scenario ids ("UI_06", "AN_02", "SETUP_01", ...) don't match
# the Excel's "Test case ID" column directly — map each to the real Excel id(s).
# UI_06..UI_11 are credited twice: once under Nominations (Nom_WN_UI_*) and once
# under the Scheduler process (SCHED_CONF/REJ_*), which shares the same tested
# mechanism (see update_sched_conf_rej_jul2026.py).
_SCHED_EXTRA_CREDIT = {
    "UI_06": "SCHED_CONF_01", "UI_07": "SCHED_REJ_01",
    "UI_08": "SCHED_CONF_02", "UI_09": "SCHED_REJ_02",
    "UI_10": "SCHED_CONF_03", "UI_11": "SCHED_REJ_03",
}


def _excel_ids_for(internal_id: str) -> list[str]:
    if internal_id.startswith("SETUP_"):
        return [f"Nom_WN_UI_{internal_id}"]
    if internal_id.startswith("UI_"):
        ids = [f"Nom_WN_{internal_id}"]
        if internal_id in _SCHED_EXTRA_CREDIT:
            ids.append(_SCHED_EXTRA_CREDIT[internal_id])
        return ids
    if internal_id.startswith("AN_"):
        return [f"Nom_{internal_id}"]
    return []

HERE          = Path(__file__).parent
WEEK36_CSV    = HERE / "Virya_Nomination_Week36.csv"
WEEK35_CSV    = HERE / "Virya_Nomination_Week35.csv"

# --------------------------------------------------------------------------
# Environment
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
# Timing
# --------------------------------------------------------------------------
def _ws(): time.sleep(WAIT_S)
def _wm(): time.sleep(WAIT_M)
def _wl(): time.sleep(WAIT_L)

def _quiet(page, ms=8000):
    try: page.wait_for_load_state("networkidle", timeout=ms)
    except Exception: pass

def _wait_for_auth_settle(page, timeout_s=15, stable_checks=3, stable_interval=0.5):
    """MSAL's client-side redirect from the /auth#code=... callback to the
    app URL can still be in flight right when the user confirms login in
    the terminal — racing our own page.goto() then throws 'interrupted by
    another navigation'. A single clean-URL check isn't reliable: the
    browser can be between navigations (e.g. briefly on about:blank or the
    login page) and look "settled" an instant before Entra actually
    redirects to /auth#code=..., so this requires the URL to look clean for
    several consecutive checks in a row, not just once."""
    deadline = time.time() + timeout_s
    consecutive_clean = 0
    while time.time() < deadline:
        try:
            url = page.url
        except Exception:
            url = ""
        if "/auth" not in url and "#code=" not in url:
            consecutive_clean += 1
            if consecutive_clean >= stable_checks:
                return
        else:
            consecutive_clean = 0
        time.sleep(stable_interval)


def _goto_nominations(page, nominations_url, retries=4):
    """page.goto(nominations_url) can race an in-flight MSAL auth redirect —
    not just right after login, but any time a scenario is run standalone
    via START_FROM and ends up being the first navigation of the run.
    Retry a few times with a short wait instead of letting the whole
    scenario error out on 'interrupted by another navigation'."""
    last_exc = None
    for _ in range(retries):
        try:
            page.goto(nominations_url, wait_until="domcontentloaded")
            return
        except Exception as e:
            last_exc = e
            if "interrupted by another navigation" not in str(e):
                raise
            time.sleep(1.5)
    raise last_exc

# Shared in-run state: lets confirm remember which slot it touched
# so the following reject can click the exact same position.
_SESSION: dict = {}

# --------------------------------------------------------------------------
# CSV helpers (generate week 36 from week 35 if missing)
# --------------------------------------------------------------------------
def _ensure_week36_csvs():
    if WEEK36_CSV.exists():
        return
    if not WEEK35_CSV.exists():
        raise FileNotFoundError(f"Base CSV not found: {WEEK35_CSV}")

    def _shift(src, dst, overrides=None):
        with open(src, newline="", encoding="utf-8") as f:
            rows = list(_csv.reader(f))
        for row in rows[1:]:
            for i in (1, 2):  # Start Time, End Time
                if len(row) > i and row[i].strip():
                    try:
                        dt = datetime.strptime(row[i].strip(), "%d/%m/%Y %H:%M")
                        row[i] = (dt + timedelta(days=7)).strftime("%d/%m/%Y %H:%M")
                    except ValueError:
                        pass
            if overrides and row[0] in overrides:
                for col, val in overrides[row[0]].items():
                    row[col] = val
        with open(dst, "w", newline="", encoding="utf-8") as f:
            _csv.writer(f).writerows(rows)

    _shift(WEEK35_CSV, WEEK36_CSV)
    print(f"  Generated: {WEEK36_CSV.name}")

# --------------------------------------------------------------------------
# Playwright helpers
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
            close = modal.locator("button", has_text=re.compile(r"^Close$", re.I)).first
            _safe_click(page, close, "Close")
    except Exception:
        try: page.keyboard.press("Escape")
        except Exception: pass
    _quiet(page)

def _current_week_and_year(page):
    try:
        _quiet(page, 4000)
        txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000)
        wk = int(m.group(1)) if (m := re.search(r"Week\s*(\d{1,2})", txt, re.I)) else None
        yr = int(m.group(1)) if (m := re.search(r"\b(20\d{2})\b", txt)) else None
    except Exception:
        return None, None
    if yr is None:
        try:
            from collections import Counter
            body = page.locator("body").inner_text(timeout=2000)
            yrs  = re.findall(r"\b(20\d{2})\b", body)
            yr   = int(Counter(yrs).most_common(1)[0][0]) if yrs else None
        except Exception:
            pass
    return wk, yr

def _goto_week_year(page, week, year, max_clicks=120):
    try: page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception: pass
    _wm()
    for _ in range(max_clicks):
        wk, yr = _current_week_and_year(page)
        if wk == week and (yr is None or yr == year):
            return
        if wk is None: _wm(); continue
        curr_yr = yr if yr is not None else year
        diff    = (year - curr_yr) * 52 + (week - wk)
        if diff == 0: return
        btn = ("div.date-picker-next > button" if diff > 0
               else "div.date-picker-previous > button")
        page.locator(btn).first.click()
        _ws()

def _snapshot_slots(page, mode="new"):
    """Bounding-boxes for visible slots, sorted top→bottom left→right.
    mode: "new"       – status-new only
          "confirmed" – not NEW and not REJECTED, with content
          "filled"    – not REJECTED, with content
    """
    _quiet(page)
    if mode == "new":
        found = page.locator("div.slot.status-new")
    elif mode == "confirmed":
        found = page.locator("div.slot:not(.status-new):not(.status-rejected)")
    else:
        found = page.locator("div.slot:not(.status-rejected)")

    seen = {}
    for i in range(found.count()):
        try:
            el    = found.nth(i)
            inner = el.inner_html(timeout=1000).strip()
            if mode != "new" and len(inner) < 10:
                continue
            bb  = el.bounding_box(timeout=1500)
            if not bb or bb["width"] < 20 or bb["height"] < 8:
                continue
            key = (round(bb["y"], 1), round(bb["x"], 1))
            if key not in seen:
                seen[key] = bb
        except Exception:
            continue
    return [seen[k] for k in sorted(seen.keys())]

def _click_slot_bb(page, bb):
    page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.mouse.down(); page.mouse.up()
    _ws()

# --------------------------------------------------------------------------
# Delete-button helper
# The trash buttons are icon-only (no text). We find all <button> elements
# inside the modal that do NOT contain the known named-button texts, giving
# us only the per-row delete buttons.
# --------------------------------------------------------------------------
_NAMED_BTN = re.compile(
    r"Reject|Confirm|Close|Save|\+\s*Add Trailer|Annuler", re.I)

def _delete_buttons(modal):
    """Return a locator for all icon-only (trash) buttons in the modal."""
    return modal.locator("button").filter(
        has_not_text=_NAMED_BTN)

_PLACEHOLDER = re.compile(r"^-\s*select\s*-$|^-+$", re.I)

def _select_option_flexible(locator, label):
    """Select by label; fall back to partial-match on enabled options."""
    try:
        locator.select_option(label=label, timeout=3000)
        return label
    except Exception:
        opts = locator.locator("option:not([disabled])").all_inner_texts()
        hit  = next(
            (t for t in opts
             if label.lower() in t.lower() and not _PLACEHOLDER.match(t.strip())),
            None)
        if hit:
            locator.select_option(label=hit, timeout=2000)
            return hit
        raise ValueError(f"Option '{label}' not found in enabled options: {opts}")

def _select_first_available(locator):
    """Select the first non-placeholder enabled option; return the chosen text."""
    opts = locator.locator("option:not([disabled])").all_inner_texts()
    choices = [o.strip() for o in opts
               if o.strip() and not _PLACEHOLDER.match(o.strip())]
    if not choices:
        raise ValueError(f"No selectable options found. All options: {opts}")
    locator.select_option(label=choices[0], timeout=3000)
    return choices[0]

def _selected_text(locator):
    """Return the display text of the currently selected option (not its value)."""
    return locator.evaluate(
        "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''")

def _reopen_and_verify(page, slot_bb, shots_dir, verify_fn, step="05"):
    """Re-open the slot at slot_bb, call verify_fn(modal) → (ok: bool, detail: str),
    screenshot, close modal.  Returns (ok, detail)."""
    _wm()
    _click_slot_bb(page, slot_bb)
    try:
        modal = _open_modal(page)
    except Exception as e:
        return False, f"Could not re-open for verification: {e}"
    try:
        ok, detail = verify_fn(modal)
    except Exception as e:
        ok, detail = False, f"Verification read error: {e}"
    page.screenshot(path=str(shots_dir / f"{step}_verify_reopen.png"))
    _close_modal(page, modal)
    _quiet(page)
    return ok, detail

# --------------------------------------------------------------------------
# Auto-detection
# --------------------------------------------------------------------------
ERROR_KW   = ["error", "exception", "invalid", "failed", "not valid",
               "cannot", "unexpected", "400", "500"]
SUCCESS_KW = ["success", "uploaded", "import complete", "confirmed", "saved"]

def _detect(page, modal=None) -> tuple[str, str]:
    if modal:
        try:
            if modal.is_visible():
                txt = modal.inner_text(timeout=2000).lower()
                if any(k in txt for k in ERROR_KW):
                    snip = next((l.strip() for l in txt.splitlines()
                                 if any(k in l for k in ERROR_KW) and len(l) > 4), "")
                    return "FAIL", f"Modal: \"{snip[:120]}\""
        except Exception:
            pass

    for sel in [".toast", ".alert", ".snackbar",
                "[class*='toast']", "[class*='error']", "[class*='success']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                txt = el.inner_text(timeout=800).lower().strip()
                if any(k in txt for k in ERROR_KW):
                    return "FAIL", f"Toast: \"{txt[:120]}\""
                if any(k in txt for k in SUCCESS_KW):
                    return "PASS", f"Toast: \"{txt[:120]}\""
        except Exception:
            pass

    try:
        body = page.locator("body").inner_text(timeout=1500).lower()
        for k in ERROR_KW:
            idx = body.find(k)
            if idx != -1:
                snip = body[max(0, idx-20):idx+60].strip().replace("\n", " ")
                return "FAIL", f"Page: \"{snip[:120]}\""
    except Exception:
        pass

    if modal:
        try:
            if not modal.is_visible():
                return "PASS", "Modal closed cleanly"
        except Exception:
            return "PASS", "Modal detached (success)"

    return "PASS", "No errors detected"

# --------------------------------------------------------------------------
# User prompt
# --------------------------------------------------------------------------
def _ask(scenario: dict, auto: str, reason: str) -> tuple[str, str]:
    label  = ("[expected PASS]" if scenario["expect"] == "PASS" else
               "[expected FAIL]" if scenario["expect"] == "FAIL" else "[observe]")
    aflag  = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL"}.get(auto, f"AUTO-{auto}")
    mismatch = (scenario["expect"] != "?" and auto != scenario["expect"])
    print(f"\n  {scenario['id']} {label}  –  {scenario['name']}")
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
# Scenario helpers
# ==========================================================================

# --------------------------------------------------------------------------
# SETUP: Import CSV
# --------------------------------------------------------------------------
def _do_import(page, nominations_url, csv_path, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url)
    _wm(); _quiet(page)
    page.screenshot(path=str(shots_dir / "01_landing.png"))

    try:
        _safe_click(page,
            page.get_by_role("button",
                name=re.compile("^Import Nominations Request$", re.I)), "Import button")
    except Exception:
        _safe_click(page,
            page.locator("text=Import Nominations Request").first, "Import fallback")

    modal = _open_modal(page)
    page.screenshot(path=str(shots_dir / "02_modal.png"))

    try:
        modal.locator("select#offtaker").select_option(label="Virya Energy NV")
    except Exception:
        modal.locator("select[name='offtaker']").select_option(label="Virya Energy NV")
    _ws()

    try:
        modal.locator("input#nominationFile").set_input_files(str(csv_path))
    except Exception:
        modal.locator("input[type='file']").set_input_files(str(csv_path))
    _ws()
    page.screenshot(path=str(shots_dir / "03_ready.png"))

    _safe_click(page,
        modal.get_by_role("button", name=re.compile("^Upload$", re.I)), "Upload")
    print("  Waiting 5 s for Hera to process…")
    time.sleep(5); _quiet(page)
    page.screenshot(path=str(shots_dir / "04_response.png"))

    auto, reason = _detect(page, modal)
    _close_modal(page, modal)

    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass
    _wm()
    shot = str(shots_dir / "05_grid_after.png")
    page.screenshot(path=shot, full_page=True)
    return shot, auto, reason

# --------------------------------------------------------------------------
# UI_01: Grid view
# --------------------------------------------------------------------------
def _view_grid(page, nominations_url, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass
    _quiet(page)
    new_count = page.locator("div.slot.status-new").count()
    shot = str(shots_dir / "grid.png")
    page.screenshot(path=shot, full_page=True)
    auto   = "PASS" if new_count > 0 else "FAIL"
    reason = f"{new_count} NEW slot(s) visible on Week {TARGET_WEEK}/{TARGET_YEAR}"
    return shot, auto, reason

# --------------------------------------------------------------------------
# UI_02: Change offtaker
# --------------------------------------------------------------------------
def _modify_offtaker(page, nominations_url, shots_dir, slot_index=0):
    """Open slot at slot_index, change offtaker to an alternative, Save."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if len(bbs) <= slot_index:
        return str(shots_dir / "no_slot.png"), "FAIL", \
               f"Not enough NEW slots (need index {slot_index}, got {len(bbs)})"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)
    _click_slot_bb(page, bbs[slot_index])

    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_before.png"), "FAIL", "Modal did not open"

    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Read current offtaker, pick the other option
    offtaker_sel = None
    for sel in ["select#offtaker", "select[name='offtaker']"]:
        try:
            el = modal.locator(sel).first
            if el.is_visible(timeout=1000):
                offtaker_sel = el; break
        except Exception:
            pass

    if offtaker_sel is None:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "FAIL", "Offtaker select not found"

    # Use the displayed text of the current selection (not its raw value/UUID)
    current_text = _selected_text(offtaker_sel)
    all_opts = offtaker_sel.locator("option:not([disabled])").all_inner_texts()
    alternative = next(
        (o.strip() for o in all_opts
         if o.strip() and o.strip() != current_text and not _PLACEHOLDER.match(o.strip())),
        None)

    if alternative is None:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "?", \
               f"Only one selectable offtaker ('{current_text}') — cannot test change"

    print(f"  Changing offtaker: '{current_text}' → '{alternative}'")
    _select_option_flexible(offtaker_sel, alternative)
    _ws()
    page.screenshot(path=str(shots_dir / "03_changed.png"))

    try:
        _safe_click(page,
            modal.get_by_role("button", name=re.compile("^Save$", re.I)), "Save")
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        page.screenshot(path=str(shots_dir / "03_save_error.png"))
        return str(shots_dir / "03_save_error.png"), "FAIL", str(e)

    save_auto, save_reason = _detect(page, modal)
    page.screenshot(path=str(shots_dir / "04_after_save.png"), full_page=True)

    if save_auto == "FAIL":
        return str(shots_dir / "04_after_save.png"), "FAIL", save_reason

    # Re-open the same slot and verify the offtaker was persisted
    slot_bb = bbs[slot_index]
    expected = alternative

    def _check_offtaker(m):
        sel = None
        for s in ["select#offtaker", "select[name='offtaker']"]:
            try:
                el = m.locator(s).first
                if el.is_visible(timeout=1000): sel = el; break
            except Exception: pass
        if sel is None:
            return False, "Offtaker select not found on re-open"
        actual = _selected_text(sel)
        ok = actual.strip() == expected.strip()
        return ok, f"Re-open: offtaker='{actual}'"

    ok, vdetail = _reopen_and_verify(page, slot_bb, shots_dir, _check_offtaker)
    shot = str(shots_dir / "06_final.png")
    page.screenshot(path=shot, full_page=True)
    if ok:
        return shot, "PASS", f"Offtaker='{alternative}' persisted. {vdetail}"
    return shot, "FAIL", f"Offtaker NOT persisted (expected '{alternative}'). {vdetail}"


# --------------------------------------------------------------------------
# UI_03: Change trailer type
# --------------------------------------------------------------------------
def _modify_trailer_type(page, nominations_url, shots_dir, slot_index=1):
    """Open slot at slot_index, change first trailer type Type 1 → Type 2, Save."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if len(bbs) <= slot_index:
        return str(shots_dir / "no_slot.png"), "FAIL", \
               f"Not enough NEW slots (need index {slot_index}, got {len(bbs)})"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)
    _click_slot_bb(page, bbs[slot_index])

    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_before.png"), "FAIL", "Modal did not open"

    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    trailer_sels = modal.locator("select[name='trailerType']")
    if trailer_sels.count() == 0:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "FAIL", \
               "No trailerTypeId select found in modal"

    first = trailer_sels.first
    current_text = _selected_text(first)
    all_opts = first.locator("option:not([disabled])").all_inner_texts()
    alternative = next(
        (o.strip() for o in all_opts
         if o.strip() and o.strip() != current_text and not _PLACEHOLDER.match(o.strip())),
        None)

    if alternative is None:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "?", \
               f"Only one trailer type selectable ('{current_text}') — cannot test change"

    print(f"  Changing trailer type: '{current_text}' → '{alternative}'")
    _select_option_flexible(first, alternative)
    _ws()
    page.screenshot(path=str(shots_dir / "03_changed.png"))

    try:
        _safe_click(page,
            modal.get_by_role("button", name=re.compile("^Save$", re.I)), "Save")
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        page.screenshot(path=str(shots_dir / "03_save_error.png"))
        return str(shots_dir / "03_save_error.png"), "FAIL", str(e)

    save_auto, save_reason = _detect(page, modal)
    page.screenshot(path=str(shots_dir / "04_after_save.png"), full_page=True)

    if save_auto == "FAIL":
        return str(shots_dir / "04_after_save.png"), "FAIL", save_reason

    # Re-open and verify the trailer type was persisted
    slot_bb  = bbs[slot_index]
    expected = alternative

    def _check_trailer_type(m):
        sels = m.locator("select[name='trailerType']")
        if sels.count() == 0:
            return False, "trailerType select not found on re-open"
        actual = _selected_text(sels.first)
        ok = actual.strip() == expected.strip()
        return ok, f"Re-open: first trailer type='{actual}'"

    ok, vdetail = _reopen_and_verify(page, slot_bb, shots_dir, _check_trailer_type)
    shot = str(shots_dir / "06_final.png")
    page.screenshot(path=shot, full_page=True)
    if ok:
        return shot, "PASS", f"Trailer type='{alternative}' persisted. {vdetail}"
    return shot, "FAIL", \
           f"Trailer type NOT persisted (expected '{alternative}'). {vdetail}"

# --------------------------------------------------------------------------
# UI_04: Add trailer row
# --------------------------------------------------------------------------
def _add_trailer_row(page, nominations_url, shots_dir, slot_index=2):
    """Open slot at slot_index, add an extra trailer row, fill it, Save."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if len(bbs) <= slot_index:
        return str(shots_dir / "no_slot.png"), "FAIL", \
               f"Not enough NEW slots (need index {slot_index}, got {len(bbs)})"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)
    _click_slot_bb(page, bbs[slot_index])

    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_before.png"), "FAIL", "Modal did not open"

    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    rows_before = modal.locator("select[name='trailerType']").count()
    print(f"  Trailer rows before: {rows_before}")

    # Click "+ Add Trailer"
    try:
        add_btn = modal.locator("button",
            has_text=re.compile(r"\+\s*Add Trailer", re.I)).first
        _safe_click(page, add_btn, "+ Add Trailer")
        _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "FAIL", \
               f"'+ Add Trailer' button not found: {e}"

    rows_after = modal.locator("select[name='trailerType']").count()
    print(f"  Trailer rows after add: {rows_after}")
    page.screenshot(path=str(shots_dir / "03_row_added.png"))

    if rows_after <= rows_before:
        _close_modal(page, modal)
        return str(shots_dir / "03_row_added.png"), "FAIL", \
               "Row count did not increase after clicking + Add Trailer"

    # Fill the new (last) row — pick the first available option for each select
    # (options are context-dependent: e.g. if Drop-Off exists, only Pick-Up may be offered)
    new_type_sel     = modal.locator("select[name='trailerType']").last
    new_transfer_sel = modal.locator("select[name='transferType']").last
    try:
        chosen_type = _select_first_available(new_type_sel);     _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "03_row_added.png"), "FAIL", f"Trailer type fill failed: {e}"
    try:
        chosen_transfer = _select_first_available(new_transfer_sel); _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "03_row_added.png"), "FAIL", f"Transfer type fill failed: {e}"
    print(f"  New row filled: type='{chosen_type}', transfer='{chosen_transfer}'")
    page.screenshot(path=str(shots_dir / "04_row_filled.png"))

    try:
        _safe_click(page,
            modal.get_by_role("button", name=re.compile("^Save$", re.I)), "Save")
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        page.screenshot(path=str(shots_dir / "04_save_error.png"))
        return str(shots_dir / "04_save_error.png"), "FAIL", str(e)

    save_auto, save_reason = _detect(page, modal)
    page.screenshot(path=str(shots_dir / "05_after_save.png"), full_page=True)

    if save_auto == "FAIL":
        return str(shots_dir / "05_after_save.png"), "FAIL", save_reason

    # Re-open and verify the new row was persisted
    slot_bb       = bbs[slot_index]
    expected_rows = rows_after

    def _check_row_added(m):
        actual = m.locator("select[name='trailerType']").count()
        ok = actual == expected_rows
        return ok, f"Re-open: {actual} row(s) (expected {expected_rows})"

    ok, vdetail = _reopen_and_verify(page, slot_bb, shots_dir, _check_row_added)
    shot = str(shots_dir / "07_final.png")
    page.screenshot(path=shot, full_page=True)
    if ok:
        return shot, "PASS", \
               f"Row added ({rows_before}→{rows_after}), type='{chosen_type}', " \
               f"transfer='{chosen_transfer}'. Persisted: {vdetail}"
    return shot, "FAIL", \
           f"Row NOT persisted after save. {vdetail}"

# --------------------------------------------------------------------------
# UI_05: Delete trailer row
# --------------------------------------------------------------------------
def _delete_trailer_row(page, nominations_url, shots_dir):
    """Find first NEW slot with ≥2 trailer rows, delete the last row, Save."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if not bbs:
        return str(shots_dir / "no_slot.png"), "FAIL", "No NEW slots found"

    # Find a slot with ≥2 trailer rows by opening each until we find one
    target_modal = None
    target_idx   = -1
    target_bb    = None
    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)

    for idx, bb in enumerate(bbs):
        _click_slot_bb(page, bb)
        try:
            m = _open_modal(page)
        except Exception:
            continue

        rows = m.locator("select[name='trailerType']").count()
        if rows >= 2:
            target_modal = m
            target_idx   = idx
            target_bb    = bb   # remember position for re-open verification
            break
        # Close and try next
        _close_modal(page, m)

    if target_modal is None:
        return str(shots_dir / "01_before.png"), "FAIL", \
               "No NEW slot with ≥2 trailer rows found"

    rows_before = target_modal.locator("select[name='trailerType']").count()
    print(f"  Found slot {target_idx} with {rows_before} trailer row(s) — deleting last")
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    # Delete buttons = all buttons in modal that are NOT named
    del_btns = _delete_buttons(target_modal)
    del_count = del_btns.count()
    print(f"  Found {del_count} icon-only (delete) button(s)")

    if del_count == 0:
        _close_modal(page, target_modal)
        return str(shots_dir / "02_modal_open.png"), "FAIL", \
               "No delete/trash buttons found in modal"

    # Click the last delete button (last trailer row)
    try:
        _safe_click(page, del_btns.last, "delete last row")
        _ws()
    except Exception as e:
        _close_modal(page, target_modal)
        return str(shots_dir / "02_modal_open.png"), "FAIL", f"Delete click failed: {e}"

    rows_after = target_modal.locator("select[name='trailerType']").count()
    print(f"  Trailer rows after delete: {rows_after}")
    page.screenshot(path=str(shots_dir / "03_row_deleted.png"))

    if rows_after >= rows_before:
        _close_modal(page, target_modal)
        return str(shots_dir / "03_row_deleted.png"), "FAIL", \
               f"Row count did not decrease ({rows_before}→{rows_after})"

    try:
        _safe_click(page,
            target_modal.get_by_role("button", name=re.compile("^Save$", re.I)), "Save")
        try: target_modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        page.screenshot(path=str(shots_dir / "03_save_error.png"))
        return str(shots_dir / "03_save_error.png"), "FAIL", str(e)

    save_auto, save_reason = _detect(page, target_modal)
    page.screenshot(path=str(shots_dir / "04_after_save.png"), full_page=True)

    if save_auto == "FAIL":
        return str(shots_dir / "04_after_save.png"), "FAIL", save_reason

    # Re-open and verify row count decreased
    expected_rows = rows_after

    def _check_row_deleted(m):
        actual = m.locator("select[name='trailerType']").count()
        ok = actual == expected_rows
        return ok, f"Re-open: {actual} row(s) (expected {expected_rows})"

    ok, vdetail = _reopen_and_verify(page, target_bb, shots_dir, _check_row_deleted)
    shot = str(shots_dir / "06_final.png")
    page.screenshot(path=shot, full_page=True)
    if ok:
        return shot, "PASS", \
               f"Deleted row ({rows_before}→{rows_after}). Persisted: {vdetail}"
    return shot, "FAIL", \
           f"Deletion NOT persisted after save. {vdetail}"

# --------------------------------------------------------------------------
# AN_02: Inconsistent trailer types across two slots (negative — expect FAIL)
# --------------------------------------------------------------------------
def _inconsistent_trailer_types(page, nominations_url, shots_dir):
    """Save Drop-Off Type 1 on an earlier NEW slot, then try to save a
    mismatched Pick-Up Type 2 on a later NEW slot — the trailer physically
    dropped was Type 1, so Hera should reject the mismatched Pick-Up.
    Returns the raw _detect() outcome, same convention as every other
    scenario here: PASS = the save went through (bug, since this scenario's
    "expect" is FAIL), FAIL = Hera rejected it (correct)."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if len(bbs) < 2:
        return str(shots_dir / "no_slot.png"), "?", \
               f"Need >=2 NEW slots (earlier + later), got {len(bbs)}"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)

    # --- Earlier slot: Drop-Off Type 1, Save (creates the plant-side state) ---
    early_bb = bbs[0]
    _click_slot_bb(page, early_bb)
    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_before.png"), "?", "Modal did not open (earlier slot)"

    try:
        _select_option_flexible(modal.locator("select[name='trailerType']").first, "Type 1"); _ws()
        _select_option_flexible(modal.locator("select[name='transferType']").first, "Drop-Off"); _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "01_before.png"), "?", f"Could not set Drop-Off Type 1: {e}"

    page.screenshot(path=str(shots_dir / "02_early_dropoff_type1.png"))

    try:
        _safe_click(page, modal.get_by_role("button", name=re.compile("^Save$", re.I)),
                    "Save (earlier slot)")
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        return str(shots_dir / "02_early_dropoff_type1.png"), "?", \
               f"Save failed on earlier (setup) slot: {e}"

    setup_auto, setup_reason = _detect(page)
    if setup_auto == "FAIL":
        return str(shots_dir / "02_early_dropoff_type1.png"), "?", \
               f"Setup slot itself failed to save, cannot test the mismatch: {setup_reason}"

    # --- Later slot: try Pick-Up Type 2 — mismatched vs. the Type 1 just dropped ---
    later_bbs = _snapshot_slots(page, "new")
    later_bb  = next((bb for bb in later_bbs if bb["y"] > early_bb["y"] + 5),
                      later_bbs[-1] if later_bbs else None)
    if later_bb is None:
        return str(shots_dir / "03_no_later_slot.png"), "?", \
               "No later NEW slot available for the mismatched Pick-Up"

    _click_slot_bb(page, later_bb)
    try:
        modal2 = _open_modal(page)
    except Exception:
        return str(shots_dir / "03_no_later_slot.png"), "?", "Modal did not open (later slot)"

    try:
        _select_option_flexible(modal2.locator("select[name='transferType']").first, "Pick-Up"); _ws()
    except Exception as e:
        _close_modal(page, modal2)
        return str(shots_dir / "03_no_later_slot.png"), "?", \
               f"'Pick-Up' not selectable on later slot (unrelated to the mismatch): {e}"

    try:
        _select_option_flexible(modal2.locator("select[name='trailerType']").first, "Type 2"); _ws()
    except Exception as e:
        _close_modal(page, modal2)
        return str(shots_dir / "03_no_later_slot.png"), "FAIL", \
               f"'Type 2' not offered for the Pick-Up — Hera restricts the dropdown to " \
               f"the trailer type actually present at the plant, preventing the " \
               f"mismatch before Save ({e})"

    page.screenshot(path=str(shots_dir / "04_later_pickup_type2.png"))

    save_btn2 = modal2.get_by_role("button", name=re.compile("^Save$", re.I))
    try:
        if save_btn2.is_disabled(timeout=1000):
            _close_modal(page, modal2)
            return str(shots_dir / "04_later_pickup_type2.png"), "FAIL", \
                   "Save disabled for the mismatched Pick-Up Type 2 (prevented at UI level)"
    except Exception:
        pass

    try:
        _safe_click(page, save_btn2, "Save (mismatched Pick-Up, expect rejection)")
        page.wait_for_timeout(1500)
        _quiet(page)
    except Exception as e:
        return str(shots_dir / "04_later_pickup_type2.png"), "FAIL", \
               f"Save could not be completed for the mismatched trailer type: {e}"

    shot = str(shots_dir / "05_after_save_attempt.png")
    page.screenshot(path=shot, full_page=True)
    auto, reason = _detect(page, modal2)
    return shot, auto, reason

# --------------------------------------------------------------------------
# AN_03: Two consecutive Drop-Offs, no Pick-Up between (negative — expect FAIL)
# --------------------------------------------------------------------------
def _two_dropoffs_without_pickup(page, nominations_url, shots_dir):
    """Add a second trailer row to a NEW slot and try to force it to
    Drop-Off too (no Pick-Up between two Drop-Offs) — Hera should reject the
    sequence. Same PASS/FAIL convention as _inconsistent_trailer_types."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if not bbs:
        return str(shots_dir / "no_slot.png"), "?", "No NEW slots found"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)
    _click_slot_bb(page, bbs[0])

    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_before.png"), "?", "Modal did not open"

    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    try:
        _select_option_flexible(modal.locator("select[name='trailerType']").first, "Type 1"); _ws()
        _select_option_flexible(modal.locator("select[name='transferType']").first, "Drop-Off"); _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "?", f"Could not set first row Drop-Off: {e}"

    try:
        add_btn = modal.locator("button", has_text=re.compile(r"\+\s*Add Trailer", re.I)).first
        _safe_click(page, add_btn, "+ Add Trailer"); _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "?", f"'+ Add Trailer' failed: {e}"

    page.screenshot(path=str(shots_dir / "03_row_added.png"))

    second_transfer = modal.locator("select[name='transferType']").last
    try:
        _select_option_flexible(second_transfer, "Drop-Off"); _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "03_row_added.png"), "FAIL", \
               f"'Drop-Off' not offered for the second row — Hera only offers " \
               f"Pick-Up next, preventing the invalid sequence at dropdown level ({e})"

    try:
        _select_first_available(modal.locator("select[name='trailerType']").last); _ws()
    except Exception as e:
        _close_modal(page, modal)
        return str(shots_dir / "03_row_added.png"), "?", f"Second row type fill failed: {e}"

    page.screenshot(path=str(shots_dir / "04_second_dropoff_filled.png"))

    save_btn = modal.get_by_role("button", name=re.compile("^Save$", re.I))
    try:
        if save_btn.is_disabled(timeout=1000):
            _close_modal(page, modal)
            return str(shots_dir / "04_second_dropoff_filled.png"), "FAIL", \
                   "Save disabled for two consecutive Drop-Offs (prevented at UI level)"
    except Exception:
        pass

    try:
        _safe_click(page, save_btn, "Save (two Drop-Offs, expect rejection)")
        page.wait_for_timeout(1500)
        _quiet(page)
    except Exception as e:
        return str(shots_dir / "04_second_dropoff_filled.png"), "FAIL", \
               f"Save could not be completed for the invalid sequence: {e}"

    shot = str(shots_dir / "05_after_save_attempt.png")
    page.screenshot(path=shot, full_page=True)
    auto, reason = _detect(page, modal)
    return shot, auto, reason

# --------------------------------------------------------------------------
# AN_05: Delete slot entirely (expect PASS — slot returns to empty)
# --------------------------------------------------------------------------
def _slot_is_empty_at(page, bb, tol=3):
    """True if the div.slot nearest this bounding box has neither status-new
    nor status-rejected and — per the same heuristic _snapshot_slots'/
    _add_slot_ui's "filled" checks use everywhere else in this file — very
    little innerHTML, i.e. it's a truly empty grid cell."""
    candidates = page.locator("div.slot:not(.status-new):not(.status-rejected)")
    for i in range(candidates.count()):
        el = candidates.nth(i)
        try:
            b = el.bounding_box(timeout=1000)
            if not b:
                continue
            if abs(b["y"] - bb["y"]) <= tol and abs(b["x"] - bb["x"]) <= tol:
                return len(el.inner_html(timeout=500).strip()) < 10
        except Exception:
            continue
    return False

def _delete_slot_entirely(page, nominations_url, shots_dir):
    """Open a NEW slot, delete every trailer row (not just one, unlike
    UI_05), Save, and confirm the slot returns to a truly empty grid cell."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    bbs = _snapshot_slots(page, "new")
    if not bbs:
        return str(shots_dir / "no_slot.png"), "FAIL", "No NEW slots found"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)
    target_bb = bbs[0]
    _click_slot_bb(page, target_bb)

    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_before.png"), "FAIL", "Modal did not open"

    rows_before = modal.locator("select[name='trailerType']").count()
    print(f"  Trailer rows before: {rows_before}")
    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    if rows_before == 0:
        _close_modal(page, modal)
        return str(shots_dir / "02_modal_open.png"), "FAIL", \
               "Slot has no trailer rows to delete"

    # Delete every row one at a time — always target the last remaining
    # delete button, since removing a row shifts the others' indices.
    deleted = 0
    for _ in range(rows_before):
        del_btns = _delete_buttons(modal)
        if del_btns.count() == 0:
            break
        try:
            _safe_click(page, del_btns.last, f"delete row {deleted + 1}")
            _ws()
            deleted += 1
        except Exception as e:
            page.screenshot(path=str(shots_dir / "03_delete_error.png"))
            return str(shots_dir / "03_delete_error.png"), "FAIL", \
                   f"Delete failed after {deleted} row(s): {e}"

    rows_after = modal.locator("select[name='trailerType']").count()
    print(f"  Trailer rows after deleting all: {rows_after}")
    page.screenshot(path=str(shots_dir / "04_all_rows_deleted.png"))

    if rows_after > 0:
        _close_modal(page, modal)
        return str(shots_dir / "04_all_rows_deleted.png"), "FAIL", \
               f"{rows_after} row(s) still remain — could not delete every transfer"

    try:
        _safe_click(page,
            modal.get_by_role("button", name=re.compile("^Save$", re.I)), "Save")
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        page.screenshot(path=str(shots_dir / "05_save_error.png"))
        return str(shots_dir / "05_save_error.png"), "FAIL", str(e)

    save_auto, save_reason = _detect(page)
    page.screenshot(path=str(shots_dir / "06_after_save.png"), full_page=True)
    if save_auto == "FAIL":
        return str(shots_dir / "06_after_save.png"), "FAIL", save_reason

    shot = str(shots_dir / "07_final.png")
    page.screenshot(path=shot, full_page=True)
    if _slot_is_empty_at(page, target_bb):
        return shot, "PASS", (
            f"Deleted all {rows_before} trailer row(s) and saved — the slot at "
            f"y={target_bb['y']:.0f} is now a truly empty grid cell "
            f"(no status-new/rejected, no content)."
        )
    return shot, "FAIL", (
        f"Deleted all {rows_before} trailer row(s) and saved, but the slot at "
        f"y={target_bb['y']:.0f} does not look truly empty afterwards."
    )

# --------------------------------------------------------------------------
# UI_06 / UI_07: Individual confirm or reject
# --------------------------------------------------------------------------
def _act_one_slot(page, nominations_url, shots_dir, action: str):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    if action == "confirm":
        bbs = _snapshot_slots(page, "new")
        hint = ""
    else:
        # Prefer the exact slot position stored when we confirmed it in this run.
        # Falling back to CSS snapshot is unreliable if the confirmed class differs.
        saved = _SESSION.get("last_confirmed_bb")
        if saved:
            bbs = [saved]
            hint = " (using remembered confirmed-slot position)"
        else:
            bbs = _snapshot_slots(page, "confirmed")
            hint = " (no remembered BB — using CSS snapshot)"

    if not bbs:
        return str(shots_dir / "no_slot.png"), "FAIL", \
               f"No eligible slot found for {action}"

    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)
    target_bb = bbs[0]
    print(f"  {action.capitalize()}ing slot at y={target_bb['y']:.0f}{hint}")
    _click_slot_bb(page, target_bb)

    try:
        modal = _open_modal(page)
    except Exception:
        page.screenshot(path=str(shots_dir / "02_no_modal.png"))
        return str(shots_dir / "02_no_modal.png"), "FAIL", "Modal did not open"

    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    try:
        if action == "confirm":
            try:
                _safe_click(page,
                    modal.get_by_role("button", name=re.compile("^Confirm$", re.I)), "Confirm")
            except Exception:
                _safe_click(page, modal.locator("button.button-confirm"), "Confirm fallback")
        else:
            try:
                _safe_click(page,
                    modal.get_by_role("button",
                        name=re.compile(r"^Reject$|^Cancel$|^Unconfirm$|^Annuler$", re.I)).first,
                    "Reject")
            except Exception:
                _safe_click(page,
                    modal.locator("button.button-reject, button.btn-danger").first,
                    "Reject fallback")

        page.screenshot(path=str(shots_dir / "03_after_click.png"))
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)

    except Exception as e:
        page.screenshot(path=str(shots_dir / "03_error.png"))
        return str(shots_dir / "03_error.png"), "FAIL", str(e)

    auto, reason = _detect(page, modal)

    # Update session state so the next reject step knows which slot to target
    if auto == "PASS":
        if action == "confirm":
            _SESSION["last_confirmed_bb"] = target_bb
        else:
            _SESSION.pop("last_confirmed_bb", None)

    shot = str(shots_dir / "04_grid_after.png")
    page.screenshot(path=shot, full_page=True)
    return shot, auto, reason

# --------------------------------------------------------------------------
# UI_08 / UI_09: Confirm or reject ALL slots one by one
# --------------------------------------------------------------------------
def _act_all_slots(page, nominations_url, shots_dir, action: str):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass

    if action == "confirm":
        slot_mode = "new"
    else:
        # "confirmed" CSS class is unreliable — use "filled" (all non-rejected
        # slots with content) just like hera_import_and_profiles.py does.
        # This catches both NEW and CONFIRMED slots; the modal Reject button
        # is available on both, so no slot is skipped unfairly.
        slot_mode = "filled"

    # Take ONE snapshot upfront and iterate those fixed positions.
    # Re-snapshotting in a while-loop causes infinite loops when a rejected
    # slot still matches the selector (class may not update immediately).
    all_bbs = _snapshot_slots(page, slot_mode)
    total   = len(all_bbs)
    print(f"  Found {total} slot(s) to {action} (mode: {slot_mode}).")

    done = errors = 0
    for idx, bb in enumerate(all_bbs, 1):
        slot_dir = shots_dir / f"slot_{idx:02d}"
        slot_dir.mkdir(exist_ok=True)

        _click_slot_bb(page, bb)

        try:
            modal = _open_modal(page)
        except Exception:
            print(f"  Slot {idx}: modal did not open, skipping."); errors += 1
            try: page.keyboard.press("Escape")
            except Exception: pass
            continue

        page.screenshot(path=str(slot_dir / "01_modal.png"))

        try:
            if action == "confirm":
                try:
                    _safe_click(page,
                        modal.get_by_role("button", name=re.compile("^Confirm$", re.I)), "Confirm")
                except Exception:
                    _safe_click(page, modal.locator("button.button-confirm"), "Confirm fb")
            else:
                try:
                    _safe_click(page,
                        modal.get_by_role("button",
                            name=re.compile(r"^Reject$|^Cancel$|^Unconfirm$", re.I)).first,
                        "Reject")
                except Exception:
                    _safe_click(page,
                        modal.locator("button.button-reject, button.btn-danger").first,
                        "Reject fb")

            try: modal.wait_for(state="detached", timeout=10_000)
            except Exception: pass
            _quiet(page)
            page.screenshot(path=str(slot_dir / "02_after.png"))
            done += 1

        except Exception as e:
            errors += 1
            print(f"  Slot {idx}: error – {e}")
            try: page.keyboard.press("Escape")
            except Exception: pass

    shot = str(shots_dir / "zz_final.png")
    page.screenshot(path=shot, full_page=True)
    auto   = "PASS" if errors == 0 else "FAIL"
    reason = f"{done}/{total} {action}ed, {errors} error(s)"
    return shot, auto, reason

# --------------------------------------------------------------------------
# UI_10 / UI_11: Bulk confirm/reject via Confirm Week button
# --------------------------------------------------------------------------
def _detect_bulk_toast(page, keyword: str) -> tuple[str, str]:
    """Trust the app's own confirmation banner (e.g. 'Rejected week / For
    Virya Energy NV, Messer Belgium NV.') instead of re-deriving success from
    slot CSS classes in the DOM — the banner is the ground truth the app
    itself gives us."""
    for sel in [".toast", ".alert", ".snackbar", "[class*='toast']",
                "[class*='notification']", "[class*='banner']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                txt = el.inner_text(timeout=800).strip()
                low = txt.lower()
                if keyword in low:
                    return "PASS", f"Toast: \"{txt[:150]}\""
                if any(k in low for k in ERROR_KW):
                    return "FAIL", f"Toast: \"{txt[:150]}\""
        except Exception:
            pass

    try:
        body = page.locator("body").inner_text(timeout=2000)
        low  = body.lower()
        idx  = low.find(keyword)
        if idx != -1:
            snip = body[max(0, idx-10):idx+80].strip().replace("\n", " ")
            return "PASS", f"Page: \"{snip[:150]}\""
        for k in ERROR_KW:
            idx = low.find(k)
            if idx != -1:
                snip = body[max(0, idx-20):idx+60].strip().replace("\n", " ")
                return "FAIL", f"Page: \"{snip[:150]}\""
    except Exception:
        pass

    return "FAIL", f"No \"{keyword}\" confirmation message found"

def _bulk_week(page, nominations_url, shots_dir, action: str):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass
    _quiet(page)
    page.screenshot(path=str(shots_dir / "01_before.png"), full_page=True)

    try:
        cw_btn = page.get_by_role("button", name=re.compile(r"Confirm Week", re.I)).first
        if not (cw_btn.is_visible() and not cw_btn.is_disabled()):
            return str(shots_dir / "01_before.png"), "FAIL", \
                   "'Confirm Week' button not visible or disabled"

        _safe_click(page, cw_btn, "Confirm Week")
        modal = _open_modal(page)
        page.screenshot(path=str(shots_dir / "02_popup.png"))

        if action == "confirm":
            _safe_click(page,
                modal.get_by_role("button", name=re.compile("^Confirm$", re.I)), "Confirm")
        else:
            _safe_click(page,
                modal.get_by_role("button", name=re.compile("^Reject$", re.I)), "Reject")

        page.screenshot(path=str(shots_dir / "03_after_click.png"))
        try: modal.wait_for(state="detached", timeout=12_000)
        except Exception: pass
        _wl(); _quiet(page)

    except Exception as e:
        page.screenshot(path=str(shots_dir / "03_error.png"))
        return str(shots_dir / "03_error.png"), "FAIL", str(e)

    shot = str(shots_dir / "04_grid_after.png")
    page.screenshot(path=shot, full_page=True)

    keyword = "confirmed week" if action == "confirm" else "rejected week"
    auto, reason = _detect_bulk_toast(page, keyword)
    return shot, auto, reason

# --------------------------------------------------------------------------
# UI_12: Add a new slot via UI (empty slot → modal → fill → Save)
# --------------------------------------------------------------------------
def _add_slot_ui(page, nominations_url, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    _goto_nominations(page, nominations_url); _wm()
    try: _goto_week_year(page, TARGET_WEEK, TARGET_YEAR)
    except Exception: pass
    _quiet(page)
    page.screenshot(path=str(shots_dir / "01_grid.png"), full_page=True)

    # Find a truly empty slot: no status-new, no status-rejected, and very little
    # innerHTML (no nomination has been placed there yet).
    # After a bulk reject, rejected slots still sit in the grid — we must skip them.
    candidates = page.locator("div.slot:not(.status-new):not(.status-rejected)")
    slot = None
    for i in range(candidates.count()):
        el = candidates.nth(i)
        try:
            if len(el.inner_html(timeout=500).strip()) < 10:
                slot = el
                break
        except Exception:
            continue

    if slot is None:
        return str(shots_dir / "01_grid.png"), "FAIL", \
               "No truly empty slot found (all non-new slots are rejected or filled)"

    _safe_click(page, slot, "empty slot")

    try:
        modal = _open_modal(page)
    except Exception:
        return str(shots_dir / "01_grid.png"), "FAIL", "Empty slot modal did not open"

    page.screenshot(path=str(shots_dir / "02_modal_open.png"))

    try:
        modal.locator("select#offtaker").select_option(label="Virya Energy NV")
    except Exception:
        modal.locator("select[name='offtaker']").select_option(label="Virya Energy NV")
    _ws()

    # Add two trailer rows
    add_btn = modal.locator("button",
                            has_text=re.compile(r"\+\s*Add Trailer", re.I)).first
    while modal.locator("select[name='trailerType']").count() < 2:
        _safe_click(page, add_btn, "+ Add Trailer"); _ws()

    page.screenshot(path=str(shots_dir / "03_rows_added.png"))

    _select_option_flexible(modal.locator("select[name='trailerType']").nth(0),  "Type 1");   _ws()
    _select_option_flexible(modal.locator("select[name='transferType']").nth(0),   "Drop-Off"); _ws()
    _select_option_flexible(modal.locator("select[name='trailerType']").nth(1),  "Type 1");   _ws()
    _select_option_flexible(modal.locator("select[name='transferType']").nth(1),   "Pick-Up");  _ws()
    page.screenshot(path=str(shots_dir / "04_filled.png"))

    try:
        _safe_click(page,
            modal.get_by_role("button", name=re.compile("^Save$", re.I)), "Save")
        try: modal.wait_for(state="detached", timeout=10_000)
        except Exception: pass
        _quiet(page)
    except Exception as e:
        page.screenshot(path=str(shots_dir / "04_save_error.png"))
        return str(shots_dir / "04_save_error.png"), "FAIL", str(e)

    auto, reason = _detect(page, modal)
    shot = str(shots_dir / "05_after_save.png")
    page.screenshot(path=shot, full_page=True)
    return shot, auto, reason

# ==========================================================================
# Main
# ==========================================================================
def main():
    global TARGET_WEEK, TARGET_YEAR

    parser = argparse.ArgumentParser(description="Hera Weekly Nomination UI Interaction Tests")
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
    _ensure_week36_csvs()  # regenerate Week36 from Week35 if it's missing entirely

    _SESSION.clear()   # ensure no stale state from a previous run in the same process

    env_name, base_url = _resolve_env()
    nominations_url = f"{base_url}/nominations/weekly"

    artifacts = HERE / "test_artifacts_ui"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    target_monday = date.fromisocalendar(TARGET_YEAR, TARGET_WEEK, 1)
    target_sunday = target_monday + timedelta(days=6)
    print(f"\n{'='*64}")
    print(f"  Hera Weekly UI Interaction Tests")
    print(f"  Environment   : {env_name}  ({base_url})")
    print(f"  Target week   : {TARGET_WEEK}/{TARGET_YEAR}  "
          f"({target_monday:%b %d} - {target_sunday:%b %d %Y})")
    print(f"  Scenarios     : {len(SCENARIOS)}  (starting from #{START_FROM})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*64}\n")

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir), headless=False, slow_mo=SLOWMO,
            viewport={"width": 1500, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("  Navigating to Hera…")
        try:
            page.goto(nominations_url, wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA in the browser if prompted.        │")
        print("  │  Press Enter here once you see the Hera app.        │")
        print("  └─────────────────────────────────────────────────────┘")
        input("  > ")
        _wait_for_auth_settle(page)
        _quiet(page)
        print("  Login confirmed. Starting scenarios.\n")

        for sc in SCENARIOS:
            if sc["n"] < START_FROM:
                continue

            shots_dir = run_dir / f"{sc['n']:02d}_{sc['id']}"
            print(f"\n{'─'*64}")
            print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['name']}")
            print(f"  {sc['desc']}")

            try:
                sid = sc["id"]

                if   sid == "SETUP_01":
                    shot, auto, reason = _do_import(
                        page, nominations_url, WEEK36_CSV, shots_dir)
                elif sid == "SETUP_02":
                    shot, auto, reason = _do_import(
                        page, nominations_url, WEEK36_CSV, shots_dir)
                elif sid == "UI_01":
                    shot, auto, reason = _view_grid(page, nominations_url, shots_dir)
                elif sid == "UI_02":
                    shot, auto, reason = _modify_offtaker(
                        page, nominations_url, shots_dir, slot_index=0)
                elif sid == "UI_03":
                    shot, auto, reason = _modify_trailer_type(
                        page, nominations_url, shots_dir, slot_index=1)
                elif sid == "UI_04":
                    shot, auto, reason = _add_trailer_row(
                        page, nominations_url, shots_dir, slot_index=2)
                elif sid == "UI_05":
                    shot, auto, reason = _delete_trailer_row(
                        page, nominations_url, shots_dir)
                elif sid == "AN_02":
                    shot, auto, reason = _inconsistent_trailer_types(
                        page, nominations_url, shots_dir)
                elif sid == "AN_03":
                    shot, auto, reason = _two_dropoffs_without_pickup(
                        page, nominations_url, shots_dir)
                elif sid == "AN_05":
                    shot, auto, reason = _delete_slot_entirely(
                        page, nominations_url, shots_dir)
                elif sid == "UI_06":
                    shot, auto, reason = _act_one_slot(
                        page, nominations_url, shots_dir, "confirm")
                elif sid == "UI_07":
                    shot, auto, reason = _act_one_slot(
                        page, nominations_url, shots_dir, "reject")
                elif sid == "UI_08":
                    shot, auto, reason = _act_all_slots(
                        page, nominations_url, shots_dir, "confirm")
                elif sid == "UI_09":
                    shot, auto, reason = _act_all_slots(
                        page, nominations_url, shots_dir, "reject")
                elif sid == "UI_10":
                    shot, auto, reason = _bulk_week(
                        page, nominations_url, shots_dir, "confirm")
                elif sid == "UI_11":
                    shot, auto, reason = _bulk_week(
                        page, nominations_url, shots_dir, "reject")
                elif sid == "UI_12":
                    shot, auto, reason = _add_slot_ui(page, nominations_url, shots_dir)
                else:
                    shot, auto, reason = "", "?", "Unknown scenario ID"

                print(f"  Screenshot: {shot}")
                result, notes = _ask(sc, auto, reason)

            except Exception as exc:
                print(f"  ERROR: {exc}")
                shot, result, notes = "", "ERROR", str(exc)

            results.append({**sc, "result": result, "notes": notes, "screenshot": shot})

        ctx.close()

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*64}")
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}")
    print(f"{'='*64}")
    print(f"  {'':3} {'ID':<12} {'Exp':<5} {'Got':<7} Name")
    print("─" * 70)
    ok = fail = skip = 0
    for r in results:
        match  = r["expect"] == "?" or r["result"] == r["expect"]
        flag   = "   " if match else "!! "
        if   r["result"] == "ERROR": skip += 1
        elif r["result"] == "PASS":  ok   += 1
        else:                        fail += 1
        print(f"  {flag}{r['n']:>2}. {r['id']:<12} {r['expect']:<5} "
              f"{r['result']:<7} {r['name']}")
    print("─" * 70)
    print(f"  PASS: {ok}  FAIL: {fail}  ERROR/SKIP: {skip}\n")

    out = run_dir / "test_results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=[
            "n", "id", "group", "name", "expect", "result",
            "notes", "screenshot", "desc"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"  Results    : {out}")
    print(f"  Screenshots: {run_dir}\n")

    excel_rows = [(excel_id, r["result"], r.get("notes", ""))
                  for r in results for excel_id in _excel_ids_for(r["id"])]
    record_results(excel_rows, xlsx_path=args.excel, source="test_weekly_ui.py")


if __name__ == "__main__":
    main()
