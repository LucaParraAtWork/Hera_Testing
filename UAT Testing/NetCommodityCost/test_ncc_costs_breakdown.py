#!/usr/bin/env python3
"""
test_ncc_costs_breakdown.py — Hera NCC / Costs Breakdown Test Suite
====================================================================
Covers the following UAT test cases:

  UI – page & navigation
    NCC_UI_01   Costs Breakdown page loads; 3 NCC Forecast cards visible   → PASS
    NCC_UI_02   7-day navigation cycle; every day triggers a fresh API call → PASS
    NCC_UI_05   €/MWh ↔ €/kg unit toggle; no errors, display updates       → PASS

  Calculation validation
    NCC_CALC_07 Daily NCC: Python reference vs Hera API
                |diff| < 0.02 €/kg OR |pct_diff| < 1 % for all 7 days     → PASS

Run (full – opens browser, captures g/s profiles then navigates costs breakdown):
    python "NetCommodityCost/test_ncc_costs_breakdown.py"

Run with a specific week:
    python "NetCommodityCost/test_ncc_costs_breakdown.py" --week 28

Skip profiles capture (reuse the most recent g/s artifact already saved):
    python "NetCommodityCost/test_ncc_costs_breakdown.py" --skip-profiles

Skip all browser work – only run the Python vs API comparison using existing artifacts:
    python "NetCommodityCost/test_ncc_costs_breakdown.py" --skip-capture

Notes
-----
- Prices are read from parameters.csv at the project root (same file used by NCC_TESTING.py).
  The script adds sensible defaults for PRICE_ELEC (100 €/MWh) and PRICE_GAS (30 €/MWh)
  if those keys are absent from the file.
- The CostsBreakdown page navigates per day; the script steps through Mon–Sun of the
  target week, capturing one GetCostsBreakdownScreenAsync call per day.
- Scheduling data (confirmed pickups) is used for offtaker allocation.  The script looks
  for the most recent SchedulingScreen artifact under artifacts/ or Weekly Nomination/.
  If none is found, all truck fillings are allocated to the offtaker with the most
  confirmed pick-ups; when that is unknown too, an even split is used.
- UTC offset for Brussels summer time (CEST) is +2 h.  The constant UTC_OFFSET_H at the
  top of the file can be changed for CET winter (+1 h).
"""

# ==============================================================================
# ENVIRONMENT
# ==============================================================================
ENV = ""   # "dev" | "test" | "" → prompt at start-up

ENVIRONMENTS = {
    "dev":  "https://herawebdev.azurewebsites.net",
    "test": "https://herawebtest.azurewebsites.net",
}

# ==============================================================================
# SCENARIO CONTROL  (set START_FROM = N to skip scenarios 1 … N-1)
# ==============================================================================
START_FROM = 1

# ==============================================================================
# TIMING
# ==============================================================================
WAIT_S = 0.4
WAIT_M = 0.9
WAIT_L = 2.0

# Brussels CEST offset (summer = +2, winter CET = +1)
UTC_OFFSET_H = 2

# ==============================================================================
# NCC CALCULATION THRESHOLDS
# ==============================================================================
NCC_ABS_TOL  = 0.02   # €/kg  — absolute tolerance per day
NCC_PCT_TOL  = 1.0    # %     — relative tolerance per day

# ==============================================================================
# TEST CASES
# ==============================================================================
SCENARIOS = [
    {"n": 1, "id": "NCC_UI_01", "group": "ui",
     "name": "Costs Breakdown page loads — 3 NCC Forecast cards visible",
     "desc": "Navigate to /schedules/costs-breakdown; API returns netCommodityCostForecasts "
             "with 3 entries (Offtaker 1, Offtaker 2, Total Planning).",
     "expect": "PASS"},

    {"n": 2, "id": "NCC_UI_02", "group": "ui",
     "name": "7-day navigation cycle — every day triggers fresh API data",
     "desc": "Navigate Mon→Sun of the target week; each day step fires a new "
             "GetCostsBreakdownScreenAsync call and the NCC cards update.",
     "expect": "PASS"},

    {"n": 3, "id": "NCC_UI_05", "group": "ui",
     "name": "€/MWh ↔ €/kg unit toggle — no errors, display updates",
     "desc": "Click €/kg toggle then €/MWh toggle; no JS error or error toast; "
             "page remains functional.",
     "expect": "PASS"},

    {"n": 4, "id": "NCC_CALC_07", "group": "calc",
     "name": "Daily NCC Python vs API: |diff| < 0.02 €/kg or |pct| < 1 % — all 7 days",
     "desc": "Python reference NCC (from g/s profiles + alpha allocation) compared against "
             "Hera API netCommodityCostForecasts for each day of the week.",
     "expect": "PASS"},
]

# ==============================================================================
# IMPORTS
# ==============================================================================
import argparse
import csv as _csv
import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results, print_test_case_info, ask_verdict, clear_screen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent   # Hera_Testing root (two levels up: UAT Testing/NetCommodityCost -> UAT Testing -> Hera_Testing)

# ==============================================================================
# WAIT HELPERS
# ==============================================================================
def _wl():
    time.sleep(WAIT_L)


def _quiet(page, ms: int = 6_000):
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass


def _safe_goto(page, url: str, wait_until: str = "domcontentloaded") -> None:
    """Navigate to url, auto-recovering from MSAL token-renewal redirects.

    MSAL occasionally intercepts a navigation mid-flight to silently renew the
    access token (/auth#code=...) and then returns the user to the app.  When
    that happens page.goto() raises 'interrupted by another navigation'.  We
    wait a few seconds for the auth round-trip to finish and retry once."""
    import time as _time
    try:
        page.goto(url, wait_until=wait_until)
    except Exception as e:
        if "interrupted by another navigation" in str(e):
            _time.sleep(5)
            page.goto(url, wait_until=wait_until)
        else:
            raise


# ==============================================================================
# ENVIRONMENT SELECTOR
# ==============================================================================
def _resolve_env(env_arg: str) -> tuple[str, str]:
    env = (env_arg or ENV).strip().lower()
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


# ==============================================================================
# API RESPONSE HANDLER
# ==============================================================================
def _make_handler(keys: list[tuple[str, str]]):
    """
    Factory that returns a Playwright response listener plus a shared dict.

    keys  – list of (label, url_substring) pairs.
    The handler stores the parsed JSON body under `handler.captured[label]`
    whenever a response URL contains the corresponding substring.
    """
    captured: dict[str, dict] = {}

    def handler(response):
        try:
            ct = response.headers.get("content-type", "").lower()
            if "application/json" not in ct:
                return
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            url = response.url.lower()
            for label, pattern in keys:
                if pattern.lower() in url:
                    try:
                        body = response.json()
                        if body:
                            captured[label] = body
                    except Exception:
                        pass
        except Exception:
            pass

    handler.captured = captured
    return handler


# ==============================================================================
# WEEK / DAY NAVIGATION
# ==============================================================================
def _current_picker_text(page) -> str:
    """Read whatever text the date-picker button currently shows."""
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


