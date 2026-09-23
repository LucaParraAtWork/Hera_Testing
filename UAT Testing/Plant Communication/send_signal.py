"""
Send ONE plant signal to the broker, as if we were the plant. This is the
basic building block -- run_scenario.py composes many of these in sequence
for a full truck visit.

Examples:
    py send_signal.py operating_mode --bay 1 --value 3
    py send_signal.py fill_rate_meas --bay 2 --value 55.0
    py send_signal.py flow_rate_mass --value 68.2
    py send_signal.py pwr_online --value 1
    py send_signal.py rfid_trailer_details --bay 1 --license-plate 1TRL285
    py send_signal.py acs_activity --event mvt_requested --gate ENT
    py send_signal.py acs_activity --event mvt_completed --gate ENT \\
        --truck-id ACS-TRK-001 --driver-id ACS-DRV-001 --trailer-id 1TRL285 --drop-bay 1

List every available signal key with: py list_signals.py
"""
from __future__ import annotations

import argparse
import sys

from broker_client import PlantBrokerClient
from config import ENV_CHOICES, DEFAULT_ENV
from envelope import build_envelope
from signal_catalog import SIGNALS, get as get_signal
from steps import build_acs_event_payload, build_rfid_trailer_details_payload


def _send_scalar(client: PlantBrokerClient, key: str, bay: int | None, value: str, env_unit: str | None) -> None:
    spec = get_signal(key)
    topic = spec.topic(bay)
    unit = env_unit if env_unit is not None else spec.unit
    payload = build_envelope(value, value_type=spec.value_type, unit=unit)
    ok = client.publish(topic, payload, retain=spec.retain)
    _report(ok, client, topic, payload)


def _send_rfid(client: PlantBrokerClient, bay: int, license_plate: str, trailer_num: int) -> None:
    spec = get_signal("rfid_trailer_details")
    topic = spec.topic(bay)
    payload = build_rfid_trailer_details_payload(license_plate, trailer_num)
    ok = client.publish(topic, payload, retain=spec.retain)
    _report(ok, client, topic, payload)


def _send_acs(
    client: PlantBrokerClient, event: str, gate: str,
    truck_id: str, driver_id: str, trailer_id: str, drop_bay: int, pick_bay: int,
    correlation_id: str | None,
) -> None:
    spec = get_signal("acs_activity")
    if event == "mvt_requested":
        data = {"gate": gate}
    else:
        data = {
            "gate": gate,
            "driver_id": driver_id,
            "truck_id": truck_id,
            "trailer_id": trailer_id,
            "pick_bay_id": f"DI{pick_bay}" if pick_bay else "",
            "drop_bay_id": f"DI{drop_bay}" if drop_bay else "",
        }
    payload, corr = build_acs_event_payload(event, data, correlation_id=correlation_id)
    ok = client.publish(spec.topic(), payload, retain=spec.retain)
    _report(ok, client, spec.topic(), payload)
    print(f"correlation_id: {corr}")


def _report(ok: bool, client: PlantBrokerClient, topic: str, payload: str) -> None:
    if ok:
        print(f"OK  {topic}\n    {payload}")
    else:
        print(f"FAILED  {topic}: {client.last_error}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("signal", choices=sorted(SIGNALS.keys()), help="signal key -- see list_signals.py")
    ap.add_argument("--env", choices=ENV_CHOICES, default=DEFAULT_ENV)
    ap.add_argument("--bay", type=int, choices=[1, 2, 3, 4], help="required for bay-scoped signals")
    ap.add_argument("--value", help="scalar value to send (ignored for structured signals)")
    ap.add_argument("--unit", help="override the catalog's default unit for this send")

    # rfid_trailer_details-specific
    ap.add_argument("--license-plate", default="1TRL285")
    ap.add_argument("--trailer-num", type=int, default=1)

    # acs_activity-specific
    ap.add_argument("--event", choices=["mvt_requested", "mvt_completed"], default="mvt_requested")
    ap.add_argument("--gate", choices=["ENT", "EXT"], default="ENT")
    ap.add_argument("--truck-id", default="ACS-TRK-001")
    ap.add_argument("--driver-id", default="ACS-DRV-001")
    ap.add_argument("--trailer-id", default="")
    ap.add_argument("--drop-bay", type=int, default=0)
    ap.add_argument("--pick-bay", type=int, default=0)
    ap.add_argument("--correlation-id", default=None)

    args = ap.parse_args()
    spec = get_signal(args.signal)

    if spec.bay_scoped and args.bay is None:
        ap.error(f"signal {args.signal!r} is bay-scoped -- pass --bay 1..4")

    with PlantBrokerClient(env=args.env) as client:
        print(f"Connected to {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']} ({args.env})")

        if args.signal == "rfid_trailer_details":
            _send_rfid(client, args.bay, args.license_plate, args.trailer_num)
        elif args.signal == "acs_activity":
            _send_acs(
                client, args.event, args.gate, args.truck_id, args.driver_id,
                args.trailer_id, args.drop_bay, args.pick_bay, args.correlation_id,
            )
        else:
            value = args.value if args.value is not None else spec.example_value
            if value is None:
                ap.error(f"signal {args.signal!r} needs --value (no example_value in the catalog)")
            _send_scalar(client, args.signal, args.bay, str(value), args.unit)


if __name__ == "__main__":
    main()
