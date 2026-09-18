# -*- coding: utf-8 -*-
"""
check_bay_occupancy.py
========================
Fetches EVERY row from the most recent dispatch batch of a plain
relational Dispatched* table -- no TimeSerie blob to decode, just
ordinary columns (e.g. DispatchedBayOccupationSessions: LoadingBayId,
OfftakerCompanyId, StartDrop/EndDrop, StartFillingWindow/EndFillingWindow,
StartPickup/EndPickup, DropOffId, PickUpId, ...).

"Most recent batch" means every row sharing the max [Created] timestamp
across the whole table -- a single dispatch inserts N sessions all at
once with the same [Created] value, so this returns exactly that many,
whatever N is (2, 4, 20, ...), rather than an arbitrary fixed count.
This mirrors check_dispatched_profiles.py's WHERE [Start] = MAX([Start]),
just keyed on [Created] since these tables have no [Start]/[End].

For the TimeSerie-blob profile tables (DispatchedTruckFillingFlowRateProfiles,
DispatchedRectifiersGlobalPowerProfiles), use check_dispatched_profiles.py
instead.

Auth: SQL Server authentication (same as SSMS), credentials read from a
local .env file -- see .env.example. Never hardcode credentials here.

Usage
-----
    py check_bay_occupancy.py
    py check_bay_occupancy.py --table DispatchedBayOccupationSessionSchedules
    py check_bay_occupancy.py --table DispatchedScheduledTransfers --csv transfers.csv
    py check_bay_occupancy.py --limit 10
"""
import argparse
import csv as csv_mod

from db_common import add_common_db_args, connect_from_args

TABLE_DEFAULT = "DispatchedBayOccupationSessions"


def fetch_latest_batch(conn, table: str, limit: int | None = None) -> list[dict]:
    """Every row from the most recent dispatch batch (all rows sharing
    the max [Created] timestamp), regardless of how many that batch turns
    out to contain. --limit is an optional safety cap on top of that, not
    the primary selection mechanism.
    """
    cur = conn.cursor()
    top_clause = f"TOP ({limit}) " if limit else ""
    cur.execute(f"""
        SELECT {top_clause}*
        FROM [dbo].[{table}]
        WHERE [Created] = (SELECT MAX([Created]) FROM [dbo].[{table}])
        ORDER BY [Id]
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch every row from the latest dispatch batch of a plain "
                    "(non-TimeSerie) Dispatched* table in heraplantdatabasedev.",
    )
    parser.add_argument("--table", default=TABLE_DEFAULT)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Optional safety cap on top of 'every row from the latest dispatch "
             "batch' (default: no cap -- fetch the whole batch, whatever its size).",
    )
    add_common_db_args(parser)
    parser.add_argument(
        "--csv", default="latest_bay_occupancy.csv",
        help="Path to save the result rows as CSV (default: latest_bay_occupancy.csv, "
             "written next to this script). Use --no-csv to skip writing one.",
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

    rows = fetch_latest_batch(conn, args.table, args.limit)
    if not rows:
        print(f"No rows found in [dbo].[{args.table}].")
        return 0

    created = rows[0]["Created"]
    print(f"\nLatest dispatch batch of '{args.table}' -- Created={created} -- {len(rows)} row(s):\n")
    for r in rows:
        fields = "  ".join(f"{k}={v}" for k, v in r.items() if k not in ("Deleted", "DeletedBy"))
        print(f"  {fields}\n")

    if args.csv:
        fieldnames = list(rows[0].keys())
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Saved {len(rows)} rows to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
