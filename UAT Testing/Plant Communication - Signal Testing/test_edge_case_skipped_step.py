"""
OBSERVATION harness, not a pass/fail test: what does Hera actually do when a
step in the confirmed sequence is missing? There's no spec for "correct"
behaviour here yet -- a real plant could plausibly drop a message (network
blip, retry storm, firmware bug), so this documents Hera's real, current
resilience rather than asserting an expectation we don't actually have.

Each scenario runs on its own bay to keep DB windows clean, then dumps every
Movements/PlantSignals row observed in that window to
`findings_skipped_step.txt` plus stdout. Read the dump and judge for
yourself whether what happened looks right -- that judgement belongs to
someone who knows Hera's intended behaviour, not to this script.

Scenarios:
  1. Skip operating_mode=2 (send 1, then jump straight to 3)
  2. Skip the ACS entry pair entirely (jump straight to the operating_mode
     sequence -- no truck ever "arrived" as far as ACS is concerned)
  3. Skip every fill_rate_meas reading during filling (modes 4,5,6 -> 8,9,10
     with zero measurements in between)
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import _paths  # noqa: F401
import db_check
from broker_client import PlantBrokerClient
import steps as S

FINDINGS_FILE = Path(__file__).resolve().parent / "findings_skipped_step.txt"
STEP_DELAY_S = 0.4
SETTLE_S = 10.0
# NOTE: 3.0 was too short in an earlier run of a similar dump (some signal
# types took up to ~14s to land) -- 10s settle after the last publish
# comfortably covers what was observed. If a scenario's dump still looks
# incomplete, re-run db_check.dump_window() again a bit later before
# concluding something didn't arrive.


def _now_utc() -> datetime:
    # Naive UTC on purpose -- see test_signal_reaction.py's _now_utc() comment.
    return datetime.utcnow()


def _run_and_dump(client: PlantBrokerClient, conn, label: str, seq: list[S.Step], out: list[str]) -> None:
    out.append(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    out.append("Steps sent, in order:")
    since = _now_utc()
    for step in seq:
        out.append(f"  {step.note}")
        ok = client.publish(step.topic, step.payload, retain=step.retain)
        if not ok:
            out.append(f"    !! publish failed: {client.last_error}")
        time.sleep(STEP_DELAY_S)

    time.sleep(SETTLE_S)
    window = db_check.dump_window(conn, since=since, tables=("Movements", "PlantSignals"))

    out.append(f"\nMovements rows observed ({len(window['Movements'])}):")
    for r in window["Movements"]:
        out.append(f"  {r.Received}  EventName={r.EventName!r}  drop={r.DropOffLoadingBayName!r} "
                    f"pick={r.PickupLoadingBayName!r}  direction={r.Direction!r}")

    op_mode_rows = [r for r in window["PlantSignals"] if r.Tag in ("operating_mode", "RFID_trailer_details")]
    out.append(f"\noperating_mode / RFID_trailer_details PlantSignals rows observed ({len(op_mode_rows)}):")
    for r in op_mode_rows:
        out.append(f"  {r.Received}  Tag={r.Tag!r}  Asset={r.Asset!r}  Value={str(r.Value)[:60]!r}")


def scenario_skip_mode2(bay: int) -> list[S.Step]:
    # dropoff() normally sends modes 1,2,3,RFID -- rebuild it manually minus mode 2.
    topic = f"HOF/PLC/DI{bay}/TLM/operating_mode/0.8.3"
    rfid_topic = f"HOF/PLC/DI{bay}/TLM/RFID_trailer_details/0.8.3"
    steps = S.entry("ACS-TRK-901", "ACS-DRV-901", "1TRL167", bay)
    steps.append(S.Step(topic, S.build_operating_mode_payload(1), f"DI{bay} operating_mode=1 (Drop-off start)"))
    # deliberately SKIP mode 2 (Vehicle approach)
    steps.append(S.Step(topic, S.build_operating_mode_payload(3),
                         f"DI{bay} operating_mode=3 (Drop-off closed) -- mode 2 SKIPPED"))
    steps.append(S.Step(rfid_topic, S.build_rfid_trailer_details_payload("1TRL167", 1),
                         f"DI{bay} RFID trailer details [1TRL167]"))
    steps += S.exit_("ACS-TRK-901", "ACS-DRV-901", bay)
    return steps


def scenario_skip_acs_entry(bay: int) -> list[S.Step]:
    # No entry() at all -- straight into the drop-off sequence.
    steps = S.dropoff(bay, "1TRL285")
    steps += S.exit_("ACS-TRK-902", "ACS-DRV-902", bay)
    return steps


def scenario_skip_fill_readings(bay: int) -> list[S.Step]:
    return S.scenario_full_visit(bay=bay, drop_plate="1TRL378", pick_plate="1TRL378", flowmeter_values_gs=[])


def main() -> None:
    conn = db_check.connect("heraplantdatabasetest")
    out: list[str] = [f"Skipped-step observation run -- {_now_utc().isoformat()}"]

    with PlantBrokerClient(env="TEST") as client:
        _run_and_dump(client, conn, "Scenario 1: skip operating_mode=2 (bay 1)",
                      scenario_skip_mode2(1), out)
        _run_and_dump(client, conn, "Scenario 2: skip the ACS entry pair entirely (bay 2)",
                      scenario_skip_acs_entry(2), out)
        _run_and_dump(client, conn, "Scenario 3: skip every fill_rate_meas reading (bay 3)",
                      scenario_skip_fill_readings(3), out)

    text = "\n".join(out)
    print(text)
    FINDINGS_FILE.write_text(text, encoding="utf-8")
    print(f"\nFindings written to {FINDINGS_FILE}")


if __name__ == "__main__":
    main()
