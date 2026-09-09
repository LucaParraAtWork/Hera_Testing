#!/usr/bin/env python3
"""
test_e2e_maintenance_profiles.py
=================================
End-to-end prerequisite setup + single profile-compliance test.

The script has two phases:

  SETUP (not a test — just automation, no result tracking)
  ────────────────────────────────────────────────────────
  Step 1  Import weekly nominations CSVs (one per offtaker)
  Step 2  Confirm Week (bulk)
  Step 3  Add High Pressure Compressor maintenance — Tuesday 00:00→23:59
  Step 4  Add Stack 1 maintenance               — Thursday 00:00→23:59
  Step 5  Add Loading Bay maintenance            — whole week Mon 00:00→Sun 23:59

  TESTS (the only things being verified)
  ────────────────────────────────────────
  E2E_MAINT_PROF
      Navigate to /schedules/profiles, capture ProfilesScreen API.
      Navigate to /schedules/maintenance, capture MaintenanceScreen API.
      For every 15-min timestamp: assert
          power_consumption (MW)  ≤  nominal_capacity (converted from kW → MW)
      Report per-window breakdown:
        • Tuesday  (HPC)     → cap = 12.5 MW
        • Thursday (Stack 1) → cap = 0 MW
        • Rest of week       → cap = 25 MW

  E2E_LOADING_BAY
      Using the already-captured ProfilesScreen data, find the Truck Filling
      graph and verify that the curve for the maintained loading bay is 0
      at every timestamp across the whole week.

Week selection
  The target week is derived automatically from the first CSV filename:
    "Virya_Nomination_Week28.csv"  →  week 28
    "Messer_Nomination_Week20.csv" →  week 20
  The year defaults to the current calendar year.  Pass --year to override.

CSV resolution
  Each --csv value is searched in this order:
    1. Absolute path as given
    2. UAT Testing/ root directory (this script's parent folder)
    3. EndToEndPlannedMaintenance/ folder (same folder as this script)
    4. Weekly Nomination/ subfolder of UAT Testing/ root

Run:
  # Full run with two CSVs (one per offtaker):
  python "EndToEndPlannedMaintenance/test_e2e_maintenance_profiles.py" \\
         --csv "Messer_Nomination_Week28.csv" "Virya_Nomination_Week28.csv"

  # Setup already done → jump straight to the profile compliance test:
  python "EndToEndPlannedMaintenance/test_e2e_maintenance_profiles.py" \\
         --csv "Messer_Nomination_Week28.csv" "Virya_Nomination_Week28.csv" --skip-setup

  # Resume setup from step 3 (maintenances not yet added) then run test:
  python "EndToEndPlannedMaintenance/test_e2e_maintenance_profiles.py" \\
         --csv "Messer_Nomination_Week28.csv" "Virya_Nomination_Week28.csv" --from 3

  # Explicit year (e.g. week 52 CSV tested in January):
  python "EndToEndPlannedMaintenance/test_e2e_maintenance_profiles.py" \\
         --csv "Messer_Nomination_Week52.csv" "Virya_Nomination_Week52.csv" \\
         --year 2026 --skip-setup

Arguments:
  --csv PATH [PATH ...]  One or more nomination CSVs (week derived from first filename)
  --env NAME             "dev" or "test"  (default: ask at startup)
  --year N               Year for the target week  (default: current calendar year)
  --from N               Start setup from step N (1–5).  Default: 1.
  --skip-setup           Skip all setup steps; go directly to the profile test.
"""

# ===========================================================================
# ENVIRONMENT
# ===========================================================================
ENV = ""

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

# ===========================================================================
# TIMING
# ===========================================================================
WAIT_S = 0.3
WAIT_M = 0.8
WAIT_L = 1.8
SLOWMO = 0

# ===========================================================================
# UTC OFFSET  (Brussels CEST = UTC+2 in summer)
# ===========================================================================
UTC_OFFSET_H = 2

# Loading bay kept in maintenance for the entire week under test
LOADING_BAY = "Loading Bay 1"

# ===========================================================================
# TEST SCENARIOS
# ===========================================================================
SCENARIOS = [
    {"n": 1, "id": "E2E_MAINT_01", "group": "maintenance",
     "name": "Power consumption ≤ nominal capacity (HPC Tuesday, Stack Thursday)",
     "desc": "For every 15-min timestamp: profiles power (MW) ≤ maintenance nominal cap (kW÷1000)",
     "expect": "PASS"},
    {"n": 2, "id": "E2E_MAINT_02", "group": "maintenance",
     "name": f"{LOADING_BAY} — truck filling = 0 throughout maintenance week",
     "desc": f"After adding {LOADING_BAY} maintenance for the whole week, "
             "verify the Truck Filling curve for that bay is 0 at every timestamp",
     "expect": "PASS"},
]

