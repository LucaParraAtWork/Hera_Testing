#!/usr/bin/env python3
"""
test_future_only_business_rules.py
======================================
Business-rule checks for the "scheduled things" screens: scheduled activity
is inherently forward-looking, so validating computed values on *past*
periods is not meaningful (there is nothing left to schedule). This script
only checks the FUTURE-ONLY business rule itself — it does not repeat the
value-level checks already covered by the other test scripts.

Scope — screens found under "scheduled things"
------------------------------------------------
  1. NCC / Costs Breakdown   /schedules/costs-breakdown   navigation: DAY-by-day
  2. Bay Occupancy           /schedules/occupancy         navigation: WEEK-by-week
  3. Profiles                /schedules/profiles          navigation: WEEK-by-week

  (Value-level checks for these screens already live in
   "NetCommodityCost/test_ncc_costs_breakdown.py" and
   "Scheduled Transfers/test_scheduled_transfers_profiles.py". This script
   reuses their navigation/capture helpers rather than duplicating logic.)

Division of tasks (one pair of scenarios per screen)
-----------------------------------------------------
  For each screen:
    a) "_DEFAULT" scenario  — capture the screen and confirm the page lands
       on today-or-later by default (no screen should default a user into
       the past). This part is concrete and implemented below.
    b) "_PAST" scenario     — navigate one period backwards (past day/week)
       and capture the result. What "correct" behaviour means for a past
       period (blocked navigation? empty data? frozen actuals instead of a
       forecast?) is NOT yet decided with the business/product owner, so
       the verdict is left as TBD. The capture/navigation plumbing is
       implemented so the scenario can be run and manually judged (or
       finished later — search for "TBD" below).

Run
---
  python "Business Rules/test_future_only_business_rules.py"
  python "Business Rules/test_future_only_business_rules.py" --from 3
"""

# ==========================================================================
# ENVIRONMENT  — "dev" | "test" | "" (ask at startup)
# ==========================================================================
ENV = ""

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

START_FROM = 1

# ==========================================================================
# TIMING
# ==========================================================================
WAIT_S = 0.3
WAIT_M = 0.8
WAIT_L = 1.8

# How far back to step for the "_PAST" scenarios.
PAST_DAYS_BACK  = 7    # NCC: one week ago (definitely fully in the past)
PAST_WEEKS_BACK = 2    # Bay Occupancy / Profiles: two weeks ago

# ==========================================================================
# SCENARIOS
# ==========================================================================
SCENARIOS = [
    # ---- NCC / Costs Breakdown (day-by-day) --------------------------------
    {"n": 1, "id": "NCC_FUT_01", "screen": "NCC / Costs Breakdown", "expect": "PASS",
     "name": "Default landing day is today or later",
     "desc": ("Navigate to /schedules/costs-breakdown. Capture the date-picker's "
              "default day and the NCC forecast API response. "
              "PASS if the default day >= today (the screen never defaults into the past).")},

    {"n": 2, "id": "NCC_FUT_02", "screen": "NCC / Costs Breakdown", "expect": "?",
     "name": "Behaviour when navigating to a past day [TBD]",
     "desc": (f"Step the date-picker back {PAST_DAYS_BACK} day(s) into the past and capture "
              "the resulting API response + screenshot. "
              "TBD: expected behaviour for a past day is not yet defined with the business "
              "owner (options: navigation blocked / no forecast data returned / historical "
              "actuals shown instead of a forecast). Capture is provided for manual review "
              "until the rule is finalised.")},

    # ---- Bay Occupancy (week-by-week) --------------------------------------
    {"n": 3, "id": "OCC_FUT_01", "screen": "Bay Occupancy", "expect": "PASS",
     "name": "Default landing week is current week or later",
     "desc": ("Navigate to /schedules/occupancy. Capture the date-picker's default week and "
              "the BayOccupancyScreen API response. "
              "PASS if the default week's Monday >= the Monday of the current ISO week.")},

    {"n": 4, "id": "OCC_FUT_02", "screen": "Bay Occupancy", "expect": "?",
     "name": "Behaviour when navigating to a past week [TBD]",
     "desc": (f"Step the week picker back {PAST_WEEKS_BACK} week(s) into the past and capture "
              "the resulting API response + screenshot. "
              "TBD: expected behaviour for a past week is not yet defined with the business "
              "owner. Capture is provided for manual review until the rule is finalised.")},

    # ---- Profiles (week-by-week) --------------------------------------------
    {"n": 5, "id": "PROF_FUT_01", "screen": "Profiles", "expect": "PASS",
     "name": "Default landing week is current week or later",
     "desc": ("Navigate to /schedules/profiles. Capture the date-picker's default week and "
              "the ProfilesScreen API response. "
              "PASS if the default week's Monday >= the Monday of the current ISO week.")},

    {"n": 6, "id": "PROF_FUT_02", "screen": "Profiles", "expect": "?",
     "name": "Behaviour when navigating to a past week [TBD]",
     "desc": (f"Step the week picker back {PAST_WEEKS_BACK} week(s) into the past and capture "
              "the resulting API response + screenshot. "
              "TBD: expected behaviour for a past week is not yet defined with the business "
              "owner. Capture is provided for manual review until the rule is finalised.")},
]

