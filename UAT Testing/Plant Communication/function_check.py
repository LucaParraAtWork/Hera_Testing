# -*- coding: utf-8 -*-
"""
function_check.py
===================
Shared "did the nightly dispatch Azure Function run, on time, and
successfully" check -- the same Application Insights query
check_dispatch_function.py already does standalone, wired in here as a
reusable pre-check for the comparison test scripts in this folder
(test_dispatch_vs_hera_profiles.py, test_bay_occupancy_vs_hera.py).

Auth: SEPARATE from everything else these test scripts use -- this needs
an Azure AD interactive browser login (azure-identity's
InteractiveBrowserCredential), NOT the SQL .env credentials and NOT the
HERA Playwright login. Expect a second, distinct login prompt/browser
window whenever a test script runs this check.

Both resource IDs confirmed live (2026-09-16), by direct portal JSON View,
not guessed:
    dev  -> resource group "hera",                    component "heraplantfunctiondev"
    test -> resource group "heraplantfunctiontest_group", component "heraplantfunctiontest"
The two do NOT follow a clean "swap dev for test" substitution (the
resource GROUP differs, not just the component name) -- an earlier version
of this module assumed they did and got an ApplicationNotFoundError against
the real test environment. Both are now explicit, confirmed literals.

Only one function name is confirmed either: DispatchTruckFillingProfiles-
ToPlantFunction. Whether bay-occupancy/db_schedules dispatch is part of
this SAME nightly run or a separate function was never independently
checked -- both test scripts currently check this one function regardless
of which data they're comparing, which is an assumption, not a confirmed
fact.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Brussels")

FUNCTION_RESOURCE_ID_BY_ENV = {
    "dev": (
        "/subscriptions/962eccc4-972b-4480-b2b4-52a54fd3a95b"
        "/resourcegroups/hera/providers/microsoft.insights/components/heraplantfunctiondev"
    ),
    "test": (
        "/subscriptions/962eccc4-972b-4480-b2b4-52a54fd3a95b"
        "/resourceGroups/heraplantfunctiontest_group/providers/microsoft.insights/components/heraplantfunctiontest"
    ),
}


def get_function_resource_id(env: str) -> str | None:
    """Returns the confirmed Application Insights resource ID for a HERA
    environment, or None if this environment isn't known (caller should
    treat that as "skip this check" rather than guessing)."""
    return FUNCTION_RESOURCE_ID_BY_ENV.get(env)


DEFAULT_FUNCTION_NAME = "DispatchTruckFillingProfilesToPlantFunction"


def check_last_run(resource_id: str, function_name: str = DEFAULT_FUNCTION_NAME,
                    expected_local_hour: int = 22, lookback_days: int = 3,
                    timing_tolerance_hours: float = 2.0) -> tuple[str, str]:
    """Returns (verdict, message) where verdict is 'PASS' or 'FAIL'."""
    try:
        from azure.identity import InteractiveBrowserCredential
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
    except ImportError:
        return ("FAIL", "Missing dependencies: py -m pip install azure-identity "
                         "azure-monitor-query (already in requirements.txt).")

    print("  Signing in to Azure (a browser window will open if needed)...")
    credential = InteractiveBrowserCredential()
    client = LogsQueryClient(credential)

    query = f"""
        requests
        | where name == "{function_name}"
        | order by timestamp desc
        | take 1
    """
    try:
        response = client.query_resource(resource_id, query, timespan=timedelta(days=lookback_days))
    except Exception as exc:
        return "FAIL", f"Application Insights query failed: {exc}"

    if response.status != LogsQueryStatus.SUCCESS:
        return "FAIL", f"Application Insights query returned status: {response.status}"

    table = response.tables[0]
    if not table.rows:
        return ("FAIL", f"No invocation of '{function_name}' found in the last "
                         f"{lookback_days} day(s).")

    row = dict(zip(table.columns, table.rows[0]))
    ts_utc = row["timestamp"]
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)
    success = row.get("success")
    ts_local = ts_utc.astimezone(LOCAL_TZ)

    now_utc = datetime.now(timezone.utc)
    age_hours = (now_utc - ts_utc).total_seconds() / 3600

    expected_local = ts_local.replace(hour=expected_local_hour, minute=0, second=0, microsecond=0)
    timing_diff_hours = abs((ts_local - expected_local).total_seconds()) / 3600

    details = (f"Last run: {ts_local.strftime('%Y-%m-%d %H:%M:%S')} local "
               f"({ts_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC), "
               f"{age_hours:.1f}h ago, success={success}")

    if not success:
        return "FAIL", f"{details} -- last run did NOT succeed."
    if age_hours > 30:
        return "FAIL", f"{details} -- more than 30h old, may not have run recently."
    if timing_diff_hours > timing_tolerance_hours:
        return ("FAIL", f"{details} -- ran {timing_diff_hours:.1f}h away from the expected "
                        f"~{expected_local_hour}:00 local trigger time "
                        f"(tolerance {timing_tolerance_hours}h).")
    return ("PASS", f"{details} -- within {timing_tolerance_hours}h of expected "
                    f"{expected_local_hour}:00 local.")