# ===========================================================================
# CAPACITY THRESHOLDS (MW)  — both profiles and maintenance APIs use MW
# ===========================================================================
CAP_FULL  = 25.0   # no maintenance
CAP_HALF  = 12.5   # HPC or LPC in maintenance
CAP_ZERO  =  0.0   # Electrolyser Stack in maintenance

# Floating-point tolerance when comparing power vs. nominal cap (MW)
TOLERANCE_MW = 0.001

# ===========================================================================
# IMPORTS
# ===========================================================================
import argparse
import csv as _csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
ROOT = HERE.parent   # UAT Testing root


# ===========================================================================
# TIMING HELPERS
# ===========================================================================
def _ws(): time.sleep(WAIT_S)
def _wm(): time.sleep(WAIT_M)
def _wl(): time.sleep(WAIT_L)

def _quiet(page, ms: int = 8_000):
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass
    for sel in (".spinner", ".loading", ".busy", ".progress", "app-loading",
                ".ngx-spinner-overlay", ".skeleton",
                ".mat-progress-spinner", ".mat-progress-bar"):
        try:
            page.wait_for_selector(sel, state="hidden", timeout=2_000)
        except Exception:
            pass


# ===========================================================================
# API RESPONSE CAPTURE
# ===========================================================================
_CAPTURED: dict = {}

def _make_handler(captures: list[tuple[str, str]]):
    """Always overwrites so the post-navigation (target-week) response wins."""
    def _h(response):
        try:
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            for key, pattern in captures:
                if pattern in response.url:
                    try:
                        _CAPTURED[key] = response.json()
                        print(f"  [CAPTURE] {key} ← {response.url.split('/')[-1].split('?')[0]}")
                    except Exception:
                        pass
        except Exception:
            pass
    return _h

def _inner(body):
    """Unwrap DataCapturer {url,method,data} wrapper if present."""
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        return body["data"]
    return body


# ===========================================================================
# FILENAME HELPERS
# ===========================================================================
def _derive_offtaker(csv_path: str) -> str:
    name = Path(csv_path).name.lower()
    if "messer" in name:
        return "Messer Belgium NV"
    if "virya" in name:
        return "Virya Energy NV"
    return "Messer Belgium NV"

def _derive_week(csv_path: str) -> int | None:
    m = re.search(r"[Ww](?:eek)?\s*_?(\d{1,2})", Path(csv_path).name)
    return int(m.group(1)) if m else None

def _monday_of_week(year: int, week: int) -> datetime:
    return datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u")


# ===========================================================================
# WEEK NAVIGATION
# ===========================================================================
def _current_week(page) -> int | None:
    try:
        _quiet(page, 4_000)
        txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2_000)
        m = re.search(r"Week\s*(\d{1,2})", txt, re.I)
        return int(m.group(1)) if m else None
    except Exception:
        return None

def _goto_week(page, target_week: int, max_clicks: int = 80):
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception:
        pass
    _wm()
    for _ in range(max_clicks):
        wk = _current_week(page)
        if wk == target_week:
            _quiet(page)
            return
        if wk is None:
            _wm(); continue
        diff = target_week - wk
        if diff > 26:  diff -= 52
        elif diff < -26: diff += 52
        btn = ("div.date-picker-next > button" if diff > 0
               else "div.date-picker-previous > button")
        try:
            page.locator(btn).first.click()
        except Exception:
            pass
        _ws()
    raise RuntimeError(f"Could not reach Week {target_week} after {max_clicks} clicks")


# ===========================================================================
# CLICK / DIALOG HELPERS
# ===========================================================================
def _safe_click(page, locator, what: str = "element"):
    for attempt in ("normal", "force", "mouse", "js"):
        try:
            if attempt == "normal":
                locator.click(timeout=2_000)
            elif attempt == "force":
                locator.scroll_into_view_if_needed(timeout=1_500)
                locator.click(timeout=1_500, force=True)
            elif attempt == "mouse":
                bb = locator.bounding_box(timeout=1_200)
                if not bb: continue
                page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                page.mouse.down(); page.mouse.up()
            elif attempt == "js":
                h = locator.element_handle(timeout=5_000)
                if not h: continue
                page.evaluate("el => { el.click(); el.dispatchEvent(new Event('click',{bubbles:true})); }", h)
            _ws()
            return
        except Exception:
            continue
    raise RuntimeError(f"Could not click {what}")

def _get_dialog(page):
    _wm()
    modal = page.locator("dialog[open]").last
    modal.wait_for(state="visible", timeout=10_000)
    _ws()
    return modal


