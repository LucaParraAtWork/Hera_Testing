"""
HERA - Plant Communication (DEMO)
──────────────────────────────────
• Subscribes to HiveMQ Cloud (HOF/+/+/CMD/#) via a background MQTT thread
• Displays live command profiles per device category in Europe/Brussels time
• Fetches scheduled profiles from the HERA Profile Calculation API
• Overlays Scheduled vs Actual for coherence verification
"""

from __future__ import annotations

import math as _math
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from api_client import (
    BRUSSELS,
    OFFTAKER_LABEL_MAP,
    aggregate_scheduled,
    dispatch_bay_schedule,
    dispatch_bay_availability,
    dispatch_profile_availability,
    dispatch_profiles,
    dispatch_rectifiers_power,
    fetch_bay_schedules,
    fetch_profiles,
    gas_day_bounds,
    gas_day_bounds_from_date,
    next_gas_day_bounds,
    parse_bay_schedules,
    parse_profiles,
)
from config import DEFAULT_ENV, ENV_CHOICES, ENV_PILL_COLORS, ENVIRONMENTS, get_config, get_env, set_env
from mqtt_client import MqttMessage, get_state, reconnect_mqtt, start_mqtt

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="HERA - Plant Communication (DEMO)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Colour palette ────────────────────────────────────────────────────────────
DEVICE_COLORS: Dict[str, str] = {
    "Electrolyser": "#00C8FF",
    "Rectifier":    "#FF6B35",
    "Loading Bay":  "#2ECC71",
    "PLC":          "#A78BFA",
}
DEFAULT_COLOR = "#A78BFA"
SCHEDULED_COLOR = "#d97706"   # amber dashed → API scheduled (visible on white)
ACTUAL_PALETTE  = [            # solid        → MQTT actual
    "#0969da", "#cf222e", "#1a7f37", "#8250df",
    "#bf3989", "#0550ae", "#e16f24", "#6e7781",
]


def _hex_to_rgba(hex_color: str, alpha: float = 0.15) -> str:
    """Convert #RRGGBB to an rgba string for Plotly fill colors."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"

# Profile types that together represent the total scheduled output rate
# (rectifier setpoint = sum of all three)
SCHEDULED_PROFILE_TYPES = {"TruckFilling", "MsolCoveragePenalty", "VoluntaryInjection"}

# Only overlay scheduled profiles on sections whose MQTT commands match these keywords
FILL_RATE_KEYWORDS = ("fill_rate", "fill", "loading", "dispens")

# Device-ID pattern for truck dispensers (DI1, DI2, DI-01 …)
import re as _re
_DI_PATTERN = _re.compile(r'^DI[\-_]?\d*$', _re.IGNORECASE)

# ── NCC / Efficiency-curve physics ────────────────────────────────────────────
# Forward:  flow [g/s] → power [MWe]  via piecewise-linear U-curve
# Inverse:  power [MWe] → flow [g/s]  (quadratic per segment)
# K = 40.0445 Nm³/h per g/s  ÷  1000  →  flow×Cs×K = MWe
_NCC_K    = 0.0400445
_NCC_HHV  = 39.4      # Higher Heating Value of H₂  [kWh / kg]
_NCC_DT   = 0.25      # 15-min time step  [h]

_NCC_DEFAULT_PTS: list[tuple[float, float]] = [
    (18.880, 5.09),   # MSOL
    (60.432, 4.54),   # BEP
    (124.860, 4.66),  # MCR
]

_NCC_POINT_LABELS = ["MSOL", "BEP", "MCR"]

_OFFTAKER_FULL: dict[str, str] = {
    "Virya":  "Virya Energy NV",
    "Messer": "Messer Belgium NV",
}


def _ncc_consumption(pts: list, flow_gs: float) -> float:
    """Piecewise-linear: flow [g/s] → specific consumption [kWh/Nm³]."""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    if flow_gs <= xs[0]:
        return ys[0]
    for i in range(len(xs) - 1):
        if flow_gs <= xs[i + 1]:
            t = (flow_gs - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + t * (ys[i + 1] - ys[i])
    return ys[-1]


def _ncc_flow_to_power(pts: list, flow_gs: float) -> float:
    """H₂ flow [g/s] → electrical power [MWe]."""
    return _NCC_K * flow_gs * _ncc_consumption(pts, flow_gs)


def _ncc_power_to_flow(pts: list, power_mwe: float) -> float:
    """Electrical power [MWe] → H₂ flow [g/s]  (quadratic per segment)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ps = [_NCC_K * p[0] * p[1] for p in pts]
    if power_mwe <= ps[0]:
        return xs[0]
    if power_mwe >= ps[-1]:
        return xs[-1]
    for i in range(len(ps) - 1):
        if power_mwe <= ps[i + 1]:
            slope = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i])
            if abs(slope) < 1e-12:
                return power_mwe / (_NCC_K * ys[i])
            a = _NCC_K * slope
            b = _NCC_K * (ys[i] - slope * xs[i])
            c = -power_mwe
            disc = b * b - 4.0 * a * c
            return (-b + _math.sqrt(max(disc, 0.0))) / (2.0 * a)
    return xs[-1]


def _offtaker_short(oid: str) -> str:
    """GUID → short label using last-4-char lookup."""
    suffix = oid[-4:].upper() if len(oid) >= 4 else ""
    return OFFTAKER_LABEL_MAP.get(suffix, f"…{oid[-8:]}" if oid else "Unknown")


def _offtaker_col(short: str) -> str:
    """Short label → full company name for column headers."""
    return _OFFTAKER_FULL.get(short, short)


# Profile type → (energyType, unit) — must match Plant Communication tab filters
_NCC_TYPE_FILTER: dict[str, tuple[str, str]] = {
    "TruckFilling":          ("Hydrogen",    "g/s"),
    "VoluntaryInjection":    ("Hydrogen",    "g/s"),
    "MsolCoveragePenalty":   ("Electricity", "kW"),
}


def _ncc_filter_profiles(
    api_df: pd.DataFrame,
    profile_type: str,
    energy_type: str | None = None,
    unit: str | None = None,
) -> pd.DataFrame:
    """Return the energyType/unit slice for one NCC profile type."""
    if api_df.empty or profile_type not in _NCC_TYPE_FILTER:
        return pd.DataFrame()
    default_etype, default_unit = _NCC_TYPE_FILTER[profile_type]
    etype = energy_type or default_etype
    u     = unit or default_unit
    return api_df[
        (api_df["type"] == profile_type)
        & (api_df["energyType"] == etype)
        & (api_df["unit"] == u)
    ].copy()


def _sum_offtaker_timeseries(df: pd.DataFrame, offtaker_id: str) -> pd.DataFrame:
    """Sum profile values per time slot for one offtaker."""
    sub = df[df["offtakerCompanyId"] == offtaker_id]
    if sub.empty:
        return pd.DataFrame(columns=["time", "value"])
    out = (
        sub.groupby("time", as_index=False)["value"]
        .sum()
        .sort_values("time")
    )
    out["time"] = pd.to_datetime(out["time"])
    return out


def _compute_ncc_metrics(
    api_df: pd.DataFrame,
    pts: list,
    price_elec: float,
    price_gas: float,
) -> tuple[dict, list]:
    """
    Compute per-offtaker NCC metrics from the API profile DataFrame.

    Calculation logic
    -----------------
    TruckFilling  (g/s, Hydrogen)
        tf_e  [MWh_e]  = Σ flow_to_power(flow_gs) × DT
        tf_h2 [MWh_H2] = Σ flow_gs × HHV × 3.6 × DT / 1000
        tf_kg [kg]     = Σ flow_gs × 900 / 1000

    VoluntaryInjection  (g/s, Hydrogen)  — same formula as TruckFilling

    MsolCoveragePenalty  (Electricity kW + Hydrogen g/s from API)
        msol_e       [MWh_e]  = Σ (kW / 1000) × DT
        msol_h2_mwh  [MWh_H2] = Σ flow_gs × HHV × 3.6 × DT / 1000   (Hydrogen profile)
        msol_kg      [kg]     = Σ flow_gs × 900 / 1000

    Cost components
        Electricity Costs For Truck Filling     = tf_e  × price_elec
        Electricity Costs For Voluntary Gas     = vol_e × price_elec
        Voluntary Gas Revenue                   = vol_h2 × price_gas
        MSOL Coverage (net cost of MSOL period) = msol_e × price_elec − msol_h2_mwh × price_gas
        Net Costs = cost_tf + cost_vol − rev_vol + msol_coverage
        NCC [€/kg] = Net Costs / (tf_kg + msol_kg)
    """
    relevant = {"TruckFilling", "MsolCoveragePenalty", "VoluntaryInjection"}
    parts = [
        _ncc_filter_profiles(api_df, "TruckFilling"),
        _ncc_filter_profiles(api_df, "VoluntaryInjection"),
        _ncc_filter_profiles(api_df, "MsolCoveragePenalty", "Electricity", "kW"),
        _ncc_filter_profiles(api_df, "MsolCoveragePenalty", "Hydrogen", "g/s"),
    ]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return {}, []

    ncc_df = pd.concat(parts, ignore_index=True)
    ncc_agg = (
        ncc_df
        .groupby(["type", "offtakerCompanyId", "time", "energyType"], as_index=False)["value"]
        .sum()
    )

    offtaker_ids = sorted(ncc_agg["offtakerCompanyId"].unique())

    results: dict[str, dict] = {}
    for oid in offtaker_ids:
        label = _offtaker_short(oid)
        sub   = ncc_agg[ncc_agg["offtakerCompanyId"] == oid]

        # TruckFilling (Hydrogen g/s)
        tf_vals = sub[
            (sub["type"] == "TruckFilling") & (sub["energyType"] == "Hydrogen")
        ]["value"].clip(lower=0)
        tf_e    = tf_vals.apply(lambda f: _ncc_flow_to_power(pts, f) * _NCC_DT).sum()
        tf_h2   = float((tf_vals * _NCC_HHV * 3.6 * _NCC_DT / 1000).sum())
        tf_kg   = float((tf_vals * 900 / 1000).sum())

        # VoluntaryInjection (Hydrogen g/s)
        vol_vals = sub[
            (sub["type"] == "VoluntaryInjection") & (sub["energyType"] == "Hydrogen")
        ]["value"].clip(lower=0)
        vol_e    = vol_vals.apply(lambda f: _ncc_flow_to_power(pts, f) * _NCC_DT).sum()
        vol_h2   = float((vol_vals * _NCC_HHV * 3.6 * _NCC_DT / 1000).sum())
        vol_kg   = float((vol_vals * 900 / 1000).sum())

        # MsolCoveragePenalty — Electricity kW + Hydrogen g/s (both from API)
        msol_kw = sub[
            (sub["type"] == "MsolCoveragePenalty") & (sub["energyType"] == "Electricity")
        ]["value"].clip(lower=0)
        msol_flow = sub[
            (sub["type"] == "MsolCoveragePenalty") & (sub["energyType"] == "Hydrogen")
        ]["value"].clip(lower=0)
        msol_e        = float((msol_kw / 1000 * _NCC_DT).sum())
        msol_h2_mwh   = float((msol_flow * _NCC_HHV * 3.6 * _NCC_DT / 1000).sum())
        msol_kg       = float((msol_flow * 900 / 1000).sum())

        cost_tf_e  = float(tf_e)  * price_elec
        cost_vol_e = float(vol_e) * price_elec
        rev_vol_h2 = vol_h2       * price_gas
        msol_cov   = msol_e * price_elec - msol_h2_mwh * price_gas
        net_cost   = cost_tf_e + cost_vol_e - rev_vol_h2 + msol_cov
        h2_kg      = tf_kg + msol_kg
        ncc_kg     = net_cost / h2_kg if h2_kg > 0 else 0.0

        results[label] = dict(
            elec_total  = float(tf_e) + float(vol_e) + msol_e,
            tf_e        = float(tf_e),
            tf_h2       = tf_h2,
            tf_kg       = tf_kg,
            vol_e       = float(vol_e),
            vol_h2      = vol_h2,
            vol_kg      = vol_kg,
            msol_e      = msol_e,
            msol_h2_mwh = msol_h2_mwh,
            msol_kg     = msol_kg,
            cost_tf_e  = cost_tf_e,
            cost_vol_e = cost_vol_e,
            rev_vol_h2 = rev_vol_h2,
            msol_cov   = msol_cov,
            net_cost   = net_cost,
            h2_kg      = h2_kg,
            ncc_kg     = ncc_kg,
        )

    # Aggregate total
    if results:
        vals  = list(results.values())
        total = {k: sum(v[k] for v in vals) for k in vals[0]}
        total["ncc_kg"] = (
            total["net_cost"] / total["h2_kg"] if total["h2_kg"] > 0 else 0.0
        )
        results["Total Planning"] = total

    return results, offtaker_ids

