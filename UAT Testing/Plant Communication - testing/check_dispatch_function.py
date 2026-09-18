# -*- coding: utf-8 -*-
"""
check_dispatch_function.py
===========================
Automates the manual Application Insights check for TC-DISP-001/002
(PlantCommunication sheet): "has the Azure dispatch function actually
triggered, when, and did it succeed?"

Runs the same KQL we validated by hand in the Azure Portal (Logs blade of
the heraplantfunctiondev Application Insights resource):

    requests
    | summarize arg_max(timestamp, *) by name
    | project name, timestamp, success, resultCode, duration
    | order by timestamp desc

...but from a script, via the Azure Monitor Query SDK, so a tester doesn't
have to click through the Portal every time.

Auth: interactive browser sign-in (opens your default browser, same MFA
flow you already use for Hera itself). No service principal / secret is
stored anywhere -- this only ever runs with YOUR OWN Azure permissions
(Reader is enough, confirmed live against heraplantfunctiondev).

Usage
-----
    py check_dispatch_function.py
    py check_dispatch_function.py --days 7
    py check_dispatch_function.py --name DispatchTruckFillingProfilesToPlantFunction
    py check_dispatch_function.py --resource-id "/subscriptions/.../components/heraplantservicedev"
    py check_dispatch_function.py --csv last_dispatch_check.csv
"""
import argparse
import sys
from datetime import timedelta

# Resource ID of the heraplantfunctiondev Application Insights resource,
# confirmed live on 2026-09-16 (subscription subs-ve-hera-dev, resource
# group Hera). Override with --resource-id to point at a different
# environment (e.g. heraplantservicedev) or subscription.
DEFAULT_RESOURCE_ID = (
    "/subscriptions/962eccc4-972b-4480-b2b4-52a54fd3a95b"
    "/resourcegroups/hera/providers/microsoft.insights/components/heraplantfunctiondev"
)

QUERY_ALL_FUNCTIONS = """
requests
| summarize arg_max(timestamp, *) by name
| project name, timestamp, success, resultCode, duration
| order by timestamp desc
"""

QUERY_ONE_FUNCTION = """
requests
| where name == "{name}"
| order by timestamp desc
| take {limit}
| project timestamp, success, resultCode, duration
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Azure Function dispatch status via Application Insights "
                    "(TC-DISP-001/002 automation).",
    )
    parser.add_argument(
        "--resource-id", default=DEFAULT_RESOURCE_ID,
        help="Application Insights resource ID to query (default: heraplantfunctiondev).",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="How many days back to look (default: 30).",
    )
    parser.add_argument(
        "--name", default=None,
        help="If given, show every recent invocation of this one function instead of "
             "the 'last run of every function' summary.",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max rows to show when --name is used (default: 50).",
    )
    parser.add_argument(
        "--csv", default=None,
        help="Optional path to also save the result table as CSV.",
    )
    args = parser.parse_args()

    try:
        from azure.identity import InteractiveBrowserCredential
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
    except ImportError:
        print(
            "Missing dependencies. Install them once with:\n"
            "    py -m pip install azure-identity azure-monitor-query\n"
            "(both are now listed in requirements.txt).",
            file=sys.stderr,
        )
        return 1

    query = (
        QUERY_ONE_FUNCTION.format(name=args.name, limit=args.limit)
        if args.name else QUERY_ALL_FUNCTIONS
    )

    print("Signing in (a browser window will open if you're not already logged in)...")
    credential = InteractiveBrowserCredential()
    client = LogsQueryClient(credential)

    print(f"Querying {args.resource_id.split('/')[-1]} — last {args.days} day(s)...")
    response = client.query_resource(
        args.resource_id, query, timespan=timedelta(days=args.days),
    )

    if response.status != LogsQueryStatus.SUCCESS:
        print(f"Query failed or partial: {response.status}", file=sys.stderr)
        if getattr(response, "partial_error", None):
            print(response.partial_error, file=sys.stderr)
        return 1

    table = response.tables[0]
    df = table_to_dataframe(table)

    if df.empty:
        print(
            "No rows returned. Either nothing has run in this window, or this "
            "Application Insights resource isn't receiving telemetry for this "
            "query (see heraplantservicedev, which was confirmed empty on "
            "2026-09-16)."
        )
        return 0

    print()
    print(df.to_string(index=False))
    print()

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"Saved to {args.csv}")

    return 0


def table_to_dataframe(table):
    import pandas as pd
    return pd.DataFrame(data=table.rows, columns=table.columns)


if __name__ == "__main__":
    raise SystemExit(main())
