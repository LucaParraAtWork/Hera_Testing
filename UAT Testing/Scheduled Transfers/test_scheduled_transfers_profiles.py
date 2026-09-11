#!/usr/bin/env python3
"""
test_scheduled_transfers_profiles.py
======================================
Verifies that confirmed scheduled transfers for week+1 produce the expected
loading profiles.  Data capture and validation are merged per screen so that
rich terminal output accompanies every navigation step.

Scenarios
---------
  SCH_CAP  Navigate to Scheduling Screen (week+1):
             – Capture confirmed DropOff/Pickup transfers
             – Display loading sessions table (offtaker, dropped at, picked up at)

  PRF_CAP  Navigate to Profiles then Bay Occupancy (week+1):
             – Capture g/s profile graph  (toggle MW → g/s)
             – Capture bay occupancy      (detect maintenance)
             – For each filling segment display:
                  bay | filling window | max flow | duration | mass | ✓/✗
                  matched loading session + offtaker
             – Auto-validate against ManualFastLoading limits
             – Check: segment count == loading session count

Profile limits (ManualFastLoading)
-----------------------------------
  No maintenance  : max flow ≤ 125 g/s, duration ≤ 165 min
  With maintenance: max flow ≤  69 g/s, duration ≤ 250 min
  Both            : 1 000 kg ≤ total mass ≤ 1 020 kg

Run
---
  python "Scheduled Transfers/test_scheduled_transfers_profiles.py"
  python "Scheduled Transfers/test_scheduled_transfers_profiles.py" --from 2


  STATUS_CHK  From the already-captured scheduling data, show the full
              status breakdown (CONFIRMED / NEW / REJECTED) and verify
              that profiles + occupancy reflect CONFIRMED transfers only:
                – NEW  transfers must NOT add segments to profiles or occupations
                    to bay occupancy.
                – REJECTED transfers must NOT appear in profiles or occupancy.

  REJ_ALL     Reject every confirmed slot on the scheduling screen, then
              re-capture profiles and bay occupancy.  Both must be empty:
                – 0 filling segments in profiles (no truck fillings graph activity)
                – 0 Drop / Filling / Pick occupations in bay occupancy
                  (maintenance windows, if any, are preserved and excluded from check)
              !! This scenario modifies live data – all confirmed transfers
                 for week+1 will be set to REJECTED. Re-import to restore.

Profile limits (ManualFastLoading)
-----------------------------------
  No maintenance  : max flow ≤ 125 g/s, duration ≤ 165 min
  With maintenance: max flow ≤  69 g/s, duration ≤ 250 min
  Both            : 1 000 kg ≤ total mass ≤ 1 020 kg

Run
---
  python "Scheduled Transfers/test_scheduled_transfers_profiles.py"
  python "Scheduled Transfers/test_scheduled_transfers_profiles.py" --from 3
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
SLOWMO = 0

# ==========================================================================
# PROFILE VALIDATION LIMITS  (ManualFastLoading)
# ==========================================================================
LOADING_TYPE = "ManualFastLoading"

LIMITS = {
    "no_maintenance":   {"max_flow_gs": 125.0, "max_duration_min": 165.0},
    "with_maintenance": {"max_flow_gs":  69.0, "max_duration_min": 250.0},
}
MASS_MIN_KG = 1000.0
MASS_MAX_KG = 1020.0
TIMESTEP_S  = 900   # each profile point = 15 min = 900 s

# Session-to-segment matching tolerance (minutes)
MATCH_TOLERANCE_MIN = 30

# Bay-occupancy derivation rules (from business spec)
DROP_DURATION_MIN = 105   # Drop occupation = first 1h45 from dropoff_slot_start
PICK_EXTRA_MIN    = 45    # Pick occupation ends 45 min after pickup_slot_end

# ==========================================================================
# SCENARIOS
# ==========================================================================
SCENARIOS = [
    {"n": 1, "id": "SCHED_TRF_01", "expect": "PASS",
     "name": "Capture scheduling screen (week+1) – identify loading sessions",
     "desc": ("Navigate to /nominations/weekly, go to week+1, capture SchedulingScreen "
               "JSON, print loading-session table (offtaker · dropped at · picked up at).")},

    {"n": 2, "id": "SCHED_TRF_02", "expect": "PASS",
     "name": "Capture profiles & bay occupancy (week+1) – raw data collection",
     "desc": ("Navigate to /schedules/profiles (toggle g/s) then /schedules/occupancy. "
               "Capture both API responses and print the filling-segment reference table. "
               "PASS if both responses are captured and 'Truck fillings by loading bay' graph is found.")},

    {"n": 3, "id": "SCHED_TRF_03", "expect": "PASS",
     "name": "Count: loading sessions == filling segments",
     "desc": ("From captured data (requires SCHED_TRF_01+02): verify that the number of "
               "confirmed DropOff→Pickup loading sessions equals the number of filling "
               "segments in the profiles screen. Show per-offtaker breakdown.")},

    {"n": 4, "id": "SCHED_TRF_04", "expect": "PASS",
     "name": "ManualFastLoading limits per filling segment",
     "desc": ("From captured profiles + occupancy data: for every filling segment check "
               "max flow ≤ limit, duration ≤ limit, mass ∈ [1000–1020] kg. "
               "Limits depend on whether maintenance overlaps the bay during the fill.")},

    {"n": 5, "id": "SCHED_TRF_05", "expect": "PASS",
     "name": "Profiles ↔ Bay Occupancy alignment – Filling occupation per bay",
     "desc": ("For every bay: each Filling occupation in bay occupancy must match a "
               "profile filling segment in the same bay (±30 min), and vice versa.")},

    {"n": 6, "id": "SCHED_TRF_06", "expect": "PASS",
     "name": "Bay Occupancy derivation – Drop/Filling/Pick from loading sessions",
     "desc": ("For every loading session, derive expected Drop (DS→DS+1h45), "
               "Filling (DS+1h45→LE), Pick (LE→PE+45min) and verify against actual "
               "bay occupancy (±30 min tolerance).")},

    {"n": 7, "id": "SCHED_TRF_07", "expect": "PASS",
     "name": "NEW & REJECTED transfers must not appear in profiles or bay occupancy",
     "desc": ("From captured scheduling data show CONFIRMED/NEW/REJECTED counts. "
               "Verify profiles segment count == confirmed-only sessions (NEW and REJECTED "
               "transfers must be absent from both profiles and bay occupancy).")},

    {"n": 8, "id": "SCHED_TRF_08", "expect": "PASS",
     "name": "Reject all confirmed slots → profiles & bay occupancy must clear to zero",
     "desc": ("!! Modifies live data: rejects every confirmed slot for week+1. "
               "Re-captures profiles (g/s) and bay occupancy. "
               "PASS if: 0 filling segments in profiles AND 0 Drop/Filling/Pick "
               "in bay occupancy.")},
]

# ==========================================================================
# Internals
# ==========================================================================
import argparse, json, re, sys, time, csv as _csv
from datetime import date, datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uat_excel_reporter import record_results, print_test_case_info

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent

# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
def _ws(): time.sleep(WAIT_S)
def _wm(): time.sleep(WAIT_M)
def _wl(): time.sleep(WAIT_L)

def _quiet(page, ms=8000):
    try: page.wait_for_load_state("networkidle", timeout=ms)
    except Exception: pass

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
# Week: current ISO week + 1
# --------------------------------------------------------------------------
def _compute_target_week():
    today   = date.today()
    nxt     = today.__class__.fromordinal(today.toordinal() + 7)
    nxt_iso = nxt.isocalendar()
    return nxt_iso.week, nxt_iso.year

# --------------------------------------------------------------------------
# Week navigation
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

# --------------------------------------------------------------------------
# API response capture
# The handler always overwrites _CAPTURED[key] so the LAST response
# (after week navigation) is stored rather than the initial page-load one.
# --------------------------------------------------------------------------
_CAPTURED: dict = {}

def _make_handler(captures):
    """
    captures: list of (key, url_substring).
    Always overwrites so the post-navigation (target week) response wins.
    """
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
# JSON helper: handle both raw API bodies and hera_import_and_profiles wrappers
# --------------------------------------------------------------------------
def _inner(body):
    """Return payload dict regardless of whether it lives under a 'data' key."""
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        return body["data"]
    return body

# --------------------------------------------------------------------------
# Display helpers
# --------------------------------------------------------------------------
def _dt(iso):
    """Short human-readable timestamp: 'Mon 02:15Z'"""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        day = d.strftime("%a")
        return f"{day} {d.strftime('%H:%M')}Z"
    except Exception:
        return iso[:16]

def _hm(minutes):
    """Format minutes as 'Xh YY'"""
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}" if h else f"{m}min"

def _tick(ok): return "✓" if ok else "✗"

# --------------------------------------------------------------------------
# Data processing
# --------------------------------------------------------------------------
def _compute_loading_sessions(scheduling_body):
    """
    Confirmed DropOff→Pickup pairs per offtaker → loading session windows.
    Within the same slot, Pickup is processed before DropOff so that the
    Pickup closes the previous session while DropOff opens the next one.

    Returns list of dicts:
        offtaker_id, dropoff_slot_start, load_start, load_end, pickup_slot_end
    """
    slots = _inner(scheduling_body).get("slots", [])
    by_offtaker: dict = {}
    for slot in slots:
        for t in slot.get("scheduledTransfers", []):
            if t.get("status") != "Confirmed":
                continue
            cid = t.get("companyId")
            if not cid:
                continue
            by_offtaker.setdefault(cid, []).append({
                "type":       t["type"],
                "slot_start": slot["start"],
                "slot_end":   slot["end"],
            })

    sessions = []
    for offtaker_id, transfers in by_offtaker.items():
        transfers.sort(key=lambda t: (t["slot_start"], 0 if t["type"] == "Pickup" else 1))
        pending_do = None
        for t in transfers:
            if t["type"] == "DropOff":
                pending_do = t
            elif t["type"] == "Pickup" and pending_do is not None:
                if t["slot_start"] > pending_do["slot_start"]:
                    sessions.append({
                        "offtaker_id":       offtaker_id,
                        "dropoff_slot_start": pending_do["slot_start"],
                        "load_start":         pending_do["slot_end"],
                        "load_end":           t["slot_start"],
                        "pickup_slot_end":    t["slot_end"],
                    })
                    pending_do = None
    sessions.sort(key=lambda s: s["load_start"])
    return sessions


def _extract_filling_segments(profiles_body):
    """Contiguous positive-flow blocks from 'Truck fillings by loading bay'."""
    inner  = _inner(profiles_body)
    graphs = inner.get("profileGraphs", [])
    truck  = next(
        (g for g in graphs if g.get("title") == "Truck fillings by loading bay"),
        None)
    if not truck:
        return []

    segments = []
    for line in truck.get("profileGraphLines", []):
        bay_name = line.get("name")
        if not bay_name:
            continue
        pts = sorted(
            [(ts, float(v)) for ts, v in line.get("points", {}).items()],
            key=lambda x: x[0])
        current: list = []
        for ts, flow in pts:
            if flow > 0:
                current.append((ts, flow))
            else:
                if current:
                    segments.append(_build_segment(bay_name, current))
                    current = []
        if current:
            segments.append(_build_segment(bay_name, current))
    segments.sort(key=lambda s: s["t_start"])
    return segments


def _build_segment(bay_name, points):
    flows         = [f for _, f in points]
    t_start       = datetime.fromisoformat(points[0][0].replace("Z", "+00:00"))
    t_end         = datetime.fromisoformat(points[-1][0].replace("Z", "+00:00"))
    # +2×TIMESTEP: one interval before the first non-zero point (ramp-up from 0)
    # and one interval after the last non-zero point (ramp-down to 0).
    # e.g. first=01:30, last=03:45 → (135 + 30) = 165 min
    duration_min  = (t_end - t_start).total_seconds() / 60.0 + 2 * TIMESTEP_S / 60.0
    total_mass_kg = sum(f * TIMESTEP_S for _, f in points) / 1000.0
    return {
        "bay":           bay_name,
        "points":        points,
        "max_flow":      max(flows),
        "duration_min":  duration_min,
        "total_mass_kg": total_mass_kg,
        "t_start":       t_start.isoformat(),
        "t_end":         t_end.isoformat(),
    }


def _detect_maintenance_map(occupancy_body):
    """bay_label → list of (from_iso, till_iso) maintenance windows."""
    mmap: dict = {}
    for bay in _inner(occupancy_body).get("bays", []):
        label   = bay.get("label") or bay.get("name") or str(bay.get("id", ""))
        windows = [
            (occ["from"], occ["till"])
            for occ in bay.get("bayOccupations", [])
            if occ.get("type", "").lower() == "maintenance"
        ]
        if windows:
            mmap[label] = windows
    return mmap


def _segment_has_maintenance(segment, mmap):
    windows = mmap.get(segment["bay"], [])
    if not windows:
        return False
    seg_s = datetime.fromisoformat(segment["t_start"])
    seg_e = datetime.fromisoformat(segment["t_end"])
    for (mf, mt) in windows:
        try:
            ms = datetime.fromisoformat(mf.replace("Z", "+00:00"))
            me = datetime.fromisoformat(mt.replace("Z", "+00:00"))
            if seg_s < me and seg_e > ms:
                return True
        except Exception:
            pass
    return False


def _match_to_session(segment, sessions):
    """Return the loading session whose window best contains this filling segment."""
    tol   = timedelta(minutes=MATCH_TOLERANCE_MIN)
    seg_s = datetime.fromisoformat(segment["t_start"])
    seg_e = datetime.fromisoformat(segment["t_end"])
    for sess in sessions:
        ls = datetime.fromisoformat(sess["load_start"].replace("Z", "+00:00"))
        le = datetime.fromisoformat(sess["load_end"].replace("Z", "+00:00"))
        if (ls - tol) <= seg_s and seg_e <= (le + tol):
            return sess
    return None


def _align_profiles_occupancy(segments, occ_data):
    """
    Bidirectional alignment check between bay occupancy Filling windows and
    profile filling segments.

    Matching logic
    ──────────────
    Profile `t_start` = first_nonzero − 15 min  →  fill_s = t_start + TIMESTEP_S
    Profile `t_end`   = last_nonzero  + 15 min  →  fill_e = t_end
    Occupancy `from`  ≈ fill_s   (within ±MATCH_TOLERANCE_MIN)
    Occupancy `till`  ≈ fill_e   (within ±MATCH_TOLERANCE_MIN)

    Returns
    ───────
    issues        – list of human-readable mismatch strings
    matched_pairs – list of dicts with occ window + segment + deltas
    unmatched_occ – occupancy Filling windows with no profile segment
    fill_occs     – {bay_label: [{"from": dt, "till": dt}, …]} full reference
    """
    TOL        = timedelta(minutes=MATCH_TOLERANCE_MIN)
    STEP_S     = TIMESTEP_S                        # 900 s = 15 min

    # ── Collect Filling occupations per bay ───────────────────────────────
    fill_occs: dict = {}
    for bay in _inner(occ_data).get("bays", []):
        label = bay.get("label") or bay.get("name") or ""
        for occ in bay.get("bayOccupations", []):
            if occ.get("type", "").lower() == "filling":
                try:
                    f = datetime.fromisoformat(occ["from"].replace("Z", "+00:00"))
                    t = datetime.fromisoformat(occ["till"].replace("Z", "+00:00"))
                    fill_occs.setdefault(label, []).append({"from": f, "till": t})
                except Exception:
                    pass

    # ── Index profile segments by bay ────────────────────────────────────
    seg_by_bay: dict = {}
    for seg in segments:
        seg_by_bay.setdefault(seg["bay"], []).append(seg)

    matched_pairs = []
    unmatched_occ = []
    issues        = []

    all_bays = sorted(set(fill_occs) | set(seg_by_bay))

    for bay in all_bays:
        occs = sorted(fill_occs.get(bay, []), key=lambda o: o["from"])
        segs = sorted(seg_by_bay.get(bay, []),  key=lambda s: s["t_start"])
        used = set()  # indices into segs that are already matched

        for occ in occs:
            best_idx   = None
            best_total = float("inf")
            best_ds = best_de = 0.0

            for j, seg in enumerate(segs):
                if j in used:
                    continue
                # actual fill window in profile
                fill_s = (datetime.fromisoformat(seg["t_start"])
                          + timedelta(seconds=STEP_S))
                fill_e = datetime.fromisoformat(seg["t_end"])
                ds = abs((occ["from"] - fill_s).total_seconds())
                de = abs((occ["till"] - fill_e).total_seconds())
                if ds <= TOL.total_seconds() and de <= TOL.total_seconds():
                    if ds + de < best_total:
                        best_total = ds + de
                        best_idx   = j
                        best_ds, best_de = ds / 60.0, de / 60.0

            if best_idx is not None:
                used.add(best_idx)
                matched_pairs.append({
                    "bay":             bay,
                    "occ_from":        occ["from"],
                    "occ_till":        occ["till"],
                    "seg":             segs[best_idx],
                    "delta_start_min": best_ds,
                    "delta_end_min":   best_de,
                })
            else:
                unmatched_occ.append({"bay": bay, "from": occ["from"], "till": occ["till"]})
                issues.append(
                    f"OCC Filling [{bay}] "
                    f"{occ['from'].strftime('%a %H:%M')} → {occ['till'].strftime('%a %H:%M')} "
                    f"has no matching profile segment (±{MATCH_TOLERANCE_MIN} min)")

        # Profile segments without a paired occupancy Filling
        for j, seg in enumerate(segs):
            if j not in used:
                issues.append(
                    f"Profile segment [{bay}] "
                    f"{seg['t_start'][:16]} → {seg['t_end'][:16]} "
                    f"has no matching bay occupancy Filling")

    return issues, matched_pairs, unmatched_occ, fill_occs


def _print_alignment_table(matched_pairs, unmatched_occ, fill_occs, segments):
    """Print the profiles ↔ bay occupancy alignment table to stdout."""
    n_occ  = sum(len(v) for v in fill_occs.values())
    n_segs = len(segments)
    n_mat  = len(matched_pairs)

    occ_ok  = (n_mat == n_occ)
    seg_ok  = (n_mat == n_segs)

    print(f"\n  {'═'*70}")
    print(f"  PROFILES  ↔  BAY OCCUPANCY  ALIGNMENT")
    print(f"  Filling occupations : {n_mat}/{n_occ}  {_tick(occ_ok)}   "
          f"Profile segments : {n_mat}/{n_segs}  {_tick(seg_ok)}")
    print(f"  {'═'*70}")

    if not matched_pairs and not unmatched_occ:
        print("  (nothing to compare — no Filling occupations or no profile segments)")
        return

    hdr = (f"  {'Bay':<20}  {'Occ Filling (from → till)':^27}"
           f"  {'Profile fill (start → end)':^27}  Δstart  Δend")
    print(hdr)
    print(f"  {'─'*20}  {'─'*27}  {'─'*27}  {'─'*6}  {'─'*6}")

    step_td = timedelta(seconds=TIMESTEP_S)
    for p in sorted(matched_pairs, key=lambda x: (x["bay"], x["occ_from"])):
        seg    = p["seg"]
        fill_s = datetime.fromisoformat(seg["t_start"]) + step_td
        fill_e = datetime.fromisoformat(seg["t_end"])
        occ_w  = (f"{p['occ_from'].strftime('%a %H:%M')} → "
                  f"{p['occ_till'].strftime('%H:%M')}")
        seg_w  = (f"{fill_s.strftime('%a %H:%M')} → "
                  f"{fill_e.strftime('%H:%M')}")
        print(f"  {p['bay']:<20}  {occ_w:^27}  {seg_w:^27}"
              f"  {p['delta_start_min']:>+5.0f}m  {p['delta_end_min']:>+5.0f}m  ✓")

    for u in unmatched_occ:
        occ_w = (f"{u['from'].strftime('%a %H:%M')} → "
                 f"{u['till'].strftime('%H:%M')}")
        print(f"  {u['bay']:<20}  {occ_w:^27}  {'NO PROFILE SEGMENT':^27}  !! FAIL !!")

    print(f"  {'─'*70}")
    if occ_ok and seg_ok:
        print(f"  All {n_mat} Filling occupation(s) aligned with profile segment(s) ✓")
    else:
        if not occ_ok:
            print(f"  !! {n_occ - n_mat} Filling occupation(s) unmatched in profiles")
        if not seg_ok:
            print(f"  !! {n_segs - n_mat} profile segment(s) unmatched in bay occupancy")

def _verify_session_occupancies(sessions, occ_data):
    """
    For every loading session, derive the expected bay occupancy windows
    from the scheduling data and verify them against BayOccupancyScreen.

    Business rules  (specification — current Hera data may not yet conform)
    ──────────────
    Given a loading session with fields:
        DS = dropoff_slot_start,  LE = load_end (= pickup_slot_start),  PE = pickup_slot_end

    Bay active window : DS           →  PE + PICK_EXTRA_MIN
    Drop  (1h45)      : DS           →  DS + DROP_DURATION_MIN   (1h45)
    Filling           : DS + 1h45    →  LE                       (= pickup_slot_start)
    Pick  (2h15)      : LE           →  PE + PICK_EXTRA_MIN      (2h15 = PE-LE + 45min)

    "first 1h45" = entire Drop occupation window from bay open to Filling start
    "last 2h15"  = entire Pick occupation window from Filling end to bay close

    Matching strategy:
      1. Find the bay whose Filling occupation overlaps the expected fill window.
      2. In that bay, verify Drop and Pick also match the derived windows.
    """
    TOL = timedelta(minutes=MATCH_TOLERANCE_MIN)

    # Build per-bay occupation index: {bay_label: {type_str: [{from, till}, ...]}}
    bay_idx: dict = {}
    for bay in _inner(occ_data).get("bays", []):
        label = bay.get("label") or bay.get("name") or ""
        by_type: dict = {}
        for occ in bay.get("bayOccupations", []):
            t = occ.get("type", "?")
            try:
                f = datetime.fromisoformat(occ["from"].replace("Z", "+00:00"))
                g = datetime.fromisoformat(occ["till"].replace("Z", "+00:00"))
                by_type.setdefault(t, []).append({"from": f, "till": g})
            except Exception:
                pass
        bay_idx[label] = by_type

    issues  = []
    results = []

    for sess in sessions:
        DS = datetime.fromisoformat(sess["dropoff_slot_start"].replace("Z", "+00:00"))
        LE = datetime.fromisoformat(sess["load_end"].replace("Z", "+00:00"))      # pickup_slot_start
        PE = datetime.fromisoformat(sess["pickup_slot_end"].replace("Z", "+00:00"))

        exp_drop_from = DS
        exp_drop_till = DS + timedelta(minutes=DROP_DURATION_MIN)   # DS + 1h45
        exp_fill_from = DS + timedelta(minutes=DROP_DURATION_MIN)   # DS + 1h45
        exp_fill_till = LE                                           # pickup_slot_start
        exp_pick_from = LE                                           # pickup_slot_start
        exp_pick_till = PE + timedelta(minutes=PICK_EXTRA_MIN)      # pickup_slot_end + 45min

        # Step 1: find which bay has a Filling occupation matching the fill window
        matched_bay  = None
        act_fill_occ = None
        for bay_label, by_type in bay_idx.items():
            for f_occ in by_type.get("Filling", []):
                if (abs((f_occ["from"] - exp_fill_from).total_seconds()) <= TOL.total_seconds()
                        and abs((f_occ["till"] - exp_fill_till).total_seconds()) <= TOL.total_seconds()):
                    matched_bay  = bay_label
                    act_fill_occ = f_occ
                    break
            if matched_bay:
                break

        sess_issues = []
        act_drop_occ = act_pick_occ = None

        if matched_bay is None:
            sess_issues.append(
                f"No Filling occupation found for fill window "
                f"{exp_fill_from.strftime('%a %H:%M')} → {exp_fill_till.strftime('%H:%M')} "
                f"(±{MATCH_TOLERANCE_MIN} min)")
        else:
            # Step 2: verify Drop in same bay
            for d in bay_idx[matched_bay].get("Drop", []):
                if (abs((d["from"] - exp_drop_from).total_seconds()) <= TOL.total_seconds()
                        and abs((d["till"] - exp_drop_till).total_seconds()) <= TOL.total_seconds()):
                    act_drop_occ = d
                    break
            if act_drop_occ is None:
                sess_issues.append(
                    f"[{matched_bay}] Drop expected "
                    f"{exp_drop_from.strftime('%a %H:%M')} → {exp_drop_till.strftime('%H:%M')} "
                    f"not found (±{MATCH_TOLERANCE_MIN} min)")

            # Step 3: verify Pick in same bay
            for p in bay_idx[matched_bay].get("Pick", []):
                if (abs((p["from"] - exp_pick_from).total_seconds()) <= TOL.total_seconds()
                        and abs((p["till"] - exp_pick_till).total_seconds()) <= TOL.total_seconds()):
                    act_pick_occ = p
                    break
            if act_pick_occ is None:
                sess_issues.append(
                    f"[{matched_bay}] Pick expected "
                    f"{exp_pick_from.strftime('%a %H:%M')} → {exp_pick_till.strftime('%H:%M')} "
                    f"not found (±{MATCH_TOLERANCE_MIN} min)")

        issues.extend(sess_issues)
        results.append({
            "session":        sess,
            "bay":            matched_bay,
            "exp_drop":       (exp_drop_from, exp_drop_till),
            "exp_fill":       (exp_fill_from, exp_fill_till),
            "exp_pick":       (exp_pick_from, exp_pick_till),
            "act_drop":       act_drop_occ,
            "act_fill":       act_fill_occ,
            "act_pick":       act_pick_occ,
            "session_issues": sess_issues,
        })

    return issues, results


def _print_session_occupancy_table(results):
    """
    Print per-session expected vs actual bay occupancy windows.

    For each session:
        Drop    expected DS → DS+1h45        actual from/till  Δstart Δend
        Filling expected DS+1h45 → LE        actual from/till  Δstart Δend
        Pick    expected LE → PE+45min       actual from/till  Δstart Δend
    """
    n_ok = sum(1 for r in results if not r["session_issues"])

    print(f"\n  {'═'*70}")
    print(f"  LOADING SESSION → BAY OCCUPANCY  DERIVATION CHECK  "
          f"({n_ok}/{len(results)} sessions ✓)")
    print(f"  {'═'*70}")
    print(f"  Rules: Drop=1h45 from slot start  |  Pick ends PE+45min  "
          f"|  Filling = in between")

    def _row(label, exp_f, exp_t, actual):
        exp_s = f"{exp_f.strftime('%a %H:%M')} → {exp_t.strftime('%H:%M')}"
        if actual is None:
            return f"    {label:<8}: expected {exp_s:<25}  actual  ?? MISSING !!"
        act_s = f"{actual['from'].strftime('%a %H:%M')} → {actual['till'].strftime('%H:%M')}"
        ds = int((actual["from"] - exp_f).total_seconds() / 60)
        de = int((actual["till"]  - exp_t).total_seconds() / 60)
        ok = abs(ds) <= MATCH_TOLERANCE_MIN and abs(de) <= MATCH_TOLERANCE_MIN
        return (f"    {label:<8}: expected {exp_s:<25}  actual {act_s:<25}"
                f"  Δ={ds:+d}m/{de:+d}m  {_tick(ok)}")

    for i, r in enumerate(results, 1):
        sess = r["session"]
        bay  = r["bay"] or "?? BAY NOT FOUND"
        ok   = not r["session_issues"]
        ds_  = sess.get("dropoff_slot_start", "?")[:16]
        le_  = sess.get("load_end", "?")[:16]
        print(f"\n  Session {i:02d}  [{bay}]  {_tick(ok)}")
        print(f"    Offtaker: {sess.get('offtaker_id', '?')}   "
              f"DropOff slot start: {ds_}  PickUp slot start: {le_}")
        print(_row("Drop",    *r["exp_drop"], r["act_drop"]))
        print(_row("Filling", *r["exp_fill"], r["act_fill"]))
        print(_row("Pick",    *r["exp_pick"], r["act_pick"]))
        for iss in r["session_issues"]:
            print(f"    !! {iss}")

    print(f"\n{SEP}")
    if n_ok == len(results):
        print(f"  Bay occupancy derivation: all {n_ok} session(s) PASS ✓")
    else:
        print(f"  !! {len(results) - n_ok} session(s) FAIL bay occupancy derivation")

# --------------------------------------------------------------------------
# Terminal display
# --------------------------------------------------------------------------
SEP = "  " + "─" * 70

def _print_sessions_table(sessions, week, year):
    print(f"\n  {'═'*70}")
    print(f"  LOADING SESSIONS  –  week {week}/{year}  –  {len(sessions)} session(s)")
    print(f"  {'═'*70}")
    if not sessions:
        print("  (no confirmed sessions found)")
        return
    for i, s in enumerate(sessions, 1):
        load_dur = (
            datetime.fromisoformat(s["load_end"].replace("Z", "+00:00")) -
            datetime.fromisoformat(s["load_start"].replace("Z", "+00:00"))
        )
        dur_min = load_dur.total_seconds() / 60
        offtaker = s["offtaker_id"][:8]
        print(f"  #{i:02d}  Offtaker : …{offtaker}")
        print(f"       Dropped  : {_dt(s['dropoff_slot_start'])} (slot ends {_dt(s['load_start'])})")
        print(f"       Picked   : {_dt(s['pickup_slot_end'])} (slot starts {_dt(s['load_end'])})")
        print(f"       Loading  : {_dt(s['load_start'])} → {_dt(s['load_end'])}  "
              f"({_hm(dur_min)})")
        if i < len(sessions):
            print()


def _print_segments_table(segments, sessions, mmap, week, year):
    print(f"\n  {'═'*70}")
    print(f"  FILLING SEGMENTS  –  week {week}/{year}  –  {len(segments)} segment(s)")
    print(f"  {'═'*70}")
    if not segments:
        print("  (no filling segments found)")
        return

    all_ok = True
    for i, seg in enumerate(segments, 1):
        has_maint = _segment_has_maintenance(seg, mmap)
        mode      = "with_maintenance" if has_maint else "no_maintenance"
        lim       = LIMITS[mode]
        flow_ok   = seg["max_flow"]      <= lim["max_flow_gs"]
        dur_ok    = seg["duration_min"]  <= lim["max_duration_min"]
        mass_ok   = MASS_MIN_KG <= seg["total_mass_kg"] <= MASS_MAX_KG
        seg_ok    = flow_ok and dur_ok and mass_ok
        if not seg_ok:
            all_ok = False

        maint_tag = " [MAINTENANCE]" if has_maint else ""
        print(f"  #{i:02d}  {seg['bay']}{maint_tag}")
        print(f"       Filling  : {_dt(seg['t_start'])} → {_dt(seg['t_end'])}  "
              f"({_hm(seg['duration_min'])})")
        print(f"       Max flow : {seg['max_flow']:>7.1f} g/s  "
              f"(limit {lim['max_flow_gs']:.0f} g/s)  {_tick(flow_ok)}")
        print(f"       Duration : {seg['duration_min']:>7.1f} min  "
              f"(limit {lim['max_duration_min']:.0f} min)   {_tick(dur_ok)}")
        print(f"       Mass     : {seg['total_mass_kg']:>7.2f} kg   "
              f"([{MASS_MIN_KG:.0f}–{MASS_MAX_KG:.0f}] kg)   {_tick(mass_ok)}")

        matched = _match_to_session(seg, sessions)
        if matched:
            print(f"       Session  : {_dt(matched['load_start'])} → {_dt(matched['load_end'])}")
            print(f"       Offtaker : …{matched['offtaker_id'][:8]}")
        else:
            print("       Session  : (no matching loading session found)")

        if not seg_ok:
            issues = []
            if not flow_ok: issues.append(f"flow {seg['max_flow']:.1f}>{lim['max_flow_gs']}")
            if not dur_ok:  issues.append(f"dur {seg['duration_min']:.0f}>{lim['max_duration_min']}")
            if not mass_ok: issues.append(f"mass {seg['total_mass_kg']:.2f} not in [{MASS_MIN_KG},{MASS_MAX_KG}]")
            print(f"       !! FAIL : {', '.join(issues)}")

        if i < len(segments):
            print()

    return all_ok

# --------------------------------------------------------------------------
# User prompt
# --------------------------------------------------------------------------
def _ask(scenario, auto, reason):
    print_test_case_info(scenario["id"])
    label    = "[expected PASS]" if scenario["expect"] == "PASS" else "[observe]"
    aflag    = {"PASS": "AUTO-PASS", "FAIL": "AUTO-FAIL"}.get(auto, f"AUTO-{auto}")
    mismatch = (scenario["expect"] != "?" and auto != scenario["expect"])
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

# --------------------------------------------------------------------------
# Scenario: SCH_CAP
# --------------------------------------------------------------------------
def _do_sch_cap(page, base_url, week, year, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)

    handler = _make_handler([("scheduling", "SchedulingScreen/GetSchedulingScreenAsync")])
    page.on("response", handler)
    try:
        page.goto(f"{base_url}/nominations/weekly", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "01_landed.png"))

        print(f"  Navigating to week {week}/{year}…")
        try: _goto_week_year(page, week, year)
        except Exception as e:
            return "FAIL", f"Week navigation failed: {e}"

        _quiet(page); page.wait_for_timeout(2000)
        page.screenshot(path=str(shots_dir / "02_week.png"), full_page=True)

        if "scheduling" not in _CAPTURED:
            return "FAIL", "SchedulingScreen API not captured"

        sessions = _compute_loading_sessions(_CAPTURED["scheduling"])
        _print_sessions_table(sessions, week, year)

        slots      = _inner(_CAPTURED["scheduling"]).get("slots", [])
        confirmed  = sum(1 for s in slots for t in s.get("scheduledTransfers", [])
                         if t.get("status") == "Confirmed")
        first_date = slots[0]["start"][:10] if slots else "?"

        (shots_dir / "scheduling_data.json").write_text(
            json.dumps(_CAPTURED["scheduling"], indent=2), encoding="utf-8")

        return ("PASS",
                f"{len(sessions)} loading session(s) from {confirmed} confirmed transfer(s). "
                f"First slot: {first_date}")
    finally:
        try: page.remove_listener("response", handler)
        except Exception: pass

# --------------------------------------------------------------------------
# Scenario: SCHED_TRF_02  –  capture profiles & bay occupancy
# --------------------------------------------------------------------------
def _do_prf_capture(page, base_url, week, year, shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)

    # ── Profiles ─────────────────────────────────────────────────────────
    handler_p = _make_handler([("profiles", "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler_p)
    try:
        page.goto(f"{base_url}/schedules/profiles", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "01_profiles_landed.png"))

        print(f"  Navigating to week {week}/{year}…")
        try: _goto_week_year(page, week, year)
        except Exception as e:
            return "FAIL", f"Profiles: week navigation failed: {e}"
        _quiet(page)
        page.screenshot(path=str(shots_dir / "02_profiles_week.png"), full_page=True)

        print("  Switching toggle: MW → g/s…")
        try:
            page.locator("app-toggle button",
                         has_text=re.compile(r"\bMW\b", re.I)).first.click()
            page.wait_for_timeout(3000)
            page.locator("app-toggle button",
                         has_text=re.compile(r"g\s*/\s*s", re.I)).first.click()
            page.wait_for_timeout(3500)
        except Exception as e:
            print(f"  [WARN] Toggle not found: {e}. Waiting for default response…")
            _wl(); _quiet(page)

        page.screenshot(path=str(shots_dir / "03_profiles_gs.png"), full_page=True)

        if "profiles" not in _CAPTURED:
            return "FAIL", "ProfilesScreen API not captured"

        inner  = _inner(_CAPTURED["profiles"])
        graphs = inner.get("profileGraphs", [])
        truck  = next((g for g in graphs
                       if g.get("title") == "Truck fillings by loading bay"), None)
        if not truck:
            titles = [g.get("title") for g in graphs]
            return ("FAIL",
                    f"'Truck fillings by loading bay' not found. "
                    f"Graphs present: {titles}")

        (shots_dir / "profiles_data.json").write_text(
            json.dumps(_CAPTURED["profiles"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler_p)
        except Exception: pass

    # ── Bay Occupancy ─────────────────────────────────────────────────────
    handler_o = _make_handler([("occupancy", "BayOccupancyScreen/GetBayOccupancyScreenAsync")])
    page.on("response", handler_o)
    try:
        page.goto(f"{base_url}/schedules/occupancy", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        page.screenshot(path=str(shots_dir / "04_occupancy_landed.png"))

        print(f"  Navigating occupancy to week {week}/{year}…")
        try: _goto_week_year(page, week, year)
        except Exception as e:
            print(f"  [WARN] Occupancy week navigation failed: {e}")

        _quiet(page); page.wait_for_timeout(2000)
        page.screenshot(path=str(shots_dir / "05_occupancy_week.png"), full_page=True)

        if "occupancy" in _CAPTURED:
            (shots_dir / "occupancy_data.json").write_text(
                json.dumps(_CAPTURED["occupancy"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler_o)
        except Exception: pass

    # ── Reference display (no checks here) ───────────────────────────────
    sessions = _compute_loading_sessions(_CAPTURED.get("scheduling", {}))
    segments  = _extract_filling_segments(_CAPTURED["profiles"])
    mmap      = (_detect_maintenance_map(_CAPTURED["occupancy"])
                 if "occupancy" in _CAPTURED else {})

    occ_note = "detected" if mmap else "none"
    print(f"  Maintenance: {occ_note}")
    _print_segments_table(segments, sessions, mmap, week, year)

    n_segs = len(segments)
    occ_captured = "occupancy" in _CAPTURED
    return ("PASS",
            f"{n_segs} filling segment(s) captured  |  "
            f"Occupancy: {'captured' if occ_captured else 'NOT captured'}")


# --------------------------------------------------------------------------
# Scenario: SCHED_TRF_03  –  count check
# --------------------------------------------------------------------------
def _do_count_chk(shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    if "scheduling" not in _CAPTURED:
        return "FAIL", "No scheduling data — run SCHED_TRF_01 first"
    if "profiles" not in _CAPTURED:
        return "FAIL", "No profiles data — run SCHED_TRF_02 first"

    sessions = _compute_loading_sessions(_CAPTURED["scheduling"])
    segments  = _extract_filling_segments(_CAPTURED["profiles"])

    # Per-offtaker breakdown
    # sessions per offtaker
    sess_by_offtaker: dict = {}
    for s in sessions:
        sess_by_offtaker.setdefault(s["offtaker_id"], []).append(s)

    # segments per offtaker (via session matching)
    mmap = (_detect_maintenance_map(_CAPTURED["occupancy"])
            if "occupancy" in _CAPTURED else {})
    seg_by_offtaker: dict = {}
    unmatched_segs = []
    for seg in segments:
        s = _match_to_session(seg, sessions)
        if s:
            seg_by_offtaker.setdefault(s["offtaker_id"], []).append(seg)
        else:
            unmatched_segs.append(seg)

    n_sess = len(sessions)
    n_segs = len(segments)
    count_ok = (n_sess == n_segs)

    print(f"\n  {'═'*70}")
    print(f"  SCHED_TRF_03  –  Count: loading sessions == filling segments")
    print(f"  {'═'*70}")
    print(f"  Loading sessions   : {n_sess}")
    print(f"  Filling segments   : {n_segs}")
    print(f"  Match              : {_tick(count_ok)}")

    all_offtakers = sorted(set(list(sess_by_offtaker) + list(seg_by_offtaker)))
    if all_offtakers:
        print(f"\n  Per-offtaker breakdown:")
        print(f"  {'Offtaker':<38}  Sessions  Segments  Match")
        print(f"  {'─'*38}  {'─'*8}  {'─'*8}  {'─'*5}")
        for oid in all_offtakers:
            ns = len(sess_by_offtaker.get(oid, []))
            ng = len(seg_by_offtaker.get(oid, []))
            print(f"  …{oid[-8:]:<37}  {ns:>8}  {ng:>8}  {_tick(ns == ng)}")

    if unmatched_segs:
        print(f"\n  !! {len(unmatched_segs)} segment(s) not matched to any session:")
        for seg in unmatched_segs:
            print(f"     {seg['bay']}  {seg['t_start'][:16]} → {seg['t_end'][:16]}")

    if count_ok:
        return "PASS", f"{n_sess} session(s) == {n_segs} segment(s) ✓"
    return "FAIL", f"{n_sess} session(s) ≠ {n_segs} segment(s)"


# --------------------------------------------------------------------------
# Scenario: SCHED_TRF_04  –  ManualFastLoading limits
# --------------------------------------------------------------------------
def _do_limits_chk(shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    if "profiles" not in _CAPTURED:
        return "FAIL", "No profiles data — run SCHED_TRF_02 first"

    sessions = _compute_loading_sessions(_CAPTURED.get("scheduling", {}))
    segments  = _extract_filling_segments(_CAPTURED["profiles"])
    mmap      = (_detect_maintenance_map(_CAPTURED["occupancy"])
                 if "occupancy" in _CAPTURED else {})

    print(f"\n  {'═'*70}")
    print(f"  SCHED_TRF_04  –  ManualFastLoading limits per filling segment")
    print(f"  {'═'*70}")
    print(f"  No-maintenance  : max ≤ {LIMITS['no_maintenance']['max_flow_gs']} g/s, "
          f"dur ≤ {LIMITS['no_maintenance']['max_duration_min']} min")
    print(f"  With-maintenance: max ≤ {LIMITS['with_maintenance']['max_flow_gs']} g/s, "
          f"dur ≤ {LIMITS['with_maintenance']['max_duration_min']} min")
    print(f"  Both modes      : mass ∈ [{MASS_MIN_KG}–{MASS_MAX_KG}] kg")

    all_ok = True
    lines  = []
    for i, seg in enumerate(segments, 1):
        has_m = _segment_has_maintenance(seg, mmap)
        ok, iss, _ = _check_segment_values(seg, has_m)
        mode  = "with maint" if has_m else "no maint"
        lim   = LIMITS["with_maintenance" if has_m else "no_maintenance"]
        sess  = _match_to_session(seg, sessions)
        otak  = ("…" + sess["offtaker_id"][-8:]) if sess else "?"

        flow_ok  = seg["max_flow"]    <= lim["max_flow_gs"]
        dur_ok   = seg["duration_min"] <= lim["max_duration_min"]
        mass_ok  = MASS_MIN_KG <= seg["total_mass_kg"] <= MASS_MAX_KG

        print(f"\n  #{i:02d}  {seg['bay']}  [{mode}]  {_tick(ok)}")
        print(f"       Offtaker : {otak}")
        print(f"       Max flow : {seg['max_flow']:>8.1f} g/s  "
              f"(limit {lim['max_flow_gs']} g/s)  {_tick(flow_ok)}")
        print(f"       Duration : {seg['duration_min']:>8.1f} min  "
              f"(limit {lim['max_duration_min']} min)  {_tick(dur_ok)}")
        print(f"       Mass     : {seg['total_mass_kg']:>8.2f} kg  "
              f"([{MASS_MIN_KG}–{MASS_MAX_KG}] kg)  {_tick(mass_ok)}")
        for iss_item in iss:
            print(f"       !! {iss_item}")
        if not ok:
            all_ok = False
        lines.append(f"  [{i:02d}] {'PASS' if ok else 'FAIL'}  "
                     f"bay={seg['bay']} mode={mode} "
                     f"flow={seg['max_flow']:.1f} dur={seg['duration_min']:.0f} "
                     f"mass={seg['total_mass_kg']:.2f}")

    (shots_dir / "limits_report.txt").write_text("\n".join(lines), encoding="utf-8")

    if all_ok:
        return "PASS", f"All {len(segments)} segment(s) within ManualFastLoading limits ✓"
    n_fail = sum(1 for seg in segments
                 if not _check_segment_values(
                     seg, _segment_has_maintenance(seg, mmap))[0])
    return "FAIL", f"{n_fail}/{len(segments)} segment(s) exceed ManualFastLoading limits"


# --------------------------------------------------------------------------
# Scenario: SCHED_TRF_05  –  profiles ↔ bay occupancy alignment
# --------------------------------------------------------------------------
def _do_align_chk(shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    if "profiles" not in _CAPTURED:
        return "FAIL", "No profiles data — run SCHED_TRF_02 first"
    if "occupancy" not in _CAPTURED:
        return "FAIL", "No occupancy data — run SCHED_TRF_02 first"

    segments = _extract_filling_segments(_CAPTURED["profiles"])
    issues, matched, unmatched_occ, fill_occs = \
        _align_profiles_occupancy(segments, _CAPTURED["occupancy"])
    _print_alignment_table(matched, unmatched_occ, fill_occs, segments)

    if not issues:
        return ("PASS",
                f"{len(matched)} Filling occupation(s) aligned with profile segment(s) ✓")
    return "FAIL", f"{len(issues)} profile↔occupancy mismatch(es)"


# --------------------------------------------------------------------------
# Scenario: SCHED_TRF_06  –  bay occupancy derivation from loading sessions
# --------------------------------------------------------------------------
def _do_deriv_chk(shots_dir):
    shots_dir.mkdir(parents=True, exist_ok=True)
    if "scheduling" not in _CAPTURED:
        return "FAIL", "No scheduling data — run SCHED_TRF_01 first"
    if "occupancy" not in _CAPTURED:
        return "FAIL", "No occupancy data — run SCHED_TRF_02 first"

    sessions = _compute_loading_sessions(_CAPTURED["scheduling"])
    issues, results = _verify_session_occupancies(sessions, _CAPTURED["occupancy"])
    _print_session_occupancy_table(results)

    if not issues:
        return ("PASS",
                f"All {len(results)} session(s) match expected Drop/Filling/Pick windows ✓")
    return "FAIL", f"{len(issues)} occupancy derivation issue(s)"


def _check_segment_values(segment, has_maintenance):
    mode   = "with_maintenance" if has_maintenance else "no_maintenance"
    lim    = LIMITS[mode]
    issues = []
    if segment["max_flow"] > lim["max_flow_gs"]:
        issues.append(f"flow {segment['max_flow']:.1f}>{lim['max_flow_gs']} g/s")
    if segment["duration_min"] > lim["max_duration_min"]:
        issues.append(f"dur {segment['duration_min']:.0f}>{lim['max_duration_min']} min")
    if not (MASS_MIN_KG <= segment["total_mass_kg"] <= MASS_MAX_KG):
        issues.append(f"mass {segment['total_mass_kg']:.2f} not in [{MASS_MIN_KG},{MASS_MAX_KG}]")
    detail = (f"bay={segment['bay']}, {mode}, "
              f"max={segment['max_flow']:.1f} g/s, "
              f"dur={segment['duration_min']:.0f} min, "
              f"mass={segment['total_mass_kg']:.2f} kg")
    return (len(issues) == 0), issues, detail

# --------------------------------------------------------------------------
# Scenario: STATUS_CHK
# --------------------------------------------------------------------------
def _do_status_chk(shots_dir):
    """
    From the already-captured scheduling data, show a breakdown of
    CONFIRMED / NEW / REJECTED transfer counts and the sessions each
    status group would generate.  Then compare with the profiles segment
    count to prove that only CONFIRMED transfers are reflected.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)

    if "scheduling" not in _CAPTURED:
        return "FAIL", "No scheduling data — run SCHED_TRF_01 first"
    if "profiles" not in _CAPTURED:
        return "FAIL", "No profiles data — run SCHED_TRF_02 first"

    slots = _inner(_CAPTURED["scheduling"]).get("slots", [])

    # Count raw transfers per status
    counts: dict = {"Confirmed": 0, "New": 0, "Rejected": 0, "Other": 0}
    for slot in slots:
        for t in slot.get("scheduledTransfers", []):
            st = t.get("status", "Other")
            counts[st] = counts.get(st, 0) + 1

    # Sessions each group would generate (using same pairing algorithm)
    def _sessions_for_status(target_status):
        body = {"slots": [
            {**slot, "scheduledTransfers": [
                t for t in slot.get("scheduledTransfers", [])
                if t.get("status") == target_status
            ]}
            for slot in slots
        ]}
        return _compute_loading_sessions(body)

    confirmed_sessions = _sessions_for_status("Confirmed")
    new_sessions       = _sessions_for_status("New")
    rejected_sessions  = _sessions_for_status("Rejected")

    segments = _extract_filling_segments(_CAPTURED["profiles"])
    mmap     = (_detect_maintenance_map(_CAPTURED["occupancy"])
                if "occupancy" in _CAPTURED else {})

    # Bay occupancy: count non-maintenance occupations per type
    occ_counts: dict = {}
    if "occupancy" in _CAPTURED:
        for bay in _inner(_CAPTURED["occupancy"]).get("bays", []):
            for occ in bay.get("bayOccupations", []):
                t = occ.get("type", "?")
                occ_counts[t] = occ_counts.get(t, 0) + 1

    n_conf_sess = len(confirmed_sessions)
    n_new_sess  = len(new_sessions)
    n_rej_sess  = len(rejected_sessions)
    n_segs      = len(segments)

    print(f"\n  {'═'*70}")
    print(f"  TRANSFER STATUS BREAKDOWN  –  week data")
    print(f"  {'═'*70}")
    print(f"  Raw transfers  :  Confirmed={counts.get('Confirmed',0)}  "
          f"New={counts.get('New',0)}  Rejected={counts.get('Rejected',0)}")
    print(f"  Loading sessions if CONFIRMED only  : {n_conf_sess}")
    print(f"  Loading sessions if NEW      only   : {n_new_sess}  "
          f"{'(none — correct)' if n_new_sess == 0 else '(exist — will they appear in profiles?)'}")
    print(f"  Loading sessions if REJECTED only   : {n_rej_sess}  "
          f"{'(none — correct)' if n_rej_sess == 0 else '(exist — will they appear in profiles?)'}")

    print(f"\n  PROFILES  (filling segments) : {n_segs}")
    print(f"  Expected (confirmed only)    : {n_conf_sess}")

    print(f"\n  BAY OCCUPANCY breakdown:")
    if occ_counts:
        for t, c in sorted(occ_counts.items()):
            note = ""
            if t.lower() == "maintenance":
                note = "  (preserved — not linked to transfer status)"
            elif t.lower() in ("drop", "filling", "pick"):
                note = "  ← driven by confirmed transfers only"
            print(f"    {t:<15}: {c}{note}")
    else:
        print("    (occupancy data not available)")

    # --- Checks ---
    issues = []

    # 1. Profiles count == confirmed sessions only
    if n_segs != n_conf_sess:
        issues.append(
            f"profiles has {n_segs} segment(s) but {n_conf_sess} confirmed session(s)")

    # 2. NEW transfers must NOT add segments beyond confirmed count
    if n_new_sess > 0 and n_segs > n_conf_sess:
        issues.append(
            f"{n_new_sess} NEW session(s) appear to have added {n_segs - n_conf_sess} "
            f"extra profile segment(s) — profiles should ignore NEW transfers")

    # 3. REJECTED transfers must NOT add segments
    if n_rej_sess > 0 and n_segs > n_conf_sess:
        issues.append(
            f"{n_rej_sess} REJECTED session(s) appear to have added extra profile segments")

    # 4. Bay occupancy: non-maintenance occupation count should match confirmed only
    #    (Drop + Filling + Pick should not include NEW or REJECTED contributions)
    non_maint_occ = sum(v for k, v in occ_counts.items()
                        if k.lower() not in ("maintenance",))
    # Each confirmed loading session produces 1 Drop + 1 Filling + 1 Pick = 3 occupations
    expected_occ = n_conf_sess * 3
    if non_maint_occ != expected_occ:
        issues.append(
            f"bay occupancy has {non_maint_occ} Drop/Filling/Pick occupation(s), "
            f"expected {expected_occ} ({n_conf_sess} sessions × 3)")

    print(f"\n  {'─'*70}")
    if not issues:
        print(f"  Result  : PASS — profiles & occupancy reflect CONFIRMED transfers only ✓")
    else:
        for iss in issues:
            print(f"  !! {iss}")

    (shots_dir / "status_report.txt").write_text(
        "\n".join([
            f"SCHED_TRF_07 report",
            f"Transfers : Confirmed={counts.get('Confirmed',0)} "
            f"New={counts.get('New',0)} Rejected={counts.get('Rejected',0)}",
            f"Sessions  : confirmed={n_conf_sess} new={n_new_sess} rejected={n_rej_sess}",
            f"Segments  : {n_segs}",
            f"Bay occ   : {occ_counts}",
            "",
        ] + (["PASS"] if not issues else ["FAIL"] + issues)),
        encoding="utf-8")

    if not issues:
        return ("PASS",
                f"Profiles: {n_segs}/{n_conf_sess} confirmed ✓  "
                f"NEW={n_new_sess} not in profiles ✓  "
                f"Rejected={n_rej_sess} not in profiles ✓")
    return "FAIL", "; ".join(issues)

