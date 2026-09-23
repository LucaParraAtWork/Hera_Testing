# -*- coding: utf-8 -*-
"""
db_common.py
============
Shared connection/credential plumbing for the heraplantdatabasedev
check scripts in this folder (check_dispatched_profiles.py,
check_bay_occupancy.py). Not a standalone tool -- imported by both.

Auth: SQL Server authentication (same as SSMS), credentials read from a
local .env file -- see .env.example. Never hardcode credentials here.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from this folder, regardless of the caller's working directory
# (e.g. if a script here is ever invoked from run_all_tests.py at the UAT
# Testing root instead of run directly from this folder).
load_dotenv(Path(__file__).resolve().parent / ".env")

DEFAULT_SERVER = os.getenv("HERA_DB_SERVER", "heradatabasedev.database.windows.net")
DEFAULT_DATABASE = os.getenv("HERA_DB_NAME", "heraplantdatabasedev")
DEFAULT_USER = os.getenv("HERA_DB_USER")
DEFAULT_PASSWORD = os.getenv("HERA_DB_PASSWORD")


def add_common_db_args(parser) -> None:
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)


def connect(server: str, database: str, user: str, password: str):
    import pyodbc
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER=tcp:{server},1433;DATABASE={database};"
        f"UID={user};PWD={password};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)


def connect_from_args(args):
    """Validate credentials and connect, or return None after printing a
    helpful message (caller should treat None as "exit with code 1")."""
    if not args.user or not args.password:
        print(
            "Missing DB credentials. Copy .env.example to .env and fill in "
            "HERA_DB_USER / HERA_DB_PASSWORD (same login you use in SSMS), "
            "or pass --user/--password explicitly.",
            file=sys.stderr,
        )
        return None
    try:
        return connect(args.server, args.database, args.user, args.password)
    except ImportError:
        print(
            "Missing dependency. Install it once with:\n"
            "    py -m pip install pyodbc\n"
            "(already listed in requirements.txt).",
            file=sys.stderr,
        )
        return None
