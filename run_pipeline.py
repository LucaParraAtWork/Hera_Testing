"""
Unified pipeline: Hera capture + Plant SIM capture + comparison + plots.

Single file, single --env argument to switch environment.

Usage:
  python run_pipeline.py                        # dev, full run
  python run_pipeline.py --env test             # test, full run
  python run_pipeline.py --env test --skip-capture   # reuse last artifacts
  python run_pipeline.py --env test --date 2026-06-12
  python run_pipeline.py --env test --save      # also save PNG charts

Steps:
  [1/3] Hera Profiles capture  (MW toggle + g/s toggle, current week)
  [2/3] Plant SIM capture      (Schedule tab)
  [3/3] Comparison + interactive matplotlib charts
"""

from __future__ import annotations

# =============================================================================
# Imports
# =============================================================================
import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

ROOT = Path(__file__).resolve().parent

# =============================================================================
# Environment URLs  -- edit here to add/change environments
# =============================================================================
HERA_URLS: dict[str, str] = {
    "dev":  "https://herawebdev.azurewebsites.net/schedules/profiles",
    "test": "https://herawebtest.azurewebsites.net/schedules/profiles",
}

PLANT_URLS: dict[str, str] = {
    "dev":  "https://dev-plant-ui-357.up.railway.app/",
    "test": "https://tst-plant-ui-357.up.railway.app/",
}

# =============================================================================
# Print helpers
# =============================================================================
def _banner(title: str, width: int = 68) -> None:
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


def _section(title: str) -> None:
    print(f"\n{'-' * 70}")
    print(f"  {title}")
    print("-" * 70)


# =============================================================================
# PART 1 -- HERA PROFILES CAPTURE
# =============================================================================
_HERA_LOGIN_MARKERS = ("login.microsoftonline.com", "login.microsoft.com")


def detect_unit_from_graphs(graphs: list) -> str:
    """Identify MW or g/s from the Power consumption graph yAxeName."""
    power = next((g for g in graphs if g.get("title") == "Power consumption"), None)
    if not power:
        power = next((g for g in graphs if "power" in str(g.get("title", "")).lower()), None)
    if not power:
        return "unknown"
    axe = str(power.get("yAxeName", ""))
    if "MW" in axe:
        return "MW"
    if "g/s" in axe or "Flow" in axe.lower() or "kg" in axe.lower():
        return "g/s"
    # Test env sends empty yAxeName -- caller handles this case
    return "unknown"


def detect_unit_from_file(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        unit = raw.get("unit_mode")
        if unit in ("MW", "g/s"):
            return unit
        graphs = raw.get("data", {}).get("profileGraphs", [])
        return detect_unit_from_graphs(graphs)
    except Exception:
        return "unknown"


def _find_captures_by_unit(data_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(data_dir.glob("*.json"), reverse=True):
        if "debug_skip" in path.name:
            continue
        unit = detect_unit_from_file(path)
        if unit in ("MW", "g/s") and unit not in found:
            found[unit] = path
    return found


class _HeraDataCapturer:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id
        self.current_step = "profiles_today"
        self._counter = 0
        self._last_sig: tuple | None = None
        self._unit_tag = "MW"
        self._file_tag = "MW"
        self._enabled = False
        (self.root / self.current_step / "data").mkdir(parents=True, exist_ok=True)

    def enable(self) -> None:
        self._enabled = True

    def set_unit(self, unit: str) -> None:
        self._unit_tag = unit
        self._file_tag = "gs" if unit == "g/s" else unit
        self._last_sig = None

    def _on_response(self, response) -> None:
        try:
            if not self._enabled:
                return
            ct = response.headers.get("content-type", "").lower()
            if "application/json" not in ct:
                return
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            if "GetProfilesScreenAsync" not in response.url:
                return
            try:
                body = response.json()
            except (Exception, asyncio.CancelledError):
                return

            graphs = body.get("profileGraphs") or body.get("data", {}).get("profileGraphs")
            if not graphs:
                return

            detected = detect_unit_from_graphs(graphs)
            if detected == "unknown":
                # Test env sends empty yAxeName -- trust the toggle we just clicked
                power = next(
                    (g for g in graphs if "power" in str(g.get("title", "")).lower()), None
                )
                if power and str(power.get("yAxeName", "")).strip() == "":
                    detected = self._unit_tag

            if detected != self._unit_tag:
                print(
                    f"  [SKIP] Response ignored: yAxeName={detected!r}, "
                    f"expected={self._unit_tag!r}"
                )
                return

            sig = (response.url, json.dumps(body, sort_keys=True))
            if sig == self._last_sig:
                return
            self._last_sig = sig
            self._counter += 1

            try:
                req_body = response.request.post_data_json()
            except Exception:
                req_body = None

            today = date.today().isoformat()
            filename = (
                f"{self._counter:03d}_POST_ProfilesScreen_{today}_{self._file_tag}.json"
            )
            yaxe = next(
                (
                    g.get("yAxeName")
                    for g in graphs
                    if "power" in str(g.get("title", "")).lower()
                ),
                None,
            )
            wrapper = {
                "url": response.url,
                "method": response.request.method,
                "status": response.status,
                "request_body": req_body,
                "data": body,
                "captured_at": datetime.now().isoformat(),
                "target_week": datetime.now().isocalendar()[:2],
                "unit_mode": self._unit_tag,
                "yAxeName_power": yaxe,
            }
            path = self.root / self.current_step / "data" / filename
            path.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")

            power_graph = next(
                (g for g in graphs if "power" in str(g.get("title", "")).lower()), None
            )
            vmax = 0.0
            if power_graph and power_graph.get("profileGraphLines"):
                pts = power_graph["profileGraphLines"][0].get("points", {})
                vmax = max(pts.values()) if pts else 0.0
            print(
                f"  [CAPTURE] {filename}  "
                f"({len(graphs)} graphs, yAxeName={yaxe!r}, power_max={vmax:.3f})"
            )
        except Exception:
            pass

    def start(self, page) -> None:
        page.on("response", self._on_response)

    def stop(self, page) -> None:
        page.remove_listener("response", self._on_response)


class _HeraShot:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id

    def save(self, page, step: str, name: str, full_page: bool = False) -> None:
        d = self.root / step
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=full_page)
            print(f"  [SCREENSHOT] {path}")
        except Exception as e:
            print(f"  [WARN] Screenshot failed: {e}")


def _hera_wait_for_login(page, host: str, timeout_s: int = 300) -> bool:
    print("\n[INFO] Entra ID login required -- please log in in the browser.")
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if page.is_closed():
                return False
            url = page.url
            if host in url and not any(m in url for m in _HERA_LOGIN_MARKERS):
                print(f"[INFO] Logged in -- {url}")
                time.sleep(2)
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return False


def _hera_goto_with_login(page, url: str) -> None:
    host = urlparse(url).netloc
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeout:
        pass
    if any(m in page.url for m in _HERA_LOGIN_MARKERS):
        if not _hera_wait_for_login(page, host):
            raise RuntimeError("Hera manual login failed.")
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)


