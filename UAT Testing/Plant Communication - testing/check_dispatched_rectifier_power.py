# -*- coding: utf-8 -*-
"""
check_dispatched_rectifier_power.py
=====================================
Fetches the latest dispatched TimeSerie-blob rectifier power profile from
heraplantdatabasedev ([dbo].[DispatchedRectifiersGlobalPowerProfiles]) and
decodes its [TimeSerie] column the same way as
check_dispatched_profiles.py (reuses its decode_timeserie() -- same blob
format, confirmed live on 2026-09-16).

Unlike DispatchedTruckFillingFlowRateProfiles, this table has NO
[LoadingBayId] -- rectifier power is plant-wide, not per bay, so a
'latest dispatch' query usually returns a single row (per PlantId).

Two columns worth noting that the truck-filling table doesn't surface the
same way:
  - [Unit] / [EnergyType]: "kW" / "Electricity" here, vs "g/s" / "Hydrogen"
    for truck filling.
  - [SourceProfileCollectionId]: FK into dbo.SourceProfileCollections --
    likely the "source profiles snapshot" the TC-SNAP-* PlantCommunication
    test cases describe. Not yet independently explored/confirmed.

Auth: SQL Server authentication (same as SSMS), credentials read from a
local .env file -- see .env.example. Never hardcode credentials here.

Usage
-----
    py check_dispatched_rectifier_power.py
    py check_dispatched_rectifier_power.py --csv rectifier.csv
"""
import argparse
import csv as csv_mod

from db_common import add_common_db_args, connect_from_args
from check_dispatched_profiles import decode_timeserie

TABLE_DEFAULT = "DispatchedRectifiersGlobalPowerProfiles"


def fetch_latest(conn, table: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT [Id], [Unit], [EnergyType], [Start], [End], [Created],
               [TimeSerie], [PlantId], [SourceProfileCollectionId]
        FROM [dbo].[{table}]
        WHERE [Start] = (SELECT MAX([Start]) FROM [dbo].[{table}])
        ORDER BY [Id]
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and decode the latest dispatched rectifier power profile(s) "
                    "from heraplantdatabasedev.",
    )
    parser.add_argument("--table", default=TABLE_DEFAULT)
    add_common_db_args(parser)
    parser.add_argument(
        "--csv", default="latest_rectifier_power.csv",
        help="Path to save every decoded (timestamp, value) point as CSV "
             "(default: latest_rectifier_power.csv). Use --no-csv to skip writing one.",
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
    print(f"\nLatest dispatched '{args.table}' rows ({len(rows)}):\n")
    for r in rows:
        raw = r["TimeSerie"]
        try:
            decoded = decode_timeserie(raw)
        except (ValueError, IndexError) as exc:
            preview = repr(raw)[:200] if raw is not None else "None"
            print(
                f"  Id={r['Id']}  PlantId={r['PlantId']}  Start={r['Start']}  End={r['End']}\n"
                f"    FAILED TO DECODE ({exc}). Raw value (first 200 chars): {preview}"
            )
            continue
        pts = decoded["points"]
        values = [v for _, v in pts]
        nonzero = sum(1 for v in values if v != 0)
        print(
            f"  Id={r['Id']}  PlantId={r['PlantId']}  Start={r['Start']}  End={r['End']}\n"
            f"    Unit={r['Unit']}  EnergyType={r['EnergyType']}  "
            f"SourceProfileCollectionId={r['SourceProfileCollectionId']}\n"
            f"    points={len(pts)}  nonzero={nonzero}  "
            f"min={min(values):.3f}  max={max(values):.3f}  "
            f"dtype={decoded['dtype']}  interp={decoded['interpolation']}"
        )
        for ts, v in pts:
            all_points.append({
                "Id": r["Id"],
                "PlantId": r["PlantId"],
                "timestamp_utc": ts.isoformat(),
                "value": v,
                "unit": r["Unit"],
            })

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(
                f, fieldnames=["Id", "PlantId", "timestamp_utc", "value", "unit"],
            )
            writer.writeheader()
            writer.writerows(all_points)
        print(f"\nSaved {len(all_points)} decoded points to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
