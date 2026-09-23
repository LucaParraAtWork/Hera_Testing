"""
HERA API client.

Endpoints:
  POST /api/ProfileCalculation/TryQueryProfileCalculationsAsync
  POST /api/BayOccupationSessionSchedule/TryQueryBayOccupationSessionScheduleAsync
  POST /api/Dispatch/DispatchTruckFillingProfilesToPlant        (Plant service)
  POST /api/Dispatch/DispatchBayOccupationSessionScheduleToPlant (Plant service)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from config import get_config

# ── Constants ─────────────────────────────────────────────────────────────────

PROFILE_CALC_PATH = "/api/ProfileCalculation/TryQueryProfileCalculationsAsync"
BAY_SCHEDULE_PATH   = "/api/BayOccupationSessionSchedule/TryQueryBayOccupationSessionScheduleAsync"
DISPATCH_PROFILES_PATH   = "/api/Dispatch/DispatchTruckFillingProfilesToPlant"
DISPATCH_RECTIFIERS_PATH = "/api/Dispatch/DispatchRectifiersGlobalPowerProfileToPlant"
DISPATCH_BAY_PATH        = "/api/Dispatch/DispatchBayOccupationSessionScheduleToPlant"


def _profile_calc_url(path: str = PROFILE_CALC_PATH) -> str:
    return get_config()["profile_calc_base"] + path


def _plant_url(path: str) -> str:
    return get_config()["plant_base"] + path

PLANT_ID   = "2366A2C9-8F16-4D45-8241-0665697EDF1C"
LOADING_BAY_IDS = [
    "5348EDBD-26E5-4E49-A948-2D9DAF3D0E47",  # Bay 1
    "174DCD98-4838-48F5-9E87-6C59424F4176",  # Bay 4
    "8DC062EF-82C0-4615-B65F-CBDE0F9CDC46",  # Bay 2
    "587C46B2-1326-4851-80C6-D26EBD7B07B4",  # Bay 3
]
BRUSSELS   = ZoneInfo("Europe/Brussels")
UTC        = timezone.utc
TIMEOUT_S  = 20

# Map the API unit strings to display-friendly labels
UNIT_LABELS: dict[str, str] = {
    "GramPerSec": "g/s",
    "kW":         "kW",
}


# ── Gas-day helpers ───────────────────────────────────────────────────────────

def next_gas_day_bounds(ref_ts: Optional[datetime] = None) -> tuple[str, str]:
    """
    Return the gas-day window STARTING the coming 22:00 UTC on the UTC date of ref_ts.
    Used for bay-schedule queries: ACS messages are sent today for tomorrow's gas day.
    """
    if ref_ts is None:
        ref_ts = datetime.now(tz=UTC)
    utc_date = ref_ts.astimezone(UTC).date()
    frm  = datetime(utc_date.year, utc_date.month, utc_date.day, 22, 0, 0, tzinfo=UTC)
    till = frm + timedelta(days=1)
    return frm.strftime("%Y-%m-%dT%H:%M:%S.000Z"), till.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def gas_day_bounds(for_date: Optional[datetime] = None) -> tuple[str, str]:
    """
    Return (from_iso, till_iso) in UTC for the gas day that contains for_date.
    A European gas day runs 22:00 UTC (D-1) → 22:00 UTC (D).
    """
    if for_date is None:
        for_date = datetime.now(tz=BRUSSELS)

    local_date = for_date.date()
    till = datetime(local_date.year, local_date.month, local_date.day,
                    22, 0, 0, tzinfo=UTC)
    frm  = till - timedelta(days=1)
    return frm.strftime("%Y-%m-%dT%H:%M:%S.000Z"), till.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── API call ──────────────────────────────────────────────────────────────────

def fetch_profiles(
    from_iso: str,
    till_iso: str,
    profile_types: list[str] | None = None,
    energy_types: list[str] | None = None,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Call the API.  Returns (data_dict, error_str).
    On success error_str is None; on failure data_dict is None.
    """
    payload = {
        "from":               {"value": from_iso},
        "till":               {"value": till_iso},
        "byProfileTypes":     profile_types or [],
        "byEnergyTypes":      energy_types  or [],
        "requestedTimeStep":  "00:15:00",
        "byPlantId":          PLANT_ID,
    }
    try:
        resp = requests.post(_profile_calc_url(), json=payload, timeout=TIMEOUT_S)
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        return None, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except Exception as exc:
        return None, str(exc)


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_step(step_str: str) -> timedelta:
    parts = step_str.split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2].split(".")[0])
    return timedelta(hours=h, minutes=m, seconds=s)