def _current_week_number(page) -> int | None:
    try:
        txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=3000)
        m = re.search(r"Week\s+(\d{1,2})", txt, re.I)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _goto_week(page, target_week: int, shots: _HeraShot, max_clicks: int = 60) -> None:
    print(f"  [NAV] Navigating to Week {target_week}...")
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except PlaywrightTimeout:
        pass
    for _ in range(max_clicks):
        wk = _current_week_number(page)
        if wk == target_week:
            print(f"  [NAV] Week {target_week} reached.")
            shots.save(page, "profiles_today", f"week_{target_week:02d}")
            page.wait_for_timeout(1500)
            return
        diff = (target_week - wk) if wk is not None else 1
        go_next = diff > 0 if abs(diff) <= 26 else diff < 0
        if go_next:
            page.locator("div.date-picker-next > button").first.click()
        else:
            page.locator("div.date-picker-previous > button").first.click()
        page.wait_for_timeout(400)
    raise RuntimeError(f"Could not reach Week {target_week}")


def _click_unit(page, unit: str) -> None:
    pattern = r"g\s*/\s*s" if unit == "g/s" else rf"\b{re.escape(unit)}\b"
    btn = page.locator("app-toggle button", has_text=re.compile(pattern, re.I)).first
    btn.wait_for(state="visible", timeout=10_000)
    btn.click()


def _ensure_unit(page, unit: str, wait_ms: int = 3500) -> None:
    """Always switch to the other unit first then back to guarantee an API call."""
    other = "g/s" if unit == "MW" else "MW"
    print(f"  [UI] Switching {other} -> {unit}...")
    _click_unit(page, other)
    page.wait_for_timeout(wait_ms)
    _click_unit(page, unit)
    page.wait_for_timeout(wait_ms)
    print(f"  [UI] Toggle set to {unit}")


def _wait_profiles_api(
    page,
    capturer: _HeraDataCapturer,
    unit: str,
    shots: _HeraShot,
    label: str,
) -> None:
    capturer.set_unit(unit)
    data_dir = capturer.root / capturer.current_step / "data"
    before = len([f for f in data_dir.glob("*.json") if "debug_skip" not in f.name])
    _ensure_unit(page, unit)
    shots.save(page, "profiles_today", f"profiles_{label}")
    print(f"  [INFO] Waiting for API response ({unit})...")
    page.wait_for_timeout(4500)
    after = len([f for f in data_dir.glob("*.json") if "debug_skip" not in f.name])
    if after == before:
        print(f"  [WARN] No {unit} capture yet -- retrying toggle...")
        _ensure_unit(page, unit)
        page.wait_for_timeout(4500)


