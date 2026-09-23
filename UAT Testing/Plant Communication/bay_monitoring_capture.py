#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bay_monitoring_capture.py
==========================
Phase 4 (sibling to Phase 3's `ui_capture.py`): Playwright helpers specific
to Hera's Plant Supervision > **Bay Monitoring** tab -- the "Bay Occupancy
Schedule" block chart and "Injection and Loading Profiles" (Forecasted vs
Actual) line chart, one loading bay at a time, one calendar week at a time.

Unlike the MQTT Stream tab (Phase 3, `ui_capture.py`), this screen is NOT a
live-only view -- it charts historical, bucketed (15-min) data for a
selectable week, so capturing it well after a signal window has elapsed is
fine. There is no "capture must be attached before sending" constraint here.

STATUS, honestly: none of this has been run against the real DOM yet.
`sustain_signal.py`'s own docstring asserts the Actual line "reads from
BayMonitoringScreen/GetBayMonitoringScreenAsync ... see PLANT_SIGNALS_
CATALOG.md / this folder's README for how that was reverse-engineered" --
but that write-up is NOT actually present in either doc as of 2026-09-23, so
treat the exact endpoint name/response shape as an unconfirmed carry-over
claim, not a verified fact. `capture_bay_monitoring()` below therefore
captures and dumps EVERY JSON response whose URL merely contains
"baymonitoring" (case-insensitive) to `raw_responses/`, not just the one
guessed endpoint -- so the first live run can confirm or correct the guess
from real evidence instead of silently trusting it.

The week-navigator ("< Week 39 >") is assumed to be the same shared
`div.date-picker-*` Angular component already confirmed live for
Nominations/Profiles/Occupancy (`Scheduled Transfers/
test_scheduled_transfers_profiles.py`) -- reused directly via that module,
not reimplemented, since it is visually identical in the screenshot the
tester shared for Bay Monitoring. The bay-selector dropdown's exact DOM is
unconfirmed; `select_bay()` tries a couple of plausible shapes and reports
whether it believes it succeeded, rather than asserting silently.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ui_capture import click_tab, wm, quiet  # Phase 3's login/nav helpers

BAY_MONITORING_TAB_LABEL = "Bay Monitoring"
SCHEDULE_HEADING = "Bay Occupancy Schedule"
PROFILE_HEADING = "Injection and Loading Profiles"


def bay_label(bay: int) -> str:
    return f"Loading Bay {bay}"


def resolve_bay(default: int = 0) -> int:
    """Prompt for a loading bay (1-4) if `default` isn't already a valid
    one, matching this project's `ENV = ""` + interactive-prompt convention
    (see `config.resolve_env()`). Callers keep their own module-level
    `BAY = 0` constant and pass it in here; hardcoding e.g. `BAY = 1` skips
    the prompt on every run of that file."""
    if default in (1, 2, 3, 4):
        return default
    print("\nSelect loading bay:")
    for i in (1, 2, 3, 4):
        print(f"  {i}. {bay_label(i)}")
    while True:
        choice = input("Enter 1-4: ").strip()
        if choice in ("1", "2", "3", "4"):
            return int(choice)


def select_bay(page, bay: int, timeout_ms: int = 8000) -> bool:
    """Best-effort: pick `bay_label(bay)` in whatever bay-picker sits above
    the Injection and Loading Profiles chart (visible as a
    "Loading Bay 1 [v]" control in the tester's screenshot). Tries a plain
    <select> first, then a generic click-to-open-a-list dropdown. Returns
    False (not an exception) if neither matches -- caller should treat that
    as "layout assumption wrong, needs a look" rather than crash."""
    label = bay_label(bay)
    try:
        sel = page.locator("select", has_text=re.compile("Loading Bay", re.I))
        if sel.count() > 0:
            sel.first.select_option(label=label, timeout=timeout_ms)
            return True
    except Exception:
        pass
    try:
        btn = page.get_by_text(re.compile(r"^Loading Bay\s*\d", re.I)).first
        if btn.count() > 0:
            btn.click(timeout=timeout_ms)
            opt = page.get_by_text(label, exact=True)
            if opt.count() > 0:
                opt.first.click(timeout=timeout_ms)
                return True
    except Exception:
        pass
    return False


