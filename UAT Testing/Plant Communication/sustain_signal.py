#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sustain_signal.py
==================
Send the SAME scalar plant signal repeatedly, at a fixed interval, over a
window that spans more than one 15-minute bucket on Hera's Bay Monitoring
"Injection and Loading Profiles" chart (which reads from
BayMonitoringScreen/GetBayMonitoringScreenAsync's "Actual" points, one per
15-min slot, see PLANT_SIGNALS_CATALOG.md / this folder's README for how
that was reverse-engineered).

Why this exists: a single short burst of sends only ever lands in ONE
15-minute bucket, and even then can get diluted or dropped -- see the
2026-09-21 findings on `oks_stat_blending_line`-adjacent aggregation
weirdness (one bucket with our DB-confirmed sends came back `null` on the
Actual line, a LATER bucket came back as what looks like an average diluted
by hera-virtual-plant's own automatic ~30s zero-tick). Sending continuously
across a longer window gives us at least two consecutive Actual points to
compare against ground truth (this script's own log + a direct PlantSignals/
LoadingSessions query afterward), instead of gambling on a single bucket.

Also resends fast enough (default every 7.5s) to reliably out-number the
automatic ~30s background tick that keeps overwriting bay signals with 0 in
DEV -- confirmed necessary in the same session (a single one-shot send was
overwritten by that tick within ~30s).

**2026-09-23 follow-up:** 7.5s was chosen (over the original 8s) because it
divides a 900s (15-min) bucket evenly -- 120 sends per bucket, 240 per two
buckets -- so a `--start` aligned exactly on a quarter-hour boundary lands a
send exactly ON every subsequent quarter-hour mark too (8s does not: 900/8
= 112.5, so the nearest send to a later boundary is off by up to ~4s). This
matters if the Bay Monitoring screen samples/keys its 15-min "Actual" points
by exact timestamp rather than aggregating the whole window -- a live run
had already shown the bucket starting at the aligned boundary (09:30) look
correct while the next one (09:45) didn't, which prompted this fix. Getting
sends to land exactly on the boundary needed a second fix too: `sustain()`
below now schedules against absolute target times instead of a relative
`sleep(interval_s)` after each publish, since that relative version was
observed drifting (a live log already showed a 9s gap where 8s was
expected, from ordinary per-publish overhead accumulating over ~70 sends) --
an interval that divides evenly doesn't help if the actual send times drift
away from the intended schedule anyway.

Usage
-----
    py sustain_signal.py --bay 1 --value 66.6 --duration-min 17 --env DEV
    py sustain_signal.py --bay 1 --value 66.6 --start 16:59 --end 17:16 --env DEV

`--start`/`--end` (local 24h HH:MM, today) are for lining the window up with
a specific wall-clock target (e.g. to straddle a 15-min boundary on
purpose); `--duration-min` (from now) is simpler for "just start it now".
Give either --duration-min, or both --start and --end.

Every send is logged to this folder's `sustain_signal_runs/<run_id>/sent_log.csv`
(utc timestamp, local timestamp, bay, signal, value, ok) so it can be
compared afterward against:
  - PlantSignals / LoadingSessions.DispensedFlow(Rate) (ground truth -- see
    db_check.py / the ad-hoc queries in this session's history)
  - The BayMonitoringScreen API's "Actual" points for the same bay/window
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from broker_client import PlantBrokerClient
from config import ENV_CHOICES, DEFAULT_ENV
from envelope import build_envelope
from signal_catalog import get as get_signal

HERE = Path(__file__).resolve().parent


def _parse_hhmm_today(s: str) -> datetime:
    hh, mm = (int(p) for p in s.split(":"))
    now = datetime.now().astimezone()
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def sustain(client: PlantBrokerClient, topic: str, spec, value: float,
            start_at: datetime, end_at: datetime, interval_s: float,
            csv_writer=None, quiet: bool = False) -> int:
    """Repeatedly publish `value` on `topic` every `interval_s`, blocking
    until `start_at` (if in the future) and stopping once `end_at` passes.
    Returns the number of sends. Factored out of `main()` so other scripts
    (e.g. a Bay Monitoring vs. broker comparison test) can drive the exact
    same sustained-send behaviour -- including its two load-bearing
    mitigations, pacing and duration -- without shelling out to this file.
    If `csv_writer` is given, each send is written as
    [ts_utc, ts_local, value, ok] (bay/signal are the caller's own context,
    not repeated per row here).

    Schedules against ABSOLUTE target times (`start_at + k*interval_s`),
    not a relative `sleep(interval_s)` after each publish -- a real run's
    log showed the relative version drifting (one gap already 9s instead of
    8s by send #68, from ordinary per-publish overhead accumulating).
    That drift matters here specifically because a caller may choose
    `interval_s` so that bucket boundaries (e.g. 900s/7.5s = 120, an exact
    multiple) fall exactly on a send -- accumulated drift would silently
    defeat that. Computing each target as `start_at + k*interval_s` (fixed,
    not compounding sleep durations) keeps every send aligned to the
    original schedule regardless of how long any single publish() call
    takes, as long as no single call takes longer than `interval_s` itself."""
    now = datetime.now().astimezone()
    if now < start_at:
        wait_s = (start_at - now).total_seconds()
        if not quiet:
            print(f"Waiting {wait_s:.0f}s for the scheduled start time {start_at.strftime('%H:%M:%S')}...")
        time.sleep(wait_s)

    sent = 0
    next_send = start_at
    while next_send < end_at:
        wait_s = (next_send - datetime.now().astimezone()).total_seconds()
        if wait_s > 0:
            time.sleep(wait_s)

        now_local = datetime.now().astimezone()
        now_utc = now_local.astimezone(timezone.utc)
        value_str = str(value) if spec.value_type == "float" else str(int(value))
        payload = build_envelope(value_str, value_type=spec.value_type, unit=spec.unit)
        ok = client.publish(topic, payload, retain=spec.retain)
        sent += 1
        if not quiet:
            drift_ms = (now_local - next_send).total_seconds() * 1000
            print(f"[{now_local.strftime('%H:%M:%S')}]  #{sent:03d}  value={value}  ok={ok}  "
                  f"(drift {drift_ms:+.0f}ms)")
        if csv_writer is not None:
            csv_writer.writerow([now_utc.isoformat(), now_local.isoformat(), value, ok])
        next_send = next_send + timedelta(seconds=interval_s)
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signal", default="fill_rate_meas", help="catalog key (default: fill_rate_meas)")
    ap.add_argument("--bay", type=int, choices=[1, 2, 3, 4], default=1)
    ap.add_argument("--value", type=float, default=66.6, help="constant value to keep resending")
    ap.add_argument("--interval-s", type=float, default=7.5,
                     help="seconds between sends (default: 7.5 -- divides a 900s/15-min "
                          "bucket evenly (120 sends), so a boundary-aligned --start lands "
                          "a send exactly on every quarter-hour mark, not just near it)")
    ap.add_argument("--env", choices=ENV_CHOICES, default=DEFAULT_ENV)
    ap.add_argument("--duration-min", type=float, default=None,
                     help="run for this many minutes starting now")
    ap.add_argument("--start", default=None, help="local HH:MM, today -- wait until this time to start")
    ap.add_argument("--end", default=None, help="local HH:MM, today -- stop at this time")
    args = ap.parse_args()

    if args.duration_min is None and (args.start is None or args.end is None):
        ap.error("give --duration-min, or both --start and --end")

    if args.start:
        start_at = _parse_hhmm_today(args.start)
        end_at = _parse_hhmm_today(args.end)
        if end_at <= start_at:
            end_at += timedelta(days=1)
    else:
        start_at = datetime.now().astimezone()
        end_at = start_at + timedelta(minutes=args.duration_min)

    spec = get_signal(args.signal)
    if not spec.bay_scoped:
        ap.error(f"signal {args.signal!r} is not bay-scoped -- this script is built for per-bay signals")
    topic = spec.topic(args.bay)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = HERE / "sustain_signal_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "sent_log.csv"

    print(f"Sustaining {args.signal}={args.value} on bay {args.bay} ({topic})")
    print(f"  window: {start_at.strftime('%H:%M:%S')} -> {end_at.strftime('%H:%M:%S')} "
          f"({(end_at - start_at).total_seconds() / 60:.1f} min), every {args.interval_s}s, env={args.env}")
    print(f"  log: {csv_path}\n")

    with csv_path.open("w", newline="", encoding="utf-8") as f, PlantBrokerClient(env=args.env) as client:
        w = csv.writer(f)
        w.writerow(["ts_utc", "ts_local", "bay", "signal", "value", "ok"])
        print(f"Connected to {client.cfg['mqtt_host']} as {client.cfg['mqtt_user']} ({args.env})")

        class _RowWriter:
            def writerow(self, row):
                ts_utc, ts_local, value, ok = row
                w.writerow([ts_utc, ts_local, args.bay, args.signal, value, ok])
                f.flush()

        sent = sustain(client, topic, spec, args.value, start_at, end_at, args.interval_s,
                        csv_writer=_RowWriter())

    print(f"\nDone. Sent {sent} messages from {start_at.strftime('%H:%M:%S')} to "
          f"{datetime.now().astimezone().strftime('%H:%M:%S')}.")
    print(f"Log saved to {csv_path}")
    print("\nNext: re-pull BayMonitoringScreen/GetBayMonitoringScreenAsync and compare its "
          f"Loading Bay {args.bay} 'Actual' points for this window against this log and the DB "
          "(PlantSignals / LoadingSessions.DispensedFlow[Rate]).")


if __name__ == "__main__":
    main()