def _is_di_device(dev: str) -> bool:
    return bool(_DI_PATTERN.match(dev.strip()))


def _build_scheduled_df(
    api_df: pd.DataFrame,
    api_types: set[str],
    mqtt_df: pd.DataFrame | None = None,
    energy_type: str | None = None,
    series_label: str = "HERA Profile",
) -> pd.DataFrame:
    """
    Return a single summed HERA Profile series for the given api_types.

    energy_type – when provided (e.g. "Electricity" or "Hydrogen"), restrict
                  to that energyType column before summing.  Pass "Electricity"
                  for the Total Plant Power view so only kW profiles are summed.

    mqtt_df     – when provided the result is additionally filtered to the
                  units seen in that MQTT slice (used for Truck Filling so the
                  g/s unit is matched).  Pass None to skip unit filtering.
    """
    if api_df.empty:
        return pd.DataFrame()

    mask = api_df["type"].isin(api_types)
    if energy_type:
        mask &= api_df["energyType"] == energy_type
    api_slice = api_df[mask]

    if mqtt_df is not None and not mqtt_df.empty:
        mqtt_units = set(mqtt_df["unit"][mqtt_df["unit"] != ""].unique())
        if mqtt_units:
            api_slice = api_slice[api_slice["unit"].isin(mqtt_units)]

    if api_slice.empty:
        return pd.DataFrame()

    unit_val   = api_slice["unit"].iloc[0]
    energy_val = api_slice["energyType"].iloc[0]
    agg = (
        api_slice.groupby("time", as_index=False)["value"].sum()
        .assign(type=series_label, unit=unit_val, energyType=energy_val)
    )
    # Convert kW → MW for readability
    if unit_val == "kW":
        agg["value"] = agg["value"] / 1000
        agg["unit"]  = "MW"
        unit_val = "MW"
    return agg

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] {background:#f5f7fa;}
[data-testid="stSidebar"]          {background:#ffffff; border-right:1px solid #e0e4ea;}
[data-testid="stHeader"]           {background:transparent;}
[data-testid="metric-container"]   {
    background:#ffffff; border:1px solid #d0d7de;
    border-radius:10px; padding:12px 16px;
    box-shadow:0 1px 3px rgba(0,0,0,.06);
}
.section-header {
    font-size:1.1rem; font-weight:600; color:#1c2128;
    border-left:4px solid; padding-left:10px; margin-bottom:6px;
}
.pill {display:inline-block;padding:2px 12px;border-radius:999px;
       font-size:.78rem;font-weight:600;letter-spacing:.04em;}
.pill-green  {background:#d4f5e0;color:#1a7f37;}
.pill-red    {background:#fde8e8;color:#b91c1c;}
.pill-yellow {background:#fef3c7;color:#92400e;}
.log-row  {font-family:monospace;font-size:.78rem;color:#57606a;}
.log-topic{color:#0969da;font-weight:600;}
/* make default text darker */
html, body, [class*="css"] {color:#1c2128;}
/* hide sidebar toggle arrow */
[data-testid="collapsedControl"] {display:none;}

/* ── Quick-action buttons in the sidebar expander ──────────────────────────
   "waiting / not yet sent"  →  orange
   "done for this day"       →  green
   Scoped to sidebar + expander so other sidebar/main buttons are unaffected. */
[data-testid="stSidebarContent"] details[open] button[data-testid="stBaseButton-secondary"] {
    background-color: #f97316 !important;
    color: #ffffff !important;
    border: none !important;
    font-weight: 600 !important;
    transition: filter .15s;
}
[data-testid="stSidebarContent"] details[open] button[data-testid="stBaseButton-secondary"]:hover {
    filter: brightness(1.1);
}
[data-testid="stSidebarContent"] details[open] button[data-testid="stBaseButton-primary"] {
    background-color: #22c55e !important;
    color: #ffffff !important;
    border: none !important;
    font-weight: 600 !important;
    transition: filter .15s;
}
[data-testid="stSidebarContent"] details[open] button[data-testid="stBaseButton-primary"]:hover {
    filter: brightness(1.1);
}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
if "hera_env" not in st.session_state or st.session_state.hera_env not in ENV_CHOICES:
    st.session_state.hera_env = DEFAULT_ENV
set_env(st.session_state.hera_env)

if "broker_state" not in st.session_state:
    st.session_state.broker_state = get_state()
    start_mqtt(st.session_state.broker_state)
if "api_df" not in st.session_state:
    st.session_state.api_df = pd.DataFrame()
if "api_error" not in st.session_state:
    st.session_state.api_error = ""
if "api_fetched_range" not in st.session_state:
    st.session_state.api_fetched_range = ""
if "api_auto_ts" not in st.session_state:
    st.session_state.api_auto_ts = ""   # last auto-fetch wall-clock time
# Bay schedule (ACS) state
if "bay_df" not in st.session_state:
    st.session_state.bay_df = pd.DataFrame()
if "bay_error" not in st.session_state:
    st.session_state.bay_error = ""
if "bay_fetched_range" not in st.session_state:
    st.session_state.bay_fetched_range = ""
if "bay_auto_ts" not in st.session_state:
    st.session_state.bay_auto_ts = ""
# Quick-action button done-state  (keyed by gas-day date string)
for _qa_key in ("_qa_done_profiles", "_qa_done_bay", "_qa_done_disp_power", "_qa_done_disp_bay"):
    if _qa_key not in st.session_state:
        st.session_state[_qa_key] = ""   # stores the date string when last confirmed
if "_qa_active_day" not in st.session_state:
    st.session_state["_qa_active_day"] = str(datetime.now(tz=BRUSSELS).date())
if "_profile_view" not in st.session_state:
    st.session_state["_profile_view"] = "Truck Filling (g/s)"

broker_state = st.session_state.broker_state


# ── Helpers ───────────────────────────────────────────────────────────────────

def color_for(cat: str) -> str:
    return DEVICE_COLORS.get(cat, DEFAULT_COLOR)


def to_brussels(ts) -> pd.Timestamp:
    """Convert a UTC-aware timestamp to Europe/Brussels."""
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(BRUSSELS)
    return ts


def _cmd_label(cmd_suffix: str) -> str:
    parts = cmd_suffix.split("/")
    return "/".join(p for p in parts if not p.replace(".", "").isdigit()) or cmd_suffix




def extract_profile_df(messages: List[MqttMessage]) -> pd.DataFrame:
    """
    Convert the latest message per (device_id, cmd) into a tidy DataFrame.
    Timestamps are converted to Europe/Brussels.

    Handles payload shapes:
      A  {"ts":"…","unit":"g/s","value":[{"time":"…","value":X},…]}  ← HOF actual
      B  {"profile":[{"t":"…","value":X},…]}
      C  {"ts/timestamp":"…","value": scalar}
      D  [scalar,…]
      E  bare scalar
    """
    latest: Dict[tuple, MqttMessage] = {}
    for msg in messages:
        key = (msg.device_id, msg.cmd_suffix)
        if key not in latest or msg.received_at > latest[key].received_at:
            latest[key] = msg

    rows = []
    for msg in latest.values():
        p       = msg.payload
        ts_base = to_brussels(pd.Timestamp(msg.received_at))
        unit    = ""

        if isinstance(p, dict):
            unit = str(p.get("unit", ""))

            # Shape A / B – array in "value" or "profile"
            array_data: list | None = None
            if "value" in p and isinstance(p["value"], list):
                array_data = p["value"]
            elif "profile" in p and isinstance(p["profile"], list):
                array_data = p["profile"]

            if array_data is not None:
                for entry in array_data:
                    if not isinstance(entry, dict):
                        continue
                    t_raw = (entry.get("time") or entry.get("t")
                             or entry.get("timestamp") or entry.get("ts"))
                    v_raw = (entry.get("value") if entry.get("value") is not None
                             else entry.get("v") or entry.get("setpoint")
                             or entry.get("power") or entry.get("flow"))
                    if v_raw is None:
                        continue
                    try:
                        ts  = to_brussels(pd.to_datetime(t_raw, utc=True)) if t_raw else ts_base
                        val = float(v_raw)
                        rows.append({"time": ts, "value": val,
                                     "device": msg.device_id,
                                     "cmd": _cmd_label(msg.cmd_suffix),
                                     "unit": unit, "topic": msg.topic})
                    except (TypeError, ValueError):
                        pass
                continue

            # Shape C – scalar with optional ts
            ts_raw = p.get("ts") or p.get("timestamp") or p.get("time")
            for k in ("value", "v", "setpoint", "power", "current",
                      "voltage", "frequency", "flow", "pressure"):
                if k in p and not isinstance(p[k], (list, dict)):
                    try:
                        ts  = to_brussels(pd.to_datetime(ts_raw, utc=True)) if ts_raw else ts_base
                        rows.append({"time": ts, "value": float(p[k]),
                                     "device": msg.device_id,
                                     "cmd": _cmd_label(msg.cmd_suffix),
                                     "unit": unit, "topic": msg.topic})
                    except (TypeError, ValueError):
                        pass
                    break

        elif isinstance(p, list):
            for i, v in enumerate(p):
                try:
                    rows.append({"time": i, "value": float(v),
                                 "device": msg.device_id,
                                 "cmd": _cmd_label(msg.cmd_suffix),
                                 "unit": "", "topic": msg.topic})
                except (TypeError, ValueError):
                    pass

        elif isinstance(p, (int, float)):
            rows.append({"time": ts_base, "value": float(p),
                         "device": msg.device_id,
                         "cmd": _cmd_label(msg.cmd_suffix),
                         "unit": "", "topic": msg.topic})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if pd.api.types.is_datetime64_any_dtype(df["time"]):
        df = df.sort_values("time")

    # Drop (device, cmd) series whose values are ALL zero — they add no information
    if not df.empty:
        max_abs = df.groupby(["device", "cmd"])["value"].transform(lambda s: s.abs().max())
        df = df[max_abs > 0].reset_index(drop=True)

    return df


def make_profile_figure(
    df_actual: pd.DataFrame,
    df_scheduled: pd.DataFrame,
    category: str,
    title: str,
    max_pts: int,
) -> go.Figure:
    """
    Build a Plotly figure overlaying:
      • solid lines  = Plant-received Profiles / MQTT (per device)
      • dashed lines = HERA Profiles / API (per type·unit·energyType)
    """
    color = color_for(category)

    y_label = "Value"
    if not df_actual.empty and "unit" in df_actual.columns:
        u = df_actual["unit"][df_actual["unit"] != ""].value_counts()
        if not u.empty:
            y_label = u.index[0]
    if y_label == "Value" and not df_scheduled.empty:
        u = df_scheduled["unit"][df_scheduled["unit"] != ""].value_counts()
        if not u.empty:
            y_label = u.index[0]

    fig = go.Figure()

    # ── HERA Profile traces (dashed) ─────────────────────────────────────────
    if not df_scheduled.empty:
        groups = df_scheduled.groupby(["type", "unit", "energyType"])
        for gi, ((ptype, punit, etype), grp) in enumerate(groups):
            label = f"HERA Profile · {ptype} ({punit})"
            fig.add_trace(go.Scatter(
                x=grp["time"],
                y=grp["value"],
                mode="lines",
                name=label,
                line=dict(color=SCHEDULED_COLOR, width=3, dash="dash"),
                fill="tozeroy",
                fillcolor=_hex_to_rgba(SCHEDULED_COLOR),
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Time: %{x|%d/%m %H:%M}<br>"
                    f"Value: %{{y:.3f}} {punit}<br>"
                    "<extra></extra>"
                ),
            ))

    # ── Plant-received Profile traces (solid) ────────────────────────────────
    if not df_actual.empty:
        devices = sorted(df_actual["device"].unique())
        cmds    = sorted(df_actual["cmd"].unique())
        trace_idx = 0
        for dev in devices:
            for cmd in cmds:
                subset = df_actual[
                    (df_actual["device"] == dev) & (df_actual["cmd"] == cmd)
                ].tail(max_pts)
                if subset.empty:
                    continue
                label = f"Plant-received · {dev}" + (f" · {cmd}" if cmd else "")
                trace_color = ACTUAL_PALETTE[trace_idx % len(ACTUAL_PALETTE)]
                fig.add_trace(go.Scatter(
                    x=subset["time"],
                    y=subset["value"],
                    mode="lines",
                    name=label,
                    line=dict(color=trace_color, width=2),
                    fill="tozeroy",
                    fillcolor=_hex_to_rgba(trace_color),
                    hovertemplate=(
                        "<b>%{fullData.name}</b><br>"
                        "Time: %{x|%d/%m %H:%M}<br>"
                        f"Value: %{{y:.3f}} {y_label}<br>"
                        "<extra></extra>"
                    ),
                ))
                trace_idx += 1

    if df_actual.empty and df_scheduled.empty:
        fig.add_annotation(
            text="No data yet…", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#8b949e"),
        )

    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1c2128")),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f5f7fa",
        font=dict(color="#57606a"),
        xaxis=dict(gridcolor="#d0d7de", zerolinecolor="#d0d7de",
                   title="Time (Brussels)", tickformat="%d/%m %H:%M",
                   linecolor="#d0d7de"),
        yaxis=dict(gridcolor="#d0d7de", zerolinecolor="#d0d7de", title=y_label,
                   linecolor="#d0d7de"),
        legend=dict(bgcolor="#ffffff", bordercolor="#d0d7de", borderwidth=1,
                    font=dict(size=11)),
        margin=dict(l=60, r=20, t=50, b=40),
        height=400,
    )
    return fig


def _fmt_ts(ts) -> str:
    """Format a Brussels-tz Timestamp for table display."""
    try:
        return pd.Timestamp(ts).strftime("%d/%m %H:%M")
    except Exception:
        return str(ts)


def render_acs_section(family_msgs: List[MqttMessage], bay_df: pd.DataFrame):
    """
    Render the ACS (Access Control System) section:
      • MQTT db_schedules timing table (skip db_mappings and other topics)
      • API bay-occupation schedule timing table + Gantt
    """
    import plotly.express as px

    # ── MQTT: only db_schedules messages ──────────────────────────────────────
    schedule_msgs = [
        m for m in family_msgs
        if "db_schedules" in m.cmd_suffix.lower()
    ]

    mqtt_rows: list[dict] = []
    latest_sched_msg = None
    for msg in reversed(schedule_msgs):
        p = msg.payload
        if isinstance(p, dict) and isinstance(p.get("value"), list):
            latest_sched_msg = msg
            for entry in p["value"]:
                if not isinstance(entry, dict):
                    continue
                row: dict = {}
                start_ts = None
                for k, v in entry.items():
                    # Convert any ISO datetime string to Brussels time
                    if isinstance(v, str):
                        try:
                            parsed = pd.to_datetime(v, utc=True).tz_convert(BRUSSELS)
                            if k in ("start", "drop_start") and start_ts is None:
                                start_ts = parsed
                            row[k] = parsed.strftime("%d/%m %H:%M")
                            continue
                        except Exception:
                            pass
                    row[k] = v
                if start_ts is None:
                    for k in ("start", "drop_start"):
                        raw = entry.get(k)
                        if raw:
                            try:
                                start_ts = pd.to_datetime(raw, utc=True).tz_convert(BRUSSELS)
                                break
                            except Exception:
                                pass
                row["_start_ts"] = start_ts
                mqtt_rows.append(row)
            break  # latest message only

    # ──────────────────────────────────────────────────────────────────────────
    # Slot-view schema  (same for both tables):
    #   # | Start → End | Drop | Pick | Type | Offtaker
    #
    # "Type" is derived:  drop-only / pick-only / drop+pick
    # "Start → End" = slot window (start and end timestamps)
    # ──────────────────────────────────────────────────────────────────────────
    SLOT_COLS = ["#", "Start → End", "Drop", "Pick", "Type", "Offtaker"]

    def _sv(d: dict, *keys: str) -> str:
        """Return first non-empty value among candidate keys, else ''."""
        for k in keys:
            v = d.get(k)
            if v not in (None, "", "—"):
                return str(v)
        return ""

    def _slot_type(drop: str, pick: str) -> str:
        if drop and pick:
            return "drop+pick"
        if drop:
            return "drop-only"
        if pick:
            return "pick-only"
        return "—"

    def _slot_range(start: str, end: str) -> str:
        if start and end:
            return f"{start} → {end}"
        return start or end or "—"

    def _title(name: str) -> str:
        """VIRYA → Virya, MESSER → Messer, etc."""
        return name.title() if name else "—"

    def _bay_label(raw: str) -> str:
        """DI1 → Bay 1, DI2 → Bay 2, etc.  Unknown values passed through."""
        if not raw:
            return "—"
        up = raw.upper().strip()
        if up.startswith("DI") and up[2:].isdigit():
            return f"Bay {up[2:]}"
        return raw

    _SORT_FALLBACK = pd.Timestamp("9999-12-31", tz=BRUSSELS)

    def _sort_slot_rows(rows: list[dict]) -> list[dict]:
        """Sort slot rows by start time and renumber the # column."""
        rows.sort(key=lambda r: r.get("_start_ts") or _SORT_FALLBACK)
        cleaned: list[dict] = []
        for i, row in enumerate(rows, 1):
            cleaned.append({
                k: v for k, v in row.items() if k != "_start_ts"
            } | {"#": i})
        return cleaned

    # ── MQTT table ─────────────────────────────────────────────────────────────
    # Show the payload fields as-is (the exact key names come from the broker).
    # We map known fields (#, Start→End, Offtaker) and expose every other field
    # as its own column so bay / pick / drop names are always visible regardless
    # of whatever key the firmware uses.
    #
    # Known stable keys: offtake_co_id, start, end
    # Bay keys: discovered at runtime from whichever fields are present.
    _KNOWN_SKIP = {"offtake_co_id", "start", "end"}   # handled explicitly
    # Detect bay-like field names: any key whose value is "DI\d+" in any row
    _bay_fields: dict[str, str] = {}   # raw_key → "drop" | "pick" | "bay"
    for entry in mqtt_rows:
        for k, v in entry.items():
            if k in _KNOWN_SKIP or k in _bay_fields:
                continue
            if isinstance(v, str) and v.upper().startswith("DI") and v[2:].isdigit():
                kl = k.lower()
                if any(w in kl for w in ("pick", "exit", "out")):
                    _bay_fields[k] = "pick"
                elif any(w in kl for w in ("drop", "entry", "in", "load")):
                    _bay_fields[k] = "drop"
                else:
                    _bay_fields[k] = "bay"

    # Build drop / pick from detected fields (or fall back to fixed candidates)
    _FIXED_DROP = ["drop_bay_id", "drop_bay", "entry_bay_id", "loading_bay_id"]
    _FIXED_PICK = ["pick_bay_id", "pickup_bay_id", "exit_bay_id"]
    _drop_keys = [k for k, r in _bay_fields.items() if r == "drop"] or _FIXED_DROP
    _pick_keys  = [k for k, r in _bay_fields.items() if r == "pick"] or _FIXED_PICK
    _solo_keys  = [k for k, r in _bay_fields.items() if r == "bay"]

    mqtt_slot_rows: list[dict] = []
    for i, entry in enumerate(mqtt_rows, 1):
        drop = _sv(entry, *_drop_keys)
        pick = _sv(entry, *_pick_keys)
        # If still not found, check solo bay keys (unknown direction)
        if not drop and not pick and _solo_keys:
            drop = _sv(entry, *_solo_keys)  # treat ambiguous bay as drop
        start = _sv(entry, "start", "drop_start")
        end   = _sv(entry, "end",   "pick_end", "drop_end")
        mqtt_slot_rows.append({
            "#":           i,
            "Start → End": _slot_range(start, end),
            "Drop":        _bay_label(drop),
            "Pick":        _bay_label(pick),
            "Type":        _slot_type(drop, pick),
            "Offtaker":    _title(_sv(entry, "offtake_co_id", "offtaker")),
            "_start_ts":   entry.get("_start_ts"),
        })

    mqtt_slot_rows = _sort_slot_rows(mqtt_slot_rows)

    # Debug: if drop/pick are still all empty, expose raw keys so the field
    # names become visible
    _all_empty = all(
        r["Drop"] == "—" and r["Pick"] == "—" for r in mqtt_slot_rows
    ) if mqtt_slot_rows else False

    df_mqtt_tbl = (
        pd.DataFrame(mqtt_slot_rows, columns=SLOT_COLS)
        if mqtt_slot_rows else pd.DataFrame(columns=SLOT_COLS)
    )

    # ── API table ──────────────────────────────────────────────────────────────
    # Build per-phase event lists from each session, then cross-match them
    # to reconstruct slots:
    #
    #   drop-only  → one session's Drop-off phase has no concurrent Pickup
    #                Start/End = drop-off window
    #
    #   drop+pick  → one Drop-off and one Pickup at DIFFERENT bays overlap in
    #                time → same slot;  Start/End = drop-off window
    #
    #   pick-only  → a Pickup phase with no concurrent Drop-off
    #                Start/End = pickup_start − 15 min → pickup_end − 15 min
    #
    _SLOT_MATCH_WINDOW = pd.Timedelta(hours=2)   # max gap to consider "same slot"
    _PICKUP_OFFSET     = pd.Timedelta(minutes=15) # pick-only slot displayed 15 min early

    api_slot_rows: list[dict] = []
    if not bay_df.empty:
        # Collect raw phase events
        drop_evts: list[dict] = []
        pick_evts: list[dict] = []

        for sid in sorted(bay_df["sessionIdx"].unique()):
            sess     = bay_df[bay_df["sessionIdx"] == sid]
            bay      = sess.iloc[0]["bayLabel"]
            offtaker = sess.iloc[0]["offtakerId"]

            d_row = sess[sess["phase"] == "Drop-off"]
            p_row = sess[sess["phase"] == "Pickup"]

            if not d_row.empty:
                drop_evts.append({
                    "bay": bay, "offtaker": offtaker,
                    "start": d_row.iloc[0]["start"],
                    "end":   d_row.iloc[0]["end"],
                })
            if not p_row.empty:
                pick_evts.append({
                    "bay": bay, "offtaker": offtaker,
                    "start": p_row.iloc[0]["start"],
                    "end":   p_row.iloc[0]["end"],
                })

        # Sort both lists chronologically
        drop_evts.sort(key=lambda e: e["start"])
        pick_evts.sort(key=lambda e: e["start"])

        # Greedy match: for each drop event find the pick event whose window
        # overlaps or is within SLOT_MATCH_WINDOW (first unmatched one wins)
        used_picks: set[int] = set()
        raw_slots: list[dict] = []

        for drop in drop_evts:
            matched_pi = None
            for pi, pick in enumerate(pick_evts):
                if pi in used_picks:
                    continue
                # Overlap: drop.start < pick.end  AND  pick.start < drop.end + window
                if drop["start"] < pick["end"] and pick["start"] < drop["end"] + _SLOT_MATCH_WINDOW:
                    matched_pi = pi
                    break

            if matched_pi is not None:
                pick = pick_evts[matched_pi]
                used_picks.add(matched_pi)
                raw_slots.append({
                    "type":     "drop+pick",
                    "drop_bay": drop["bay"],
                    "pick_bay": pick["bay"],
                    "offtaker": drop["offtaker"],
                    "start":    drop["start"],
                    "end":      drop["end"],
                })
            else:
                raw_slots.append({
                    "type":     "drop-only",
                    "drop_bay": drop["bay"],
                    "pick_bay": None,
                    "offtaker": drop["offtaker"],
                    "start":    drop["start"],
                    "end":      drop["end"],
                })

        # Remaining unmatched pickups → pick-only (shifted −15 min for display)
        for pi, pick in enumerate(pick_evts):
            if pi in used_picks:
                continue
            raw_slots.append({
                "type":     "pick-only",
                "drop_bay": None,
                "pick_bay": pick["bay"],
                "offtaker": pick["offtaker"],
                "start":    pick["start"] - _PICKUP_OFFSET,
                "end":      pick["end"]   - _PICKUP_OFFSET,
            })

        # Sort final slots by start time and build display rows
        raw_slots.sort(key=lambda s: s["start"])
        for slot in raw_slots:
            api_slot_rows.append({
                "#":           0,
                "Start → End": _slot_range(_fmt_ts(slot["start"]), _fmt_ts(slot["end"])),
                "Drop":        slot["drop_bay"] or "—",
                "Pick":        slot["pick_bay"] or "—",
                "Type":        slot["type"],
                "Offtaker":    slot["offtaker"],
                "_start_ts":   slot["start"],
            })

        api_slot_rows = _sort_slot_rows(api_slot_rows)

    df_api_tbl = (
        pd.DataFrame(api_slot_rows, columns=SLOT_COLS)
        if api_slot_rows else pd.DataFrame(columns=SLOT_COLS)
    )

    # ── Cross-table row colouring ──────────────────────────────────────────────
    # Rows that represent the same slot in both tables get the same pastel
    # highlight.  Match key = (offtaker, drop bay, pick bay) — normalised so
    # "—" values are treated as empty strings for comparison.
    _PAIR_COLORS = [
        "#dbeafe",  # blue-100
        "#dcfce7",  # green-100
        "#fef9c3",  # yellow-100
        "#fce7f3",  # pink-100
        "#ede9fe",  # violet-100
        "#ffedd5",  # orange-100
        "#ccfbf1",  # teal-100
    ]

    def _mkey(row: dict) -> tuple:
        norm = lambda v: str(v).strip().lower().replace("—", "")
        return (norm(row["Offtaker"]), norm(row["Drop"]), norm(row["Pick"]))

    mqtt_keys = [_mkey(r) for r in df_mqtt_tbl.to_dict("records")]
    api_keys  = [_mkey(r) for r in df_api_tbl.to_dict("records")]

    # Assign a colour to every key that appears in BOTH tables
    shared_keys = set(mqtt_keys) & set(api_keys) - {("", "", "")}
    key_color: dict[tuple, str] = {
        k: _PAIR_COLORS[i % len(_PAIR_COLORS)]
        for i, k in enumerate(sorted(shared_keys))
    }

    def _style_df(df: pd.DataFrame, keys: list[tuple]):
        """Return a Styler with matching rows highlighted."""
        def _row_bg(row):
            color = key_color.get(keys[row.name], "")
            return [f"background-color:{color}" if color else "" for _ in row]
        return df.reset_index(drop=True).style.apply(_row_bg, axis=1)

    # ── Render side by side — same column widths force header alignment ────────
    col_l, col_r = st.columns(2, gap="large")

    with col_l:
        st.markdown("#### 📨 MQTT — Sent Schedule")
        if latest_sched_msg:
            ts_str = latest_sched_msg.received_at.strftime("%H:%M:%S UTC")
            st.caption(f"`{latest_sched_msg.topic}`  ·  {ts_str}")
        else:
            st.caption(" ")
        if df_mqtt_tbl.empty or df_mqtt_tbl.eq("—").all(axis=None):
            st.info("Waiting for `db_schedules` messages…")
        else:
            st.dataframe(
                _style_df(df_mqtt_tbl, mqtt_keys),
                use_container_width=True, hide_index=True,
            )
            if _all_empty and mqtt_rows:
                with st.expander("🔍 Debug — raw payload keys (drop/pick not found)"):
                    st.json({k: v for k, v in mqtt_rows[0].items()})

    with col_r:
        st.markdown("#### 📡 API — Bay Occupation Schedule")
        if st.session_state.bay_fetched_range:
            st.caption(
                "`/api/BayOccupationSessionSchedule/TryQueryBayOccupationSessionScheduleAsync`"
                f"  ·  {st.session_state.bay_fetched_range}"
            )
        else:
            st.caption(" ")
        if df_api_tbl.empty:
            st.info("Fetch from the API to see scheduled sessions.")
        else:
            st.dataframe(
                _style_df(df_api_tbl, api_keys),
                use_container_width=True, hide_index=True,
            )

    # ── Gantt timeline ─────────────────────────────────────────────────────────
    if not bay_df.empty:
        with st.expander("🗓 Bay Occupation Timeline (API)", expanded=False):
            PHASE_COLORS = {
                "Drop-off": "#93c5fd",   # blue-300
                "Filling":  "#86efac",   # green-300
                "Pickup":   "#fcd34d",   # amber-300
            }
            # Always show all 4 bays in fixed order, even if some have no data
            _ALL_BAYS = ["Bay 1", "Bay 2", "Bay 3", "Bay 4"]
            _x_ref = bay_df["start"].min()
            _pad_rows = [
                {"bayLabel": b, "phase": "Drop-off",
                 "start": _x_ref, "end": _x_ref,
                 "offtakerId": "", "trailerType": "", "targetMassKg": None,
                 "dropStatus": "", "sessionIdx": -1}
                for b in _ALL_BAYS if b not in bay_df["bayLabel"].values
            ]
            _plot_df = pd.concat(
                [bay_df] + ([pd.DataFrame(_pad_rows)] if _pad_rows else []),
                ignore_index=True,
            )
            _plot_df["bayLabel"] = pd.Categorical(
                _plot_df["bayLabel"], categories=_ALL_BAYS, ordered=True
            )
            _plot_df = _plot_df.sort_values("bayLabel")

            fig = px.timeline(
                _plot_df,
                x_start="start",
                x_end="end",
                y="bayLabel",
                color="phase",
                color_discrete_map=PHASE_COLORS,
                hover_data={
                    "offtakerId":   True,
                    "trailerType":  True,
                    "targetMassKg": True,
                    "dropStatus":   True,
                    "start":        "|%d/%m %H:%M",
                    "end":          "|%d/%m %H:%M",
                    "bayLabel":     False,
                    "phase":        False,
                },
                labels={"bayLabel": "Loading Bay", "phase": "Phase"},
                category_orders={"bayLabel": _ALL_BAYS},
            )
            fig.update_yaxes(
                autorange="reversed",
                categoryorder="array",
                categoryarray=_ALL_BAYS,
            )
            fig.update_layout(
                paper_bgcolor="#ffffff",
                plot_bgcolor="#f5f7fa",
                font=dict(color="#57606a"),
                xaxis=dict(
                    gridcolor="#d0d7de",
                    title="Time (Brussels)",
                    tickformat="%d/%m %H:%M",
                ),
                yaxis=dict(gridcolor="#d0d7de"),
                legend=dict(
                    bgcolor="#ffffff", bordercolor="#d0d7de", borderwidth=1,
                    title="Phase",
                ),
                margin=dict(l=120, r=20, t=30, b=40),
                height=80 * 4 + 80,  # fixed height for 4 bays
            )
            st.plotly_chart(fig, use_container_width=True, key="acs_gantt")

            # ── Detailed session table ─────────────────────────────────────────
            with st.expander("📋 Full session details"):
                detail = bay_df.copy()
                detail["start"] = detail["start"].dt.strftime("%d/%m %H:%M")
                detail["end"]   = detail["end"].dt.strftime("%d/%m %H:%M")
                detail["offtakerId"] = detail["offtakerId"].str[-8:]
                detail = detail[[
                    "bayLabel", "phase", "start", "end",
                    "offtakerId", "trailerType", "targetMassKg", "dropStatus",
                ]].rename(columns={
                    "bayLabel":    "Bay",
                    "phase":       "Phase",
                    "start":       "Start",
                    "end":         "End",
                    "offtakerId":  "Offtaker",
                    "trailerType": "Trailer",
                    "targetMassKg":"Target (kg)",
                    "dropStatus":  "Status",
                })
                st.dataframe(detail, use_container_width=True, hide_index=True)


def render_raw_log(messages: List[MqttMessage], n: int = 50):
    recent = list(reversed(messages[-n:]))
    lines  = []
    for m in recent:
        ts  = m.received_at.strftime("%H:%M:%S")
        cat = m.device_category
        col = color_for(cat)
        preview = m.raw_payload[:130] + ("…" if len(m.raw_payload) > 130 else "")
        lines.append(
            f'<div class="log-row">'
            f'<span style="color:#484f58;">[{ts}]</span> '
            f'<span class="log-topic">{m.topic}</span> '
            f'<span style="color:{col};">[{cat}]</span> '
            f'<span>{preview}</span>'
            f'</div>'
        )
    st.markdown("\n".join(lines), unsafe_allow_html=True)


# ── NCC Forecast tab ──────────────────────────────────────────────────────────

def render_ncc_tab(api_df: pd.DataFrame, api_fetched_range: str):
    """
    Reproduce the HERA Net Commodity Costs table from API profiles.

    Reads TruckFilling (g/s), VoluntaryInjection (g/s), and
    MsolCoveragePenalty (kW) profiles and applies the electrolyzer
    efficiency U-curve to compute per-offtaker and total NCC metrics.
    """

    # ── Price & parameter inputs ─────────────────────────────────────────────
    st.markdown(
        '<p style="color:#57606a;margin-top:0;">'
        'Reproduces the HERA NCC table — enter the commodity prices used in HERA '
        'to verify alignment with the efficiency curve implementation.</p>',
        unsafe_allow_html=True,
    )

    inp_c1, inp_c2, inp_c3 = st.columns([1, 1, 3])
    with inp_c1:
        price_elec = st.number_input(
            "Electricity price (€/MWh_e)",
            value=100.0, step=5.0, min_value=0.0, format="%.2f",
            key="ncc_price_elec",
            help="Cost of electricity consumed by the electrolyzer",
        )
    with inp_c2:
        price_gas = st.number_input(
            "Gas / H₂ price (€/MWh_H₂)",
            value=30.0, step=5.0, min_value=0.0, format="%.2f",
            key="ncc_price_gas",
            help="Value of hydrogen produced (HHV basis)",
        )

    # ── Efficiency U-curve ───────────────────────────────────────────────────
    _OLD_NCC_DEFAULT_PTS = (
        (18.880, 5.09), (34.460, 4.71), (47.548, 4.58), (60.432, 4.54), (124.860, 4.66),
    )
    if "ncc_curve_pts" not in st.session_state:
        st.session_state.ncc_curve_pts = list(_NCC_DEFAULT_PTS)
    elif tuple(st.session_state.ncc_curve_pts) == _OLD_NCC_DEFAULT_PTS:
        st.session_state.ncc_curve_pts = list(_NCC_DEFAULT_PTS)
    pts = st.session_state.ncc_curve_pts

    with st.expander("🔧 Efficiency U-Curve", expanded=False):
        ec1, ec2 = st.columns([1, 2])

        with ec1:
            st.markdown("**Reference points** (flow g/s → specific consumption kWh/Nm³)")
            lbls = (_NCC_POINT_LABELS + [""] * len(pts))[: len(pts)]
            pts_rows = []
            for i, (flow, cs) in enumerate(pts):
                lbl = lbls[i] if i < len(lbls) and lbls[i] else ""
                pwr = _ncc_flow_to_power(pts, flow)
                pts_rows.append({
                    "":           lbl,
                    "Flow (g/s)": f"{flow:.3f}",
                    "Cs (kWh/Nm³)": f"{cs:.4f}",
                    "Power (MWe)": f"{pwr:.4f}",
                })
            st.dataframe(pd.DataFrame(pts_rows), use_container_width=True, hide_index=True)

            if st.button("↺ Reset to defaults", key="ncc_reset_curve"):
                st.session_state.ncc_curve_pts = list(_NCC_DEFAULT_PTS)
                st.rerun()

        with ec2:
            x_axis_mode = st.radio(
                "X-axis",
                ["H₂ Flow [g/s]", "Electrical Power [MWe]"],
                horizontal=True,
                key="ncc_ucurve_xaxis",
            )
            flow_plot = np.linspace(pts[0][0], pts[-1][0], 300)
            ys_plot = [_ncc_consumption(pts, f) for f in flow_plot]
            knot_flow = [p[0] for p in pts]
            knot_y    = [p[1] for p in pts]
            knot_pwr  = [_ncc_flow_to_power(pts, f) for f in knot_flow]
            knot_lbl  = _NCC_POINT_LABELS[: len(pts)]
            cs_min, cs_max = min(knot_y), max(knot_y)
            cs_pad = max((cs_max - cs_min) * 0.15, 0.05)

            if x_axis_mode == "H₂ Flow [g/s]":
                xs_plot = list(flow_plot)
                knot_x  = knot_flow
                x_title = "H₂ Flow [g/s]"
                x_pad   = max((knot_flow[-1] - knot_flow[0]) * 0.04, 1.0)
                x_range = [knot_flow[0] - x_pad, knot_flow[-1] + x_pad]
                hover_pts = (
                    "<b>%{text}</b><br>"
                    "%{x:.2f} g/s → %{y:.4f} kWh/Nm³<extra></extra>"
                )
            else:
                xs_plot = [_ncc_flow_to_power(pts, f) for f in flow_plot]
                knot_x  = knot_pwr
                x_title = "Electrical Power [MWe]"
                x_pad   = max((knot_pwr[-1] - knot_pwr[0]) * 0.04, 0.1)
                x_range = [knot_pwr[0] - x_pad, knot_pwr[-1] + x_pad]
                hover_pts = (
                    "<b>%{text}</b><br>"
                    "%{x:.4f} MWe → %{y:.4f} kWh/Nm³<extra></extra>"
                )

            fig_uc = go.Figure()
            fig_uc.add_trace(go.Scatter(
                x=xs_plot, y=ys_plot, mode="lines",
                name="U-Curve",
                line=dict(color="#0969da", width=2.5),
                fill="tozeroy",
                fillcolor=_hex_to_rgba("#0969da"),
            ))
            fig_uc.add_trace(go.Scatter(
                x=knot_x, y=knot_y, mode="markers+text",
                text=knot_lbl, textposition="top center",
                marker=dict(color="#d97706", size=9, line=dict(color="white", width=1.5)),
                name="Reference points",
                hovertemplate=hover_pts,
            ))
            fig_uc.update_layout(
                height=260, paper_bgcolor="#ffffff", plot_bgcolor="#f5f7fa",
                font=dict(color="#57606a"),
                xaxis=dict(title=x_title, gridcolor="#d0d7de", range=x_range),
                yaxis=dict(
                    title="Specific Consumption Cs [kWh/Nm³]",
                    gridcolor="#d0d7de",
                    range=[cs_min - cs_pad, cs_max + cs_pad],
                ),
                margin=dict(l=55, r=15, t=10, b=40),
                legend=dict(bgcolor="#ffffff", bordercolor="#d0d7de", borderwidth=1,
                            font=dict(size=10)),
                showlegend=True,
            )
            st.plotly_chart(
                fig_uc, use_container_width=True,
                key=f"ncc_ucurve_plot_{x_axis_mode.replace(' ', '_')}",
            )

    st.markdown("---")

    # ── Guard: need profiles ─────────────────────────────────────────────────
    if api_df.empty:
        st.info(
            "No API profiles loaded yet.  "
            "Use **🟠 Update Profiles** in the bar above to fetch the gas-day profiles.",
        )
        return

    # ── Compute metrics ──────────────────────────────────────────────────────
    metrics, offtaker_ids = _compute_ncc_metrics(api_df, pts, price_elec, price_gas)

    if not metrics:
        st.warning(
            "No NCC-relevant profiles found in the fetched data "
            "(expected: TruckFilling, MsolCoveragePenalty, VoluntaryInjection)."
        )
        return

    if api_fetched_range:
        st.caption(f"Calculated from API profiles · {api_fetched_range}")

    # Ordered list: individual offtakers first, Total Planning last
    offtaker_shorts = [_offtaker_short(oid) for oid in offtaker_ids]
    col_keys   = offtaker_shorts + (["Total Planning"] if "Total Planning" in metrics else [])
    col_labels = [_offtaker_col(k) for k in offtaker_shorts] + (["Total Planning"] if "Total Planning" in metrics else [])

    def _fmt_mwh_e(v: float) -> str:
        return f"{v:,.2f} MWh_e"

    def _fmt_eur(v: float) -> str:
        return f"€ {v:,.2f}"

    def _fmt_energy_triplet(m: dict, e_key: str, h2_key: str, kg_key: str) -> str:
        """ELEC_MWh / H2_MWh / H2_kg on one line."""
        return (
            f"{m[e_key]:,.2f} / {m[h2_key]:,.2f} / {m[kg_key]:,.2f}"
        )

    # ── NCC metrics table (native Streamlit dataframe — HTML tables break in markdown)
    # Each entry: (row label, value builder, sep_after, highlight_net)
    # value builder: metrics dict → formatted cell string
    TABLE_ROWS: list[tuple] = [
        ("Electricity Consumed", lambda m: _fmt_mwh_e(m["elec_total"]), False, False),
        (
            "Truck Filling (ELEC_MWh / H2_MWh / H2_kg)",
            lambda m: _fmt_energy_triplet(m, "tf_e", "tf_h2", "tf_kg"),
            False, False,
        ),
        (
            "Gas (Voluntary) (ELEC_MWh / H2_MWh / H2_kg)",
            lambda m: _fmt_energy_triplet(m, "vol_e", "vol_h2", "vol_kg"),
            False, False,
        ),
        (
            "Gas (MSOL) (ELEC_MWh / H2_MWh / H2_kg)",
            lambda m: _fmt_energy_triplet(m, "msol_e", "msol_h2_mwh", "msol_kg"),
            True, False,
        ),
        ("Electricity Costs For Truck Filling", lambda m: _fmt_eur(m["cost_tf_e"]), False, False),
        ("Electricity Costs for Voluntary Gas", lambda m: _fmt_eur(m["cost_vol_e"]), False, False),
        ("Voluntary Gas Revenue", lambda m: _fmt_eur(m["rev_vol_h2"]), False, False),
        ("MSOL Coverage", lambda m: _fmt_eur(m["msol_cov"]), True, False),
        ("Net Costs", lambda m: _fmt_eur(m["net_cost"]), False, True),
    ]

    table_rows: list[dict] = []
    for row_name, value_fn, _sep_after, _highlight in TABLE_ROWS:
        row: dict = {"Quantities": row_name}
        for k, lbl in zip(col_keys, col_labels):
            row[lbl] = value_fn(metrics[k]) if k in metrics else "—"
        table_rows.append(row)

    df_table = pd.DataFrame(table_rows)

    row_styles = pd.DataFrame("", index=df_table.index, columns=df_table.columns)
    for i, (_row_name, _value_fn, sep_after, highlight_net) in enumerate(TABLE_ROWS):
        if sep_after:
            row_styles.iloc[i] = "border-bottom: 2px solid #d0d7de"
        if highlight_net:
            row_styles.iloc[i] = (
                "font-weight: 700; background-color: #f0faf5; "
                "border-top: 2px solid #0a5c47"
            )

    st.dataframe(
        df_table.style.apply(lambda _: row_styles, axis=None),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("")

    # ── NCC Forecast cards ───────────────────────────────────────────────────
    CARD_COLORS = ["#0a5c47", "#0969da", "#6f42c1", "#d97706"]

    card_cols = st.columns(len(col_keys))
    for ci, (k, lbl) in enumerate(zip(col_keys, col_labels)):
        if k not in metrics:
            continue
        ncc_val  = metrics[k]["ncc_kg"]
        net_val  = metrics[k]["net_cost"]
        h2_kg    = metrics[k]["h2_kg"]
        bdr      = CARD_COLORS[ci % len(CARD_COLORS)]
        ncc_sign = "+" if ncc_val >= 0 else "−"
        ncc_abs  = abs(ncc_val)
        with card_cols[ci]:
            st.markdown(
                f'<div style="border:2px solid {bdr};border-radius:10px;'
                f'padding:14px 18px;text-align:center;background:#fff;">'
                f'<div style="font-size:.78rem;font-weight:600;color:#57606a;'
                f'letter-spacing:.04em;">NCC Forecast</div>'
                f'<div style="font-size:1.6rem;font-weight:700;color:{bdr};'
                f'margin:4px 0;">{ncc_abs:.2f} €/kg</div>'
                f'<div style="font-size:.75rem;color:#57606a;">{lbl}</div>'
                f'<div style="font-size:.72rem;color:#8c959f;margin-top:4px;">'
                f'Net {_fmt_eur(net_val)} · {h2_kg:,.0f} kg H₂'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    # ── Time-series charts (collapsed by default)
    with st.expander("📈 Profile Time-Series", expanded=False):
        CHART_PALETTE = ["#0969da", "#cf222e", "#1a7f37", "#8250df", "#bf3989", "#d97706"]

        # Chart 1 – API Electricity profiles [MW]  (matches Total Plant Power view)
        fig_e = go.Figure()
        trace_i = 0
        for ptype in ["TruckFilling", "VoluntaryInjection", "MsolCoveragePenalty"]:
            type_df = api_df[
                (api_df["type"] == ptype)
                & (api_df["energyType"] == "Electricity")
                & (api_df["unit"] == "kW")
            ]
            if type_df.empty:
                continue
            for oid in sorted(type_df["offtakerCompanyId"].unique()):
                grp = _sum_offtaker_timeseries(type_df, oid)
                short = _offtaker_short(oid)
                y_vals = grp["value"] / 1000          # kW → MW

                trace_color = CHART_PALETTE[trace_i % len(CHART_PALETTE)]
                fig_e.add_trace(go.Scatter(
                    x=grp["time"], y=y_vals,
                    mode="lines", name=f"{ptype} · {short}",
                    line=dict(color=trace_color, width=2),
                    fill="tozeroy",
                    fillcolor=_hex_to_rgba(trace_color),
                    hovertemplate=(
                        f"<b>{ptype} · {short}</b><br>"
                        "Time: %{x|%d/%m %H:%M}<br>"
                        "Power: %{y:.3f} MW<extra></extra>"
                    ),
                ))
                trace_i += 1

        fig_e.update_layout(
            title=dict(text="Electrical Power [MW]",
                       font=dict(size=14, color="#1c2128")),
            paper_bgcolor="#ffffff", plot_bgcolor="#f5f7fa",
            font=dict(color="#57606a"),
            xaxis=dict(gridcolor="#d0d7de", tickformat="%d/%m %H:%M",
                       title="Time (Brussels)", linecolor="#d0d7de"),
            yaxis=dict(gridcolor="#d0d7de", title="MW", linecolor="#d0d7de"),
            legend=dict(bgcolor="#ffffff", bordercolor="#d0d7de", borderwidth=1,
                        font=dict(size=11)),
            margin=dict(l=60, r=20, t=50, b=40), height=380,
        )
        st.plotly_chart(fig_e, use_container_width=True, key="ncc_chart_elec")

        # Chart 2 – H₂ flow [g/s]  (Hydrogen profiles from API for all three types)
        fig_h2 = go.Figure()
        trace_i = 0
        h2_series: list[tuple[str, str]] = [
            ("TruckFilling",        "TruckFilling"),
            ("VoluntaryInjection",  "Voluntary Gas"),
            ("MsolCoveragePenalty", "MSOL Coverage"),
        ]
        for ptype, label in h2_series:
            type_df = _ncc_filter_profiles(api_df, ptype, "Hydrogen", "g/s")
            if type_df.empty:
                continue
            for oid in sorted(type_df["offtakerCompanyId"].unique()):
                grp = _sum_offtaker_timeseries(type_df, oid)
                short = _offtaker_short(oid)
                trace_color = CHART_PALETTE[trace_i % len(CHART_PALETTE)]
                fig_h2.add_trace(go.Scatter(
                    x=grp["time"], y=grp["value"],
                    mode="lines", name=f"{label} · {short}",
                    line=dict(color=trace_color, width=2),
                    fill="tozeroy",
                    fillcolor=_hex_to_rgba(trace_color),
                    hovertemplate=(
                        f"<b>{label} · {short}</b><br>"
                        "Time: %{x|%d/%m %H:%M}<br>"
                        "Flow: %{y:.3f} g/s<extra></extra>"
                    ),
                ))
                trace_i += 1

        if trace_i > 0:
            fig_h2.update_layout(
                title=dict(text="H₂ Flow Rate [g/s]",
                           font=dict(size=14, color="#1c2128")),
                paper_bgcolor="#ffffff", plot_bgcolor="#f5f7fa",
                font=dict(color="#57606a"),
                xaxis=dict(gridcolor="#d0d7de", tickformat="%d/%m %H:%M",
                           title="Time (Brussels)", linecolor="#d0d7de"),
                yaxis=dict(gridcolor="#d0d7de", title="g/s", linecolor="#d0d7de"),
                legend=dict(bgcolor="#ffffff", bordercolor="#d0d7de", borderwidth=1,
                            font=dict(size=11)),
                margin=dict(l=60, r=20, t=50, b=40), height=340,
            )
            st.plotly_chart(fig_h2, use_container_width=True, key="ncc_chart_h2")


# ── Display settings (no sidebar — fixed sensible defaults, exposed inline) ───
refresh_rate = 3     # seconds between auto-refresh
max_points   = 200   # max data-points per chart series
show_raw     = True  # always show raw log

stats = broker_state.stats_snapshot()

# ── Header ────────────────────────────────────────────────────────────────────
connected, connect_error = broker_state.is_connected()
if connected:
    _conn_pill = '<span class="pill pill-green">● LIVE</span>'
elif connect_error:
    _conn_pill = f'<span class="pill pill-red">● DISCONNECTED</span>'
else:
    _conn_pill = '<span class="pill pill-yellow">● CONNECTING…</span>'

_env_pill_color = ENV_PILL_COLORS.get(get_env(), "#57606a")
_env_label = ENVIRONMENTS[get_env()]["label"]

_hdr_left, _hdr_right = st.columns([5, 1])
with _hdr_left:
    st.markdown(
        f'<h1 style="color:#1c2128;margin-bottom:0;">'
        f'⚡ HERA — Plant Communication '
        f'<span style="font-size:.6em;font-weight:400;color:#57606a;">DEMO</span>'
        f'&nbsp;&nbsp;{_conn_pill}</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="color:#57606a;margin-top:2px;">'
        'Live command profiles in <b>Europe/Brussels</b> — '
        '<span style="color:#b45309;font-weight:600;">━━ HERA Profiles</span> &nbsp;'
        '<span style="color:#0969da;font-weight:600;">── Plant-received Profiles</span></p>',
        unsafe_allow_html=True,
    )
with _hdr_right:
    _env_opts = list(ENV_CHOICES)
    _env_index = (
        _env_opts.index(st.session_state.hera_env)
        if st.session_state.hera_env in _env_opts
        else _env_opts.index(DEFAULT_ENV)
    )
    _env_choice = st.selectbox(
        "Environment",
        _env_opts,
        index=_env_index,
        key="hera_env_select",
        help="Switch API endpoints and MQTT broker (TEST and TEST_BIS share the same APIs).",
    )
    st.markdown(
        f'<div style="text-align:right;margin-top:-8px;">'
        f'<span class="pill" style="background:{_env_pill_color}22;color:{_env_pill_color};">'
        f'{_env_label}</span></div>',
        unsafe_allow_html=True,
    )

if _env_choice != st.session_state.hera_env:
    st.session_state.hera_env = _env_choice
    set_env(_env_choice)
    broker_state.messages.clear()
    broker_state.stats.clear()
    st.session_state.api_df            = pd.DataFrame()
    st.session_state.api_error         = ""
    st.session_state.api_fetched_range = ""
    st.session_state.bay_df            = pd.DataFrame()
    st.session_state.bay_error         = ""
    st.session_state.bay_fetched_range = ""
    for _k in ("_qa_done_profiles", "_qa_done_bay", "_qa_done_disp_power", "_qa_done_disp_bay"):
        st.session_state[_k] = ""
    reconnect_mqtt(broker_state)
    st.toast(f"Switched to {_env_label} environment", icon="🔀")
    st.rerun()

_cfg = get_config()
st.caption(
    f"API: `{_cfg['plant_base']}` · MQTT: `{_cfg['mqtt_host']}`"
)

# ── Quick Actions bar ─────────────────────────────────────────────────────────
with st.container(border=True):
    _today = datetime.now(tz=BRUSSELS).date()
    _qa_cols = st.columns([2, 2, 2, 1, 2, 2])

    with _qa_cols[0]:
        qa_date = st.date_input(
            "Day",
            value=_today,
            min_value=_today - timedelta(days=14),
            max_value=_today + timedelta(days=2),
            key="qa_date",
            label_visibility="visible",
        )
    qa_date_str = str(qa_date)
    qa_from, qa_till = gas_day_bounds_from_date(qa_date)

    # ── Clear everything when the selected day changes ────────────────────────
    if st.session_state.get("_qa_active_day") != qa_date_str:
        st.session_state["_qa_active_day"] = qa_date_str
        # Reset all "done" flags
        for _k in ("_qa_done_profiles", "_qa_done_bay", "_qa_done_disp_power", "_qa_done_disp_bay"):
            st.session_state[_k] = ""
        # Clear MQTT history and API results
        broker_state.messages.clear()
        broker_state.stats.clear()
        st.session_state.api_df            = pd.DataFrame()
        st.session_state.api_error         = ""
        st.session_state.api_fetched_range = ""
        st.session_state.bay_df            = pd.DataFrame()
        st.session_state.bay_error         = ""
        st.session_state.bay_fetched_range = ""
        st.rerun()

    def _qa_btn(col, label: str, state_key: str) -> bool:
        done = st.session_state[state_key] == qa_date_str
        with col:
            return st.button(
                ("✅  " if done else "🟠  ") + label,
                key=f"qa_btn_{state_key}",
                type="primary" if done else "secondary",
                use_container_width=True,
            )

    # Fetch group
    if _qa_btn(_qa_cols[1], "Update Profiles", "_qa_done_profiles"):
        with st.spinner("Fetching profiles…"):
            _data, _err = fetch_profiles(qa_from, qa_till)
        if _err:
            st.error(_err)
        else:
            st.session_state.api_df            = parse_profiles(_data)
            st.session_state.api_error         = ""
            st.session_state.api_fetched_range = f"{qa_from[:10]} → {qa_till[:10]}"
            st.session_state["_qa_done_profiles"] = qa_date_str
            st.rerun()

    if _qa_btn(_qa_cols[2], "Update Bay Occupancy", "_qa_done_bay"):
        with st.spinner("Fetching bay schedules…"):
            _data_b, _err_b = fetch_bay_schedules(qa_from, qa_till)
        if _err_b:
            st.error(_err_b)
        else:
            st.session_state.bay_df            = parse_bay_schedules(_data_b) if _data_b else pd.DataFrame()
            st.session_state.bay_error         = ""
            st.session_state.bay_fetched_range = f"{qa_from[:10]} → {qa_till[:10]}"
            st.session_state["_qa_done_bay"]   = qa_date_str
            st.rerun()

    # Divider column
    with _qa_cols[3]:
        st.markdown(
            '<div style="border-left:1px solid #d0d7de;height:52px;margin:8px auto;width:1px;"></div>',
            unsafe_allow_html=True,
        )

    # Dispatch group
    if _qa_btn(_qa_cols[4], "Dispatch Power to Plant", "_qa_done_disp_power"):
        _can_truck, _can_rect = dispatch_profile_availability(st.session_state.api_df)
        _disp_errors: list[str] = []
        _disp_ok: list[str] = []

        if not _can_truck and not _can_rect:
            st.error(
                "No dispatchable profiles for this gas day. "
                "Use **Update Profiles** first."
            )
        else:
            if _can_truck:
                with st.spinner("Dispatching truck filling profiles…"):
                    _ok1, _err1 = dispatch_profiles(qa_from, qa_till)
                if _err1:
                    _disp_errors.append(_err1)
                else:
                    _disp_ok.append("truck filling")
            else:
                st.info(
                    "No **TruckFilling** profiles for this gas day — no trucks planned; "
                    "truck dispatch skipped. Rectifier / MSOL dispatch continues if available."
                )

            if _can_rect:
                with st.spinner("Dispatching rectifier power profiles…"):
                    _ok2, _err2 = dispatch_rectifiers_power(qa_from, qa_till)
                if _err2:
                    _disp_errors.append(_err2)
                else:
                    _disp_ok.append("rectifier power")

            if _disp_errors and not _disp_ok:
                st.error("Dispatch error(s): " + " | ".join(_disp_errors))
            elif _disp_errors:
                st.warning(
                    "Partial dispatch: "
                    + ", ".join(_disp_ok)
                    + " succeeded · "
                    + " | ".join(_disp_errors)
                )
                st.session_state["_qa_done_disp_power"] = qa_date_str
                st.rerun()
            elif _disp_ok:
                st.session_state["_qa_done_disp_power"] = qa_date_str
                st.toast(
                    "✅ Dispatched: " + " + ".join(_disp_ok),
                    icon="⚡",
                )
                st.rerun()

    if _qa_btn(_qa_cols[5], "Dispatch Bay Occupancy", "_qa_done_disp_bay"):
        _bay_df = st.session_state.bay_df
        if not dispatch_bay_availability(_bay_df):
            with st.spinner("Checking bay schedule in API…"):
                _data_b, _err_b = fetch_bay_schedules(qa_from, qa_till)
            if _err_b:
                st.error(_err_b)
            else:
                _bay_df = parse_bay_schedules(_data_b) if _data_b else pd.DataFrame()
                st.session_state.bay_df = _bay_df
                st.session_state.bay_fetched_range = f"{qa_from[:10]} → {qa_till[:10]}"

        if not dispatch_bay_availability(_bay_df):
            st.info(
                "No **bay occupation sessions** for this gas day — dispatch skipped."
            )
        else:
            with st.spinner("Dispatching bay schedule…"):
                _ok, _err = dispatch_bay_schedule(qa_from, qa_till)
            if _ok:
                st.session_state["_qa_done_disp_bay"] = qa_date_str
                st.toast("✅ Bay schedule dispatched!", icon="🏭")
                st.rerun()
            else:
                st.error(f"Dispatch failed: {_err}")

# ── Auto-fetch: triggered by ACS database-init messages ──────────────────────
# Trigger topics: db_mappings, db_drivers, db_trailers, db_trucks
# These indicate a new day's data is being prepared → fetch both APIs.
# We do NOT trigger on db_schedules or PLC fill_rate_profile (those are the
# actual scheduled payloads being compared, not a signal to re-fetch).
msgs    = broker_state.snapshot()

_DB_INIT_TOPICS = {"db_mappings", "db_drivers", "db_trailers", "db_trucks"}

db_init_msgs = [
    m for m in msgs
    if m.device_category == "ACS"
    and any(t in m.cmd_suffix.lower() for t in _DB_INIT_TOPICS)
]

if db_init_msgs:
    # Use the timestamp of the latest db-init message to compute gas-day bounds.
    # Convert to Brussels time first so the calendar date is correct locally
    # (e.g. a message at 18:55 UTC = 20:55 Brussels → still June 11 gas day).
    _trigger_ts  = db_init_msgs[-1].received_at
    _auto_from, _auto_till = gas_day_bounds(_trigger_ts.astimezone(BRUSSELS))
    _auto_range  = f"{_auto_from[:10]} → {_auto_till[:10]}"

    # Only auto-fetch if the range hasn't been fetched yet (manual fetch wins).
    # ── Profiles ──────────────────────────────────────────────────────────────
    if not st.session_state.api_fetched_range:
        _data, _err = fetch_profiles(_auto_from, _auto_till)
        if _err:
            st.session_state.api_error = _err
            st.session_state.api_df    = pd.DataFrame()
        else:
            st.session_state.api_df            = parse_profiles(_data)
            st.session_state.api_error         = ""
            st.session_state.api_fetched_range = _auto_range
            st.toast(f"📡 Auto-fetched profiles for {_auto_range}", icon="📡")

    # ── Bay occupation schedule ───────────────────────────────────────────────
    if not st.session_state.bay_fetched_range:
        _data_b, _err_b = fetch_bay_schedules(_auto_from, _auto_till)
        if _err_b:
            st.session_state.bay_error = _err_b
            st.session_state.bay_df    = pd.DataFrame()
        else:
            st.session_state.bay_df            = parse_bay_schedules(_data_b) if _data_b else pd.DataFrame()
            st.session_state.bay_error         = ""
            st.session_state.bay_fetched_range = _auto_range
            st.toast(f"🚛 Auto-fetched bay schedules for {_auto_range}", icon="🚛")

total  = len(msgs)
api_df = st.session_state.api_df
api_ok = not api_df.empty

st.markdown("---")

tab_comm, tab_ncc = st.tabs(["📡 Plant Communication", "📊 NCC Forecast"])

# ── Tab 1 : Plant Communication ───────────────────────────────────────────────
with tab_comm:

    PREFERRED_ORDER = ["Electrolyser", "Rectifier", "Loading Bay", "PLC"]
    seen_categories = list(stats.keys())
    device_families = (
        [c for c in PREFERRED_ORDER if c in seen_categories]
        + sorted(c for c in seen_categories if c not in PREFERRED_ORDER)
    )

    if not device_families:
        st.info("Waiting for messages on `HOF/+/+/CMD/#`…")

    for family in device_families:
        color       = color_for(family)
        family_msgs = [m for m in msgs if m.device_category == family]

        # ── ACS gets its own bespoke render ──────────────────────────────────
        if family == "ACS":
            render_acs_section(family_msgs, st.session_state.bay_df)
            st.markdown("")
            continue

        # Section header for non-ACS families
        st.markdown(
            f'<div class="section-header" style="border-color:{color};">'
            f'{family} &nbsp;<span style="color:#484f58;font-weight:400;font-size:.85rem;">'
            f'({len(family_msgs)} msg{"s" if len(family_msgs)!=1 else ""})'
            f'</span></div>',
            unsafe_allow_html=True,
        )

        df_actual = extract_profile_df(family_msgs)
        devices   = sorted({m.device_id for m in family_msgs})

        cmd_suffixes = {_cmd_label(m.cmd_suffix).lower() for m in family_msgs}
        is_fill_rate_section = (family == "PLC") or any(
            kw in cmd for cmd in cmd_suffixes for kw in FILL_RATE_KEYWORDS
        )

        if not df_actual.empty and family == "PLC":
            di_mask = df_actual["device"].apply(_is_di_device)

            _view_opts = ["Truck Filling (g/s)", "Total Plant Power (kW)"]
            st.session_state["_profile_view"] = st.selectbox(
                "View",
                _view_opts,
                index=_view_opts.index(st.session_state["_profile_view"]),
                key=f"profile_view_sel_{family}",
                label_visibility="collapsed",
            )
            _show_truck = st.session_state["_profile_view"] == "Truck Filling (g/s)"

            subgroups = []
            if _show_truck and di_mask.any():
                subgroups.append((
                    "Truck Filling",
                    df_actual[di_mask],
                    {"TruckFilling"},
                    df_actual[di_mask],
                    "Hydrogen",
                ))
            if not _show_truck:
                subgroups.append((
                    "Total Plant Power",
                    df_actual[~di_mask] if (~di_mask).any() else pd.DataFrame(),
                    SCHEDULED_PROFILE_TYPES,
                    None,
                    "Electricity",
                ))
            if not subgroups:
                st.info("No data yet for this view.")
        else:
            subgroups = [("", df_actual, SCHEDULED_PROFILE_TYPES, df_actual, None)]

        for sub_name, df_sub, api_types_set, mqtt_df_units, sched_etype in subgroups:
            df_scheduled = pd.DataFrame()
            if api_ok and is_fill_rate_section:
                df_scheduled = _build_scheduled_df(
                    api_df, api_types_set,
                    mqtt_df=mqtt_df_units,
                    energy_type=sched_etype,
                    series_label=sub_name or "HERA Profile",
                )

            cmd_names = ", ".join(sorted(df_sub["cmd"].unique())) if not df_sub.empty else ""
            if sub_name:
                sub_color = "#0969da" if "DI" in sub_name else "#cf222e"
                st.markdown(
                    f'<div style="font-size:.95rem;font-weight:600;color:{sub_color};'
                    f'margin:12px 0 4px 0;padding-left:8px;border-left:3px solid {sub_color};">'
                    f'{sub_name}'
                    f'<span style="color:#8c959f;font-weight:400;font-size:.8rem;"> — {cmd_names}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            fig_title = (
                f"{family}"
                + (f" — {sub_name}" if sub_name else "")
                + (f" — {cmd_names}" if cmd_names and not sub_name else "")
            )
            chart_key = f"chart_{family}_{sub_name.replace(' ', '_')}"
            fig = make_profile_figure(df_sub, df_scheduled, family, fig_title, max_points)
            st.plotly_chart(fig, use_container_width=True, key=chart_key)

        st.markdown("")

    # ── API-only full breakdown ───────────────────────────────────────────────
    if api_ok and not api_df.empty:
        api_types = api_df[["type", "unit", "energyType"]].drop_duplicates()
        with st.expander("📡 All HERA Profiles (API)  — full breakdown", expanded=False):
            for _, row in api_types.iterrows():
                ptype, unit, etype = row["type"], row["unit"], row["energyType"]
                grp = api_df[
                    (api_df["type"] == ptype) &
                    (api_df["unit"] == unit) &
                    (api_df["energyType"] == etype)
                ]
                agg = grp.groupby("time")["value"].sum().reset_index()
                if agg["value"].sum() == 0:
                    continue

                fig_api = go.Figure()
                fig_api.add_trace(go.Scatter(
                    x=agg["time"], y=agg["value"],
                    mode="lines",
                    name=f"{ptype} ({unit})",
                    line=dict(color=SCHEDULED_COLOR, width=2, dash="dash"),
                    fill="tozeroy",
                    fillcolor=_hex_to_rgba(SCHEDULED_COLOR),
                    hovertemplate=(
                        f"<b>{ptype}</b><br>"
                        "%{x|%d/%m %H:%M}<br>"
                        f"%{{y:.3f}} {unit}<extra></extra>"
                    ),
                ))
                fig_api.update_layout(
                    title=dict(text=f"{ptype} · {unit} · {etype}",
                               font=dict(size=14, color="#1c2128")),
                    paper_bgcolor="#ffffff", plot_bgcolor="#f5f7fa",
                    font=dict(color="#57606a"),
                    xaxis=dict(gridcolor="#d0d7de", tickformat="%d/%m %H:%M",
                               title="Time (Brussels)", linecolor="#d0d7de"),
                    yaxis=dict(gridcolor="#d0d7de", title=unit, linecolor="#d0d7de"),
                    margin=dict(l=50, r=20, t=45, b=40), height=300,
                )
                st.plotly_chart(fig_api, use_container_width=True,
                                key=f"api_{ptype}_{unit}_{etype}")

    # ── Raw message log ───────────────────────────────────────────────────────
    if show_raw:
        st.markdown("---")
        st.markdown("### 📋 Raw Message Log")
        if msgs:
            render_raw_log(msgs, n=60)
        else:
            st.caption("No messages received yet.")

# ── Tab 2 : NCC Forecast ──────────────────────────────────────────────────────
with tab_ncc:
    render_ncc_tab(api_df, st.session_state.api_fetched_range)

# ── Footer / auto-refresh ─────────────────────────────────────────────────────
st.markdown("---")
col_l, col_r = st.columns([8, 1])
with col_l:
    st.caption(f"Auto-refreshing every {refresh_rate}s · {total} msgs · Brussels timezone")
with col_r:
    if st.button("↺ Now"):
        st.rerun()

time.sleep(refresh_rate)
st.rerun()