def capture_bay_monitoring(
    page, sched_mod, week: int, year: int, bay: int, shots_dir: Path,
) -> tuple[Optional[dict], dict]:
    """Open the Bay Monitoring tab, select `bay`, navigate to `week`/`year`,
    and capture every JSON XHR/fetch response whose URL mentions
    "baymonitoring" (case-insensitive) -- ALL of them dumped to
    `shots_dir/raw_responses/`, not just a hardcoded guess.

    Returns (best_guess_body_or_None, meta) where `meta` records whether the
    tab/bay/week steps each appeared to work, and the full `captured` dict
    keyed by response URL (for manual inspection on the first live run).
    `best_guess_body` is the response body whose URL contains
    "getbaymonitoringscreenasync" (case-insensitive), if one fired --
    otherwise None, meaning the real endpoint name needs to be read from
    `raw_responses/` and this module updated accordingly.
    """
    shots_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, dict] = {}

    def _handler(response):
        try:
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            if "baymonitoring" not in response.url.lower():
                return
            captured[response.url] = response.json()
            print(f"  [CAPTURE] {response.url}")
        except Exception:
            pass

    page.on("response", _handler)
    try:
        clicked = click_tab(page, BAY_MONITORING_TAB_LABEL)
        wm(); quiet(page)
        page.screenshot(path=str(shots_dir / "01_bay_monitoring_tab.png"), full_page=True)

        bay_selected = select_bay(page, bay)
        wm(); quiet(page)

        week_ok = True
        try:
            sched_mod._goto_week_year(page, week, year)
        except Exception as exc:
            week_ok = False
            print(f"  [WARN] Week navigation failed: {exc}")
        quiet(page); page.wait_for_timeout(2000)
        page.screenshot(path=str(shots_dir / "02_bay_monitoring_week.png"), full_page=True)

        raw_dir = shots_dir / "raw_responses"
        if captured:
            raw_dir.mkdir(parents=True, exist_ok=True)
            for i, (url, body) in enumerate(captured.items()):
                safe = re.sub(r"[^A-Za-z0-9]+", "_", url)[-80:]
                (raw_dir / f"{i:02d}_{safe}.json").write_text(
                    json.dumps(body, indent=2), encoding="utf-8")

        best = None
        for url, body in captured.items():
            if "getbaymonitoringscreenasync" in url.lower():
                best = body

        meta = {
            "tab_clicked": clicked, "bay_selected": bay_selected, "week_ok": week_ok,
            "captured_urls": list(captured.keys()),
        }
        return best, meta
    finally:
        try:
            page.remove_listener("response", _handler)
        except Exception:
            pass


def find_bay(body: dict, bay: int) -> Optional[dict]:
    """Return the raw `bays` entry for `bay` from a GetBayMonitoringScreenAsync
    body -- CONFIRMED shape, 2026-09-23, from a real captured response (this
    is the SAME response `extract_actual_points()` reads; `bays` is a
    sibling top-level key alongside `injectionAndLoadingGraph`, so no
    separate capture is needed). Matched by `label` (== `bay_label(bay)`) --
    the same match every other script in this folder already uses.

    Each entry looks like:
        {"id": "<guid>", "label": "Loading Bay 1", "bayOccupations": [...]}
    Each `bayOccupations` item has: `type` (confirmed value so far:
    "Filling", covering the WHOLE entry-to-exit visit as one block -- a
    DIFFERENT vocabulary than the separate DISPATCH-side BayOccupancyScreen
    endpoint's "DropOff"/"Filling"/"PickUp" types, which
    `test_bay_occupancy_vs_hera.py` reads -- these are two different
    screens/APIs, don't conflate them), `schedulingType` ("Actual" or
    "Planned"), `movements` (see `BAY_OCCUPATION_CHECKLIST` below), `color`,
    `from`/`till` (ISO UTC; `till` for an "Ongoing" occupation was observed
    holding roughly "now", not a real end time -- don't assert against it
    while status is Ongoing), `licensePlate` (matches the trailer_id we send
    in the ACS `mvt_completed` payload), `status` ("Ongoing" confirmed; the
    value once a visit fully completes is NOT yet confirmed), `offtakerName`
    (NOT something we send over MQTT -- appears to be a server-side lookup,
    so don't assert an exact expected value for it, just observe it)."""
    bays = body.get("bays") or []
    label = bay_label(bay)
    for b in bays:
        if b.get("label") == label:
            return b
    return None


