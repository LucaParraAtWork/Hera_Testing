# -*- coding: utf-8 -*-
"""
check_dispatched_profiles.py
=============================
Fetches the latest dispatched TimeSerie-blob profile(s) from
heraplantdatabasedev -- one row per loading bay, for whichever [Start]
date is most recent -- and decodes the pipe-delimited [TimeSerie] column
into a clean list of (timestamp_utc, value) points.

Confirmed for:
  - DispatchedTruckFillingFlowRateProfiles
  - DispatchedRectifiersGlobalPowerProfiles (assumed same shape -- not
    yet independently verified, check before trusting it)

For plain relational Dispatched* tables with no TimeSerie blob (bay
occupancy sessions, scheduled transfers, ...), use check_bay_occupancy.py
instead.

[TimeSerie] format, confirmed by manually decoding a real row on 2026-09-16:

    <start_ticks>|<step_ticks>|<dtype>|<interpolation>|v0|v1|...|vN

    - start_ticks : .NET DateTime.Ticks (100ns since 0001-01-01), UTC.
                    The real column value has a leading empty field
                    (string starts with "|") -- handled automatically.
    - step_ticks  : .NET TimeSpan.Ticks between points.
    - dtype       : value type, e.g. "double"/"Double".
    - interpolation: e.g. "Constant" (step function).
    - v0..vN      : the actual series values.

Auth: SQL Server authentication (same as SSMS), credentials read from a
local .env file -- see .env.example. Never hardcode credentials here.

Usage
-----
    py check_dispatched_profiles.py
    py check_dispatched_profiles.py --table DispatchedRectifiersGlobalPowerProfiles
    py check_dispatched_profiles.py --csv latest_dispatch.csv
"""
import argparse
import csv as csv_mod
from datetime import datetime, timedelta, timezone

from db_common import add_common_db_args, connect_from_args

TABLE_DEFAULT = "DispatchedTruckFillingFlowRateProfiles"

# DateTime(1970, 1, 1).Ticks in .NET -- the offset between .NET's tick
# epoch (0001-01-01) and the Unix epoch.
DOTNET_UNIX_EPOCH_TICKS = 621355968000000000
TICKS_PER_SECOND = 10_000_000


def decode_timeserie(raw: str) -> dict:
    parts = raw.split("|")
    # The real column value observed in heraplantdatabasedev starts with a
    # leading empty field (i.e. the string itself starts with "|") -- drop
    # it if present. A hand-copied example without it is also tolerated.
    if parts and parts[0] == "":
        parts = parts[1:]
    start_ticks = int(parts[0])
    step_ticks = int(parts[1])
    dtype = parts[2]
    interpolation = parts[3]
    values = [float(v) for v in parts[4:]]

    start_utc = datetime.fromtimestamp(
        (start_ticks - DOTNET_UNIX_EPOCH_TICKS) / TICKS_PER_SECOND, tz=timezone.utc,
    )
    step = timedelta(seconds=step_ticks / TICKS_PER_SECOND)
    points = [(start_utc + i * step, v) for i, v in enumerate(values)]

    return {
        "start_utc": start_utc, "step": step, "dtype": dtype,
        "interpolation": interpolation, "points": points,
    }


def fetch_latest(conn, table: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT [Id], [LoadingBayId], [Start], [End], [Created], [TimeSerie]
        FROM [dbo].[{table}]
        WHERE [Start] = (SELECT MAX([Start]) FROM [dbo].[{table}])
        ORDER BY [LoadingBayId]
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and decode the latest dispatched TimeSerie profile(s) "
                    "from heraplantdatabasedev.",
    )
    parser.add_argument("--table", default=TABLE_DEFAULT)
    add_common_db_args(parser)
    parser.add_argument(
        "--csv", default="latest_dispatched_profiles.csv",
        help="Path to save every decoded (bay, timestamp, value) point as CSV "
             "(default: latest_dispatched_profiles.csv). Use --no-csv to skip writing one.",
    )
    parser.add_argument(
        "--no-csv", action="store_true",
        help="Don't write a CSV file, console output only.",
    )
    args = parser.parse_args()
    if args.no_csv:
        args.csv = None

    conn = connect_from_args(args)
    if conn is None:
        return 1

    rows = fetch_latest(conn, args.table)
    if not rows:
        print(f"No rows found in [dbo].[{args.table}].")
        return 0

    all_points = []
    print(f"\nLatest dispatched '{args.table}' rows ({len(rows)} bay(s)):\n")
    for r in rows:
        raw = r["TimeSerie"]
        try:
            decoded = decode_timeserie(raw)
        except (ValueError, IndexError) as exc:
            preview = repr(raw)[:200] if raw is not None else "None"
            print(
                f"  LoadingBayId={r['LoadingBayId']}  Start={r['Start']}  End={r['End']}\n"
                f"    FAILED TO DECODE ({exc}). Raw value (first 200 chars): {preview}"
            )
            continue
        pts = decoded["points"]
        values = [v for _, v in pts]
        nonzero = sum(1 for v in values if v != 0)
        print(
            f"  LoadingBayId={r['LoadingBayId']}  Start={r['Start']}  End={r['End']}\n"
            f"    points={len(pts)}  nonzero={nonzero}  "
            f"min={min(values):.3f}  max={max(values):.3f}  "
            f"dtype={decoded['dtype']}  interp={decoded['interpolation']}"
        )
        for ts, v in pts:
            all_points.append({
                "LoadingBayId": r["LoadingBayId"],
                "timestamp_utc": ts.isoformat(),
                "value": v,
            })

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=["LoadingBayId", "timestamp_utc", "value"])
            writer.writeheader()
            writer.writerows(all_points)
        print(f"\nSaved {len(all_points)} decoded points to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
