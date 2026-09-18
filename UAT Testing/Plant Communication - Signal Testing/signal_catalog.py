"""
Registry of every Plant -> Hera signal this fake-plant simulator can send.

Two evidence levels, tracked per signal via `evidence`:

  "code"    -- topic, envelope shape AND payload values are confirmed from
              hera-virtual-plant's own source (backend/virtual_plant/*.py,
              app.py) -- see PLANT_SIGNALS_CATALOG.md sections 2-6.
  "db"      -- topic + tag + unit + a real sample value are confirmed
              directly from heraplantdatabasetest/dev's PlantSignals table
              (this signal does NOT exist anywhere in hera-virtual-plant's
              code) -- see PLANT_SIGNALS_CATALOG.md section 7. The envelope
              *shape* ({ts,value,type,unit}) is inferred by analogy with
              every "code"-evidence signal, not independently confirmed from
              a raw wire capture, since PlantSignals stores Hera's own
              already-parsed columns, not the raw bytes it received.
  "db-topic-only" -- topic + tag confirmed present in PlantSignals, but no
              real sample value was captured for it in this pass (query
              either didn't return a row, or wasn't run against every
              environment) -- the example_value below is a reasonable guess,
              not observed data. Flagged explicitly so nobody mistakes it
              for confirmed.

See README.md for how to use this catalog and PLANT_SIGNALS_CATALOG.md
(repo root) for the full narrative writeup this was built from.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SignalSpec:
    key: str                # short id used on the CLI, e.g. "operating_mode"
    topic_template: str     # "HOF/PLC/DI{bay}/TLM/operating_mode/0.8.3" or fixed
    tag: str                # matches PlantSignals.Tag
    category: str           # "TLM" | "EVT"
    unit: str
    value_type: str         # envelope "type" field
    retain: bool
    bay_scoped: bool
    asset: str              # fixed asset code when not bay_scoped (e.g. "OKS", "PWR", "HPC", "ACS")
    description: str
    evidence: str            # "code" | "db" | "db-topic-only"
    structured: bool = False  # True => payload is an object; use the dedicated
                               # builder in steps.py, not send_signal.py's --value
    example_value: Optional[object] = None  # scalar example; None for structured signals

    def topic(self, bay: Optional[int] = None) -> str:
        if self.bay_scoped:
            if bay is None:
                raise ValueError(f"signal {self.key!r} is bay-scoped -- pass bay=1..4")
            return self.topic_template.format(bay=bay)
        return self.topic_template


SIGNALS: dict[str, SignalSpec] = {

    # ── Confirmed from hera-virtual-plant code (PLANT_SIGNALS_CATALOG.md §2-6) ──

    "flow_rate_mass": SignalSpec(
        key="flow_rate_mass",
        topic_template="HOF/PLC/OKS/TLM/flow_rate_mass/0.8.3",
        tag="flow_rate_mass",
        category="TLM",
        # NOTE: hera-virtual-plant's own simulator sends unit="kg/h" here, but
        # every real row observed in heraplantdatabasetest/dev has
        # unit="m3(n)/h" -- using the REAL unit here since the whole point of
        # this folder is to match what the real plant actually sends, not
        # replicate the simulator's (apparently outdated) behaviour.
        unit="m3(n)/h",
        value_type="float",
        retain=True,
        bay_scoped=False,
        asset="OKS",
        description="Gas-network injection flow (normal cubic metres/hour).",
        evidence="db",  # topic/shape from code, unit corrected from real DB data
        example_value=67.5,
    ),
    "oks_stat_blending_line": SignalSpec(
        key="oks_stat_blending_line",
        topic_template="HOF/PLC/OKS/TLM/oks_stat_blending_line/0.8.3",
        tag="oks_stat_blending_line",
        category="TLM",
        unit="none",
        value_type="str",
        retain=True,
        bay_scoped=False,
        asset="OKS",
        description="Blending-line availability flag ('1' available / '0' not). "
                     "Real rows exist in dev; heraplantdatabasetest has none at all "
                     "(feature not exercised there as of 2026-09-17). POSSIBLE UI QUIRK "
                     "(2026-09-17, per screenshot, not yet independently confirmed): Hera's "
                     "own MQTT Stream (PLC) screen shows the literal word 'integer' in the "
                     "Unit column for this tag -- reads like a type name leaking into the "
                     "unit display, not a real unit. Left as unit='none' here.",
        evidence="code",
        example_value="1",
    ),
    "fill_rate_meas": SignalSpec(
        key="fill_rate_meas",
        topic_template="HOF/PLC/DI{bay}/TLM/fill_rate_meas/0.8.3",
        tag="fill_rate_meas",
        category="TLM",
        unit="g/s",
        value_type="float",
        retain=True,
        bay_scoped=True,
        asset="DI",
        description="Actual per-bay dispenser fill rate.",
        evidence="code",
        example_value=42.5,
    ),
    "operating_mode": SignalSpec(
        key="operating_mode",
        topic_template="HOF/PLC/DI{bay}/TLM/operating_mode/0.8.3",
        tag="operating_mode",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Bay state machine, 0-12 (see OPERATING_MODE_LABELS below). "
                     "Real PlantSignals.DataType for this tag is 'Int' even though "
                     "the working simulator declares type=\"str\" on the wire -- Hera "
                     "evidently classifies DataType itself rather than trusting the "
                     "wire 'type' field 1:1, so this is kept as \"str\" (unchanged, "
                     "matches the simulator that is known to work against real Hera).",
        evidence="code",
        example_value="3",
    ),
    "rfid_trailer_details": SignalSpec(
        key="rfid_trailer_details",
        topic_template="HOF/PLC/DI{bay}/TLM/RFID_trailer_details/0.8.3",
        tag="RFID_trailer_details",
        category="TLM",
        unit="none",
        value_type="trailer_object",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Full trailer identity + tank technical spec dropped/picked at a bay. "
                     "Structured -- use steps.build_rfid_trailer_details_payload().",
        evidence="code",
        structured=True,
    ),
    "acs_activity": SignalSpec(
        key="acs_activity",
        topic_template="HOF/ACS/ACS/EVT/activity/0.8.3",
        tag="activity",
        category="EVT",
        unit="none",
        value_type="acs_event",
        retain=False,
        bay_scoped=False,
        asset="ACS",
        description="Truck gate movement, mvt_requested/mvt_completed pair sharing a "
                     "correlation_id. Structured -- use steps.build_acs_event_payload().",
        evidence="code",
        structured=True,
    ),

    # ── Confirmed present in real PlantSignals, NOT in hera-virtual-plant's code
    #    (PLANT_SIGNALS_CATALOG.md §7) ──────────────────────────────────────────

    "connected": SignalSpec(
        key="connected",
        topic_template="HOF/PLC/DI{bay}/TLM/connected/0.8.3",
        tag="connected",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Bay/dispenser connectivity established. Likely feeds the "
                     "Movements.EventName='CONNECTED' rows observed alongside this tag.",
        evidence="db-topic-only",
        example_value="1",
    ),
    "disconnected": SignalSpec(
        key="disconnected",
        topic_template="HOF/PLC/DI{bay}/TLM/disconnected/0.8.3",
        tag="disconnected",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Bay/dispenser connectivity lost. Companion of 'connected'.",
        evidence="db-topic-only",
        example_value="1",
    ),
    "rfid_signal_strength": SignalSpec(
        key="rfid_signal_strength",
        topic_template="HOF/PLC/DI{bay}/TLM/RFID_signal_strength/0.8.3",
        tag="RFID_signal_strength",
        category="TLM",
        # Unit corrected 2026-09-17: Hera's own MQTT Stream (PLC) screen shows
        # "dBm" in the Unit column for this tag -- per the user's screenshot
        # observation of the live Test screen, NOT yet independently
        # re-verified by a live run from this codebase. If a live Phase-3 run
        # (see test_plant_ui_reaction.py) shows something else, trust that.
        unit="dBm",
        value_type="float",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="RFID reader signal strength for the trailer tag at this bay.",
        evidence="db",  # was db-topic-only; upgraded on the UI-displayed unit,
                        # still no example_value observed on the wire.
        example_value=85.0,
    ),
    "rfid_trailer_licence_plate": SignalSpec(
        key="rfid_trailer_licence_plate",
        topic_template="HOF/PLC/DI{bay}/TLM/RFID_trailer_licence_plate/0.8.3",
        tag="RFID_trailer_licence_plate",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Plain license-plate string -- DISTINCT from rfid_trailer_details "
                     "(that one is the full technical object; this is just the plate).",
        evidence="db-topic-only",
        example_value="1TRL285",
    ),
    "rfid_trailer_type": SignalSpec(
        key="rfid_trailer_type",
        topic_template="HOF/PLC/DI{bay}/TLM/RFID_trailer_type/0.8.3",
        tag="RFID_trailer_type",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Trailer/MEGC type string, e.g. matches rfid_trailer_details' "
                     "own megc_type field ('type1' in every sample seen). CONFIRMED "
                     "FRONTEND BUG, but non-fatal (2026-09-17, live, "
                     "test_plant_ui_reaction.py PLANT_UI_04): sending this value to "
                     "the MQTT Stream screen throws 'SyntaxError: Unexpected token, "
                     "\"type1\" is not valid JSON' in the browser console -- BUT the "
                     "value still renders correctly on screen (confirmed: the Raw "
                     "panel shows Payload='type1' fine) -- so the exception is being "
                     "caught somewhere and the raw string used as a fallback, not "
                     "actually breaking the display. Stack trace shows a shared "
                     "r.getValue() formatter (chunk-FO6DEE4N.js) calling JSON.parse() "
                     "unconditionally on the raw MQTT value field, with no upfront "
                     "check for whether it's actually JSON -- works for structured "
                     "signals (RFID_trailer_details, ACS events) but throws-and-falls-"
                     "back on any bare-string value. Real finding (unnecessary "
                     "JSON.parse attempts + noisy console.error on a path clearly "
                     "meant to also handle plain strings), just not a data-loss bug. "
                     "Likely affects every other bare-string signal in this catalog "
                     "too (connected/disconnected/filling/fill_requested/"
                     "main_setpoint_active/system_in_ESD/pwr_online/"
                     "rfid_trailer_licence_plate/rfid_system_failure/"
                     "oks_stat_blending_line) -- not independently re-tested per "
                     "signal, but same value_type='str' shape, so same code path is "
                     "the reasonable expectation until proven otherwise.",
        evidence="db-topic-only",
        example_value="type1",
    ),
    "rfid_system_failure": SignalSpec(
        key="rfid_system_failure",
        topic_template="HOF/PLC/DI{bay}/TLM/RFID_system_failure/0.8.3",
        tag="RFID_system_failure",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="RFID reader failure flag. Seen only once, in dev, for DI1 -- "
                     "genuinely rare/edge signal.",
        evidence="db-topic-only",
        example_value="1",
    ),
    "filling": SignalSpec(
        key="filling",
        topic_template="HOF/PLC/DI{bay}/TLM/filling/0.8.3",
        tag="filling",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Filling-in-progress status flag, distinct from the fill_rate_meas "
                     "measurement itself and from operating_mode's numeric state.",
        evidence="db-topic-only",
        example_value="1",
    ),
    "fill_requested": SignalSpec(
        key="fill_requested",
        topic_template="HOF/PLC/DI{bay}/TLM/fill_requested/0.8.3",
        tag="fill_requested",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="A fill has been requested at this bay (precedes 'filling').",
        evidence="db-topic-only",
        example_value="1",
    ),
    "fill_rate_trailer_max": SignalSpec(
        key="fill_rate_trailer_max",
        topic_template="HOF/PLC/DI{bay}/TLM/fill_rate_trailer_max/0.8.3",
        tag="fill_rate_trailer_max",
        category="TLM",
        unit="g/s",
        value_type="float",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Max fill rate the currently-docked trailer can accept.",
        evidence="db-topic-only",
        example_value=130.0,
    ),
    "fill_rate_calc_max": SignalSpec(
        key="fill_rate_calc_max",
        topic_template="HOF/PLC/DI{bay}/TLM/fill_rate_calc_max/0.8.3",
        tag="fill_rate_calc_max",
        category="TLM",
        unit="g/s",
        value_type="float",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Plant-calculated max fill rate for this bay right now "
                     "(distinct from the trailer's own ceiling above).",
        evidence="db-topic-only",
        example_value=110.0,
    ),
    "main_setpoint_active": SignalSpec(
        key="main_setpoint_active",
        topic_template="HOF/PLC/DI{bay}/TLM/main_setpoint_active/0.8.3",
        tag="main_setpoint_active",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Flag: this bay is currently following HERA's dispatched setpoint. "
                     "POSSIBLE UI QUIRK (2026-09-17, per screenshot, not yet independently "
                     "confirmed): Hera's own MQTT Stream (PLC) screen shows a literal '0' "
                     "in the Unit column for this tag -- not a real physical unit. Left as "
                     "unit='none' here rather than adopting '0'; see test_plant_ui_reaction.py's "
                     "unit-cross-check scenario to confirm/re-check live.",
        evidence="db-topic-only",
        example_value="1",
    ),
    "system_in_esd": SignalSpec(
        key="system_in_esd",
        topic_template="HOF/PLC/DI{bay}/TLM/system_in_ESD/0.8.3",
        tag="system_in_ESD",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=True,
        asset="DI",
        description="Emergency Shutdown status for this bay. Safety-relevant --  "
                     "worth a dedicated negative-path test once ordering is worked out. "
                     "Same possible Unit-column display quirk noted on main_setpoint_active "
                     "applies here too (UI reportedly shows '0') -- unconfirmed live.",
        evidence="db-topic-only",
        example_value="0",
    ),
    "pwr_online": SignalSpec(
        key="pwr_online",
        topic_template="HOF/PLC/PWR/TLM/online/0.8.3",
        tag="online",
        category="TLM",
        unit="none",
        value_type="str",
        retain=False,
        bay_scoped=False,
        asset="PWR",
        description="A whole asset ('PWR', presumably the power/rectifier subsystem) "
                     "that hera-virtual-plant does not model at all -- not DI/OKS/ELY. "
                     "Same possible Unit-column display quirk noted on main_setpoint_active "
                     "applies here too (UI reportedly shows '0') -- unconfirmed live.",
        evidence="db-topic-only",
        example_value="1",
    ),
    "hpc_pressure_max": SignalSpec(
        key="hpc_pressure_max",
        topic_template="HOF/PLC/HPC/TLM/pressure_max/0.8.3",
        tag="pressure_max",
        category="TLM",
        unit="none",   # unit not captured -- likely bar; not confirmed
        value_type="float",
        retain=False,
        bay_scoped=False,
        asset="HPC",
        description="High-Pressure Compressor max pressure -- another asset the "
                     "simulator has no concept of. Seen in dev only.",
        evidence="db-topic-only",
        example_value=380.0,
    ),
}


# 0-12 bay state machine, confirmed from hera-virtual-plant's own
# _OPERATING_MODE_LABELS (app.py) -- kept here as the reference for *which
# numeric values* are valid and their hera-virtual-plant label.
OPERATING_MODE_LABELS: dict[int, str] = {
    0: "Reset / Pickup closed",
    1: "Drop-off start",
    2: "Vehicle approach",
    3: "Drop-off closed",
    4: "Ready",
    5: "Authorized",
    6: "Filling",
    8: "Fill done",
    9: "Wait disconnect",
    10: "Wait pickup",
    11: "Pickup created",
    12: "Vehicle leaving",
}

# Real Hera's Movements.EventName for each operating_mode value -- CONFIRMED
# live, 2026-09-17, by playing the full 0-12 lifecycle against
# heraplantdatabasetest (see this folder's test_signal_reaction.py --
# SIG_REACT_02's operating_mode deep-dive) and reading back every
# resulting Movements row for that bay in order. Distinct from
# OPERATING_MODE_LABELS above -- this is Hera's own internal naming, not
# hera-virtual-plant's.
OPERATING_MODE_EVENT_NAME: dict[int, str] = {
    0: "IDLE",
    1: "WAIT_DROP",
    2: "VEH_APPROACH",
    3: "CONNECTED",
    4: "READY",
    5: "AUTHORIZED",
    6: "FILLING",
    8: "FILL_DONE",
    9: "WAIT_DISCON",
    10: "WAIT_PICK",
    11: "DISCONNECTED",
    12: "VEH_LEAVING",
}


def all_keys() -> list[str]:
    return sorted(SIGNALS.keys())


def get(key: str) -> SignalSpec:
    try:
        return SIGNALS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown signal {key!r}. Known keys: {', '.join(all_keys())}") from exc
