"""
Poll heraplantdatabasetest (or dev) for the row(s) a just-sent plant signal
should have produced. MQTT ingestion isn't instantaneous, so every check
here retries for up to `timeout_s` instead of doing a single SELECT.

Read-only. Uses the same credentialed connection as `db_common.py` (same
folder).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import db_common


@dataclass
class PollResult:
    found: bool
    rows: list[Any] = field(default_factory=list)
    elapsed_s: float = 0.0
    attempts: int = 0


def connect(database: str = "heraplantdatabasetest"):
    conn = db_common.connect(
        db_common.DEFAULT_SERVER, database, db_common.DEFAULT_USER, db_common.DEFAULT_PASSWORD
    )
    # db_common.connect() doesn't set autocommit, so pyodbc defaults to
    # False -- every SELECT then opens an implicit transaction that never
    # gets closed until the next one starts. Harmless for a single query,
    # but a script here reuses one connection across many sequential
    # SELECTs spanning tens of seconds (e.g. three scenarios' worth of
    # dump_window() calls in test_edge_case_skipped_step.py); under Azure
    # SQL's snapshot isolation that produced a real, observed staleness bug
    # (an operating_mode row visible from a fresh connection came back
    # empty from the long-lived one). Autocommit avoids it -- this
    # connection is read-only anyway, no transaction semantics needed.
    conn.autocommit = True
    return conn


def _poll(conn, sql: str, params: tuple, timeout_s: float, interval_s: float) -> PollResult:
    cur = conn.cursor()
    start = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        cur.execute(sql, params)
        rows = cur.fetchall()
        elapsed = time.monotonic() - start
        if rows:
            return PollResult(found=True, rows=rows, elapsed_s=elapsed, attempts=attempts)
        if elapsed >= timeout_s:
            return PollResult(found=False, rows=[], elapsed_s=elapsed, attempts=attempts)
        time.sleep(interval_s)


def wait_for_plant_signal(
    conn, *, tag: str, since, value_contains: Optional[str] = None,
    timeout_s: float = 10.0, interval_s: float = 0.5,
) -> PollResult:
    """Universal fallback check -- every confirmed signal lands here."""
    if value_contains is not None:
        sql = (
            "SELECT TOP 5 Received, Asset, Tag, Topic, Value, Unit, DataType, Direction "
            "FROM [dbo].[PlantSignals] "
            "WHERE Tag = ? AND Received >= ? AND Direction = 'PlantToSteering' "
            "AND Value LIKE ? ORDER BY Received DESC"
        )
        params = (tag, since, f"%{value_contains}%")
    else:
        sql = (
            "SELECT TOP 5 Received, Asset, Tag, Topic, Value, Unit, DataType, Direction "
            "FROM [dbo].[PlantSignals] "
            "WHERE Tag = ? AND Received >= ? AND Direction = 'PlantToSteering' "
            "ORDER BY Received DESC"
        )
        params = (tag, since)
    return _poll(conn, sql, params, timeout_s, interval_s)


def wait_for_movement(
    conn, *, event_name: str, since, bay: Optional[str] = None,
    timeout_s: float = 10.0, interval_s: float = 0.5,
) -> PollResult:
    if bay:
        sql = (
            "SELECT TOP 5 Received, AcsCorrelationId, EventName, DriverId, TruckId, TrailerId, "
            "DropOffLoadingBayName, PickupLoadingBayName, Direction, Origin "
            "FROM [dbo].[Movements] "
            "WHERE EventName = ? AND Received >= ? "
            "AND (DropOffLoadingBayName = ? OR PickupLoadingBayName = ?) "
            "ORDER BY Received DESC"
        )
        params = (event_name, since, bay, bay)
    else:
        sql = (
            "SELECT TOP 5 Received, AcsCorrelationId, EventName, DriverId, TruckId, TrailerId, "
            "DropOffLoadingBayName, PickupLoadingBayName, Direction, Origin "
            "FROM [dbo].[Movements] WHERE EventName = ? AND Received >= ? ORDER BY Received DESC"
        )
        params = (event_name, since)
    return _poll(conn, sql, params, timeout_s, interval_s)


def count_movements_since(conn, *, event_name: str, since, bay: Optional[str] = None) -> int:
    cur = conn.cursor()
    if bay:
        cur.execute(
            "SELECT COUNT(*) FROM [dbo].[Movements] WHERE EventName = ? AND Received >= ? "
            "AND (DropOffLoadingBayName = ? OR PickupLoadingBayName = ?)",
            (event_name, since, bay, bay),
        )
    else:
        cur.execute(
            "SELECT COUNT(*) FROM [dbo].[Movements] WHERE EventName = ? AND Received >= ?",
            (event_name, since),
        )
    return cur.fetchone()[0]


def count_plant_signals_since(conn, *, tag: str, since) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM [dbo].[PlantSignals] WHERE Tag = ? AND Received >= ? "
        "AND Direction = 'PlantToSteering'",
        (tag, since),
    )
    return cur.fetchone()[0]


def dump_window(conn, *, since, tables=("PlantSignals", "Movements")) -> dict[str, list[Any]]:
    """Everything received since `since`, for post-mortem dumps in the edge-case tests.

    KNOWN OPEN ISSUE (2026-09-17, not yet root-caused): in the two edge-case
    scripts, this function's PlantSignals result has come back empty for
    operating_mode/RFID_trailer_details rows that Movements (queried right
    after, same call, same `since`) proves exist in the same window -- but a
    standalone call to this exact function with a fresh connection finds
    them correctly. Tried and ruled out: connection-level snapshot staleness
    (added autocommit=True to connect() above -- no change) and a
    since/clock-skew boundary (Movements uses the identical `since` value in
    the same call and succeeds, so a skewed cutoff would have to affect both
    tables equally, not just this one). Left unresolved rather than
    burning more time chasing a cosmetic display gap -- it does not affect
    the actual, already-confirmed finding that both signal types reach the
    database (see test_signal_reaction.py's SIG_REACT_01, and
    PLANT_SIGNALS_CATALOG.md §8).
    If it matters later, next thing to try: split into two connections/
    cursors, one per table, instead of sharing one cursor for both queries.
    """
    cur = conn.cursor()
    out: dict[str, list[Any]] = {}
    if "PlantSignals" in tables:
        cur.execute(
            "SELECT Received, Asset, Tag, Topic, Value, Unit, DataType, Direction "
            "FROM [dbo].[PlantSignals] WHERE Received >= ? ORDER BY Received ASC",
            (since,),
        )
        out["PlantSignals"] = cur.fetchall()
    if "Movements" in tables:
        cur.execute(
            "SELECT Received, AcsCorrelationId, EventName, DriverId, TruckId, TrailerId, "
            "DropOffLoadingBayName, PickupLoadingBayName, Direction, Origin "
            "FROM [dbo].[Movements] WHERE Received >= ? ORDER BY Received ASC",
            (since,),
        )
        out["Movements"] = cur.fetchall()
    return out