# The Bay Occupancy Schedule's per-occupation movement checklist is a FIXED,
# 16-slot ordered list -- CONFIRMED live, 2026-09-23, from a real captured
# response. An unfilled slot carries id=None, time=None, origin=None,
# status="None" until that step actually happens.
#
# FULL operating_mode -> UI-label mapping now CONFIRMED, 2026-09-23, from a
# real end-to-end happy-path run (test_bay_occupancy_happy_path.py,
# HAPPY_01-03) -- every raw signal's own send timestamp was correlated
# against whichever checklist slot's `time` field landed closest to it
# (within 5s; actual observed gaps were ~0.3s):
#
#   mode 1  -> DROP-OFF START        mode 8         -> FILLING DONE
#   mode 2  -> VEHICLE APPROACH      mode 9         -> (see note below)
#   mode 3  -> DROP-OFF END          mode 10        -> PICK-UP START
#   mode 4  -> READY                 mode 11 (+RFID)-> DISCONNECTED
#   mode 5  -> AUTHORIZED            mode 12        -> VEHICLE LEAVING
#   mode 6  -> FILLING               mode 0 (final) -> PICK-UP END
#   mode 7  -> never used (confirmed absent from hera-virtual-plant's own
#              source too, see [[bay_occupancy_happy_path]] memory)
#
# NOTE, also confirmed by that same run: `operating_mode=9` produces NO
# observable effect on this checklist at all -- "WAITING FOR DISCONNECT"
# stayed unfilled (status "None", shown as "-" in Hera's own UI) for the
# entire run despite mode 9 being sent, confirmed correct by both the API
# response AND a screenshot of the real screen. This settles the earlier
# "6 UI labels vs 5 simulator modes" mismatch: it isn't that a 6th signal
# is missing from our sender -- WAITING FOR DISCONNECT simply never gets
# triggered by this signal set, full stop. The overall occupation's
# `status` field still became "Success" despite this one permanently-empty
# slot, confirming Hera doesn't require every checklist slot filled for the
# occupation to be considered complete.
#
# This differs from steps.py's own inline comments for modes 9-12 ("Wait
# disconnect"/"Wait pickup"/"Pickup created"/"Vehicle leaving") -- those
# reflect hera-virtual-plant's OWN internal naming for the mode (confirmed
# correct against that repo's source), a genuinely separate vocabulary from
# what Hera's UI chooses to display for the same signal. Both are correct
# facts about different things; this list is the one that matches what a
# human actually sees on screen.
BAY_OCCUPATION_CHECKLIST = [
    "MOVEMENT REQUESTED", "MOVEMENT COMPLETED",              # ACS entry
    "DROP-OFF START", "VEHICLE APPROACH", "DROP-OFF END",    # modes 1, 2, 3
    "READY", "AUTHORIZED", "FILLING", "FILLING DONE",        # modes 4, 5, 6, 8
    "WAITING FOR DISCONNECT",                                # mode 9 -- never fills, confirmed
    "PICK-UP START", "DISCONNECTED",                         # modes 10, 11(+RFID)
    "VEHICLE LEAVING", "PICK-UP END",                        # modes 12, 0 (final)
    "MOVEMENT REQUESTED", "MOVEMENT COMPLETED",              # ACS exit
]


def find_occupation(bay_entry: dict, *, license_plate: Optional[str] = None,
                     scheduling_type: str = "Actual") -> Optional[dict]:
    """Find a `bayOccupations` entry within `bay_entry` (from `find_bay()`)
    by `schedulingType` (default "Actual") and, if given, `licensePlate`.
    Returns the first match, or None."""
    for occ in bay_entry.get("bayOccupations") or []:
        if occ.get("schedulingType") != scheduling_type:
            continue
        if license_plate is not None and occ.get("licensePlate") != license_plate:
            continue
        return occ
    return None


def movement_status(occupation: dict, event_name: str) -> tuple[Optional[str], Optional[str]]:
    """Return (status, time) for the FIRST `movements` checklist entry in
    `occupation` whose `event` matches `event_name` (case-insensitive).
    (None, None) if that event name isn't present in the checklist at all
    (e.g. a typo) -- distinct from a present-but-unfilled slot, which
    returns ("None", None).

    CAUTION -- confirmed bug source, 2026-09-23: "MOVEMENT REQUESTED" and
    "MOVEMENT COMPLETED" each appear TWICE in the checklist (entry AND exit
    ACS pairs). This function always returns the FIRST (entry-side) match,
    even when the caller actually wants to check the exit-side pair -- a
    real happy-path run's HAPPY_03 scenario silently re-reported the entry
    pair's already-"Success" status/time while believing it was confirming
    the exit pair, because of exactly this. Use `movement_at()` with an
    explicit checklist index (see `BAY_OCCUPATION_CHECKLIST`) instead of
    this function for either ACS label."""
    for mv in occupation.get("movements") or []:
        if str(mv.get("event", "")).strip().upper() == event_name.strip().upper():
            return mv.get("status"), mv.get("time")
    return None, None