# ===========================================================================
# ENVIRONMENT RESOLVER
# ===========================================================================
def _resolve_env(env_arg: str) -> tuple[str, str]:
    env = (env_arg or ENV).strip().lower()
    if env not in ENVIRONMENTS:
        print("\nSelect environment:")
        for i, key in enumerate(ENVIRONMENTS, 1):
            print(f"  {i}. {key}  ({ENVIRONMENTS[key]})")
        while True:
            choice = input("Enter 1 or 2: ").strip()
            if choice == "1": env = "dev"; break
            if choice == "2": env = "test"; break
    return env, ENVIRONMENTS[env]


# ===========================================================================
# SETUP STEPS  (automation only — no test result tracking)
# ===========================================================================

def _setup_import(page, base_url: str, csv_file: Path, offtaker: str,
                  target_week: int | None, shots_dir: Path,
                  idx: int = 1, total: int = 1):
    """Step 1 — Import one nominations CSV for the given offtaker."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    sub = f" ({idx}/{total})" if total > 1 else ""
    print(f"  [Setup 1/5] Importing {offtaker} CSV{sub}…")
    page.goto(f"{base_url}/nominations/weekly", wait_until="domcontentloaded")
    _wm(); _quiet(page)
    page.screenshot(path=str(shots_dir / "s1_01_landing.png"))

    try:
        _safe_click(page,
            page.get_by_role("button", name=re.compile("^Import Nominations Request$", re.I)),
            "Import Nominations Request")
    except Exception:
        _safe_click(page,
            page.locator("text=Import Nominations Request").first,
            "Import Nominations Request fallback")

    modal = _get_dialog(page)
    page.screenshot(path=str(shots_dir / f"s1_{idx:02d}_a_modal.png"))
    try:
        modal.locator("select#offtaker").select_option(label=offtaker)
    except Exception:
        modal.locator("select[name='offtaker']").select_option(label=offtaker)
    _ws()

    modal.locator("input#nominationFile").set_input_files(str(csv_file))
    _ws()
    page.screenshot(path=str(shots_dir / f"s1_{idx:02d}_b_ready.png"))

    _safe_click(page, modal.get_by_role("button", name=re.compile("^Upload$", re.I)), "Upload")
    try:
        modal.wait_for(state="detached", timeout=15_000)
    except Exception:
        pass
    _quiet(page)

    if target_week is not None:
        _goto_week(page, target_week)
    _wm()
    page.screenshot(path=str(shots_dir / f"s1_{idx:02d}_c_done.png"))
    sub = f" ({idx}/{total})" if total > 1 else ""
    print(f"  [Setup 1/5] Done{sub} — '{csv_file.name}' imported for {offtaker}.")


def _setup_confirm_week(page, base_url: str, target_week: int | None, shots_dir: Path):
    """Step 2 — Bulk-confirm all NEW slots."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    print("  [Setup 2/5] Confirming week (bulk)…")
    page.goto(f"{base_url}/nominations/weekly", wait_until="domcontentloaded")
    _wm()
    if target_week is not None:
        _goto_week(page, target_week)

    btn = page.get_by_role("button", name=re.compile(r"Confirm Week", re.I)).first
    if not (btn.is_visible() and not btn.is_disabled()):
        print("  [Setup 2/5] WARN — 'Confirm Week' button not visible/disabled. Skipping.")
        return

    _safe_click(page, btn, "Confirm Week")
    cmodal = _get_dialog(page)
    page.screenshot(path=str(shots_dir / "s2_01_modal.png"))
    _safe_click(page, cmodal.get_by_role("button", name=re.compile("^Confirm$", re.I)), "Confirm")
    try:
        cmodal.wait_for(state="detached", timeout=10_000)
    except Exception:
        pass
    _wl(); _quiet(page)
    page.screenshot(path=str(shots_dir / "s2_02_done.png"))
    print(f"  [Setup 2/5] Done — week {target_week} confirmed.")


