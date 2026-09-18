"""
Play a full truck-visit scenario against the broker, step by step, exactly
as hera-virtual-plant's own "Truck Scenario" tab does it (same topics, same
envelopes, same order -- see steps.py's docstring for the evidence trail).

This is the tool for step 1 of "does Hera react correctly": generate a
believable sequence of real plant signals on demand, then go check Hera's
own DB/UI/API afterwards to see what it did with them.

Examples:
    py run_scenario.py drop_only --drop-bay 1
    py run_scenario.py drop_and_pick --drop-bay 1 --pick-bay 2
    py run_scenario.py full_visit --bay 3 --flowmeter 42.5,43.8,41.2
    py run_scenario.py full_visit --bay 3 --delay 0.5 --env DEV
"""
from __future__ import annotations

import argparse
import sys
import time

from broker_client import PlantBrokerClient
from config import ENV_CHOICES, DEFAULT_ENV
import steps as S


def _parse_flowmeter(raw: str) -> list[float]:
    return [float(v.strip()) for v in raw.split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=["drop_only", "drop_and_pick", "full_visit"])
    ap.add_argument("--env", choices=ENV_CHOICES, default=DEFAULT_ENV)
    ap.add_argument("--bay", type=int, choices=[1, 2, 3, 4], help="full_visit: the single bay used")
    ap.add_argument("--drop-bay", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--pick-bay", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--drop-plate", default="1TRL285")
    ap.add_argument("--pick-plate", default="1TRL378")
    ap.add_argument("--flowmeter", default="42.5,43.8,41.2", help="comma-separated g/s readings")
    ap.add_argument("--truck-id", default="ACS-TRK-001")
    ap.add_argument("--driver-id", default="ACS-DRV-001")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between each published step")
    ap.add_argument("--dry-run", action="store_true", help="print the steps without connecting/publishing")
    args = ap.parse_args()

    if args.scenario == "drop_only":
        if args.drop_bay is None:
            ap.error("drop_only needs --drop-bay")
        seq = S.scenario_drop_only(
            drop_bay=args.drop_bay, license_plate=args.drop_plate,
            truck_id=args.truck_id, driver_id=args.driver_id,
        )
    elif args.scenario == "drop_and_pick":
        if args.drop_bay is None or args.pick_bay is None:
            ap.error("drop_and_pick needs --drop-bay and --pick-bay")
        seq = S.scenario_drop_and_pick(
            drop_bay=args.drop_bay, pick_bay=args.pick_bay,
            drop_plate=args.drop_plate, pick_plate=args.pick_plate,
            truck_id=args.truck_id, driver_id=args.driver_id,
        )
    else:  # full_visit
        if args.bay is None:
            ap.error("full_visit needs --bay")
        seq = S.scenario_full_visit(
            bay=args.bay, drop_plate=args.drop_plate, pick_plate=args.drop_plate,
            flowmeter_values_gs=_parse_flowmeter(args.flowmeter),
            truck_id=args.truck_id, driver_id=args.driver_id,
        )

    print(f"{len(seq)} steps for scenario {args.scenario!r}")
    for i, step in enumerate(seq, start=1):
        print(f"  #{i:02d}  {step.note:<55s} {step.topic}")

    if args.dry_run:
        return

    with PlantBrokerClient(env=args.env) as client:
        print(f"\nConnected to {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']} ({args.env})\n")
        for i, step in enumerate(seq, start=1):
            ok = client.publish(step.topic, step.payload, retain=step.retain)
            status = "OK" if ok else f"FAILED ({client.last_error})"
            print(f"  #{i:02d}  {status:<10s} {step.note}")
            if not ok:
                sys.exit(1)
            if i < len(seq):
                time.sleep(args.delay)

    print("\nDone.")


if __name__ == "__main__":
    main()
