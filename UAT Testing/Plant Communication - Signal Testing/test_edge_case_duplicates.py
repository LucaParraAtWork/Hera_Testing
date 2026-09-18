"""
OBSERVATION harness (see test_edge_case_skipped_step.py's docstring for why
this doesn't assert pass/fail): what does Hera do when the exact same signal
arrives twice? A real plant retransmitting on a flaky link, or a firmware
bug double-firing, is a plausible real-world failure mode -- this documents
Hera's actual behaviour so a real expectation can be set afterwards.

Scenarios:
  1. Same operating_mode value sent twice in a row (bay stuck resending)
  2. Same ACS mvt_completed sent twice, SAME correlation_id both times
     (simulates a plant-side retry of one physical movement)
  3. Same RFID_trailer_details sent twice (should be the most "idempotent"
     case, if anything is)

Findings -> findings_duplicates.txt + stdout.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import _paths  # noqa: F401
import db_check
from broker_client import PlantBrokerClient
import steps as S

FINDINGS_FILE = Path(__file__).resolve().parent / "findings_duplicates.txt"
STEP_DELAY_S = 0.4
SETTLE_S = 10.0
# NOTE: see test_edge_case_skipped_step.py's SETTLE_S comment -- some signal
# types took up to ~14s to land in an earlier run, 3.0 was too short.


def _now_utc() -> datetime:
    # Naive UTC on purpose -- see test_signal_reaction.py's _now_utc() comment.
    return datetime.utcnow()


def _send_all(client: PlantBrokerClient, steps: list[S.Step], out: list[str]) -> None:
    for step in steps:
        out.append(f"  {step.note}")
        ok = client.publish(step.topic, step.payload, retain=step.retain)
        if not ok:
            out.append(f"    !! publish failed: {client.last_error}")
        time.sleep(STEP_DELAY_S)


def _dump(conn, since: datetime, out: list[str]) -> None:
    time.sleep(SETTLE_S)
    window = db_check.dump_window(conn, since=since, tables=("Movements", "PlantSignals"))
    out.append(f"\nMovements rows observed ({len(window['Movements'])}):")
    for r in window["Movements"]:
        out.append(f"  {r.Received}  EventName={r.EventName!r}  corr={str(r.AcsCorrelationId)[:8]!r}  "
                    f"drop={r.DropOffLoadingBayName!r} pick={r.PickupLoadingBayName!r}")
    relevant = [r for r in window["PlantSignals"] if r.Tag in ("operating_mode", "RFID_trailer_details")]
    out.append(f"\nRelevant PlantSignals rows observed ({len(relevant)}):")
    for r in relevant:
        out.append(f"  {r.Received}  Tag={r.Tag!r}  Value={str(r.Value)[:60]!r}")


def scenario_duplicate_operating_mode(client: PlantBrokerClient, conn, bay: int, out: list[str]) -> None:
    out.append(f"\n{'=' * 78}\nScenario 1: operating_mode=3 sent TWICE in a row (bay {bay})\n{'=' * 78}")
    topic = f"HOF/PLC/DI{bay}/TLM/operating_mode/0.8.3"
    since = _now_utc()
    steps = [
        S.Step(topic, S.build_operating_mode_payload(1), f"DI{bay} operating_mode=1"),
        S.Step(topic, S.build_operating_mode_payload(3), f"DI{bay} operating_mode=3 (1st send)"),
        S.Step(topic, S.build_operating_mode_payload(3), f"DI{bay} operating_mode=3 (2nd send -- DUPLICATE)"),
    ]
    out.append("Steps sent, in order:")
    _send_all(client, steps, out)
    out.append(f"\nExpectation to check: does 'CONNECTED' appear ONCE or TWICE in Movements for DI{bay}?")
    _dump(conn, since, out)


def scenario_duplicate_acs_completed(client: PlantBrokerClient, conn, bay: int, out: list[str]) -> None:
    out.append(f"\n{'=' * 78}\nScenario 2: ACS mvt_completed sent TWICE, SAME correlation_id (bay {bay})\n{'=' * 78}")
    since = _now_utc()
    entry_steps = S.entry("ACS-TRK-903", "ACS-DRV-903", "1TRL639", bay)
    # entry_steps = [requested, completed]. Re-publish the SAME completed
    # payload (identical correlation_id) a second time right after.
    steps = list(entry_steps) + [
        S.Step(entry_steps[1].topic, entry_steps[1].payload, "ACS entry completed (2nd send -- DUPLICATE, same correlation_id)")
    ]
    out.append("Steps sent, in order:")
    _send_all(client, steps, out)
    out.append("Expectation to check: does mvt_completed appear ONCE or TWICE with the same AcsCorrelationId?")
    _dump(conn, since, out)


def scenario_duplicate_rfid(client: PlantBrokerClient, conn, bay: int, out: list[str]) -> None:
    out.append(f"\n{'=' * 78}\nScenario 3: RFID_trailer_details sent TWICE, identical payload (bay {bay})\n{'=' * 78}")
    since = _now_utc()
    topic = f"HOF/PLC/DI{bay}/TLM/RFID_trailer_details/0.8.3"
    payload = S.build_rfid_trailer_details_payload("1TRL968", 1)
    steps = [
        S.Step(topic, payload, "RFID trailer details (1st send)"),
        S.Step(topic, payload, "RFID trailer details (2nd send -- DUPLICATE, identical payload)"),
    ]
    out.append("Steps sent, in order:")
    _send_all(client, steps, out)
    out.append("Expectation to check: 1 PlantSignals row or 2? (TLM state signals are plausibly idempotent)")
    _dump(conn, since, out)


def main() -> None:
    conn = db_check.connect("heraplantdatabasetest")
    out: list[str] = [f"Duplicate-signal observation run -- {_now_utc().isoformat()}"]

    with PlantBrokerClient(env="TEST") as client:
        scenario_duplicate_operating_mode(client, conn, bay=4, out=out)
        scenario_duplicate_acs_completed(client, conn, bay=1, out=out)
        scenario_duplicate_rfid(client, conn, bay=2, out=out)

    text = "\n".join(out)
    print(text)
    FINDINGS_FILE.write_text(text, encoding="utf-8")
    print(f"\nFindings written to {FINDINGS_FILE}")


if __name__ == "__main__":
    main()