# --------------------------------------------------------------------------
# Scenario: REJ_ALL
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

def _slot_bounding_boxes(page):
    """Return bounding boxes of filled, non-rejected slots (confirmed or new)."""
    _quiet(page)
    found = page.locator("div.slot:not(.status-rejected)")
    seen = {}
    for i in range(found.count()):
        try:
            el    = found.nth(i)
            inner = el.inner_html(timeout=800).strip()
            if len(inner) < 10:
                continue
            bb = el.bounding_box(timeout=1000)
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

def _do_rej_all(page, base_url, week, year, shots_dir):
    """
    REJ_ALL:
      1. Navigate to Scheduling Screen for week+1.
      2. Reject every non-rejected slot (confirmed or new).
      3. Re-capture Profiles (g/s) for week+1 → expect 0 filling segments.
      4. Re-capture Bay Occupancy for week+1 → expect 0 Drop/Filling/Pick occupations.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("  ┌─────────────────────────────────────────────────────────────┐")
    print("  │  !! WARNING – REJ_ALL will reject ALL confirmed transfers   │")
    print("  │     for week+1.  Re-import nominations CSV to restore.      │")
    print("  │  Press Enter to continue, or Ctrl+C to abort.              │")
    print("  └─────────────────────────────────────────────────────────────┘")
    try:
        input("  > ")
    except KeyboardInterrupt:
        return "?", "Aborted by user"

    # ── Step 1: reject all non-rejected slots on scheduling screen ────────
    handler_s = _make_handler([("scheduling_post_rej",
                                "SchedulingScreen/GetSchedulingScreenAsync")])
    page.on("response", handler_s)
    try:
        page.goto(f"{base_url}/nominations/weekly", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        print(f"  Navigating to week {week}/{year}…")
        try: _goto_week_year(page, week, year)
        except Exception as e:
            return "FAIL", f"Week navigation failed: {e}"
        _quiet(page)
        page.screenshot(path=str(shots_dir / "01_before_reject.png"), full_page=True)

        bbs   = _slot_bounding_boxes(page)
        total = len(bbs)
        print(f"  Found {total} non-rejected slot(s) to reject.")
        done = errors = 0

        for idx, bb in enumerate(bbs, 1):
            slot_dir = shots_dir / f"slot_{idx:02d}"
            slot_dir.mkdir(exist_ok=True)
            _click_slot_bb(page, bb)
            _wm()
            try:
                modal = page.locator("dialog[open]").last
                modal.wait_for(state="visible", timeout=8000)
            except Exception:
                print(f"    Slot {idx}: modal did not open, skipping"); errors += 1
                try: page.keyboard.press("Escape")
                except Exception: pass
                continue

            page.screenshot(path=str(slot_dir / "01_modal.png"))
            try:
                reject_btn = modal.get_by_role(
                    "button", name=re.compile(r"^Reject$|^Cancel$|^Unconfirm$", re.I)).first
                _safe_click(page, reject_btn, "Reject")
                try: modal.wait_for(state="detached", timeout=10_000)
                except Exception: pass
                _quiet(page)
                page.screenshot(path=str(slot_dir / "02_after.png"))
                print(f"    Slot {idx}/{total}: rejected ✓")
                done += 1
            except Exception as e:
                errors += 1
                print(f"    Slot {idx}/{total}: error – {e}")
                try: page.keyboard.press("Escape")
                except Exception: pass

        page.screenshot(path=str(shots_dir / "02_after_reject_all.png"), full_page=True)
        print(f"  Rejected: {done}/{total}  Errors: {errors}")
    finally:
        try: page.remove_listener("response", handler_s)
        except Exception: pass

    # ── Step 2: re-capture Profiles ───────────────────────────────────────
    _CAPTURED.pop("profiles_post_rej", None)
    handler_p = _make_handler([("profiles_post_rej",
                                "ProfilesScreen/GetProfilesScreenAsync")])
    page.on("response", handler_p)
    try:
        page.goto(f"{base_url}/schedules/profiles", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        print(f"  Re-navigating profiles to week {week}/{year}…")
        try: _goto_week_year(page, week, year)
        except Exception as e:
            print(f"  [WARN] Profiles week nav failed: {e}")
        _quiet(page)

        # Toggle MW → g/s
        try:
            page.locator("app-toggle button",
                         has_text=re.compile(r"\bMW\b", re.I)).first.click()
            page.wait_for_timeout(3000)
            page.locator("app-toggle button",
                         has_text=re.compile(r"g\s*/\s*s", re.I)).first.click()
            page.wait_for_timeout(3500)
        except Exception:
            _wl(); _quiet(page)

        page.screenshot(path=str(shots_dir / "03_profiles_post_rej.png"), full_page=True)
        if "profiles_post_rej" in _CAPTURED:
            (shots_dir / "profiles_post_rej.json").write_text(
                json.dumps(_CAPTURED["profiles_post_rej"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler_p)
        except Exception: pass

    # ── Step 3: re-capture Bay Occupancy ─────────────────────────────────
    _CAPTURED.pop("occupancy_post_rej", None)
    handler_o = _make_handler([("occupancy_post_rej",
                                "BayOccupancyScreen/GetBayOccupancyScreenAsync")])
    page.on("response", handler_o)
    try:
        page.goto(f"{base_url}/schedules/occupancy", wait_until="domcontentloaded")
        _wm(); _quiet(page)
        print(f"  Re-navigating occupancy to week {week}/{year}…")
        try: _goto_week_year(page, week, year)
        except Exception as e:
            print(f"  [WARN] Occupancy week nav failed: {e}")
        _quiet(page); page.wait_for_timeout(2000)
        page.screenshot(path=str(shots_dir / "04_occupancy_post_rej.png"), full_page=True)
        if "occupancy_post_rej" in _CAPTURED:
            (shots_dir / "occupancy_post_rej.json").write_text(
                json.dumps(_CAPTURED["occupancy_post_rej"], indent=2), encoding="utf-8")
    finally:
        try: page.remove_listener("response", handler_o)
        except Exception: pass

    # ── Step 4: evaluate results ──────────────────────────────────────────
    issues = []

    # Profiles: 0 filling segments
    if "profiles_post_rej" in _CAPTURED:
        segs_post = _extract_filling_segments(_CAPTURED["profiles_post_rej"])
        print(f"\n  PROFILES after reject-all  : {len(segs_post)} filling segment(s)")
        if len(segs_post) == 0:
            print("    → 0 segments ✓  (no truck fillings after rejection)")
        else:
            print(f"    !! FAIL : {len(segs_post)} segment(s) still present in profiles")
            for s in segs_post:
                print(f"       {s['bay']}  {s['t_start'][:16]} → {s['t_end'][:16]}")
            issues.append(f"profiles still has {len(segs_post)} filling segment(s) after reject-all")
    else:
        print("\n  [WARN] Profiles post-rejection not captured")
        segs_post = []

    # Bay occupancy: 0 Drop + Filling + Pick
    if "occupancy_post_rej" in _CAPTURED:
        occ_post: dict = {}
        for bay in _inner(_CAPTURED["occupancy_post_rej"]).get("bays", []):
            for occ in bay.get("bayOccupations", []):
                t = occ.get("type", "?")
                occ_post[t] = occ_post.get(t, 0) + 1

        non_maint = {k: v for k, v in occ_post.items()
                     if k.lower() not in ("maintenance",)}
        maint     = {k: v for k, v in occ_post.items()
                     if k.lower() == "maintenance"}

        print(f"\n  BAY OCCUPANCY after reject-all:")
        for t, c in sorted(occ_post.items()):
            note = "  (preserved ✓)" if t.lower() == "maintenance" else ""
            tick = "" if t.lower() == "maintenance" else f"  {_tick(c == 0)}"
            print(f"    {t:<15}: {c}{note}{tick}")

        total_non_maint = sum(non_maint.values())
        if total_non_maint == 0:
            print("    → 0 Drop/Filling/Pick occupations ✓")
        else:
            issues.append(
                f"bay occupancy still has {total_non_maint} "
                f"Drop/Filling/Pick occupation(s) after reject-all")
    else:
        print("\n  [WARN] Occupancy post-rejection not captured")

    # Profiles ↔ Occupancy alignment (post-rejection)
    # Both should be empty — if either is non-zero the alignment will expose the mismatch.
    if "profiles_post_rej" in _CAPTURED and "occupancy_post_rej" in _CAPTURED:
        align_issues, matched, unmatched_occ, fill_occs = \
            _align_profiles_occupancy(segs_post, _CAPTURED["occupancy_post_rej"])
        _print_alignment_table(matched, unmatched_occ, fill_occs, segs_post)
        if align_issues:
            issues.extend(align_issues)

    if not issues:
        return ("PASS",
                f"All {total} slot(s) rejected. "
                f"Profiles: 0 segments ✓  Bay occupancy: 0 active occupations ✓  "
                f"Alignment: consistent ✓")
    return "FAIL", "; ".join(issues)

# ==========================================================================
# Main
# ==========================================================================
def main():
    _CAPTURED.clear()

    parser = argparse.ArgumentParser(
        description="Hera Scheduled Transfers – Profiles Check")
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
    week, year         = _compute_target_week()

    artifacts = HERE / "test_artifacts_profiles"
    run_id    = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir   = artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*64}")
    print(f"  Hera Scheduled Transfers – Profiles Check")
    print(f"  Environment   : {env_name}  ({base_url})")
    print(f"  Target week   : {week}/{year}  (week+1 from today)")
    print(f"  Loading type  : {LOADING_TYPE}")
    print(f"  Scenarios     : {len(SCENARIOS)}  (from #{start_from})")
    print(f"  Run ID        : {run_id}")
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
            page.goto(f"{base_url}/nominations/weekly",
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
            print(f"  [{sc['n']:02d}/{len(SCENARIOS)}]  {sc['id']}  |  {sc['name']}")
            print(f"  {sc['desc']}")

            try:
                if   sc["id"] == "SCHED_TRF_01":
                    auto, reason = _do_sch_cap(page, base_url, week, year, shots_dir)
                elif sc["id"] == "SCHED_TRF_02":
                    auto, reason = _do_prf_capture(page, base_url, week, year, shots_dir)
                elif sc["id"] == "SCHED_TRF_03":
                    auto, reason = _do_count_chk(shots_dir)
                elif sc["id"] == "SCHED_TRF_04":
                    auto, reason = _do_limits_chk(shots_dir)
                elif sc["id"] == "SCHED_TRF_05":
                    auto, reason = _do_align_chk(shots_dir)
                elif sc["id"] == "SCHED_TRF_06":
                    auto, reason = _do_deriv_chk(shots_dir)
                elif sc["id"] == "SCHED_TRF_07":
                    auto, reason = _do_status_chk(shots_dir)
                elif sc["id"] == "SCHED_TRF_08":
                    auto, reason = _do_rej_all(page, base_url, week, year, shots_dir)
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
    print(f"  SUMMARY  –  {env_name.upper()}  /  run {run_id}  /  week {week}/{year}")
    print(f"{'='*64}")
    ok = fail = err = 0
    for r in results:
        match = r["expect"] == "?" or r["result"] == r["expect"]
        flag  = "   " if match else "!! "
        if   r["result"] == "ERROR": err  += 1
        elif r["result"] == "PASS":  ok   += 1
        else:                        fail += 1
        print(f"  {flag}{r['n']:>2}. {r['id']:<10} exp={r['expect']} "
              f"got={r['result']}  {r['name']}")
    print(f"\n  PASS: {ok}  FAIL: {fail}  ERROR: {err}")

    out = run_dir / "test_results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["n", "id", "name", "expect", "result", "notes", "desc"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"\n  Results    : {out}")
    print(f"  Artifacts  : {run_dir}\n")

    record_results([(r["id"], r["result"], r.get("notes", "")) for r in results],
                    xlsx_path=args.excel, source="test_scheduled_transfers_profiles.py")


if __name__ == "__main__":
    main()