def capture_hera(hera_url: str, initial_wait: int = 15, week: int | None = None) -> str:
    """Capture Hera Profiles (MW + g/s) for the current ISO week."""
    today = date.today()
    iso = today.isocalendar()
    target_week = week or iso.week
    target_year = iso.year

    artifacts = ROOT / "artifacts"
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    shots = _HeraShot(artifacts / "hera_profiles", run_id)
    capturer = _HeraDataCapturer(artifacts / "hera_profiles", run_id)
    session_dir = artifacts / "playwright_session"

    _banner(f"[1/3] HERA CAPTURE -- {hera_url.split('/')[2]}")
    print(f"  Date   : {today}  (ISO week {target_week}, year {target_year})")
    print(f"  URL    : {hera_url}")
    print(f"  Run ID : {run_id}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(session_dir),
            headless=False,
            slow_mo=0,
            viewport={"width": 1500, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        capturer.start(page)
        try:
            _hera_goto_with_login(page, hera_url)
            try:
                page.wait_for_selector("app-line-graph", timeout=20_000)
            except PlaywrightTimeout:
                print("  [WARN] Charts not detected.")
            try:
                page.wait_for_selector("app-toggle", timeout=10_000)
            except PlaywrightTimeout:
                print("  [ERROR] Toggle missing -- aborting Hera capture.")
                return run_id

            _goto_week(page, target_week, shots)
            capturer.enable()

            print("\n  -- Capturing MW (Power consumption) --")
            _wait_profiles_api(page, capturer, "MW", shots, "mw")

            print("\n  -- Capturing g/s (Dispensers / Loading bays) --")
            _wait_profiles_api(page, capturer, "g/s", shots, "gs")

            by_unit = _find_captures_by_unit(
                artifacts / "hera_profiles" / run_id / "profiles_today" / "data"
            )
            print("\n  Unit verification:")
            for u, p_ in by_unit.items():
                print(f"    {u:4s}  ->  {p_.name}")
            if "MW" not in by_unit:
                print("  [ERROR] MW capture missing!")
            if "g/s" not in by_unit:
                print("  [WARN] g/s capture missing.")

            if initial_wait > 0:
                print(f"\n  [INFO] Waiting {initial_wait}s...")
                page.wait_for_timeout(initial_wait * 1000)

            data_dir = artifacts / "hera_profiles" / run_id / "profiles_today" / "data"
            n = len([f for f in data_dir.glob("*.json") if "debug_skip" not in f.name])
            print(f"\n  [OK] Hera capture done -- {n} file(s) in {data_dir}")
        finally:
            capturer.stop(page)

    return run_id


# =============================================================================
# PART 2 -- PLANT SIM SCHEDULE CAPTURE
# =============================================================================
_PLANT_LOGIN_MARKERS = ("login.microsoftonline.com", "login.microsoft.com")


def _is_plant_login_url(url: str) -> bool:
    return any(m in url for m in _PLANT_LOGIN_MARKERS)


def _is_plant_app_url(url: str) -> bool:
    return (
        ("railway.app" in url or "azurewebsites.net" in url)
        and not _is_plant_login_url(url)
    )


def _plant_wait_for_login(page, timeout_s: int = 300) -> bool:
    print("\n[INFO] Entra ID login required -- please log in in the browser.")
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if page.is_closed():
                return False
            if _is_plant_app_url(page.url):
                print(f"[INFO] Login detected -- {page.url}")
                time.sleep(2)
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    print("[ERROR] Plant SIM login timeout.")
    return False


def _plant_goto_with_login(page, url: str, timeout_ms: int = 60_000) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeout:
        pass
    for _ in range(10):
        if _is_plant_login_url(page.url):
            break
        page.wait_for_timeout(500)
    if _is_plant_login_url(page.url):
        if not _plant_wait_for_login(page):
            raise RuntimeError("Plant SIM manual login failed.")
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


class _PlantDataCapturer:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id
        self.current_step = "unknown"
        self._counter = 0
        self._last_sig: tuple | None = None

    def set_step(self, step_name: str) -> None:
        self.current_step = step_name
        self._counter = 0
        self._last_sig = None
        (self.root / step_name / "data").mkdir(parents=True, exist_ok=True)

    def _on_response(self, response) -> None:
        try:
            ct = response.headers.get("content-type", "").lower()
            if "application/json" not in ct:
                return
            if response.request.resource_type not in ("fetch", "xhr"):
                return
            try:
                body = response.json()
            except (Exception, asyncio.CancelledError):
                return
            url = response.url
            parsed = re.sub(r"^https?://[^/]+/", "", url)
            skip_words = ("app-config", "assets", "favicon", "onecollector", "browser.events")
            if any(s in parsed or s in url.lower() for s in skip_words):
                return
            method = response.request.method
            try:
                req_body = response.request.post_data_json()
            except Exception:
                try:
                    req_body = response.request.post_data
                except Exception:
                    req_body = None
            sig = (url, method, json.dumps(body, sort_keys=True))
            if self._last_sig == sig:
                return
            self._last_sig = sig
            self._counter += 1
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", parsed)[:60]
            filename = f"{self._counter:03d}_{method}_{slug}.json"
            wrapper = {
                "url": url,
                "method": method,
                "status": response.status,
                "headers": dict(response.headers),
                "request_body": req_body,
                "data": body,
                "captured_at": datetime.now().isoformat(),
            }
            save_dir = self.root / self.current_step / "data"
            save_dir.mkdir(parents=True, exist_ok=True)
            (save_dir / filename).write_text(
                json.dumps(wrapper, indent=2), encoding="utf-8"
            )
            print(f"  [CAPTURE] {filename}")
        except Exception:
            pass

    def start(self, page) -> None:
        page.on("response", self._on_response)

    def stop(self, page) -> None:
        page.remove_listener("response", self._on_response)


class _PlantShot:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id

    def save(self, page, step: str, name: str, full_page: bool = False) -> None:
        d = self.root / step
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=full_page)
            print(f"  [SCREENSHOT] {path}")
        except Exception as e:
            print(f"  [WARN] Screenshot failed: {e}")


def _click_schedule_tab(page, timeout_ms: int = 15_000) -> None:
    page.wait_for_selector("ul#main-tabs", state="visible", timeout=timeout_ms)
    page.wait_for_timeout(1500)
    selectors = [
        lambda: page.locator("#main-tabs").get_by_role(
            "tab", name=re.compile(r"^schedule$", re.I)
        ),
        lambda: page.locator(
            "#main-tabs a.nav-link", has_text=re.compile(r"^schedule$", re.I)
        ),
        lambda: page.get_by_role("tab", name=re.compile(r"^schedule$", re.I)),
    ]
    for locator_fn in selectors:
        try:
            loc = locator_fn().first
            loc.wait_for(state="visible", timeout=5000)
            loc.click()
            page.wait_for_timeout(2000)
            print("  [NAV] Schedule tab clicked.")
            return
        except Exception:
            continue
    raise RuntimeError("Could not find the 'Schedule' tab.")


def capture_plant_sim(plant_url: str, run_id: str | None = None) -> str:
    """Capture Plant SIM Schedule data."""
    artifacts = ROOT / "artifacts"
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    shots = _PlantShot(artifacts / "plant_sim", run_id)
    capturer = _PlantDataCapturer(artifacts / "plant_sim", run_id)
    session_dir = artifacts / "plant_sim_session"

    _banner(f"[2/3] PLANT SIM CAPTURE -- {plant_url.split('/')[2]}")
    print(f"  URL    : {plant_url}")
    print(f"  Run ID : {run_id}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(session_dir),
            headless=False,
            slow_mo=0,
            viewport={"width": 1500, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        capturer.start(page)
        try:
            capturer.set_step("step02_schedule")
            if not _is_plant_app_url(page.url):
                _plant_goto_with_login(page, plant_url)
            shots.save(page, "step02_schedule", "01_before_schedule")
            print("  [NAV] Clicking Schedule tab...")
            _click_schedule_tab(page)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            page.wait_for_timeout(4000)
            shots.save(page, "step02_schedule", "02_schedule_view", full_page=True)
            shots.save(page, "step02_schedule", "03_schedule_viewport")
            print("  [INFO] Waiting for API responses...")
            page.wait_for_timeout(5000)
            data_dir = capturer.root / "step02_schedule" / "data"
            n = len(list(data_dir.glob("*.json"))) if data_dir.exists() else 0
            print(f"\n  [OK] Plant SIM capture done -- {n} file(s) in {data_dir}")
        finally:
            capturer.stop(page)

    return run_id


# =============================================================================
# PART 3 -- DATA LOADING, COMPARISON & PLOTS
# =============================================================================
TZ = "Europe/Brussels"

BAY_MAP = [
    ("DI1", "Loading Bay 1"),
    ("DI2", "Loading Bay 2"),
    ("DI3", "Loading Bay 3"),
    ("DI4", "Loading Bay 4"),
]

C_HERA  = "#2563EB"
C_PLANT = "#06B6D4"
C_DIFF  = "#DC2626"


@dataclass
class SeriesBundle:
    label: str
    timestamps: pd.DatetimeIndex
    values: np.ndarray
    unit: str
    source: str


# --- File discovery -----------------------------------------------------------

def find_latest_hera_pair(root: Path) -> tuple[Path | None, Path | None]:
    runs = sorted(
        (root / "artifacts" / "hera_profiles").glob("*/profiles_today/data"),
        reverse=True,
    )
    for data_dir in runs:
        found: dict[str, Path] = {}
        for path in sorted(data_dir.glob("*.json"), reverse=True):
            if "debug_skip" in path.name:
                continue
            unit = detect_unit_from_file(path)
            if unit in ("MW", "g/s") and unit not in found:
                found[unit] = path
        if "MW" in found and "g/s" in found:
            return found["MW"], found["g/s"]
        if "MW" in found:
            return found["MW"], found["MW"]
    return None, None


def find_latest_plant_schedule(root: Path) -> Path | None:
    runs = sorted(
        (root / "artifacts" / "plant_sim").glob("*/step02_schedule/data"),
        reverse=True,
    )
    for data_dir in runs:
        for path in sorted(data_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            req = raw.get("request_body") or ""
            if isinstance(req, dict):
                req = json.dumps(req)
            if "schedule-ely-graph" not in req:
                continue
            if (
                "main-tabs.active_tab" not in req
                and '"active_tab","value":"tab-schedule"' not in req.replace(" ", "")
            ):
                continue
            resp = raw.get("data", {}).get("response", {})
            if "schedule-ely-graph" in resp:
                return path
    return None


# --- Loaders ------------------------------------------------------------------

def _sorted_points(points: dict) -> tuple[pd.DatetimeIndex, np.ndarray]:
    items = sorted((pd.Timestamp(k), float(v)) for k, v in points.items())
    ts   = pd.DatetimeIndex([i[0] for i in items])
    vals = np.array([i[1] for i in items], dtype=float)
    return ts, vals


def _load_hera_profiles(path: Path) -> dict[str, SeriesBundle]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    payload = raw.get("data", raw)
    if isinstance(payload, dict) and "profileGraphs" not in payload:
        payload = payload.get("data", payload)
    graphs = payload.get("profileGraphs", [])
    unit_mode = raw.get("unit_mode", "")
    out: dict[str, SeriesBundle] = {}

    for graph in graphs:
        title = graph.get("title", "")
        axe   = str(graph.get("yAxeName", ""))
        unit  = "MW" if "MW" in axe else "g/s"
        for line in graph.get("profileGraphLines", []):
            points = line.get("points") or {}
            if not points:
                continue
            ts, vals = _sorted_points(points)
            name = line.get("name") or title
            out[f"{title}::{name}"] = SeriesBundle(str(name), ts, vals, unit, f"Hera ({title})")

    # Shortcut keys used by comparison
    for graph in graphs:
        title = graph.get("title", "")
        lines = graph.get("profileGraphLines", [])
        if "power" in title.lower() and lines:
            ts, vals = _sorted_points(lines[0]["points"])
            axe  = str(graph.get("yAxeName", unit_mode or "MW_e"))
            unit = "MW" if ("MW" in axe or unit_mode == "MW") else "g/s"
            out["power"] = SeriesBundle(
                f"Power consumption ({axe or unit_mode})", ts, vals, unit, "Hera"
            )
        if title == "Truck fillings by loading bay":
            for line in lines:
                name = line.get("name", "")
                ts, vals = _sorted_points(line["points"])
                out[f"bay::{name}"] = SeriesBundle(name, ts, vals, "g/s", "Hera")

    return out


def _load_hera_mw_gs(mw_path: Path, gs_path: Path) -> dict[str, SeriesBundle]:
    mw_data = _load_hera_profiles(mw_path)
    gs_data = _load_hera_profiles(gs_path)
    merged: dict[str, SeriesBundle] = {}
    if "power" in mw_data:
        merged["power"] = mw_data["power"]
    elif "power" in gs_data:
        merged["power"] = gs_data["power"]
    for key, bundle in gs_data.items():
        if key.startswith("bay::"):
            merged[key] = bundle
    return merged


def _load_plant_schedule(path: Path) -> dict[str, SeriesBundle]:
    raw      = json.loads(path.read_text(encoding="utf-8"))
    response = raw.get("data", {}).get("response", {})
    out: dict[str, SeriesBundle] = {}

    ely = response.get("schedule-ely-graph", {}).get("figure", {})
    for trace in ely.get("data", []):
        if not trace.get("x"):
            continue
        out["ely"] = SeriesBundle(
            trace.get("name", "ELY setpoint"),
            pd.to_datetime(trace["x"]),
            np.array(trace["y"], dtype=float),
            "MW",
            "Plant SIM",
        )
        break

    di = response.get("schedule-di-graph", {}).get("figure", {})
    for trace in di.get("data", []):
        name = trace.get("name", "")
        if not trace.get("x"):
            continue
        out[f"di::{name}"] = SeriesBundle(
            name,
            pd.to_datetime(trace["x"]),
            np.array(trace["y"], dtype=float),
            "g/s",
            "Plant SIM",
        )

    return out


# --- Metrics & alignment ------------------------------------------------------

def _to_brussels(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if ts.tz is None:
        return ts.tz_localize(TZ).tz_localize(None)
    return ts.tz_convert(TZ).tz_localize(None)


def _filter_to_date(
    data: dict[str, SeriesBundle], target: date
) -> dict[str, SeriesBundle]:
    out = {}
    for k, b in data.items():
        local = _to_brussels(b.timestamps)
        mask  = np.array([t.date() == target for t in local], dtype=bool)
        if mask.any():
            out[k] = SeriesBundle(b.label, local[mask], b.values[mask], b.unit, b.source)
        else:
            out[k] = SeriesBundle(
                b.label, pd.DatetimeIndex([]), np.array([]), b.unit, b.source
            )
    return out


def _align(
    a: SeriesBundle, b: SeriesBundle
) -> tuple[np.ndarray, np.ndarray, str]:
    sa = pd.Series(a.values, index=_to_brussels(a.timestamps))
    sb = pd.Series(b.values, index=_to_brussels(b.timestamps))
    combined = pd.concat([sa, sb], axis=1, keys=["a", "b"]).dropna()
    if len(combined) >= 1:
        return (
            combined["a"].to_numpy(dtype=float),
            combined["b"].to_numpy(dtype=float),
            f"timestamp ({len(combined)} common points)",
        )
    n = min(len(a.values), len(b.values))
    return a.values[:n], b.values[:n], f"index ({n} points)"


def _metrics(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    diff  = a - b
    denom = np.maximum(np.maximum(np.abs(a), np.abs(b)), 1e-9)
    corr  = (
        float(np.corrcoef(a, b)[0, 1])
        if len(a) > 1 and np.std(a) > 0 and np.std(b) > 0
        else float("nan")
    )
    return {
        "n_points":         len(a),
        "mae":              float(np.mean(np.abs(diff))),
        "rmse":             float(np.sqrt(np.mean(diff ** 2))),
        "max_abs_diff":     float(np.max(np.abs(diff))),
        "mean_rel_diff_pct":float(np.mean(np.abs(diff / denom)) * 100),
        "correlation":      corr,
        "match_pct_1pct":   float(np.mean(np.abs(diff / denom) <= 0.01) * 100),
        "match_pct_5pct":   float(np.mean(np.abs(diff / denom) <= 0.05) * 100),
    }


def _normalize(v: np.ndarray) -> np.ndarray:
    lo, hi = v.min(), v.max()
    return np.zeros_like(v) if hi - lo < 1e-12 else (v - lo) / (hi - lo)


def _print_metrics(name: str, m: dict) -> None:
    print(f"\n  [{name}]")
    print(f"    Points      : {m['n_points']}")
    print(f"    Correlation : {m['correlation']:.4f}")
    print(f"    MAE         : {m['mae']:.4f}")
    print(f"    RMSE        : {m['rmse']:.4f}")
    print(f"    Max |diff|  : {m['max_abs_diff']:.4f}")
    print(f"    Mean rel.   : {m['mean_rel_diff_pct']:.2f} %")
    print(f"    Match <=1%  : {m['match_pct_1pct']:.1f} %")
    print(f"    Match <=5%  : {m['match_pct_5pct']:.1f} %")


# --- Plots --------------------------------------------------------------------

def _fmt_time(ax) -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def _plot_power(
    hera: SeriesBundle,
    plant: SeriesBundle,
    compare_date: date,
    m_raw: dict,
    m_norm: dict,
) -> None:
    ha, pa, _ = _align(hera, plant)
    ts = _to_brussels(hera.timestamps)[: len(ha)]
    fig, axes = plt.subplots(3, 1, figsize=(15, 11), sharex=True)
    fig.suptitle(
        f"ELECTROLYZER -- {compare_date} | "
        f"[HERA] Power consumption (MW) vs [Plant SIM] ELY setpoint (MW)",
        fontsize=13,
        fontweight="bold",
    )
    axes[0].plot(ts, ha, color=C_HERA,  lw=2,      label="[HERA] Power (MW)")
    axes[0].plot(ts, pa, color=C_PLANT, lw=2, ls="--", label="[Plant SIM] ELY (MW)")
    axes[0].set_ylabel("MW")
    axes[0].legend(loc="upper right")
    axes[0].grid(True, alpha=0.3)
    axes[0].set_title("Raw values")

    axes[1].plot(ts, _normalize(ha), color=C_HERA,  lw=2,      label="HERA (norm.)")
    axes[1].plot(ts, _normalize(pa), color=C_PLANT, lw=2, ls="--", label="Plant SIM (norm.)")
    axes[1].set_ylabel("0-1")
    axes[1].legend(loc="upper right")
    axes[1].grid(True, alpha=0.3)
    axes[1].set_title(f"Normalized shape  |  correlation = {m_norm['correlation']:.4f}")

    diff = ha - pa
    axes[2].fill_between(ts, diff, 0, alpha=0.35, color=C_DIFF)
    axes[2].plot(ts, diff, color=C_DIFF, lw=1.5, label="Hera - Plant SIM")
    axes[2].axhline(0, color="black", lw=0.8)
    axes[2].set_ylabel("Delta MW")
    axes[2].set_xlabel("Time (Europe/Brussels)")
    axes[2].legend(loc="upper right")
    axes[2].grid(True, alpha=0.3)
    axes[2].set_title(
        f"Difference  |  RMSE={m_raw['rmse']:.3f} MW  |  MAE={m_raw['mae']:.3f} MW"
    )
    _fmt_time(axes[2])
    plt.tight_layout()


def _plot_bays(
    hera_bays: list[SeriesBundle],
    plant_dis: list[SeriesBundle],
    compare_date: date,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)
    fig.suptitle(
        f"DISPENSERS -- {compare_date} | "
        f"[HERA] Loading Bay (g/s) vs [Plant SIM] DI flow (g/s)",
        fontsize=13,
        fontweight="bold",
    )
    for ax, hb, pd_, (di_name, bay_name) in zip(
        axes.flatten(), hera_bays, plant_dis, BAY_MAP
    ):
        h, p, _ = _align(hb, pd_)
        ts = _to_brussels(hb.timestamps)[: len(h)]
        m  = _metrics(h, p)
        ax.plot(ts, h, color=C_HERA,  lw=2,      label=f"[HERA] {bay_name}")
        ax.plot(ts, p, color=C_PLANT, lw=2, ls="--", label=f"[Plant SIM] {di_name}")
        ax.set_title(
            f"{bay_name} vs {di_name}  |  corr={m['correlation']:.4f}  MAE={m['mae']:.2f} g/s",
            fontsize=10,
        )
        ax.set_ylabel("g/s")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        _fmt_time(ax)
    for ax in axes[1, :]:
        ax.set_xlabel("Time (Europe/Brussels)")
    plt.tight_layout()


def _plot_side_by_side(
    hera_power: SeriesBundle,
    plant_ely: SeriesBundle,
    hera_bays: list[SeriesBundle],
    plant_dis: list[SeriesBundle],
    compare_date: date,
    env: str,
) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(16, 11))
    fig.suptitle(
        f"SIDE BY SIDE -- {compare_date}\n"
        f"Left = HERA [{env.upper()}]  |  Right = PLANT SIM [{env.upper()}]",
        fontsize=13,
        fontweight="bold",
    )
    ht = _to_brussels(hera_power.timestamps)
    pt = _to_brussels(plant_ely.timestamps)

    axes[0, 0].plot(ht, hera_power.values, color=C_HERA,  lw=2)
    axes[0, 0].set_title("[HERA] Power (MW)")
    axes[0, 0].set_ylabel("MW")
    axes[0, 0].grid(True, alpha=0.3)

    axes[0, 1].plot(pt, plant_ely.values, color=C_PLANT, lw=2)
    axes[0, 1].set_title("[Plant SIM] ELY (MW)")
    axes[0, 1].set_ylabel("MW")
    axes[0, 1].grid(True, alpha=0.3)

    for row, (hb, pd_, (di_name, bay_name)) in enumerate(
        zip(hera_bays[:2], plant_dis[:2], BAY_MAP[:2]), 1
    ):
        axes[row, 0].plot(_to_brussels(hb.timestamps),  hb.values,  color=C_HERA,  lw=1.8)
        axes[row, 0].set_title(f"[HERA] {bay_name} (g/s)")
        axes[row, 0].set_ylabel("g/s")
        axes[row, 0].grid(True, alpha=0.3)

        axes[row, 1].plot(_to_brussels(pd_.timestamps), pd_.values, color=C_PLANT, lw=1.8)
        axes[row, 1].set_title(f"[Plant SIM] {di_name} (g/s)")
        axes[row, 1].set_ylabel("g/s")
        axes[row, 1].grid(True, alpha=0.3)

    for ax in axes[-1, :]:
        ax.set_xlabel("Time")
        _fmt_time(ax)
    plt.tight_layout()


# --- Comparison orchestration -------------------------------------------------

def run_comparison(
    hera_mw: Path,
    hera_gs: Path,
    plant_path: Path,
    compare_date: date,
    env: str,
    save_dir: Path | None,
) -> int:
    _banner(f"[3/3] COMPARISON HERA vs PLANT SIM  --  {compare_date}")
    print(f"  Hera MW   : {hera_mw.name}")
    print(f"  Hera g/s  : {hera_gs.name}")
    print(f"  Plant SIM : {plant_path.name}")

    hera  = _filter_to_date(_load_hera_mw_gs(hera_mw, hera_gs), compare_date)
    plant = _filter_to_date(_load_plant_schedule(plant_path),    compare_date)

    if "power" not in hera or len(hera["power"].values) == 0:
        print("\n[ERROR] Hera Power (MW) not found for this date.")
        return 1
    if "ely" not in plant or len(plant["ely"].values) == 0:
        print("\n[ERROR] Plant SIM ELY not found for this date.")
        return 1

    hera_power = hera["power"]
    plant_ely  = plant["ely"]

    _section(f"Stats -- {compare_date}")
    for b in (hera_power, plant_ely):
        v = b.values
        print(
            f"    {b.source:12s} | {b.label:34s} | n={len(v):4d} | "
            f"min={v.min():8.3f}  max={v.max():8.3f}  mean={v.mean():8.3f} {b.unit}"
        )

    ha, pa, align_mode = _align(hera_power, plant_ely)
    print(f"\n  Alignment : {align_mode}")
    m_raw  = _metrics(ha, pa)
    m_norm = _metrics(_normalize(ha), _normalize(pa))

    _section("[HERA] Power consumption  vs  [Plant SIM] ELY setpoint")
    _print_metrics("Raw (MW)", m_raw)
    _print_metrics("Normalized", m_norm)

    hera_bays: list[SeriesBundle] = []
    plant_dis:  list[SeriesBundle] = []
    _section("Dispensers g/s : [HERA] Loading Bay  vs  [Plant SIM] DI")
    for di_name, bay_name in BAY_MAP:
        hk, pk = f"bay::{bay_name}", f"di::{di_name}"
        if hk not in hera or pk not in plant:
            print(f"  [SKIP] {bay_name} / {di_name}")
            continue
        hb, pd_ = hera[hk], plant[pk]
        hera_bays.append(hb)
        plant_dis.append(pd_)
        hv, pv, _ = _align(hb, pd_)
        _print_metrics(f"[HERA] {bay_name}  vs  [Plant SIM] {di_name}", _metrics(hv, pv))

    verdict = (
        "[OK] SIMILAR"        if m_raw["correlation"] >= 0.95 else
        "[!!] MODERATE GAP"   if m_raw["correlation"] >= 0.80 else
        "[XX] DIFFERENT"
    )
    print(f"\n  VERDICT (power MW raw) : {verdict}  (corr={m_raw['correlation']:.4f})")
    print("=" * 68)

    _plot_power(hera_power, plant_ely, compare_date, m_raw, m_norm)
    if hera_bays:
        _plot_bays(hera_bays, plant_dis, compare_date)
        _plot_side_by_side(hera_power, plant_ely, hera_bays, plant_dis, compare_date, env)

    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for i, fig_num in enumerate(plt.get_fignums()):
            plt.figure(fig_num).savefig(
                save_dir / f"pipeline_fig{i + 1}_{ts}.png",
                dpi=150,
                bbox_inches="tight",
            )
        print(f"\n  Charts saved in {save_dir}")

    print("\n  Close the chart windows to exit.")
    plt.show()
    return 0


# =============================================================================
# ENTRY POINT
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Unified pipeline: Hera + Plant SIM capture -> comparison -> charts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py                            # dev, full run
  python run_pipeline.py --env test                 # test, full run
  python run_pipeline.py --env test --skip-capture  # reuse last artifacts
  python run_pipeline.py --env test --save          # also save PNG charts
        """,
    )
    parser.add_argument(
        "--env",
        choices=["dev", "test"],
        default="dev",
        help="Target environment: dev (default) or test",
    )
    parser.add_argument(
        "--skip-capture",
        action="store_true",
        help="Skip browser captures, use latest existing artifacts",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Comparison date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--initial-wait",
        type=int,
        default=15,
        help="Seconds to pause after Hera capture for manual check (default: 15)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save charts as PNG in --output-dir",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "comparisons"),
        help="Output folder for PNG files (default: ./comparisons)",
    )
    args = parser.parse_args()

    env          = args.env
    compare_date = date.fromisoformat(args.date) if args.date else date.today()
    save_dir     = Path(args.output_dir) if args.save else None

    _banner(
        f"PIPELINE HERA <-> PLANT SIM  |  env={env.upper()}  |  date={compare_date}"
    )
    print(f"  Hera URL  : {HERA_URLS[env]}")
    print(f"  Plant URL : {PLANT_URLS[env]}")

    # Step 1 -- Hera capture
    if not args.skip_capture:
        capture_hera(HERA_URLS[env], initial_wait=args.initial_wait)

    # Step 2 -- Plant SIM capture
    if not args.skip_capture:
        capture_plant_sim(PLANT_URLS[env])

    # Step 3 -- Find latest artifacts
    hera_mw, hera_gs = find_latest_hera_pair(ROOT)
    plant_path        = find_latest_plant_schedule(ROOT)

    if not hera_mw or not hera_gs:
        print("\n[ERROR] Hera artifacts not found. Re-run without --skip-capture.")
        sys.exit(1)
    if not plant_path:
        print("\n[ERROR] Plant SIM artifact not found. Re-run without --skip-capture.")
        sys.exit(1)

    print(f"\n  [INFO] Using Hera MW   : {hera_mw}")
    print(f"  [INFO] Using Hera g/s  : {hera_gs}")
    print(f"  [INFO] Using Plant SIM : {plant_path}")

    # Step 3 -- Comparison
    code = run_comparison(hera_mw, hera_gs, plant_path, compare_date, env, save_dir)
    sys.exit(code)


if __name__ == "__main__":
    main()
