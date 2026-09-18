"""
HERA plant->steering MQTT JSON envelope.

Confirmed shape (from hera-virtual-plant's own envelope.py, and cross-checked
against real payloads captured live in heraplantdatabasetest/dev's
PlantSignals table -- see PLANT_SIGNALS_CATALOG.md):

    {"ts": "<ISO-8601 UTC>", "value": <string>, "type": "<tag>", "unit": "<unit>"}

`value` is (almost) always a JSON string, even for numbers -- e.g. 42.5 is
serialised as "42.5", not the bare number 42.5. Nested objects/arrays (RFID
trailer details, ACS event data) are json.dumps'd and then embedded as *that
string* inside `value` (double-encoded).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def iso_now() -> str:
    """Plant wire format: 2026-07-16T08:37:12.730702Z (UTC with Z)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_envelope(value: Any, value_type: str = "float", unit: str = "none") -> str:
    """Build the standard envelope as a JSON string ready to publish as-is."""
    if isinstance(value, (dict, list)):
        value_out: Any = json.dumps(value, separators=(",", ":"))
    elif value_type == "float" and isinstance(value, (int, float)):
        value_out = format(round(float(value), 6), "f").rstrip("0").rstrip(".")
        if value_out in ("-0", ""):
            value_out = "0"
    else:
        value_out = str(value)

    return json.dumps(
        {"ts": iso_now(), "value": value_out, "type": value_type, "unit": unit},
        separators=(",", ":"),
    )


def build_envelope_bytes(value: Any, value_type: str = "float", unit: str = "none") -> bytes:
    return build_envelope(value, value_type, unit).encode("utf-8")