def _parse_picker_date(text: str) -> date | None:
    """Try to parse a date out of the picker button text."""
    if not text:
        return None
    # strip weekday names and ordinal suffixes so dateutil can handle it
    cleaned = re.sub(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", text, flags=re.I)
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", cleaned).strip()
    # explicit formats first
    for fmt in (
        "%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y",
        "%B %d %Y", "%b %d %Y", "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    # pandas fallback
    try:
        return pd.to_datetime(cleaned, dayfirst=True).date()
    except Exception:
        pass
    return None


def _click_next_day(page) -> None:
    selectors = [
        "div.date-picker-next > button",
        "[class*='date-picker-next'] button",
        "button[aria-label*='next']",
        "button.nav-btn:last-of-type",
    ]
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=3_000)
            time.sleep(WAIT_S)
            return
        except Exception:
            pass
    raise RuntimeError("Cannot find 'next day' button on costs breakdown page.")


def _click_prev_day(page) -> None:
    selectors = [
        "div.date-picker-previous > button",
        "[class*='date-picker-previous'] button",
        "button[aria-label*='prev']",
        "button.nav-btn:first-of-type",
    ]
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=3_000)
            time.sleep(WAIT_S)
            return
        except Exception:
            pass
    raise RuntimeError("Cannot find 'previous day' button on costs breakdown page.")


def _goto_day(page, target: date, max_clicks: int = 20) -> bool:
    """Navigate the day picker to `target`.  Returns True on success."""
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except Exception:
        pass
    time.sleep(WAIT_M)
    for _ in range(max_clicks):
        txt = _current_picker_text(page)
        current = _parse_picker_date(txt)
        if current == target:
            return True
        if current is None:
            time.sleep(WAIT_M)
            continue
        if current < target:
            _click_next_day(page)
        else:
            _click_prev_day(page)
    return False


def _week_monday(week: int, year: int) -> date:
    """Return the Monday of the given ISO week/year."""
    return date.fromisocalendar(year, week, 1)


# ==============================================================================
# UNIT TOGGLE HELPER
# ==============================================================================
def _click_toggle(page, label: str) -> bool:
    """Click a toggle button whose text matches `label` (e.g. '€/kg').  Returns success."""
    patterns = [
        f"app-toggle button:has-text('{label}')",
        f"button:has-text('{label}')",
        f"[class*='toggle'] button:has-text('{label}')",
        f"[class*='unit'] button:has-text('{label}')",
    ]
    for sel in patterns:
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=5_000)
            btn.click()
            time.sleep(WAIT_S)
            return True
        except Exception:
            pass
    return False


# ==============================================================================
# PARAMETERS
# ==============================================================================
def _load_params(path: Path | None = None) -> dict[str, float]:
    """
    Load numeric parameters from parameters.csv.
    Falls back to defaults for any key not present.
    """
    defaults = {
        "P_MAX":       25.0,
        "M_SOL":        5.0,
        "ETA":          0.643,
        "P_FLUXYS":    25.0,
        "PI_AFRR":      0.0,
        "PRICE_ELEC": 100.0,
        "PRICE_GAS":   30.0,
    }
    csv_path = path or ROOT / "parameters.csv"
    if csv_path.exists():
        try:
            reader = _csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines())
            for row in reader:
                k = row.get("Parameter", "").strip()
                v = row.get("Value", "").strip()
                if k and v:
                    try:
                        defaults[k] = float(v)
                    except ValueError:
                        pass
        except Exception as e:
            print(f"  [WARN] Could not read parameters.csv: {e}")
    return defaults


# ==============================================================================
# U-CURVE: FLOW (g/s) → POWER (MW)
# ==============================================================================
def _get_plant_power_mw(flow_gs) -> np.ndarray:
    """
    Dynamic efficiency mapping (U-Curve).
    Converts hydrogen flow (g/s) to electrolyser power consumption (MW).
    """
    GS_TO_NM3H    = 40.0445
    FLOW_POINTS   = [18.88, 60.432, 124.86]   # MSOL, BEP, MCR  (g/s)
    CS_POINTS     = [5.09,   4.54,    4.66]   # specific consumption (kWh/Nm3)
    flow_gs       = np.asarray(flow_gs, dtype=float)
    flow_clipped  = np.clip(flow_gs, 18.88, 124.86)
    cs_kwh_nm3    = np.interp(flow_clipped, FLOW_POINTS, CS_POINTS)
    flow_nm3h     = flow_gs * GS_TO_NM3H
    return np.where(flow_gs > 1e-3, flow_nm3h * cs_kwh_nm3 / 1_000.0, 0.0)


# ==============================================================================
# ARTIFACT DISCOVERY
# ==============================================================================
def _find_latest_gs_profiles(shots_dir: Path) -> Path | None:
    """
    Look for the most recently saved g/s profiles JSON artifact.
    Search order: current run > artifacts/hera_profiles > Weekly Nomination artifacts.
    """
    candidates: list[Path] = []

    # 1. Within this test's own run directory
    candidates += sorted(shots_dir.glob("profiles/data/*.json"), reverse=True)

    # 2. run_pipeline hera_profiles artifacts
    hp = ROOT / "artifacts" / "hera_profiles"
    if hp.exists():
        for data_dir in sorted(hp.glob("*/profiles_today/data"), reverse=True):
            candidates += sorted(data_dir.glob("*gs*.json"), reverse=True)
            candidates += sorted(data_dir.glob("*g_s*.json"), reverse=True)

    # 3. Old hera_import style artifacts (step07_profiles)
    arts = ROOT / "artifacts"
    if arts.exists():
        for run in sorted(
            [d for d in arts.iterdir() if d.is_dir() and d.name[0].isdigit()],
            reverse=True,
        ):
            data_dir = run / "step07_profiles" / "data"
            if data_dir.exists():
                candidates += sorted(data_dir.glob("*POST_*ProfilesScreen*.json"), reverse=True)

    for p in candidates:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            graphs = (
                raw.get("data", {}).get("profileGraphs")
                or raw.get("profileGraphs")
                or []
            )
            if any("truck fillings" in str(g.get("title", "")).lower() for g in graphs):
                return p
        except Exception:
            pass
    return None


def _find_latest_scheduling(shots_dir: Path) -> Path | None:
    """Look for a scheduling JSON artifact with confirmed slot data."""
    candidates: list[Path] = []

    # 1. Current run's scheduling capture (highest priority — same week as profiles)
    cur = shots_dir / "scheduling" / "data"
    if cur.exists():
        candidates += sorted(cur.glob("*.json"), reverse=True)

    # 2. Old hera_import style (step06_confirm)
    arts = ROOT / "artifacts"
    if arts.exists():
        for run in sorted(
            [d for d in arts.iterdir() if d.is_dir() and d.name[0].isdigit()],
            reverse=True,
        ):
            data_dir = run / "step06_confirm" / "data"
            if data_dir.exists():
                candidates += sorted(data_dir.glob("*SchedulingScreen*.json"), reverse=True)

    for p in candidates:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            payload = raw.get("data", raw)
            if payload.get("slots"):
                return p
        except Exception:
            pass
    return None