def _setup_add_maintenance(page, base_url: str, asset: str,
                           start_dt: datetime, end_dt: datetime,
                           target_week: int | None,
                           step_n: int, shots_dir: Path, total: int = 5):
    """Steps 3–5 — Add one maintenance block."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M")
    end_str   = end_dt.strftime("%Y-%m-%dT%H:%M")
    print(f"  [Setup {step_n}/{total}] Adding maintenance: {asset}  {start_str} → {end_str}…")

    page.goto(f"{base_url}/schedules/maintenance", wait_until="domcontentloaded")
    _wm()
    if target_week is not None:
        _goto_week(page, target_week)
    _quiet(page)
    page.screenshot(path=str(shots_dir / f"s{step_n}_01_land.png"))

    _safe_click(page,
        page.get_by_role("button", name=re.compile("^Add Maintenance$", re.I)),
        "Add Maintenance")
    modal = _get_dialog(page)

    try:
        modal.locator("select").first.select_option(label=asset)
    except Exception:
        modal.locator("text=- Select -").first.click(); _ws()
        modal.locator(f"text={asset}").last.click()
    _ws()

    try:
        modal.locator("input[type='datetime-local']").nth(0).fill(start_str)
        modal.locator("input[type='datetime-local']").nth(1).fill(end_str)
    except Exception:
        modal.locator("input").nth(0).fill(start_str)
        modal.locator("input").nth(1).fill(end_str)
    _ws()
    page.screenshot(path=str(shots_dir / f"s{step_n}_02_ready.png"))

    _safe_click(page, modal.get_by_role("button", name=re.compile("^Create$", re.I)), "Create")
    try:
        modal.wait_for(state="detached", timeout=10_000)
    except Exception:
        pass
    _quiet(page)
    page.screenshot(path=str(shots_dir / f"s{step_n}_03_done.png"))
    print(f"  [Setup {step_n}/{total}] Done — maintenance created.")


# ===========================================================================
# THE TEST  — Profile compliance check
# ===========================================================================

def _test_profiles_comply_with_maintenance(
        page, base_url: str, target_week: int | None,
        shots_dir: Path,
        hpc_window: tuple[datetime, datetime],
        stack_window: tuple[datetime, datetime]) -> tuple[str, str]:
    """
    Capture ProfilesScreen + MaintenanceScreen APIs for target_week.
    Validate: power_consumption (MW) × 1000  ≤  nominal_capacity (kW)
    at every 15-min timestamp in the week.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Capture ProfilesScreen/GetProfilesScreenAsync
    # ------------------------------------------------------------------
    handler_prof = _make_handler([("profiles", "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler_prof)
    try:
        page.goto(f"{base_url}/schedules/profiles", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "01_profiles_landed.png"))

        if target_week is not None:
            try:
                _goto_week(page, target_week)
            except Exception as e:
                return "FAIL", f"Profiles week navigation failed: {e}"
        _quiet(page)
        page.screenshot(path=str(shots_dir / "02_profiles_week.png"), full_page=True)

        # Stay on MW — maintenance capacity is also in MW, no toggle needed.
        _wl(); _quiet(page)
        page.screenshot(path=str(shots_dir / "03_profiles_mw.png"), full_page=True)

        if "profiles" not in _CAPTURED:
            return "FAIL", "ProfilesScreen API response was not captured"

        (shots_dir / "profiles_data.json").write_text(
            json.dumps(_CAPTURED["profiles"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler_prof)
        except Exception: pass

    # ------------------------------------------------------------------
    # Capture MaintenanceScreen/GetMaintenanceScreenAsync
    # ------------------------------------------------------------------
    handler_maint = _make_handler([("maintenance", "MaintenanceScreen/GetMaintenanceScreenAsync")])
    page.on("response", handler_maint)
    try:
        page.goto(f"{base_url}/schedules/maintenance", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        if target_week is not None:
            try:
                _goto_week(page, target_week)
            except Exception as e:
                print(f"  [WARN] Maintenance week navigation failed: {e}")
        _quiet(page)
        page.screenshot(path=str(shots_dir / "04_maintenance_week.png"), full_page=True)

        if "maintenance" not in _CAPTURED:
            return "FAIL", "MaintenanceScreen API response was not captured"

        (shots_dir / "maintenance_data.json").write_text(
            json.dumps(_CAPTURED["maintenance"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler_maint)
        except Exception: pass

    # ------------------------------------------------------------------
    # Extract nominal capacity per timestamp  (kW)
    # ------------------------------------------------------------------
    maint_body = _inner(_CAPTURED["maintenance"])
    try:
        raw_pts: dict[str, float] = (
            maint_body["plantCapacityProfile"]["profileGraphLines"][0]["points"]
        )
        # API returns kW (25 000 / 12 500 / 0) — convert to MW for comparison
        # with the profiles screen which uses MW.
        nominal_pts: dict[str, float] = {ts: float(v) / 1_000 for ts, v in raw_pts.items()}
        print(f"  Nominal capacity: {len(nominal_pts)} timestamps  "
              f"(range: {min(nominal_pts.values()):.1f}–{max(nominal_pts.values()):.1f} MW)")
    except (KeyError, IndexError, TypeError) as exc:
        return "FAIL", f"Cannot read plantCapacityProfile from maintenance API: {exc}"

    # ------------------------------------------------------------------
    # Extract "Power consumption" per timestamp  (MW → converted to kW)
    # ------------------------------------------------------------------
    prof_body  = _inner(_CAPTURED["profiles"])
    graphs     = prof_body.get("profileGraphs", [])
    power_graph = next(
        (g for g in graphs if g.get("title", "").lower() == "power consumption"),
        None)

    if power_graph is None:
        titles = [g.get("title") for g in graphs]
        return "FAIL", (
            f"'Power consumption' graph not found in ProfilesScreen response. "
            f"Available graphs: {titles}"
        )

    # Sum all lines within the graph (should be a single line, but sum for safety)
    power_mw: dict[str, float] = {}
    for line in power_graph.get("profileGraphLines", []):
        for ts, val in line.get("points", {}).items():
            power_mw[ts] = power_mw.get(ts, 0.0) + float(val)

    print(f"  Power consumption: {len(power_mw)} timestamps")
    (shots_dir / "power_data.json").write_text(
        json.dumps(power_mw, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Validate: power_kw ≤ nominal_kw  at every shared timestamp
    # ------------------------------------------------------------------
    violations: list[dict] = []
    checked = 0

    for ts in sorted(nominal_pts.keys()):
        if ts not in power_mw:
            continue
        nominal_mw = float(nominal_pts[ts])
        power_val  = float(power_mw[ts])    # already MW
        checked   += 1
        if power_val > nominal_mw + TOLERANCE_MW:
            violations.append({
                "ts": ts,
                "power_mw":   round(power_val, 4),
                "nominal_mw": round(nominal_mw, 4),
                "excess_mw":  round(power_val - nominal_mw, 4),
            })

    # ------------------------------------------------------------------
    # Per-window breakdown  (printed + saved)
    # ------------------------------------------------------------------
    _print_window_breakdown(
        shots_dir, nominal_pts, power_mw, hpc_window, stack_window)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------
    if not violations:
        cap_summary = ", ".join(
            f"{mw} MW × {n}"
            for mw, n in sorted(Counter(round(float(v), 4) for v in nominal_pts.values()).items()))
        return "PASS", (
            f"All {checked} timestamps pass — power consumption ≤ nominal capacity. "
            f"Nominal caps in week: {cap_summary}."
        )

    all_sorted = sorted(violations, key=lambda v: v["excess_mw"], reverse=True)
    lines = [f"{len(violations)} violation(s) out of {checked} timestamps (sorted by excess):"]
    for i, v in enumerate(all_sorted, 1):
        lines.append(
            f"  {i:>3}.  {v['ts']}  "
            f"power={v['power_mw']:.4f} MW  "
            f"cap={v['nominal_mw']:.4f} MW  "
            f"excess=+{v['excess_mw']:.4f} MW"
        )
    detail = "\n".join(lines)
    print(f"\n{detail}\n")
    (shots_dir / "violations.txt").write_text(detail, encoding="utf-8")
    return "FAIL", f"{len(violations)} violation(s) out of {checked} timestamps — see violations.txt"


def _print_window_breakdown(shots_dir: Path, nominal_pts: dict, power_mw: dict,
                             hpc_window: tuple[datetime, datetime],
                             stack_window: tuple[datetime, datetime]):
    """Print and save a per-maintenance-window summary table."""
    windows = [
        ("HPC maintenance  → cap 12.5 MW", hpc_window[0],   hpc_window[1],   CAP_HALF),
        ("Stack 1 maint.   → cap   0 MW",  stack_window[0], stack_window[1], CAP_ZERO),
    ]
    lines = ["Power consumption vs. Nominal Capacity — per maintenance window (MW)",
             "=" * 72]

    for label, w_start, w_end, expected_cap in windows:
        # Convert local window boundaries to UTC keys for comparison
        w_start_utc = (w_start - timedelta(hours=UTC_OFFSET_H)).replace(tzinfo=None)
        w_end_utc   = (w_end   - timedelta(hours=UTC_OFFSET_H)).replace(tzinfo=None)
        w_start_key = w_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        w_end_key   = w_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        in_nominal = {ts: float(v) for ts, v in nominal_pts.items()
                      if w_start_key <= ts <= w_end_key}
        in_power   = {ts: float(power_mw[ts]) for ts in in_nominal if ts in power_mw}

        max_pw  = max(in_power.values(), default=0.0)
        mean_pw = sum(in_power.values()) / len(in_power) if in_power else 0.0
        viols   = sum(1 for v in in_power.values() if v > expected_cap + TOLERANCE_MW)
        actual_caps = Counter(round(float(v), 4) for v in in_nominal.values())

        lines += [
            "",
            f"  {label}",
            f"  Window (local) : {w_start:%Y-%m-%d %H:%M} → {w_end:%H:%M}",
            f"  Timestamps     : {len(in_nominal)} nominal  /  {len(in_power)} power",
            f"  Nominal caps   : " +
                ", ".join(f"{k} MW × {n}" for k, n in sorted(actual_caps.items())),
            f"  Max power      : {max_pw:.4f} MW   (mean: {mean_pw:.4f} MW)",
            f"  Expected cap   : {expected_cap} MW",
            f"  Violations     : {viols}  {'✓' if viols == 0 else '✗'}",
        ]

    report = "\n".join(lines)
    print()
    for l in lines:
        print(f"  {l}")
    (shots_dir / "window_breakdown.txt").write_text(report, encoding="utf-8")


# ===========================================================================
# TEST 2 — Loading bay zeroed out in Truck Filling profiles
# ===========================================================================

def _test_loading_bay_zero(page, base_url: str, target_week: int | None,
                           shots_dir: Path, bay_name: str) -> tuple[str, str]:
    """
    Re-navigate to profiles (after loading bay maintenance was added) and verify
    that the Truck Filling graph line for bay_name is 0 at every timestamp.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)

    handler = _make_handler([("profiles_bay", "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/profiles", wait_until="domcontentloaded")
        _wl(); _quiet(page)
        if target_week is not None:
            try:
                _goto_week(page, target_week)
            except Exception as e:
                return "FAIL", f"Profiles week navigation failed: {e}"
        _wl(); _quiet(page, ms=12_000)
        page.screenshot(path=str(shots_dir / "01_profiles.png"), full_page=True)

        if "profiles_bay" not in _CAPTURED:
            return "FAIL", "ProfilesScreen API response was not captured"

        (shots_dir / "profiles_bay_data.json").write_text(
            json.dumps(_CAPTURED["profiles_bay"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

    prof_body = _inner(_CAPTURED["profiles_bay"])
    graphs    = prof_body.get("profileGraphs", [])

    # Locate the Truck Filling graph (case-insensitive)
    truck_graph = next(
        (g for g in graphs if "truck" in g.get("title", "").lower()), None)

    if truck_graph is None:
        titles = [g.get("title") for g in graphs]
        return "FAIL", (
            f"Truck Filling graph not found in ProfilesScreen response. "
            f"Available graphs: {titles}"
        )

    # Find the line(s) matching bay_name  (API uses "name", not "title")
    def _line_label(line: dict) -> str:
        return (line.get("name") or line.get("title") or "").strip()

    bay_lines = [
        line for line in truck_graph.get("profileGraphLines", [])
        if bay_name.lower() in _line_label(line).lower()
    ]

    if not bay_lines:
        all_names = [_line_label(l) for l in truck_graph.get("profileGraphLines", [])]
        return "FAIL", (
            f"No line found for '{bay_name}' in graph '{truck_graph.get('title')}'. "
            f"Available lines: {all_names}"
        )

    # Check every point is 0
    violations: list[dict] = []
    checked = 0
    for line in bay_lines:
        for ts, val in line.get("points", {}).items():
            checked += 1
            if abs(float(val)) > TOLERANCE_MW:
                violations.append({
                    "ts":    ts,
                    "value": round(float(val), 6),
                    "line":  _line_label(line) or bay_name,
                })

    if not violations:
        return "PASS", (
            f"All {checked} timestamps are 0 for '{bay_name}' in "
            f"'{truck_graph.get('title')}' — loading bay correctly zeroed during maintenance."
        )

    viol_sorted = sorted(violations, key=lambda v: abs(v["value"]), reverse=True)
    lines_out = [
        f"{len(violations)} non-zero point(s) for '{bay_name}' "
        f"in '{truck_graph.get('title')}' (should all be 0):"
    ]
    for i, v in enumerate(viol_sorted, 1):
        lines_out.append(f"  {i:>3}.  {v['ts']}  value={v['value']}")
    detail = "\n".join(lines_out)
    print(f"\n{detail}\n")
    (shots_dir / "bay_violations.txt").write_text(detail, encoding="utf-8")
    return "FAIL", (
        f"{len(violations)} non-zero timestamp(s) for '{bay_name}' "
        f"out of {checked} — see bay_violations.txt"
    )


# ===========================================================================
# CONFIRM PROMPT  (same pattern as other Hera test scripts)
# ===========================================================================
def _ask(scenario: dict, auto: str, reason: str) -> tuple[str, str]:
    label    = "[expected PASS]" if scenario["expect"] == "PASS" else "[observe]"
    aflag    = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL"}.get(auto, f"AUTO-{auto}")
    mismatch = scenario["expect"] not in ("?",) and auto != scenario["expect"]
    flag     = " !! MISMATCH" if mismatch else " OK"

    print(f"\n  {scenario['id']}  {label}  –  {scenario['name']}")
    print(f"  Auto-detected : {aflag}{flag}")
    if reason:
        print(f"  Reason        : {reason}")
    default = scenario["expect"] if scenario["expect"] in ("PASS", "FAIL") else "PASS"
    print(f"  Confirm? (P=pass / F=fail / Enter={default}): ", end="", flush=True)
    try:
        raw = input().strip().upper()
    except EOFError:
        raw = ""
    result = {"P": "PASS", "F": "FAIL"}.get(raw, auto if auto in ("PASS", "FAIL") else default)
    notes  = ""
    try:
        notes = input("  Notes (optional): ").strip()
    except EOFError:
        pass
    return result, notes


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    _CAPTURED.clear()

    parser = argparse.ArgumentParser(
        description="E2E maintenance profiles compliance test")
    parser.add_argument("--csv", nargs="+", default=[],
                        help="One or more nomination CSVs (one per offtaker). "
                             "Week number is derived from the first filename "
                             "(e.g. Virya_Nomination_Week28.csv → week 28).")
    parser.add_argument("--env", default=ENV, help="'dev' or 'test'")
    parser.add_argument("--year", dest="year", type=int, default=0,
                        help="Year for the target week (default: current calendar year).")
    parser.add_argument("--from", dest="start_from", type=int, default=1, metavar="N",
                        help="Start setup from step N (1–4). Default: 1.")
    parser.add_argument("--skip-setup", action="store_true",
                        help="Skip all setup steps; go directly to the profile test.")
    args = parser.parse_args()

    env_name, base_url = _resolve_env(args.env)
    skip_setup = args.skip_setup
    start_from = max(1, args.start_from)

    # ------------------------------------------------------------------
    # Resolve CSV paths — accept absolute paths, ROOT-relative paths,
    # filenames in the EndToEndPlannedMaintenance folder (HERE), or
    # the Weekly Nomination/ subfolder.
    # ------------------------------------------------------------------
    def _find_csv(raw: str) -> Path | None:
        for candidate in (
            Path(raw),
            ROOT / raw,
            HERE / raw,
            ROOT / "Weekly Nomination" / raw,
        ):
            if candidate.exists():
                return candidate
        return None

    csv_files: list[Path] = []
    target_week: int | None = None

    if args.csv:
        for raw_csv in args.csv:
            found = _find_csv(raw_csv)
            if found is None:
                if not skip_setup:
                    print(f"ERROR: CSV not found: {raw_csv}")
                    sys.exit(1)
                print(f"  [WARN] CSV not found: {raw_csv} — skipping.")
            else:
                csv_files.append(found)
                if target_week is None:
                    target_week = _derive_week(str(found))
    elif not skip_setup:
        print("ERROR: --csv is required unless --skip-setup is given.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Determine year: explicit --year flag, else current calendar year.
    # No auto-bump — if week 26 is already past, the user explicitly ran
    # setup for week 26/current_year and wants to test that same week.
    # ------------------------------------------------------------------
    year = args.year if args.year else datetime.now().year

    # Compute maintenance windows
    if target_week is not None:
        monday = _monday_of_week(year, target_week)
    else:
        now = datetime.now()
        days_to_mon = (7 - now.weekday()) % 7 or 7
        monday = (now + timedelta(days=days_to_mon)).replace(
            hour=0, minute=0, second=0, microsecond=0)

    tuesday  = monday + timedelta(days=1)
    thursday = monday + timedelta(days=3)
    sunday   = monday + timedelta(days=6)
    hpc_start = tuesday.replace(hour=0,  minute=0)
    hpc_end   = tuesday.replace(hour=23, minute=59)
    stk_start = thursday.replace(hour=0,  minute=0)
    stk_end   = thursday.replace(hour=23, minute=59)
    bay_start = monday.replace(hour=0,  minute=0)
    bay_end   = sunday.replace(hour=23, minute=59)

    artifacts = HERE / "test_artifacts"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*64}")
    print(f"  Hera E2E  –  Maintenance Impact on Profiles")
    print(f"  Environment  : {env_name}  ({base_url})")
    for cf in csv_files:
        print(f"  CSV          : {cf.name}  (offtaker: {_derive_offtaker(str(cf))})")
    print(f"  Target week  : W{target_week}/{year}")
    print(f"  HPC maint.   : Tuesday   {hpc_start:%Y-%m-%d}  → 12.5 MW")
    print(f"  Stack maint. : Thursday  {stk_start:%Y-%m-%d}  → 0 MW")
    print(f"  Bay maint.   : {LOADING_BAY}  {bay_start:%Y-%m-%d} → {bay_end:%Y-%m-%d}  (whole week)")
    if skip_setup:
        print(f"  Setup        : SKIPPED")
    else:
        print(f"  Setup from   : step {start_from}")
    print(f"  Run ID       : {run_id}")
    print(f"{'='*64}\n")

    with sync_playwright() as pw:
        session_dir = HERE / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir), headless=False, slow_mo=SLOWMO,
            viewport={"width": 1500, "height": 900}
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("  Opening Hera…")
        try:
            page.goto(f"{base_url}/nominations/weekly",
                      wait_until="commit", timeout=20_000)
        except Exception:
            pass

        print()
        print("  ┌──────────────────────────────────────────────────────────┐")
        print("  │  Log in with MFA if prompted in the browser.             │")
        print("  │  Press Enter here once you see the Hera app.             │")
        print("  └──────────────────────────────────────────────────────────┘")
        input("  > ")
        _quiet(page)
        print("  Login confirmed.\n")

        # ---------------------------------------------------------------
        # SETUP PHASE  (no result tracking)
        # ---------------------------------------------------------------
        if not skip_setup:
            setup_dir = run_dir / "setup"
            if start_from <= 1 and csv_files:
                for i, cf in enumerate(csv_files, 1):
                    _setup_import(page, base_url, cf, _derive_offtaker(str(cf)),
                                  target_week, setup_dir,
                                  idx=i, total=len(csv_files))
            if start_from <= 2:
                _setup_confirm_week(page, base_url, target_week, setup_dir)
            if start_from <= 3:
                _setup_add_maintenance(page, base_url,
                                       "High Pressure Compressor",
                                       hpc_start, hpc_end,
                                       target_week, 3, setup_dir)
            if start_from <= 4:
                _setup_add_maintenance(page, base_url, "Stack 1",
                                       stk_start, stk_end,
                                       target_week, 4, setup_dir)
            print()

        # ---------------------------------------------------------------
        # TEST 1  –  E2E_MAINT_01
        # ---------------------------------------------------------------
        sc1 = SCENARIOS[0]
        test1_dir = run_dir / sc1["id"]
        print(f"\n{'═'*64}")
        print(f"  [{sc1['n']}/{len(SCENARIOS)}]  {sc1['id']}  –  {sc1['name']}")
        print(f"  Power consumption (MW)  ≤  nominal capacity (kW ÷ 1000)")
        print(f"{'═'*64}")

        try:
            auto1, reason1 = _test_profiles_comply_with_maintenance(
                page, base_url, target_week, test1_dir,
                hpc_window=(hpc_start, hpc_end),
                stack_window=(stk_start, stk_end))
        except Exception as exc:
            import traceback; traceback.print_exc()
            auto1, reason1 = "FAIL", f"Unexpected error: {exc}"

        result1, notes1 = _ask(sc1, auto1, reason1)

        # ---------------------------------------------------------------
        # INTERMEDIATE SETUP  –  step 5: add loading bay maintenance
        # ---------------------------------------------------------------
        if not skip_setup or start_from <= 5:
            inter_dir = run_dir / "setup_step5"
            print(f"\n{'─'*64}")
            print(f"  INTERMEDIATE SETUP — adding {LOADING_BAY} maintenance (whole week)")
            print(f"{'─'*64}")
            _setup_add_maintenance(page, base_url, LOADING_BAY,
                                   bay_start, bay_end,
                                   target_week, 5, inter_dir)
            print()

        # ---------------------------------------------------------------
        # TEST 2  –  E2E_MAINT_02
        # ---------------------------------------------------------------
        sc2 = SCENARIOS[1]
        test2_dir = run_dir / sc2["id"]
        print(f"\n{'═'*64}")
        print(f"  [{sc2['n']}/{len(SCENARIOS)}]  {sc2['id']}  –  {sc2['name']}")
        print(f"  Truck Filling curve for {LOADING_BAY} must be 0 at every timestamp")
        print(f"{'═'*64}")

        try:
            auto2, reason2 = _test_loading_bay_zero(
                page, base_url, target_week, test2_dir, LOADING_BAY)
        except Exception as exc:
            import traceback; traceback.print_exc()
            auto2, reason2 = "FAIL", f"Unexpected error: {exc}"

        result2, notes2 = _ask(sc2, auto2, reason2)

        ctx.close()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    all_results = [
        (SCENARIOS[0], result1, reason1, notes1),
        (SCENARIOS[1], result2, reason2, notes2),
    ]
    print(f"\n{'='*64}")
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}")
    print(f"{'='*64}")
    print(f"  {'ID':<18} {'Expect':<7} {'Result':<7} Description")
    print(f"  {'─'*60}")
    for sc, res, _, _ in all_results:
        mark = "✓" if res == sc["expect"] else "✗"
        print(f"  {mark} {sc['id']:<17} {sc['expect']:<7} {res:<7} {sc['name']}")
    print(f"\n  Run dir :  {run_dir}")
    print(f"{'='*64}\n")

    out = run_dir / "test_results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["id", "name", "expect", "result", "notes"])
        for sc, res, _, notes in all_results:
            w.writerow([sc["id"], sc["name"], sc["expect"], res, notes])


if __name__ == "__main__":
    main()
