"""List every signal this fake-plant simulator knows how to send.

Usage:
    py list_signals.py
    py list_signals.py --evidence db-topic-only     # only the unconfirmed-shape ones
"""
from __future__ import annotations

import argparse

from signal_catalog import SIGNALS


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence", choices=["code", "db", "db-topic-only"], default=None)
    args = ap.parse_args()

    rows = sorted(SIGNALS.values(), key=lambda s: (s.evidence, s.key))
    if args.evidence:
        rows = [s for s in rows if s.evidence == args.evidence]

    w_key, w_topic, w_ev = 26, 46, 14
    print(f"{'key':<{w_key}} {'topic':<{w_topic}} {'evidence':<{w_ev}} bay?  retain?")
    print("-" * (w_key + w_topic + w_ev + 16))
    for s in rows:
        topic = s.topic_template
        print(f"{s.key:<{w_key}} {topic:<{w_topic}} {s.evidence:<{w_ev}} "
              f"{'yes' if s.bay_scoped else 'no':<5} {'yes' if s.retain else 'no'}")
        print(f"{'':<{w_key}} {s.description}")
        print()


if __name__ == "__main__":
    main()