def _find_costs_breakdown_artifacts(shots_dir: Path) -> dict[str, dict]:
    """
    Collect all captured CostsBreakdown JSON files from this run.
    Returns {date_str: api_body}.
    """
    result: dict[str, dict] = {}
    data_dir = shots_dir / "costs_breakdown" / "data"
    if not data_dir.exists():
        return result
    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    for p in sorted(data_dir.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            m = date_re.search(p.name)
            if m:
                date_str = m.group(1)
                result[date_str] = raw
        except Exception:
            pass
    return result


# ==============================================================================
# NCC CALCULATION  (reference Python implementation)
# ==============================================================================
def _calculate_ncc_per_day(
    gs_profiles_path: Path,
    scheduling_path: Path | None,
    params: dict[str, float],
    name_hint: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    """
    Calculate NCC (€/kg) per day per offtaker from g/s profiles.

    Returns dict keyed by date string (YYYY-MM-DD), each value a dict:
        {"offtaker_1_name": ncc, "offtaker_2_name": ncc, "Total Planning": ncc}

    If profiles data is unavailable, returns {}.
    """
    P_MAX     = params["P_MAX"]
    M_SOL     = params["M_SOL"]
    ETA       = params["ETA"]
    P_FLUXYS  = params["P_FLUXYS"]
    PI_AFRR   = params["PI_AFRR"]
    PRICE_ELEC = params["PRICE_ELEC"]
    PRICE_GAS  = params["PRICE_GAS"]

    LHV_H2    = 0.033333   # MWh/kg
    HHV_H2    = 0.03941    # MWh/kg
    MW_PER_GS = (3.6 * LHV_H2) / ETA
    GS_PER_MW = 1.0 / MW_PER_GS
    M_SOL_GS  = M_SOL * GS_PER_MW

    # ------------------------------------------------------------------
    # 1. Load g/s profiles
    # ------------------------------------------------------------------
    try:
        raw      = json.loads(gs_profiles_path.read_text(encoding="utf-8"))
        api_data = raw.get("data", raw)
        if isinstance(api_data, dict) and "profileGraphs" not in api_data:
            api_data = api_data.get("data", api_data)
        graphs   = api_data.get("profileGraphs", [])
    except Exception as e:
        print(f"  [WARN] Cannot read profiles JSON: {e}")
        return {}

    df_data: dict[str, dict] = {}
    bay_names: list[str]     = []

    for graph in graphs:
        title = graph.get("title", "")
        for line in graph.get("profileGraphLines", []):
            name   = line.get("name", "")
            points = line.get("points", {})
            if not points:
                continue
            if "truck fillings by loading bay" in title.lower():
                key = f"Bay_{name.replace(' ', '_')}"
                df_data[key] = points
                bay_names.append(key)
            elif (
                "gas injection" in title.lower()
                or "injection to fluxys" in title.lower()
            ):
                df_data["M_gas_Global"] = points

    if not df_data:
        print("  [WARN] No bay or gas data found in profiles JSON.")
        return {}

    df = pd.DataFrame(df_data)
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)
    df.fillna(0.0, inplace=True)

    if "P_aFRR_Reserved" not in df.columns:
        df["P_aFRR_Reserved"] = 0.0
    df["Price_Elec"] = PRICE_ELEC
    df["Price_Gas"]  = PRICE_GAS

    # ------------------------------------------------------------------
    # 2. Determine offtaker names
    #    Priority: caller-supplied hint (from CostsBreakdown API) > profiles
    #    API netCommodityCostsChart > generic fallback.
    #    Using CostsBreakdown API names guarantees comparison keys match exactly.
    # ------------------------------------------------------------------
    if name_hint and len(name_hint) >= 2:
        name_l1, name_l2 = name_hint[0], name_hint[1]
    else:
        offtaker_names = [
            oc.get("offtakerName")
            for oc in api_data.get("netCommodityCostsChart", {}).get("offtakerCosts", [])
        ]
        if len(offtaker_names) >= 2:
            name_l1, name_l2 = offtaker_names[0], offtaker_names[1]
        elif len(offtaker_names) == 1:
            name_l1, name_l2 = offtaker_names[0], "Offtaker 2"
        else:
            name_l1, name_l2 = "Offtaker 1", "Offtaker 2"

    # ------------------------------------------------------------------
    # 3. Load confirmed pickups for trailer → offtaker allocation
    # ------------------------------------------------------------------
    pickups: list[dict] = []
    company_map: dict   = {}

    if scheduling_path:
        try:
            sched_raw = json.loads(scheduling_path.read_text(encoding="utf-8"))
            payload   = sched_raw.get("data", sched_raw)
            for comp in payload.get("availableCompanies", []):
                company_map[comp["id"]] = comp["name"]
            for slot in payload.get("slots", []):
                for transfer in slot.get("scheduledTransfers", []):
                    if (
                        transfer.get("type", "").lower() == "pickup"
                        and transfer.get("status", "").lower() == "confirmed"
                    ):
                        pickups.append({
                            "time":    pd.to_datetime(transfer["earliest"], utc=True),
                            "company": company_map.get(transfer["companyId"], "Unknown"),
                        })
            pickups.sort(key=lambda x: x["time"])
        except Exception as e:
            print(f"  [WARN] Could not read scheduling JSON: {e}")

    # ------------------------------------------------------------------
    # 4. Smart trailer → offtaker allocation
    # ------------------------------------------------------------------
    # Validate scheduling coverage before using it.
    # If the scheduling artifact is from a different week its pickup timestamps
    # won't overlap the profiles window.  Without this check, no pickup ever
    # matches within the 4-hour window so ALL trailers fall to default_company
    # (whichever company had more confirmed pickups in the old artifact) — this
    # silently assigns every truck filling to one offtaker and gives the other
    # NCC = 0.  When coverage is missing we fall back to a 50/50 split.
    split_50_50 = False
    if pickups:
        prof_start = df.index[0]
        prof_end   = df.index[-1]
        in_range   = [p for p in pickups if prof_start <= p["time"] <= prof_end]
        if not in_range:
            print(
                f"  [WARN] {len(pickups)} confirmed pickup(s) found but NONE overlap the "
                f"profiles window ({prof_start.date()} → {prof_end.date()})."
            )
            print(
                f"         Pickup range: {pickups[0]['time'].date()} → {pickups[-1]['time'].date()}"
            )
            print("         Scheduling artifact is from a different week.")
            print("         Re-run without --skip-profiles to capture scheduling for this week.")
            print("         Using 50/50 split as fallback.")
            split_50_50 = True
    else:
        split_50_50 = True
        print("  [INFO] No confirmed pickups found — using 50/50 truck split.")

    if not split_50_50:
        pickup_counts   = Counter(p["company"] for p in pickups)
        default_company: str | None = pickup_counts.most_common(1)[0][0]
        print(f"  Confirmed pickups: {dict(pickup_counts)}  — default offtaker: {default_company}")
    else:
        default_company = None   # signals loop to use 50/50 split

    df["M_truck_Total"] = df[bay_names].sum(axis=1) if bay_names else 0.0
    df["M_truck_L1"]    = 0.0
    df["M_truck_L2"]    = 0.0

    all_fillings: list[tuple] = []
    for bay in bay_names:
        is_flowing = df[bay] > 0
        trailer_id = (is_flowing != is_flowing.shift()).cumsum()
        for t_id, trailer_data in df[is_flowing].groupby(trailer_id[is_flowing]):
            if not trailer_data.empty:
                all_fillings.append((bay, trailer_data))
    all_fillings.sort(key=lambda x: x[1].index[0])

    if default_company is None:
        # No valid same-week scheduling — split each trailer evenly
        for bay, trailer_data in all_fillings:
            half = trailer_data[bay] / 2.0
            df.loc[trailer_data.index, "M_truck_L1"] += half
            df.loc[trailer_data.index, "M_truck_L2"] += half
    else:
        available_pickups = list(pickups)
        for bay, trailer_data in all_fillings:
            start_time       = trailer_data.index[0]
            assigned_company = default_company
            if available_pickups:
                closest = min(
                    available_pickups,
                    key=lambda p: abs((p["time"] - start_time).total_seconds()),
                )
                if abs((closest["time"] - start_time).total_seconds()) <= 14_400:
                    assigned_company = closest["company"]
                    available_pickups.remove(closest)
            if assigned_company == name_l2:
                df.loc[trailer_data.index, "M_truck_L2"] += trailer_data[bay]
            else:
                df.loc[trailer_data.index, "M_truck_L1"] += trailer_data[bay]

    # ------------------------------------------------------------------
    # 5. MSOL gas fallback
    # ------------------------------------------------------------------
    if "M_gas_Global" not in df.columns:
        df["M_gas_Global"] = np.maximum(0.0, M_SOL_GS - df["M_truck_Total"])

    # ------------------------------------------------------------------
    # 6. Alpha allocation (gas & aFRR)
    # ------------------------------------------------------------------
    alpha_gas_l1, alpha_gas_l2   = [], []
    alpha_afrr_l1, alpha_afrr_l2 = [], []

    for _, row in df.iterrows():
        m_l1    = row["M_truck_L1"]
        m_l2    = row["M_truck_L2"]
        m_tot   = row["M_truck_Total"]
        m_gas   = row["M_gas_Global"]

        # Gas alpha
        if m_gas <= 1e-6:
            g1, g2 = 0.0, 0.0
        elif (m_tot + m_gas) > (M_SOL_GS + 1e-4):   # Conditional bidding
            cap  = min(P_MAX, P_FLUXYS) * GS_PER_MW / 2.0
            f1   = max(0.0, cap - m_l1)
            f2   = max(0.0, cap - m_l2)
            tot  = f1 + f2
            g1, g2 = (f1 / tot, f2 / tot) if tot > 0 else (0.5, 0.5)
        else:                                          # MSOL coverage
            s1   = min(0.0, m_l1 - M_SOL_GS / 2.0)
            s2   = min(0.0, m_l2 - M_SOL_GS / 2.0)
            tot  = s1 + s2
            g1, g2 = (s1 / tot, s2 / tot) if tot < 0 else (0.5, 0.5)
        alpha_gas_l1.append(g1)
        alpha_gas_l2.append(g2)

        # aFRR alpha
        p_l1  = m_l1  * MW_PER_GS
        p_l2  = m_l2  * MW_PER_GS
        p_tot = m_tot * MW_PER_GS
        cap   = min(P_MAX, P_FLUXYS)
        base  = (cap - M_SOL) / 2.0
        if p_tot <= M_SOL:
            a1, a2 = 0.5, 0.5
        else:
            l1_low = p_l1 <= M_SOL / 2.0
            l2_low = p_l2 <= M_SOL / 2.0
            if l1_low and not l2_low:
                c2 = (p_tot - M_SOL) * (p_l2 / p_tot)
                f1, f2 = base, max(0.0, base - c2)
            elif l2_low and not l1_low:
                c1 = (p_tot - M_SOL) * (p_l1 / p_tot)
                f1, f2 = max(0.0, base - c1), base
            else:
                half = cap / 2.0
                f1   = max(0.0, half - p_l1)
                f2   = max(0.0, half - p_l2)
            tot  = f1 + f2
            a1, a2 = (f1 / tot, f2 / tot) if tot > 0 else (0.5, 0.5)
        alpha_afrr_l1.append(a1)
        alpha_afrr_l2.append(a2)

    df["A_Gas_L1"]  = alpha_gas_l1
    df["A_Gas_L2"]  = alpha_gas_l2
    df["A_aFRR_L1"] = alpha_afrr_l1
    df["A_aFRR_L2"] = alpha_afrr_l2

    # ------------------------------------------------------------------
    # 7. Financial calculations
    # ------------------------------------------------------------------
    dt_hr = 15.0 / 60.0

    df["Mass_L1_kg"]     = (df["M_truck_L1"] + df["M_truck_L1"].shift(-1)) / 2.0 * 900.0 / 1_000.0
    df["Mass_L2_kg"]     = (df["M_truck_L2"] + df["M_truck_L2"].shift(-1)) / 2.0 * 900.0 / 1_000.0
    df["Mass_Gas_kg"]    = (df["M_gas_Global"] + df["M_gas_Global"].shift(-1)) / 2.0 * 900.0 / 1_000.0
    df["Mass_Total_kg"]  = df["Mass_L1_kg"] + df["Mass_L2_kg"] + df["Mass_Gas_kg"]

    total_flow     = df["M_truck_Total"] + df["M_gas_Global"]
    pwr_mw         = _get_plant_power_mw(total_flow)
    pwr_mw_next    = _get_plant_power_mw(total_flow.shift(-1))
    df["Elec_MWh"] = (pwr_mw + pwr_mw_next) / 2.0 * dt_hr

    share_l1       = df["Mass_L1_kg"]    / (df["Mass_Total_kg"] + 1e-9)
    share_l2       = df["Mass_L2_kg"]    / (df["Mass_Total_kg"] + 1e-9)
    share_gas      = df["Mass_Gas_kg"]   / (df["Mass_Total_kg"] + 1e-9)

    df["Cost_L1"]   = df["Elec_MWh"] * share_l1  * PRICE_ELEC
    df["Cost_L2"]   = df["Elec_MWh"] * share_l2  * PRICE_ELEC
    df["Cost_Gas"]  = df["Elec_MWh"] * share_gas * PRICE_ELEC

    df["Gas_L1_kg"]  = df["Mass_Gas_kg"] * df["A_Gas_L1"]
    df["Gas_L2_kg"]  = df["Mass_Gas_kg"] * df["A_Gas_L2"]

    df["Rev_L1"]    = df["Gas_L1_kg"] * HHV_H2 * PRICE_GAS
    df["Rev_L2"]    = df["Gas_L2_kg"] * HHV_H2 * PRICE_GAS
    df["Rev_Gas"]   = df["Mass_Gas_kg"] * HHV_H2 * PRICE_GAS

    p_afrr          = df["P_aFRR_Reserved"]
    e_afrr          = (p_afrr + p_afrr.shift(-1)) / 2.0 * dt_hr
    df["Rev_aFRR_L1"] = e_afrr * df["A_aFRR_L1"] * PI_AFRR
    df["Rev_aFRR_L2"] = e_afrr * df["A_aFRR_L2"] * PI_AFRR

    df = df.dropna().copy()
    df.index = df.index.tz_convert("Europe/Brussels")

    # ------------------------------------------------------------------
    # 8. Aggregate per day
    # ------------------------------------------------------------------
    results: dict[str, dict[str, float]] = {}

    for day, ddf in df.groupby(df.index.date):
        net_l1  = (ddf["Cost_L1"] + ddf["Cost_Gas"] * ddf["A_Gas_L1"]
                   - ddf["Rev_L1"] - ddf["Rev_aFRR_L1"]).sum()
        net_l2  = (ddf["Cost_L2"] + ddf["Cost_Gas"] * ddf["A_Gas_L2"]
                   - ddf["Rev_L2"] - ddf["Rev_aFRR_L2"]).sum()
        net_tot = net_l1 + net_l2

        kg_l1   = ddf["Mass_L1_kg"].sum() + ddf["Gas_L1_kg"].sum()
        kg_l2   = ddf["Mass_L2_kg"].sum() + ddf["Gas_L2_kg"].sum()
        kg_tot  = kg_l1 + kg_l2

        results[str(day)] = {
            name_l1:          round(net_l1  / kg_l1,  4) if kg_l1  > 0 else 0.0,
            name_l2:          round(net_l2  / kg_l2,  4) if kg_l2  > 0 else 0.0,
            "Total Planning": round(net_tot / kg_tot, 4) if kg_tot > 0 else 0.0,
        }

    return results


# ==============================================================================
# PROFILES CAPTURE  (g/s toggle needed for truck filling data)
# ==============================================================================
def _capture_gs_profiles(
    page, base_url: str, target_week: int, shots_dir: Path
) -> Path | None:
    """
    Navigate to Profiles screen, switch to g/s toggle, wait for API response.
    Saves the captured JSON and returns its path.
    """
    print("\n  [Setup] Capturing g/s profiles for NCC calculation…")
    data_dir = shots_dir / "profiles" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scr_dir  = shots_dir / "profiles"

    handler = _make_handler([("profiles_gs", "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler)

    _safe_goto(page, f"{base_url}/schedules/profiles")
    _wl(); _quiet(page)

    try:
        page.wait_for_selector("div.date-picker-current", timeout=8_000)
    except Exception:
        pass

    for _ in range(60):
        txt = _current_picker_text(page)
        m   = re.search(r"Week\s+(\d{1,2})", txt, re.I)
        if m and int(m.group(1)) == target_week:
            break
        if not m:
            time.sleep(WAIT_M)
            continue
        wk   = int(m.group(1))
        diff = target_week - wk
        go_next = (diff > 0 and diff <= 26) or (diff < -26)
        if go_next:
            page.locator("div.date-picker-next > button").first.click()
        else:
            page.locator("div.date-picker-previous > button").first.click()
        time.sleep(0.4)

    _wl(); _quiet(page)

    # Switch to g/s toggle to get truck filling data
    other = "MW"
    try:
        btn = page.locator("app-toggle button", has_text=re.compile(r"\bMW\b", re.I)).first
        btn.wait_for(state="visible", timeout=8_000)
        btn.click()
        time.sleep(WAIT_L)
    except Exception:
        pass

    gs_label = re.compile(r"g\s*/\s*s", re.I)
    try:
        btn = page.locator("app-toggle button", has_text=gs_label).first
        btn.wait_for(state="visible", timeout=8_000)
        btn.click()
        time.sleep(WAIT_L)
    except Exception:
        pass

    _quiet(page, ms=8_000)
    page.screenshot(path=str(scr_dir / "01_profiles_gs.png"), full_page=True)
    print(f"  [Profiles] Screenshot saved.")

    page.remove_listener("response", handler)

    captured = handler.captured.get("profiles_gs")
    if not captured:
        print("  [WARN] g/s profiles API not captured.")
        return None

    out_path = data_dir / f"001_gs_profiles_week{target_week:02d}.json"
    out_path.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    graphs   = (
        captured.get("profileGraphs")
        or captured.get("data", {}).get("profileGraphs")
        or []
    )
    bays = [
        l.get("name")
        for g in graphs if "truck fillings" in str(g.get("title", "")).lower()
        for l in g.get("profileGraphLines", [])
    ]
    print(f"  [Profiles] Captured {len(graphs)} graphs — bays found: {bays}")
    return out_path


def _capture_scheduling(
    page,
    base_url: str,
    target_week: int,
    shots_dir: Path,
) -> Path | None:
    """
    Navigate to the Scheduling screen for target_week and capture
    GetSchedulingScreenAsync.  Returns path to saved JSON or None.

    This is the critical prerequisite for correct truck-filling allocation in
    _calculate_ncc_per_day(): without same-week scheduling data, all trailers
    fall back to default_company (the most-common company in whatever old
    artifact is found), which silently gives one offtaker NCC=0.
    """
    print(f"\n  [Setup] Capturing scheduling data for Week {target_week}…")
    data_dir = shots_dir / "scheduling" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    handler = _make_handler([("sched", "SchedulingScreen/GetSchedulingScreenAsync")])
    page.on("response", handler)

    try:
        page.goto(f"{base_url}/schedules", wait_until="domcontentloaded", timeout=15_000)
    except Exception:
        pass
    _wl(); _quiet(page)

    # Navigate to target week using the week picker
    try:
        page.wait_for_selector("div.date-picker-current", timeout=8_000)
    except Exception:
        pass

    for _ in range(60):
        txt = _current_picker_text(page)
        m   = re.search(r"Week\s+(\d{1,2})", txt, re.I)
        if m and int(m.group(1)) == target_week:
            break
        if not m:
            time.sleep(WAIT_M)
            continue
        wk   = int(m.group(1))
        diff = target_week - wk
        go_next = (diff > 0 and diff <= 26) or (diff < -26)
        if go_next:
            page.locator("div.date-picker-next > button").first.click()
        else:
            page.locator("div.date-picker-previous > button").first.click()
        time.sleep(0.4)

    _wl(); _quiet(page)
    page.remove_listener("response", handler)

    captured = handler.captured.get("sched")
    if not captured:
        print(f"  [WARN] Scheduling API not captured for Week {target_week}.")
        return None

    out = data_dir / f"001_scheduling_week{target_week:02d}.json"
    out.write_text(json.dumps(captured, indent=2), encoding="utf-8")

    payload  = captured.get("data", captured)
    slots    = payload.get("slots", [])
    confirmed = sum(
        1 for s in slots for t in s.get("scheduledTransfers", [])
        if t.get("type", "").lower() == "pickup"
        and t.get("status", "").lower() == "confirmed"
    )
    print(f"  [Scheduling] Week {target_week}: {confirmed} confirmed pickups across {len(slots)} slots.")
    return out


def _names_from_costs_api(costs_by_date: dict) -> list[str]:
    """
    Extract the two per-offtaker entity names from the first available
    CostsBreakdown response, excluding the 'Total Planning' row.

    Using these names as the calculation keys ensures the Python results dict
    and the API dict share identical keys — the same "anchor to the API's own
    data" principle that run_pipeline.py applies via _filter_to_date().
    """
    for body in costs_by_date.values():
        forecasts = (
            body.get("netCommodityCostForecasts")
            or body.get("data", {}).get("netCommodityCostForecasts")
            or []
        )
        names = [
            f.get("name", "")
            for f in forecasts
            if f.get("name") and "total" not in f.get("name", "").lower()
        ]
        if len(names) >= 2:
            return names[:2]
    return []


# ==============================================================================
# COSTS BREAKDOWN: NAVIGATE + CAPTURE ALL 7 DAYS
# ==============================================================================
def _capture_costs_breakdown(
    page,
    base_url: str,
    week_monday: date,
    shots_dir: Path,
) -> dict[str, dict]:
    """
    Navigate to Costs Breakdown, step through Mon–Sun, capture API per day.
    Returns {date_str: api_body}.
    """
    print("\n  [Setup] Navigating Costs Breakdown – capturing 7 days…")
    data_dir = shots_dir / "costs_breakdown" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scr_dir  = shots_dir / "costs_breakdown"

    costs_by_date: dict[str, dict] = {}

    # ── ONE persistent handler for the whole session ───────────────────────────
    # Each entry in _pending is {"body": <api dict>, "url": <str>, "post": <str>}.
    # Storing the request URL lets us match each response to its calendar date
    # (the date appears in the URL or POST payload as "YYYY-MM-DD"), which is the
    # only reliable way to pick the right entry when _goto_day() fires responses
    # for several intermediate days or the SPA pre-fetches the next day.
    # ── Collect ALL API responses during navigation, then map by Brussels date ──
    # The SPA pre-fetches neighbouring days; navigating to a specific day often
    # returns a cached result and fires no new request.  Instead we simply capture
    # every CostsBreakdown response that fires during the full navigation pass and
    # key each one by Brussels date, derived from the POST body's 'till' field:
    #   till = "<Brussels_date>T22:00:00.000Z"  (Brussels midnight in UTC)
    # so  till[:10]  equals the Brussels calendar date exactly.
    all_responses: dict[str, dict] = {}  # Brussels date ("YYYY-MM-DD") -> body
    _req_post_data: dict = {}            # Request object -> raw POST body string
    _post_logged: list[bool] = [False]   # one-shot: print POST body format once

    def _costs_request_listener(request):
        """Capture POST body while the Request object is still live."""
        try:
            if request.method != "POST" or "costsbreakdown" not in request.url.lower():
                return
            _req_post_data[request] = request.post_data or ""
        except Exception:
            pass

    def _costs_response(response):
        """Store every CostsBreakdown response, keyed by Brussels calendar date."""
        try:
            ct = response.headers.get("content-type", "").lower()
            if "application/json" not in ct:
                return
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "costsbreakdown" not in response.url.lower():
                return
            body = response.json()
            if not body:
                return
            post = _req_post_data.pop(response.request, "")
            if not _post_logged[0]:
                print(f"  [Debug] CostsBreakdown POST body: {post[:200] or '(empty)'}")
                _post_logged[0] = True
            try:
                till = json.loads(post).get("till", "")
                if len(till) >= 10:
                    bdate = till[:10]
                    if bdate not in all_responses:
                        print(f"  [Capture] {bdate} received")
                    all_responses[bdate] = body
            except Exception:
                pass
        except Exception:
            pass

    page.on("request", _costs_request_listener)
    page.on("response", _costs_response)

    try:
        # Navigate to Costs Breakdown
        _safe_goto(page, f"{base_url}/schedules/costs-breakdown")
        _wl(); _quiet(page)
        page.screenshot(path=str(scr_dir / "00_landing.png"))

        # Navigate to Monday, then click through all 7 days taking screenshots.
        # Pre-fetches fire at every step and accumulate in all_responses.
        _goto_day(page, week_monday)
        _quiet(page, ms=10_000)
        time.sleep(WAIT_L)

        for day_offset in range(7):
            target_day = week_monday + timedelta(days=day_offset)
            page.screenshot(
                path=str(scr_dir / f"{day_offset + 1:02d}_{target_day.strftime('%a_%Y-%m-%d')}.png")
            )
            if day_offset < 6:
                try:
                    _click_next_day(page)
                    _quiet(page, ms=8_000)
                    time.sleep(WAIT_S)
                except Exception as e:
                    print(f"  [WARN] Next-day navigation failed: {e}")

        # Give any in-flight responses a moment to land
        time.sleep(WAIT_L)

        # Check coverage
        expected_dates = [str(week_monday + timedelta(days=i)) for i in range(7)]
        missing = [d for d in expected_dates if d not in all_responses]

        # Targeted retry for any dates still missing
        if missing:
            print(f"  [Retry] Dates not yet captured: {missing}")
            for date_str in missing:
                day_index = expected_dates.index(date_str)
                target_day = week_monday + timedelta(days=day_index)
                try:
                    _goto_day(page, target_day)
                    _quiet(page, ms=10_000)
                    time.sleep(WAIT_L)
                    if date_str not in all_responses:
                        _click_prev_day(page)
                        time.sleep(1.0)
                        _click_next_day(page)
                        _quiet(page, ms=10_000)
                        time.sleep(WAIT_L)
                except Exception as e:
                    print(f"  [WARN] Retry navigation failed for {date_str}: {e}")
                status = "recovered" if date_str in all_responses else "still missing"
                print(f"  [Retry] {date_str}: {status}")

        # Populate costs_by_date from all_responses
        for day_offset, date_str in enumerate(expected_dates):
            body = all_responses.get(date_str)
            if body:
                out = data_dir / f"{day_offset + 1:02d}_costs_{date_str}.json"
                out.write_text(json.dumps(body, indent=2), encoding="utf-8")
                forecasts = (
                    body.get("netCommodityCostForecasts")
                    or body.get("data", {}).get("netCommodityCostForecasts")
                    or []
                )
                print(f"  [{date_str}] Captured — {len(forecasts)} NCC entries")
                costs_by_date[date_str] = body
            else:
                print(f"  [{date_str}] No CostsBreakdown API data captured.")

    finally:
        try:
            page.remove_listener("request", _costs_request_listener)
        except Exception:
            pass
        try:
            page.remove_listener("response", _costs_response)
        except Exception:
            pass

    return costs_by_date


# ==============================================================================
# TEST FUNCTIONS
# ==============================================================================
def _test_ui_page_loads(costs_by_date: dict) -> tuple[str, str]:
    """
    NCC_UI_01: verify the Costs Breakdown page returns 3 NCC Forecast cards.
    Uses the first available day's API response.
    """
    if not costs_by_date:
        return "FAIL", "No CostsBreakdown API response captured."

    first_body = next(iter(costs_by_date.values()))
    forecasts  = (
        first_body.get("netCommodityCostForecasts")
        or first_body.get("data", {}).get("netCommodityCostForecasts")
        or []
    )

    if not forecasts:
        return "FAIL", "netCommodityCostForecasts array absent or empty in API response."
    if len(forecasts) < 3:
        names = [f.get("name") for f in forecasts]
        return "FAIL", f"Only {len(forecasts)} NCC card(s) found: {names}"

    names = [f.get("name") for f in forecasts]
    vals  = [round(float(f.get("value", 0)), 4) for f in forecasts]
    return "PASS", f"{len(forecasts)} NCC cards: {list(zip(names, vals))}"


def _test_7day_navigation(costs_by_date: dict, week_monday: date) -> tuple[str, str]:
    """
    NCC_UI_02: verify that API data was received for all 7 days.
    """
    expected = {str(week_monday + timedelta(days=i)) for i in range(7)}
    captured = set(costs_by_date.keys())
    missing  = expected - captured

    if missing:
        return "FAIL", f"No API data for {len(missing)} day(s): {sorted(missing)}"

    days_with_ncc = sum(
        1 for body in costs_by_date.values()
        if (
            body.get("netCommodityCostForecasts")
            or body.get("data", {}).get("netCommodityCostForecasts")
        )
    )
    return "PASS", f"API captured for all 7 days; {days_with_ncc}/7 have NCC forecasts."


def _test_unit_toggle(page, shots_dir: Path) -> tuple[str, str]:
    """
    NCC_UI_05: click €/kg then €/MWh; verify no error toast or crash.
    """
    errors_before = []
    page.on("console", lambda m: errors_before.append(m.text) if m.type == "error" else None)

    clicked_kg  = _click_toggle(page, "€/kg")
    time.sleep(WAIT_L)
    page.screenshot(path=str(shots_dir / "toggle_ekg.png"))

    clicked_mwh = _click_toggle(page, "€/MWh")
    time.sleep(WAIT_L)
    page.screenshot(path=str(shots_dir / "toggle_mwh.png"))

    if not clicked_kg or not clicked_mwh:
        missing = []
        if not clicked_kg:  missing.append("€/kg")
        if not clicked_mwh: missing.append("€/MWh")
        return "FAIL", f"Toggle button(s) not found: {missing}"

    # Check for visible error toast
    try:
        body_txt = page.locator("body").inner_text(timeout=1_500).lower()
        error_kw = ("error", "failed", "exception", "could not", "something went wrong")
        for kw in error_kw:
            if kw in body_txt:
                snippet = body_txt[body_txt.find(kw):body_txt.find(kw) + 80]
                return "FAIL", f"Error text on page after toggle: \"{snippet}\""
    except Exception:
        pass

    return "PASS", "€/kg → €/MWh toggle succeeded; no errors detected."


def _test_ncc_calc_vs_api(
    calculated: dict[str, dict[str, float]],
    costs_by_date: dict[str, dict],
) -> tuple[str, str]:
    """
    NCC_CALC_07: compare Python-calculated NCC vs Hera API per day per entity.
    PASS if every comparison satisfies |diff|<0.02 €/kg OR |pct_diff|<1 %.
    """
    if not calculated:
        return "FAIL", "Python NCC calculation returned no results (missing profiles data)."
    if not costs_by_date:
        return "FAIL", "No CostsBreakdown API data to compare against."

    fail_count = 0
    pass_count = 0
    detail_lines: list[str] = []

    for date_str in sorted(costs_by_date.keys()):
        body      = costs_by_date[date_str]
        forecasts = (
            body.get("netCommodityCostForecasts")
            or body.get("data", {}).get("netCommodityCostForecasts")
            or []
        )
        api_dict = {item["name"]: float(item["value"]) for item in forecasts}

        py_dict = calculated.get(date_str, {})
        if not py_dict:
            detail_lines.append(f"  {date_str}: no Python data for this day (skipped)")
            continue

        detail_lines.append(f"\n  {date_str}:")
        for entity, calc_val in py_dict.items():
            api_val = api_dict.get(entity)
            if api_val is None:
                detail_lines.append(f"    {entity:<22s} — not found in API response")
                continue
            diff     = calc_val - api_val
            pct      = (diff / api_val * 100) if api_val != 0 else 0.0
            ok       = abs(pct) < NCC_PCT_TOL or abs(diff) < NCC_ABS_TOL
            mark     = "OK " if ok else "XX "
            detail_lines.append(
                f"    [{mark}] {entity:<22s}  "
                f"api={api_val:.4f}  py={calc_val:.4f}  "
                f"diff={diff:+.4f} €/kg  ({pct:+.2f} %)"
            )
            if ok:
                pass_count += 1
            else:
                fail_count += 1

    summary = "\n".join(detail_lines)
    print(summary)
    total = pass_count + fail_count
    if fail_count == 0 and pass_count > 0:
        return "PASS", f"All {pass_count}/{total} comparisons within tolerance."
    if fail_count > 0:
        return "FAIL", f"{fail_count}/{total} comparison(s) exceeded tolerance — see above."
    return "FAIL", "No comparisons could be made (data mismatch or missing)."


# ==============================================================================
# ASK + SUMMARY
# ==============================================================================
def _ask(scenario: dict, auto: str, reason: str) -> tuple[str, str]:
    return ask_verdict(
        tc_id=scenario["id"], title=scenario["name"], expect=scenario["expect"],
        auto=auto, reason=reason, index=scenario.get("n"), total=len(SCENARIOS),
        excel_id=scenario["id"],
    )


def _print_summary(all_results: list) -> None:
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"  {'ID':<18} {'Expect':<7} {'Result':<7} Description")
    print("-" * 70)
    for sc, res, _notes, _reason in all_results:
        mark = "✓" if res == sc["expect"] else "✗"
        print(f"  {mark} {sc['id']:<17} {sc['expect']:<7} {res:<7} {sc['name']}")
    print("=" * 70)

    csv_path = HERE / "test_results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["id", "name", "expect", "result", "notes"])
        for sc, res, notes, _ in all_results:
            w.writerow([sc["id"], sc["name"], sc["expect"], res, notes])
    print(f"\n  Results saved to {csv_path}")

    record_results([(sc["id"], res, notes) for sc, res, notes, _ in all_results],
                    xlsx_path=args.excel, source="test_ncc_costs_breakdown.py")


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hera NCC / Costs Breakdown Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env", default="", help="dev | test (or leave blank to prompt)")
    parser.add_argument("--week", type=int, default=0, help="ISO week number (default: current)")
    parser.add_argument(
        "--skip-profiles",
        action="store_true",
        help="Skip g/s profile capture; reuse most recent artifact",
    )
    parser.add_argument(
        "--skip-capture",
        action="store_true",
        help="Skip all browser work; only run Python vs API comparison",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=START_FROM,
        help="Skip scenarios 1…N-1 (default: 1)",
    )
    parser.add_argument(
        "--excel", default=None, metavar="PATH",
        help="Report file to write Pass/Fail into. Default: create a new "
             "timestamped copy under UAT Testing/Reports/.",
    )
    args = parser.parse_args()

    env_name, base_url = _resolve_env(args.env)

    today    = date.today()
    if args.week:
        iso      = today.isocalendar()
        target_w = args.week
        target_y = iso.year
    else:
        # Default: current week + 1 (the week we are preparing / about to run)
        next_week_day = today + timedelta(weeks=1)
        nw            = next_week_day.isocalendar()
        target_w      = nw.week
        target_y      = nw.year
    week_mon = _week_monday(target_w, target_y)

    run_id   = datetime.now().strftime("%Y%m%d-%H%M%S")
    shots    = HERE / "artifacts" / run_id
    shots.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print(f"  NCC / Costs Breakdown Test Suite  —  env={env_name.upper()}")
    print(f"  Target week : Week {target_w} ({week_mon} → {week_mon + timedelta(days=6)})")
    print(f"  Run ID      : {run_id}")
    print("=" * 70)

    all_results: list[tuple[dict, str, str, str]] = []
    costs_by_date: dict[str, dict] = {}
    gs_profiles_path: Path | None  = None

    # ------------------------------------------------------------------
    # Browser automation
    # ------------------------------------------------------------------
    if not args.skip_capture:
        artifacts_dir = HERE / "artifacts"
        session_dir   = artifacts_dir / "playwright_session"
        session_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(session_dir),
                headless=False,
                slow_mo=0,
                viewport={"width": 1500, "height": 900},
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            print("  Navigating to Costs Breakdown…")
            try:
                page.goto(
                    f"{base_url}/schedules/costs-breakdown",
                    wait_until="commit",
                    timeout=20_000,
                )
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
            print("  Login confirmed. Starting scenarios.\n")

            # ── Step 1: Capture g/s profiles (needed for NCC_CALC_07) ────────
            if not args.skip_profiles:
                gs_profiles_path = _capture_gs_profiles(
                    page, base_url, target_w, shots
                )
            else:
                print("  [skip-profiles] Skipping profiles capture.")
                gs_profiles_path = _find_latest_gs_profiles(shots)
                if gs_profiles_path:
                    print(f"  [skip-profiles] Using existing artifact: {gs_profiles_path.name}")
                else:
                    print("  [skip-profiles] No artifact found; NCC_CALC_07 will be skipped.")

            # ── Step 1b: Capture scheduling for target week ───────────────────
            # Must happen before costs breakdown so _find_latest_scheduling()
            # returns same-week data when running NCC_CALC_07.
            _capture_scheduling(page, base_url, target_w, shots)

            # ── Step 2: Navigate Costs Breakdown, capture all 7 days ──────────
            costs_by_date = _capture_costs_breakdown(
                page, base_url, week_mon, shots
            )

            # ── NCC_UI_01: page loads ─────────────────────────────────────────
            sc = next(s for s in SCENARIOS if s["id"] == "NCC_UI_01")
            if sc["n"] >= args.start_from:
                clear_screen()
                auto, reason = _test_ui_page_loads(costs_by_date)
                result, notes = _ask(sc, auto, reason)
                all_results.append((sc, result, notes, reason))

            # ── NCC_UI_02: 7-day navigation ───────────────────────────────────
            sc = next(s for s in SCENARIOS if s["id"] == "NCC_UI_02")
            if sc["n"] >= args.start_from:
                clear_screen()
                auto, reason = _test_7day_navigation(costs_by_date, week_mon)
                result, notes = _ask(sc, auto, reason)
                all_results.append((sc, result, notes, reason))

            # ── NCC_UI_05: unit toggle ────────────────────────────────────────
            sc = next(s for s in SCENARIOS if s["id"] == "NCC_UI_05")
            if sc["n"] >= args.start_from:
                clear_screen()
                auto, reason = _test_unit_toggle(page, shots)
                result, notes = _ask(sc, auto, reason)
                all_results.append((sc, result, notes, reason))

    else:
        # ------------------------------------------------------------------
        # --skip-capture: reload from saved artifacts
        # ------------------------------------------------------------------
        print("\n  [skip-capture] Skipping browser automation.")
        costs_by_date    = _find_costs_breakdown_artifacts(shots)
        gs_profiles_path = _find_latest_gs_profiles(shots)

        if not costs_by_date:
            # Try most recent run
            for run_dir in sorted((HERE / "artifacts").glob("*"), reverse=True):
                costs_by_date = _find_costs_breakdown_artifacts(run_dir)
                if costs_by_date:
                    shots = run_dir
                    break

        if not gs_profiles_path:
            gs_profiles_path = _find_latest_gs_profiles(shots)

        if costs_by_date:
            print(f"  Found costs data for {len(costs_by_date)} day(s): {sorted(costs_by_date)}")
        else:
            print("  [WARN] No costs breakdown artifacts found.")
        if gs_profiles_path:
            print(f"  Profiles artifact: {gs_profiles_path.name}")
        else:
            print("  [WARN] No g/s profiles artifact found; NCC_CALC_07 will be skipped.")

    # ------------------------------------------------------------------
    # NCC_CALC_07: Python vs API comparison  (always runs if data available)
    # ------------------------------------------------------------------
    sc = next(s for s in SCENARIOS if s["id"] == "NCC_CALC_07")
    if sc["n"] >= args.start_from:
        clear_screen()
        print(f"\n  [{sc['id']}] Running Python NCC reference calculation…")
        params = _load_params()
        print(
            f"  Parameters: PRICE_ELEC={params['PRICE_ELEC']:.0f} €/MWh  "
            f"PRICE_GAS={params['PRICE_GAS']:.0f} €/MWh  "
            f"ETA={params['ETA']}  M_SOL={params['M_SOL']} MW"
        )

        if gs_profiles_path:
            sched_path  = _find_latest_scheduling(shots)
            if sched_path:
                print(f"  Scheduling artifact: {sched_path.name}")
            else:
                print("  [INFO] No scheduling artifact found; using default offtaker allocation.")

            name_hint = _names_from_costs_api(costs_by_date)
            if name_hint:
                print(f"  Offtaker names (from CostsBreakdown API): {name_hint}")
            calculated = _calculate_ncc_per_day(gs_profiles_path, sched_path, params, name_hint=name_hint)
            auto, reason = _test_ncc_calc_vs_api(calculated, costs_by_date)
        else:
            auto, reason = "FAIL", "Cannot run: no g/s profiles artifact available."

        result, notes = _ask(sc, auto, reason)
        all_results.append((sc, result, notes, reason))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    if all_results:
        _print_summary(all_results)
    else:
        print("\n  No scenarios were run.")

    print(f"\n  Screenshots and artifacts saved in: {shots}")


if __name__ == "__main__":
    main()