def movement_at(occupation: dict, index: int) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (status, time, event_label) for the `movements` checklist
    slot at position `index` (0-based, matching `BAY_OCCUPATION_CHECKLIST`'s
    own order) in `occupation`. Prefer this over `movement_status()` for the
    two ACS labels, which appear twice (entry and exit) -- looking them up
    by position, not by label text, is the only way to tell the two pairs
    apart. (None, None, None) if `index` is out of range."""
    movements = occupation.get("movements") or []
    if not (0 <= index < len(movements)):
        return None, None, None
    mv = movements[index]
    return mv.get("status"), mv.get("time"), mv.get("event")


def poll_until(check_fn, first_wait_s: float = 30.0, max_wait_s: float = 300.0,
               poll_interval_s: float = 30.0):
    """Call `check_fn()` (returns `(satisfied: bool, *payload)`) after
    `first_wait_s`, retrying every `poll_interval_s` until either it's
    satisfied or `max_wait_s` total has elapsed. Returns whatever
    `check_fn()` last returned (so a timeout still yields the last real
    capture, not nothing).

    Confirmed necessary, 2026-09-23: watching the real Bay Monitoring
    screen live during a happy-path run showed everything eventually
    working, but sometimes with a real, several-minute delay before a
    just-sent signal's effect actually appeared -- longer than either
    test's original flat 30-90s wait accounted for. 5 minutes
    (`max_wait_s` default) was confirmed enough in practice. Mirrors this
    project's established `_settle_and_retry()` pattern
    (`test_signal_reaction.py`) but as a bounded poll loop rather than a
    single second check, since the wait needed here is much longer."""
    deadline = time.time() + max_wait_s
    time.sleep(min(first_wait_s, max(0.0, deadline - time.time())))
    result = check_fn()
    if result[0]:
        return result
    while time.time() < deadline:
        time.sleep(min(poll_interval_s, max(0.0, deadline - time.time())))
        result = check_fn()
        if result[0]:
            return result
    return result


def extract_actual_points(body: dict, bay: int) -> dict[str, Optional[float]]:
    """Return {iso_utc_timestamp: value_or_None} for `bay`'s "Actual" line.

    CONFIRMED shape, 2026-09-23, from a real captured
    GetBayMonitoringScreenAsync response (not a guess -- the earlier version
    of this function assumed a nested `bays: [...]` structure, which was
    wrong). The real shape is:

        body["injectionAndLoadingGraph"]["profileGraphLines"] = [
            {"name": "Loading Bay 1", "subName": "Forecasted", "points": {...}, ...},
            {"name": "Loading Bay 1", "subName": "Actual",     "points": {...}, ...},
            {"name": "Loading Bay 2", "subName": "Forecasted", "points": {...}, ...},
            ...
            {"name": "Injection", "subName": "Actual Injection", "points": {...}, ...},
        ]

    -- a FLAT list (not nested per bay), matched by `name` + `subName`. Each
    line's `points` is a dict keyed by an EXACT ISO-8601 **UTC** timestamp
    (15-min spaced, seconds always :00, e.g. "2026-09-20T22:00:00Z") mapping
    to a numeric value or `null`. `null` is the default/baseline state for a
    bucket nothing has been received for yet -- not necessarily an error.
    Returns {} (never raises) if this bay's Actual line isn't found, so the
    caller can fall back to the raw JSON dump instead of asserting blindly.
    """
    if not isinstance(body, dict):
        return {}
    lines = (body.get("injectionAndLoadingGraph") or {}).get("profileGraphLines") or []
    label = bay_label(bay)
    for line in lines:
        if line.get("name") == label and line.get("subName") == "Actual":
            return line.get("points") or {}
    return {}


def bucket_key_utc(dt: datetime) -> str:
    """Format a (timezone-aware) bucket-start datetime as the exact UTC ISO
    key format used by `points` above, e.g. "2026-09-23T07:30:00Z"."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lookup_bucket_value(points: dict[str, Optional[float]], bucket_start: datetime,
                         tol_s: float = 180.0) -> tuple[Optional[str], Optional[float]]:
    """Look up `bucket_start`'s value in `points` (see `extract_actual_points`),
    by exact UTC key first, falling back to the nearest key within `tol_s`
    seconds (small clock-skew tolerance -- the same kind of tolerance this
    project's other DB-polling scripts already use). Returns
    (matched_key_or_None, value_or_None) -- a matched key with value `None`
    means "found, but null" (a real, distinct outcome), while a `None` key
    means "no point found at all near this time"."""
    key = bucket_key_utc(bucket_start)
    if key in points:
        return key, points[key]

    target = bucket_start.astimezone(timezone.utc)
    best_key, best_diff = None, None
    for k in points:
        try:
            ts = datetime.strptime(k, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        diff = abs((ts - target).total_seconds())
        if diff <= tol_s and (best_diff is None or diff < best_diff):
            best_key, best_diff = k, diff
    if best_key is None:
        return None, None
    return best_key, points[best_key]
