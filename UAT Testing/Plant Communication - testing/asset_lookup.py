# -*- coding: utf-8 -*-
"""
asset_lookup.py
=================
Authoritative LoadingBayId (GUID) -> bay name / MQTT DI identifier mapping,
read from dbo.Assets in heraassetdatabasedev/...test -- NOT the same
database as the plant dispatch tables (heraplantdatabasedev/...test), but
the same SQL Server, same login.

Confirmed live on 2026-09-16 (all 4 bays, both columns real, not guessed):

    Id                                    Name           NameForPlantCommunication
    5348EDBD-26E5-4E49-A948-2D9DAF3D0E47  Loading Bay 1  DI1
    8DC062EF-82C0-4615-B65F-CBDE0F9CDC46  Loading Bay 2  DI2
    587C46B2-1326-4851-80C6-D26EBD7B07B4  Loading Bay 3  DI3
    174DCD98-4838-48F5-9E87-6C59424F4176  Loading Bay 4  DI4

    AssetTypeId for all four: D4F3C2B1-A0E9-4786-9F5D-1234567890AB
    (used as the WHERE filter instead of a Name pattern match, so this
    keeps working even if bay naming conventions change later).

This is a MUCH better source of the bay mapping than deriving it from
segment timing or a live HERA screen capture (both used elsewhere in this
folder as fallbacks) -- it's a permanent, environment-scoped lookup that
works even on a day with zero dispatched activity.

Both heraassetdatabasedev and heraassetdatabasetest are CONFIRMED to exist
(seen directly in SSMS's Object Explorer on 2026-09-16), unlike some of the
other "...test" database names elsewhere in this project which are only
inferred from the naming pattern.
"""
from db_common import connect

ASSET_DB_BY_ENV = {
    "dev": "heraassetdatabasedev",
    "test": "heraassetdatabasetest",
}

LOADING_BAY_ASSET_TYPE_ID = "D4F3C2B1-A0E9-4786-9F5D-1234567890AB"


def fetch_loading_bay_map(server: str, user: str, password: str, env: str) -> dict[str, dict]:
    """Returns {guid_lowercase: {"name": "Loading Bay 1", "di": "DI1"}}."""
    database = ASSET_DB_BY_ENV.get(env, ASSET_DB_BY_ENV["dev"])
    conn = connect(server, database, user, password)
    cur = conn.cursor()
    cur.execute("""
        SELECT Id, Name, NameForPlantCommunication
        FROM dbo.Assets
        WHERE AssetTypeId = ?
    """, LOADING_BAY_ASSET_TYPE_ID)
    return {
        str(row.Id).lower(): {"name": row.Name, "di": row.NameForPlantCommunication}
        for row in cur.fetchall()
    }