def parse_profiles(data: dict) -> pd.DataFrame:
    """
    Expand the API response into a tidy DataFrame (one row per time slot).

    Columns:
      time            – tz-aware timestamp in Europe/Brussels
      value           – numeric setpoint
      type            – profile type  (e.g. "TruckFilling")
      unit            – display unit  (e.g. "g/s")
      energyType      – "Hydrogen" | "Electricity"
      connectionPointId
      offtakerCompanyId
      profileIdx      – index within profiles list (for dedup / tracing)
    """
    rows: list[dict] = []

    for idx, profile in enumerate(data.get("profiles", [])):
        ts_obj     = profile.get("timeSerie", {})
        start_str  = (ts_obj.get("start") or {}).get("value", "")
        step_str   = ts_obj.get("step", "00:15:00")
        values     = ts_obj.get("values", [])

        try:
            start = pd.to_datetime(start_str, utc=True).tz_convert(BRUSSELS)
        except Exception:
            continue

        step         = _parse_step(step_str)
        ptype        = profile.get("type", "")
        raw_unit     = profile.get("unit", "")
        unit         = UNIT_LABELS.get(raw_unit, raw_unit)
        energy_type  = profile.get("energyType", "")
        cp_id        = (profile.get("connectionPoint") or {}).get("connectionPointId", "")
        offtaker_id  = profile.get("offtakerCompanyId", "")

        for i, val in enumerate(values):
            try:
                rows.append({
                    "time":              start + step * i,
                    "value":             float(val),
                    "type":              ptype,
                    "unit":              unit,
                    "energyType":        energy_type,
                    "connectionPointId": cp_id,
                    "offtakerCompanyId": offtaker_id,
                    "profileIdx":        idx,
                })
            except (TypeError, ValueError):
                pass

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def aggregate_scheduled(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sum all profiles of the same (type, unit, energyType) across connection points.
    Returns one row per (time, type, unit, energyType).
    """
    if df.empty:
        return df
    agg = (
        df.groupby(["time", "type", "unit", "energyType"], as_index=False)["value"]
        .sum()
        .sort_values("time")
    )
    return agg


# ── Bay Occupation Session Schedule ───────────────────────────────────────────

# Map the last 4 chars of loadingBayId (upper-cased) → friendly label
BAY_LABEL_MAP: dict[str, str] = {
    "0E47": "Bay 1",
    "DC46": "Bay 2",
    "07B4": "Bay 3",
    "4176": "Bay 4",
}

OFFTAKER_LABEL_MAP: dict[str, str] = {
    "FEC4": "Virya",
    "789F": "Messer",
}

def fetch_bay_schedules(
    from_iso: str,
    till_iso: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Call TryQueryBayOccupationSessionScheduleAsync. Returns (data, error)."""
    payload = {
        "from":      {"value": from_iso},
        "till":      {"value": till_iso},
        "byPlantId": PLANT_ID,
    }
    try:
        resp = requests.post(_profile_calc_url(BAY_SCHEDULE_PATH), json=payload, timeout=TIMEOUT_S)
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        return None, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except Exception as exc:
        return None, str(exc)


def _ts(obj: dict | None, key: str) -> Optional[pd.Timestamp]:
    """Safely parse a nested {value: "..."} timestamp → Brussels-aware Timestamp."""
    if not obj:
        return None
    raw = obj.get(key, {})
    val = raw.get("value") if isinstance(raw, dict) else raw
    if not val:
        return None
    try:
        return pd.to_datetime(val, utc=True).tz_convert(BRUSSELS)
    except Exception:
        return None


def parse_bay_schedules(data: dict) -> pd.DataFrame:
    """
    Expand BayOccupationSessionSchedule response into a flat DataFrame.
    Each session produces 3 rows (one per phase: Drop-off, Filling, Pickup)
    so it can be directly fed into a Plotly timeline / Gantt chart.

    Extra columns (session-level) are kept for the side table.
    """
    sessions = (
        data.get("bayOccupationSessionSchedule") or {}
    ).get("bayOccupationSessions", [])

    rows: list[dict] = []
    for idx, s in enumerate(sessions):
        bay_id      = s.get("loadingBayId", "")
        offtaker_id = s.get("offtakerCompanyId", "")
        suffix4     = bay_id[-4:].upper() if len(bay_id) >= 4 else ""
        bay_label   = BAY_LABEL_MAP.get(suffix4) or (f"Bay …{bay_id[-8:]}" if bay_id else f"Bay {idx+1}")
        offtaker_suffix = offtaker_id[-4:].upper() if len(offtaker_id) >= 4 else ""
        offtaker_label  = OFFTAKER_LABEL_MAP.get(offtaker_suffix, f"…{offtaker_id[-8:]}" if offtaker_id else "?")

        # Trailer metadata (from dropOff sub-object)
        drop_obj   = s.get("dropOff") or {}
        trailer    = (drop_obj.get("estimatedTrailerType") or {})
        trailer_name   = trailer.get("name", "")
        target_mass_g  = trailer.get("targetMass", None)
        target_mass_kg = round(target_mass_g / 1000) if target_mass_g else None
        drop_status    = drop_obj.get("status", "")

        common = dict(
            sessionIdx    = idx,
            bayId         = bay_id,
            bayLabel      = bay_label,
            offtakerId    = offtaker_label,
            trailerType   = trailer_name,
            targetMassKg  = target_mass_kg,
            dropStatus    = drop_status,
        )

        phases = [
            ("Drop-off",      _ts(s, "startDrop"),          _ts(s, "endDrop")),
            ("Filling",       _ts(s, "startFillingWindow"),  _ts(s, "endFillingWindow")),
            ("Pickup",        _ts(s, "startPickup"),         _ts(s, "endPickup")),
        ]
        for phase, start, end in phases:
            if start and end:
                rows.append({**common, "phase": phase, "start": start, "end": end})

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(["sessionIdx", "start"]).reset_index(drop=True)


# ── Gas-day helper (date → ISO UTC strings) ───────────────────────────────────

def gas_day_bounds_from_date(d: date) -> tuple[str, str]:
    """
    Return (from_iso, till_iso) UTC strings for the gas day whose *calendar date*
    is `d`.  Gas day: 22:00 UTC (d-1) → 22:00 UTC (d).
    """
    till = datetime(d.year, d.month, d.day, 22, 0, 0, tzinfo=UTC)
    frm  = till - timedelta(days=1)
    return frm.strftime("%Y-%m-%dT%H:%M:%S.000Z"), till.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# Profile types used by each plant-dispatch endpoint
_TRUCK_FILLING_TYPES = {"TruckFilling"}
_RECTIFIER_POWER_TYPES = {"TruckFilling", "MsolCoveragePenalty", "VoluntaryInjection"}


def dispatch_profile_availability(api_df: pd.DataFrame) -> tuple[bool, bool]:
    """
    Return (can_dispatch_truck_filling, can_dispatch_rectifier_power) for the
    profiles already loaded in ``api_df``.
    """
    if api_df.empty:
        return False, False

    can_truck = not api_df[
        (api_df["type"].isin(_TRUCK_FILLING_TYPES))
        & (api_df["energyType"] == "Hydrogen")
        & (api_df["unit"] == "g/s")
    ].empty

    can_rect = not api_df[
        (api_df["type"].isin(_RECTIFIER_POWER_TYPES))
        & (api_df["energyType"] == "Electricity")
        & (api_df["unit"] == "kW")
    ].empty

    return can_truck, can_rect


def dispatch_bay_availability(bay_df: pd.DataFrame) -> bool:
    """True when at least one bay occupation session is loaded for the gas day."""
    return bay_df is not None and not bay_df.empty


def _friendly_dispatch_error(raw: str, profile_kind: str) -> str:
    """Turn API error payloads into a short user-facing message."""
    if "Failed to fetch profiles for the provided query" in raw:
        return (
            f"{profile_kind}: Profile Calculation returned no profiles for this gas day "
            f"(e.g. no trucks planned → no TruckFilling fields). "
            f"Fetch profiles first, or pick a day with scheduled truck filling."
        )
    if "Failed to retrieve bay occupation session schedule" in raw:
        return (
            "Bay occupancy: no bay occupation sessions for this gas day. "
            "Use **Update Bay Occupancy** first, or pick a day with scheduled trucks."
        )
    return raw


# ── Plant dispatch calls ──────────────────────────────────────────────────────

def dispatch_profiles(from_iso: str, till_iso: str) -> tuple[bool, str]:
    """POST DispatchTruckFillingProfilesToPlant.  Returns (success, error_msg)."""
    body = {
        "from":             {"value": from_iso},
        "till":             {"value": till_iso},
        "energyTypes":      ["Hydrogen"],
        "loadingBayIds":    LOADING_BAY_IDS,
        "requestedTimeStep": "00:15:00",
        "plantId":          PLANT_ID,
    }
    try:
        r = requests.post(_plant_url(DISPATCH_PROFILES_PATH), json=body, timeout=TIMEOUT_S)
        r.raise_for_status()
        return True, ""
    except requests.HTTPError:
        return False, _friendly_dispatch_error(
            f"HTTP {r.status_code}: {r.text[:300]}", "Truck filling"
        )
    except Exception as exc:
        return False, str(exc)


def dispatch_rectifiers_power(from_iso: str, till_iso: str) -> tuple[bool, str]:
    """POST DispatchRectifiersGlobalPowerProfileToPlant.  Returns (success, error_msg)."""
    body = {
        "from":              {"value": from_iso},
        "till":              {"value": till_iso},
        "energyTypes":       ["Electricity"],
        "requestedTimeStep": "00:15:00",
        "plantId":           PLANT_ID,
    }
    try:
        r = requests.post(_plant_url(DISPATCH_RECTIFIERS_PATH), json=body, timeout=TIMEOUT_S)
        r.raise_for_status()
        return True, ""
    except requests.HTTPError:
        return False, _friendly_dispatch_error(
            f"HTTP {r.status_code}: {r.text[:300]}", "Rectifier power"
        )
    except Exception as exc:
        return False, str(exc)


def dispatch_bay_schedule(from_iso: str, till_iso: str) -> tuple[bool, str]:
    """POST DispatchBayOccupationSessionScheduleToPlant.  Returns (success, error_msg)."""
    body = {
        "from":    {"value": from_iso},
        "till":    {"value": till_iso},
        "plantId": PLANT_ID,
    }
    try:
        r = requests.post(_plant_url(DISPATCH_BAY_PATH), json=body, timeout=TIMEOUT_S)
        r.raise_for_status()
        return True, ""
    except requests.HTTPError:
        return False, _friendly_dispatch_error(
            f"HTTP {r.status_code}: {r.text[:300]}", "Bay occupancy"
        )
    except Exception as exc:
        return False, str(exc)
