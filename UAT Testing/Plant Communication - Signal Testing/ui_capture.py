"""
Phase 3 shared helpers: does a plant signal sent via broker_client.py
correctly show up on Hera's own Plant Supervision screen
(/plant-supervision/plant-state)?

**2026-09-17, updated after the first live run.** The MQTT Stream tab's
real DOM is now CONFIRMED (via DevTools, live, herawebtest): it's an
Angular app, and each panel is `div.message-group` (with an `h4.group-title`
holding the heading text, e.g. "MQTT Stream (Raw)") containing
`div.grid-table > div.headers` (one child `div` per column header) and
`div.grid-table > div.messages > div.message` (one child `div` per row,
one grandchild `div` per cell, in column order -- e.g. `div.topic` /
`div.payload` for the Raw panel). It is an append-only live message log
(each new MQTT message becomes a new row; older rows are not overwritten),
NOT a single current-value cell per tag. No real `<table>`, no ARIA roles
at all -- `read_grid_after_heading()`'s "Attempt 0" below matches this
confirmed shape; its `<table>`/ARIA fallbacks are kept only in case a
future redesign changes this, not because either has ever matched
anything on the real screen.

Also observed on the first live run: `fill_rate_meas` sent via the broker
appeared on the Raw panel (Topic + Payload) and PLC panel (Tag/Asset/
Value/Unit) within about a second -- effectively instant, not needing the
generous poll timeouts below, though those are kept as a safety margin.
The WebSocket capture, by contrast, saw **zero** frames mentioning the
signal despite the DOM updating correctly -- so either this deployment's
SignalR connection falls back to a non-WebSocket transport (long-polling /
Server-Sent Events, both invisible to `page.on("websocket")`), or the
update path here doesn't go through the WebSocket Playwright attached to
at all. Not root-caused yet (only one data point) -- DOM is therefore the
ONLY reliable source of truth confirmed so far, not just the "primary" one
as originally planned; WebSocket capture is kept in case a signal type
that updates less instantly than fill_rate_meas reveals different
behaviour, but don't rely on it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PLANT_STATE_PATH = "/plant-supervision/plant-state"

TAB_LABELS = {
    "bay": "Bay Monitoring",
    "plant": "Plant Monitoring",
    "mqtt": "MQTT Stream",
    "assets": "Assets State",
}

PANEL_HEADINGS = {
    "acs": "MQTT Stream (ACS)",
    "plc": "MQTT Stream (PLC)",
    "raw": "MQTT Stream (Raw)",
}

PLC_COLUMNS = ["TimeStamp", "Asset", "Type", "Tag", "Value", "Unit"]
RAW_COLUMNS = ["Topic", "Payload"]


def wl():
    time.sleep(1.8)


def wm():
    time.sleep(0.8)


def quiet(page, ms=8000):
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass


def goto_resilient(page, url, wait_until="domcontentloaded", retries=2):
    """Same OAuth-redirect-race workaround as test_dispatch_vs_hera_profiles.py
    / test_bay_occupancy_vs_hera.py's own copy of this helper."""
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until=wait_until)
            return
        except Exception as exc:
            if "interrupted by another navigation" not in str(exc) or attempt == retries:
                raise
            print(f"  [WARN] Navigation interrupted by auth redirect, retrying "
                  f"({attempt + 1}/{retries})...")
            wl()
            quiet(page)


