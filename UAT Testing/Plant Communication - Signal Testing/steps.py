"""
Reusable, ordered signal sequences for a truck visit -- the confirmed part of
the lifecycle (topic + envelope + ORDER all confirmed from hera-virtual-plant's
own working code, app.py's Truck Scenario tab / truck_sim.py, see
PLANT_SIGNALS_CATALOG.md sections 2 & 4b).

Each function returns a list of Step(topic, payload, note) tuples in the
order they should be sent. Ordering/placement of the newer, DB-only signals
(connected/disconnected/RFID_signal_strength/filling/fill_requested/...,
see signal_catalog.py) is NOT yet determined -- send those standalone via
send_signal.py until real ordering is confirmed (e.g. from a timestamp-sorted
PlantSignals capture of one real visit). Deliberately not guessed here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from envelope import build_envelope
from signal_catalog import get as get_signal

_TRAILER_TECH_DEFAULTS: dict = {
    "megc_type": "type1",
    "vol": 50.4,
    "nominal_press": 380.4,
    "minimal_initial_press": 0.5,
    "number_of_sections": 7,
    "number_of_filling_flex": 2,
    "number_of_main_valves": 3,
    "global_maximum_flow_ac": 34.4,
    "maximum_flow_acceptable": 23.4,
    "maximum_delta_of_press": 23.4,
}

RECOGNIZED_LICENSE_PLATES = (
    "1TRL167", "1TRL285", "1TRL378", "1TRL441", "1TRL639", "1TRL696", "1TRL968",
)


@dataclass
class Step:
    topic: str
    payload: str
    note: str
    retain: bool = False


def _bay_id(bay: int) -> str:
    return f"DI{bay}"


# ── structured payload builders (registered as `structured=True` in the catalog) ──

def build_rfid_trailer_details_payload(license_plate: str, trailer_num: int = 1, **tech_overrides) -> str:
    obj = {
        "trailer_id": str(trailer_num),
        "license_plate": license_plate,
        **_TRAILER_TECH_DEFAULTS,
        **tech_overrides,
    }
    return build_envelope(obj, value_type="trailer_object", unit="none")


def build_acs_event_payload(event: str, data: dict, correlation_id: Optional[str] = None) -> tuple[str, str]:
    """Returns (payload, correlation_id) -- pass the same correlation_id to the
    matching mvt_requested/mvt_completed pair."""
    corr = correlation_id or str(uuid.uuid4())
    payload = build_envelope(
        {"event": event, "data": data, "correlation_id": corr},
        value_type="acs_event",
        unit="none",
    )
    return payload, corr


def build_operating_mode_payload(mode: int) -> str:
    return build_envelope(str(mode), value_type="str", unit="none")


def build_fill_rate_meas_payload(value_gs: float) -> str:
    return build_envelope(float(value_gs), value_type="float", unit="g/s")


# ── ordered visit steps (confirmed sequence + payload shape) ──────────────────

def entry(truck_id: str, driver_id: str, trailer_id: str, drop_bay: int, pick_bay: int = 0) -> list[Step]:
    """ACS entry pair (mvt_requested -> mvt_completed), gate=ENT."""
    acs = get_signal("acs_activity")
    req_payload, corr = build_acs_event_payload("mvt_requested", {"gate": "ENT"})
    steps = [Step(acs.topic(), req_payload, "ACS entry requested")]
    completed_data = {
        "gate": "ENT",
        "driver_id": driver_id,
        "truck_id": truck_id,
        "trailer_id": trailer_id,
        "pick_bay_id": _bay_id(pick_bay) if pick_bay else "",
        "drop_bay_id": _bay_id(drop_bay),
    }
    done_payload, _ = build_acs_event_payload("mvt_completed", completed_data, correlation_id=corr)
    steps.append(Step(acs.topic(), done_payload, "ACS entry completed"))
    return steps


def dropoff(bay: int, license_plate: str, trailer_num: int = 1) -> list[Step]:
    """Modes 1 -> 2 -> 3, then RFID_trailer_details."""
    mode_sig = get_signal("operating_mode")
    rfid_sig = get_signal("rfid_trailer_details")
    steps = [
        Step(mode_sig.topic(bay), build_operating_mode_payload(1), f"DI{bay} operating_mode=1 (Drop-off start)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(2), f"DI{bay} operating_mode=2 (Vehicle approach)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(3), f"DI{bay} operating_mode=3 (Drop-off closed)"),
        Step(rfid_sig.topic(bay), build_rfid_trailer_details_payload(license_plate, trailer_num),
             f"DI{bay} RFID trailer details [{license_plate}]"),
    ]
    return steps


def filling(bay: int, flowmeter_values_gs: list[float]) -> list[Step]:
    """Modes 4 -> 5 -> 6, N x fill_rate_meas readings, then 8 -> 9 -> 10."""
    mode_sig = get_signal("operating_mode")
    fill_sig = get_signal("fill_rate_meas")
    steps = [
        Step(mode_sig.topic(bay), build_operating_mode_payload(4), f"DI{bay} operating_mode=4 (Ready)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(5), f"DI{bay} operating_mode=5 (Authorized)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(6), f"DI{bay} operating_mode=6 (Filling)"),
    ]
    for v in flowmeter_values_gs:
        steps.append(Step(fill_sig.topic(bay), build_fill_rate_meas_payload(v),
                           f"DI{bay} fill_rate_meas={v} g/s", retain=fill_sig.retain))
    steps += [
        Step(mode_sig.topic(bay), build_operating_mode_payload(8), f"DI{bay} operating_mode=8 (Fill done)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(9), f"DI{bay} operating_mode=9 (Wait disconnect)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(10), f"DI{bay} operating_mode=10 (Wait pickup)"),
    ]
    return steps


def pickup(bay: int, license_plate: str, trailer_num: int = 2) -> list[Step]:
    """Mode 11 -> RFID_trailer_details -> Mode 12 -> Mode 0."""
    mode_sig = get_signal("operating_mode")
    rfid_sig = get_signal("rfid_trailer_details")
    return [
        Step(mode_sig.topic(bay), build_operating_mode_payload(11), f"DI{bay} operating_mode=11 (Pickup created)"),
        Step(rfid_sig.topic(bay), build_rfid_trailer_details_payload(license_plate, trailer_num),
             f"DI{bay} RFID trailer details [{license_plate}]"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(12), f"DI{bay} operating_mode=12 (Vehicle leaving)"),
        Step(mode_sig.topic(bay), build_operating_mode_payload(0), f"DI{bay} operating_mode=0 (Reset / Pickup closed)"),
    ]


def exit_(truck_id: str, driver_id: str, drop_bay: int, pick_bay: int = 0, pick_trailer_id: str = "") -> list[Step]:
    """ACS exit pair (mvt_requested -> mvt_completed), gate=EXT."""
    acs = get_signal("acs_activity")
    req_payload, corr = build_acs_event_payload("mvt_requested", {"gate": "EXT"})
    steps = [Step(acs.topic(), req_payload, "ACS exit requested")]
    completed_data = {
        "gate": "EXT",
        "driver_id": driver_id,
        "truck_id": truck_id,
        "trailer_id": pick_trailer_id or "",
        "pick_bay_id": _bay_id(pick_bay) if pick_bay else "",
        "drop_bay_id": _bay_id(drop_bay) if drop_bay else "",
    }
    done_payload, _ = build_acs_event_payload("mvt_completed", completed_data, correlation_id=corr)
    steps.append(Step(acs.topic(), done_payload, "ACS exit completed"))
    return steps


# ── full scenarios (composed from the building blocks above) ─────────────────

def scenario_drop_only(
    *, drop_bay: int, license_plate: str = "1TRL285",
    truck_id: str = "ACS-TRK-001", driver_id: str = "ACS-DRV-001",
) -> list[Step]:
    return (
        entry(truck_id, driver_id, license_plate, drop_bay)
        + dropoff(drop_bay, license_plate)
        + exit_(truck_id, driver_id, drop_bay)
    )


def scenario_drop_and_pick(
    *, drop_bay: int, pick_bay: int,
    drop_plate: str = "1TRL285", pick_plate: str = "1TRL378",
    truck_id: str = "ACS-TRK-001", driver_id: str = "ACS-DRV-001",
) -> list[Step]:
    return (
        entry(truck_id, driver_id, drop_plate, drop_bay, pick_bay=pick_bay)
        + dropoff(drop_bay, drop_plate)
        + pickup(pick_bay, pick_plate)
        + exit_(truck_id, driver_id, drop_bay, pick_bay=pick_bay, pick_trailer_id=pick_plate)
    )


def scenario_full_visit(
    *, bay: int, drop_plate: str = "1TRL285", pick_plate: str = "1TRL285",
    flowmeter_values_gs: Optional[list[float]] = None,
    truck_id: str = "ACS-TRK-001", driver_id: str = "ACS-DRV-001",
) -> list[Step]:
    """One truck: drop at `bay`, the bay fills, the SAME truck's trailer is
    picked back up from the same bay, then it exits. (For the "different
    truck picks up later" pattern, compose entry/dropoff/filling/pickup/exit
    yourself -- see hera-virtual-plant's app.py 'full_path' scenario for the
    two-truck reference.)"""
    flow = flowmeter_values_gs or [42.5, 43.8, 41.2]
    return (
        entry(truck_id, driver_id, drop_plate, bay)
        + dropoff(bay, drop_plate)
        + filling(bay, flow)
        + pickup(bay, pick_plate)
        + exit_(truck_id, driver_id, bay, pick_bay=bay, pick_trailer_id=pick_plate)
    )