# ==========================================================================
# Internals
# ==========================================================================
import argparse, json, re, sys, time, csv as _csv
from datetime import date, datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent
SEP  = "  " + "─" * 70

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
# Environment  (same pattern as the other scripts)
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
# JSON helper: handle both raw API bodies and wrapped { "data": {...} }
# --------------------------------------------------------------------------
def _inner(body):
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        return body["data"]
    return body

# --------------------------------------------------------------------------
# API response capture (same pattern as test_scheduled_transfers_profiles.py)
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

# --------------------------------------------------------------------------
# Date-picker navigation — DAY granularity (ported from
# NetCommodityCost/test_ncc_costs_breakdown.py)
# --------------------------------------------------------------------------
def _current_picker_text(page) -> str:
    selectors = [
        "div.date-picker-current > button",
        "[class*='date-picker'] button",
        "button[class*='date-label']",
        "span.date-label",
    ]
    for sel in selectors:
        try:
            txt = page.locator(sel).first.inner_text(timeout=2_000)
            if txt and txt.strip():
                return txt.strip()
        except Exception:
            pass
    return ""

def _parse_picker_date(text: str):
    if not text:
        return None
    cleaned = re.sub(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", text, flags=re.I)
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", cleaned).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    try:
        import pandas as pd
        return pd.to_datetime(cleaned, dayfirst=True).date()
    except Exception:
        return None

def _click_next_day(page):
    for sel in ("div.date-picker-next > button", "[class*='date-picker-next'] button"):
        try:
            page.locator(sel).first.click(timeout=3_000); time.sleep(WAIT_S); return
        except Exception:
            pass
    raise RuntimeError("Cannot find 'next day' button.")

def _click_prev_day(page):
    for sel in ("div.date-picker-previous > button", "[class*='date-picker-previous'] button"):
        try:
            page.locator(sel).first.click(timeout=3_000); time.sleep(WAIT_S); return
        except Exception:
            pass
    raise RuntimeError("Cannot find 'previous day' button.")

def _goto_day(page, target: date, max_clicks: int = 20) -> bool:
    try: page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception: pass
    _wm()
    for _ in range(max_clicks):
        current = _parse_picker_date(_current_picker_text(page))
        if current == target:
            return True
        if current is None:
            _wm(); continue
        _click_next_day(page) if current < target else _click_prev_day(page)
    return False

# --------------------------------------------------------------------------
# Date-picker navigation — WEEK granularity (ported from
# Scheduled Transfers/test_scheduled_transfers_profiles.py)
# --------------------------------------------------------------------------
def _current_week_year(page):
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
        wk, yr = _current_week_year(page)
        if wk == week and (yr is None or yr == year):
            return
        if wk is None: _wm(); continue
        curr_yr = yr if yr is not None else year
        diff    = (year - curr_yr) * 52 + (week - wk)
        if diff == 0: return
        btn = ("div.date-picker-next > button" if diff > 0
               else "div.date-picker-previous > button")
        try: page.locator(btn).first.click()
        except Exception: pass
        _ws()

def _week_monday(week: int, year: int) -> date:
    return date.fromisocalendar(year, week, 1)

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
        raw    = input(f"  OK?  (P=pass / F=fail / Enter={default}): ").strip().lower()
        result = ("FAIL" if raw in ("f", "fail", "n", "no")
                  else "PASS" if raw in ("p", "pass", "y", "yes", "o", "oui")
                  else default)
        notes  = input("  Notes (optional): ").strip()
        return result, notes
    except EOFError:
        return default, reason

# ==========================================================================
# Screen 1 — NCC / Costs Breakdown  (day-by-day)
# ==========================================================================
def _do_ncc_default_landing(page, base_url, shots_dir):
    """NCC_FUT_01 — default landing day must be today or later."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    handler = _make_handler([("ncc", "CostsBreakdownScreen/GetCostsBreakdownScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/costs-breakdown", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "01_ncc_default_landing.png"), full_page=True)

        landing_txt  = _current_picker_text(page)
        landing_date = _parse_picker_date(landing_txt)
        today        = date.today()

        if landing_date is None:
            return "FAIL", f"Could not read/parse the date-picker text ({landing_txt!r})"

        ok = landing_date >= today
        return ("PASS" if ok else "FAIL",
                f"Default landing day = {landing_date.isoformat()} "
                f"({'>=' if ok else '<'} today {today.isoformat()})")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass


def _do_ncc_past_day_tbd(page, base_url, shots_dir):
    """NCC_FUT_02 — TBD: navigate to a past day and capture for manual review."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    target = date.today() - timedelta(days=PAST_DAYS_BACK)

    handler = _make_handler([("ncc_past", "CostsBreakdownScreen/GetCostsBreakdownScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/costs-breakdown", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        reached = _goto_day(page, target)
        _quiet(page); page.wait_for_timeout(1500)
        page.screenshot(path=str(shots_dir / "01_ncc_past_day.png"), full_page=True)

        if "ncc_past" in _CAPTURED:
            (shots_dir / "ncc_past_day.json").write_text(
                json.dumps(_CAPTURED["ncc_past"], indent=2), encoding="utf-8")

        # TBD — no business rule has been agreed yet for past-day behaviour.
        # Once decided (blocked navigation / empty forecast / frozen actuals),
        # replace this stub with the real assertion.
        note = (f"Navigated to {target.isoformat()} (reached={reached}). "
                f"API {'captured' if 'ncc_past' in _CAPTURED else 'NOT captured'}. "
                f"TBD: expected past-day behaviour not yet defined — manual review required.")
        return "?", note
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# ==========================================================================
# Screen 2 — Bay Occupancy  (week-by-week)
# ==========================================================================
def _do_occupancy_default_landing(page, base_url, shots_dir):
    """OCC_FUT_01 — default landing week must be current week or later."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    handler = _make_handler([("occ", "BayOccupancyScreen/GetBayOccupancyScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/occupancy", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "01_occ_default_landing.png"), full_page=True)

        wk, yr = _current_week_year(page)
        if wk is None or yr is None:
            return "FAIL", f"Could not read the default week/year from the date-picker (week={wk}, year={yr})"

        landing_monday = _week_monday(wk, yr)
        today_monday   = _week_monday(*date.today().isocalendar()[:2])
        ok = landing_monday >= today_monday

        return ("PASS" if ok else "FAIL",
                f"Default landing week = {wk}/{yr} (Monday {landing_monday.isoformat()}) "
                f"{'>=' if ok else '<'} current week Monday {today_monday.isoformat()}")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass


def _do_occupancy_past_week_tbd(page, base_url, shots_dir):
    """OCC_FUT_02 — TBD: navigate to a past week and capture for manual review."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    iso = (date.today() - timedelta(weeks=PAST_WEEKS_BACK)).isocalendar()
    target_week, target_year = iso.week, iso.year

    handler = _make_handler([("occ_past", "BayOccupancyScreen/GetBayOccupancyScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/occupancy", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        _goto_week_year(page, target_week, target_year)
        _quiet(page); page.wait_for_timeout(1500)
        page.screenshot(path=str(shots_dir / "01_occ_past_week.png"), full_page=True)

        if "occ_past" in _CAPTURED:
            (shots_dir / "occ_past_week.json").write_text(
                json.dumps(_CAPTURED["occ_past"], indent=2), encoding="utf-8")

        # TBD — same open question as NCC_FUT_02, applied to Bay Occupancy.
        note = (f"Navigated to week {target_week}/{target_year}. "
                f"API {'captured' if 'occ_past' in _CAPTURED else 'NOT captured'}. "
                f"TBD: expected past-week behaviour not yet defined — manual review required.")
        return "?", note
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# ==========================================================================
# Screen 3 — Profiles  (week-by-week)
# ==========================================================================
def _do_profiles_default_landing(page, base_url, shots_dir):
    """PROF_FUT_01 — default landing week must be current week or later."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    handler = _make_handler([("prof", "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/profiles", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "01_prof_default_landing.png"), full_page=True)

        wk, yr = _current_week_year(page)
        if wk is None or yr is None:
            return "FAIL", f"Could not read the default week/year from the date-picker (week={wk}, year={yr})"

        landing_monday = _week_monday(wk, yr)
        today_monday   = _week_monday(*date.today().isocalendar()[:2])
        ok = landing_monday >= today_monday

        return ("PASS" if ok else "FAIL",
                f"Default landing week = {wk}/{yr} (Monday {landing_monday.isoformat()}) "
                f"{'>=' if ok else '<'} current week Monday {today_monday.isoformat()}")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass


def _do_profiles_past_week_tbd(page, base_url, shots_dir):
    """PROF_FUT_02 — TBD: navigate to a past week and capture for manual review."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    iso = (date.today() - timedelta(weeks=PAST_WEEKS_BACK)).isocalendar()
    target_week, target_year = iso.week, iso.year

    handler = _make_handler([("prof_past", "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/schedules/profiles", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        _goto_week_year(page, target_week, target_year)
        _quiet(page); page.wait_for_timeout(1500)
        page.screenshot(path=str(shots_dir / "01_prof_past_week.png"), full_page=True)

        if "prof_past" in _CAPTURED:
            (shots_dir / "prof_past_week.json").write_text(
                json.dumps(_CAPTURED["prof_past"], indent=2), encoding="utf-8")

        # TBD — same open question as NCC_FUT_02, applied to Profiles.
        note = (f"Navigated to week {target_week}/{target_year}. "
                f"API {'captured' if 'prof_past' in _CAPTURED else 'NOT captured'}. "
                f"TBD: expected past-week behaviour not yet defined — manual review required.")
        return "?", note
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# ==========================================================================
# Main
# ==========================================================================
def main():
    _CAPTURED.clear()

    parser = argparse.ArgumentParser(
        description="Hera Business Rules — scheduled screens are future-only")
    parser.add_argument(
        "--from", dest="start_from", type=int, default=START_FROM, metavar="N",
        help="Resume from scenario N (1-based).")
    args, _ = parser.parse_known_args()
    start_from = max(1, args.start_from)

    env_name, base_url = _resolve_env()

    artifacts = HERE / "test_artifacts_future_only"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*64}")
    print(f"  Hera Business Rules – Scheduled screens are future-only")
    print(f"  Environment   : {env_name}  ({base_url})")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
    print(f"{'='*64}\n")

    results: list[dict] = []

    with sync_playwright() as pw:
        session_dir = artifacts / "playwright_session"
        ctx = pw.chromium.launch_persistent_context(
            str(session_dir), headless=False,
            viewport={"width": 1500, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("  Navigating to Hera…")
        try:
            page.goto(f"{base_url}/schedules/profiles",
                      wait_until="commit", timeout=20_000)
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
            print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['screen']}  |  {sc['name']}")
            print(f"  {sc['desc']}")

            try:
                if   sc["id"] == "NCC_FUT_01":
                    auto, reason = _do_ncc_default_landing(page, base_url, shots_dir)
                elif sc["id"] == "NCC_FUT_02":
                    auto, reason = _do_ncc_past_day_tbd(page, base_url, shots_dir)
                elif sc["id"] == "OCC_FUT_01":
                    auto, reason = _do_occupancy_default_landing(page, base_url, shots_dir)
                elif sc["id"] == "OCC_FUT_02":
                    auto, reason = _do_occupancy_past_week_tbd(page, base_url, shots_dir)
                elif sc["id"] == "PROF_FUT_01":
                    auto, reason = _do_profiles_default_landing(page, base_url, shots_dir)
                elif sc["id"] == "PROF_FUT_02":
                    auto, reason = _do_profiles_past_week_tbd(page, base_url, shots_dir)
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
        w = _csv.DictWriter(f, fieldnames=["n", "id", "screen", "name", "expect", "result", "notes", "desc"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"\n  Results    : {out}")
    print(f"  Artifacts  : {run_dir}\n")


if __name__ == "__main__":
    main()