def open_logged_in_session(pw, base_url: str, session_dir: Path, viewport=None):
    """Launch a persistent Chromium context, navigate to the Plant
    Supervision screen, and block on manual MFA login -- same convention as
    every other Playwright script in this project.

    Returns (ctx, page).
    """
    ctx = pw.chromium.launch_persistent_context(
        str(session_dir), headless=False, slow_mo=0,
        viewport=viewport or {"width": 1600, "height": 950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    print("  Navigating to Hera...")
    try:
        page.goto(f"{base_url}{PLANT_STATE_PATH}", wait_until="commit", timeout=20_000)
    except Exception:
        pass

    print()
    print("  +-------------------------------------------------------+")
    print("  |  Log in with MFA if prompted.                          |")
    print("  |  Navigate to Plant Supervision > Plant State if it     |")
    print("  |  doesn't land there automatically.                     |")
    print("  |  Press Enter once you see the four tabs.               |")
    print("  +-------------------------------------------------------+")
    input("  > ")
    wl()
    quiet(page)
    print("  Login confirmed.\n")
    return ctx, page


def click_tab(page, tab_label: str, timeout_ms: int = 10_000) -> bool:
    """Click a tab by its visible text. Tries role=tab first (the semantically
    correct target if this is a real tab widget), falls back to a plain text
    match. Returns True if something was clicked."""
    try:
        loc = page.get_by_role("tab", name=re.compile(re.escape(tab_label), re.I))
        if loc.count() > 0:
            loc.first.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    try:
        loc = page.get_by_text(tab_label, exact=False)
        if loc.count() > 0:
            loc.first.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def text_present(page, text: str, timeout_ms: int = 5000) -> bool:
    try:
        page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Grid / table reading -- best-effort across <table> markup or ARIA grids
# (MUI DataGrid / ag-grid style), since the real markup hasn't been seen.
# --------------------------------------------------------------------------
@dataclass
class Grid:
    headers: list[str]
    rows: list[list[str]]

    def as_dicts(self) -> list[dict[str, str]]:
        out = []
        for r in self.rows:
            d = {}
            for i, h in enumerate(self.headers):
                d[h] = r[i] if i < len(r) else ""
            out.append(d)
        return out


def read_grid_after_heading(page, heading_text: str, fallback_columns: Optional[list[str]] = None) -> Optional[Grid]:
    """Find the heading text, then the next table-like structure in document
    order after it, and read it. Returns None if nothing plausible was found.

    Strategy (in order):
      0. Angular div-grid -- CONFIRMED live 2026-09-17 via DevTools on the
         real MQTT Stream tab: each panel is a `div.message-group` whose
         `h4.group-title` holds the heading text (e.g. "MQTT Stream
         (Raw)"), containing `div.grid-table > div.headers` (one child
         `div` per column header) and `div.grid-table > div.messages >
         div.message` (one child `div` per row, one grandchild `div` per
         cell, in column order -- e.g. `div.topic` / `div.payload` for the
         Raw panel). No real <table>, no ARIA roles at all -- attempts 1/2
         below are kept only as a fallback for a future redesign of this
         screen, not because they've ever matched anything here.
      1. Real <table>: `following::table[1]`, header from <th>, rows from
         <tbody tr>/<td>.
      2. ARIA grid (role=grid/row/columnheader/gridcell/cell): common in
         MUI DataGrid / ag-grid, neither of which renders a real <table>.
      3. If no header row could be read, use `fallback_columns` positionally.
    """
    # -- Attempt 0: Angular div-grid (the real, confirmed shape) --
    try:
        container = page.locator(".message-group").filter(has_text=heading_text).first
        if container.count() > 0:
            header_root = container.locator(".headers").first
            headers = []
            if header_root.count() > 0:
                headers = [h.strip() for h in header_root.locator(":scope > div").all_inner_texts()]
            message_rows = container.locator(".messages > .message")
            n = message_rows.count()
            rows = []
            for i in range(n):
                cells = message_rows.nth(i).locator(":scope > div")
                rows.append([c.strip() for c in cells.all_inner_texts()])
            if rows or headers:
                return Grid(headers=headers or (fallback_columns or []), rows=rows)
    except Exception:
        pass

    try:
        heading = page.get_by_text(heading_text, exact=False).first
        if heading.count() == 0:
            return None
    except Exception:
        return None

    # -- Attempt 1: real <table> --
    try:
        table = heading.locator("xpath=following::table[1]")
        if table.count() > 0:
            headers = [h.strip() for h in table.locator("thead th, tr th").all_inner_texts()]
            body_rows = table.locator("tbody tr")
            n = body_rows.count()
            rows = []
            for i in range(n):
                cells = body_rows.nth(i).locator("td")
                rows.append([c.strip() for c in cells.all_inner_texts()])
            if rows or headers:
                return Grid(headers=headers or (fallback_columns or []), rows=rows)
    except Exception:
        pass

    # -- Attempt 2: ARIA grid --
    try:
        grid_root = heading.locator("xpath=following::*[@role='grid' or @role='table'][1]")
        if grid_root.count() > 0:
            headers = [h.strip() for h in
                       grid_root.locator("[role='columnheader']").all_inner_texts()]
            row_locs = grid_root.locator("[role='row']")
            n = row_locs.count()
            rows = []
            start = 1 if headers else 0  # row 0 is often the header row itself
            for i in range(start, n):
                cells = row_locs.nth(i).locator("[role='gridcell'], [role='cell']")
                if cells.count() == 0:
                    continue
                rows.append([c.strip() for c in cells.all_inner_texts()])
            if rows or headers:
                return Grid(headers=headers or (fallback_columns or []), rows=rows)
    except Exception:
        pass

    return None


def poll_grid_until(
    page, heading_text: str, predicate: Callable[[dict], bool],
    fallback_columns: Optional[list[str]] = None,
    timeout_s: float = 30.0, poll_interval_s: float = 2.0,
) -> tuple[Optional[dict], Optional[Grid]]:
    """Re-read the grid under `heading_text` every `poll_interval_s` until a
    row matches `predicate`, or timeout. Returns (matching_row_dict_or_None,
    last_grid_read)."""
    deadline = time.time() + timeout_s
    last_grid = None
    while time.time() < deadline:
        grid = read_grid_after_heading(page, heading_text, fallback_columns)
        last_grid = grid
        if grid:
            for row in grid.as_dicts():
                try:
                    if predicate(row):
                        return row, grid
                except Exception:
                    continue
        time.sleep(poll_interval_s)
    return None, last_grid


# --------------------------------------------------------------------------
# Console / page-error capture
# --------------------------------------------------------------------------
@dataclass
class ConsoleCapture:
    page: object
    entries: list[dict] = field(default_factory=list)
    _attached: bool = False

    def attach(self):
        if self._attached:
            return
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_pageerror)
        self._attached = True

    def detach(self):
        if not self._attached:
            return
        try:
            self.page.remove_listener("console", self._on_console)
            self.page.remove_listener("pageerror", self._on_pageerror)
        except Exception:
            pass
        self._attached = False

    def _on_console(self, msg):
        try:
            self.entries.append({
                "ts": time.time(), "kind": "console",
                "type": msg.type, "text": msg.text,
            })
        except Exception:
            pass

    def _on_pageerror(self, exc):
        try:
            self.entries.append({
                "ts": time.time(), "kind": "pageerror",
                "type": "pageerror", "text": str(exc),
            })
        except Exception:
            pass

    def errors(self) -> list[dict]:
        return [e for e in self.entries if e["kind"] == "pageerror" or e.get("type") == "error"]

    def matching(self, pattern: str, since_ts: Optional[float] = None) -> list[dict]:
        rx = re.compile(pattern, re.I)
        out = [e for e in self.entries if rx.search(e["text"])]
        if since_ts is not None:
            out = [e for e in out if e["ts"] >= since_ts]
        return out


# --------------------------------------------------------------------------
# WebSocket frame capture
# --------------------------------------------------------------------------
@dataclass
class WebSocketCapture:
    page: object
    frames: list[dict] = field(default_factory=list)
    sockets: list[str] = field(default_factory=list)
    _attached: bool = False

    def attach(self):
        if self._attached:
            return
        self.page.on("websocket", self._on_websocket)
        self._attached = True

    def detach(self):
        if not self._attached:
            return
        try:
            self.page.remove_listener("websocket", self._on_websocket)
        except Exception:
            pass
        self._attached = False

    def _on_websocket(self, ws):
        try:
            self.sockets.append(ws.url)
            ws.on("framereceived", lambda payload, url=ws.url: self._record(url, payload, "recv"))
            ws.on("framesent", lambda payload, url=ws.url: self._record(url, payload, "sent"))
        except Exception:
            pass

    def _record(self, url, payload, direction):
        try:
            text = payload if isinstance(payload, str) else payload.decode("utf-8", "replace")
        except Exception:
            text = repr(payload)
        self.frames.append({"ts": time.time(), "url": url, "direction": direction, "raw": text})

    def received(self) -> list[dict]:
        return [f for f in self.frames if f["direction"] == "recv"]

    def frames_mentioning(self, *substrings: str, since_ts: Optional[float] = None) -> list[dict]:
        """Raw substring search across received frame text -- deliberately
        NOT parsed into a JSON/SignalR schema, since that schema hasn't been
        confirmed live yet. SignalR JSON-Hub-Protocol frames are normally
        one or more '\\x1e'-terminated JSON records; a frame here is kept
        as the raw text paho/the browser delivered, un-split, so a match
        just means "this frame's raw bytes contained all of these
        substrings somewhere", not "one JSON field equalled this value"."""
        out = []
        for f in self.received():
            if since_ts is not None and f["ts"] < since_ts:
                continue
            if all(s in f["raw"] for s in substrings):
                out.append(f)
        return out
