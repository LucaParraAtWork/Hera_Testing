"""
Wires this folder up to its one remaining external dependency:
`db_common.py` in "../Plant Communication - testing/" -- the credentialed,
read-only SQL Server connection to heraplantdatabasetest/dev, shared with
that folder's own check_*.py / test_*.py scripts so there is one source of
truth for DB credentials (one .env, not a duplicated copy here).

Import this module first (before `import db_common`) so it's on sys.path.
Everything else this folder needs (config.py, envelope.py, broker_client.py,
signal_catalog.py, steps.py, db_check.py) lives right here -- plain
same-directory imports, no path setup required for those.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DB_TESTING_DIR = _HERE.parent / "Plant Communication - testing"

if str(DB_TESTING_DIR) not in sys.path:
    sys.path.insert(0, str(DB_TESTING_DIR))
