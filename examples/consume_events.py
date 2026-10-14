"""Run with: framewire run | python examples/consume_events.py

Replace print_action with calls to your actuator's SDK.
"""

import json
import sys


def print_action(message: str) -> None:
    print(message, flush=True)


for line in sys.stdin:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("schema") != "framewire.event.v1":
        continue
    kind = event.get("type")
    if kind in {"target.acquired", "target.moved"}:
        target = event["target"]
        print_action(f"Aim at x={target['x']:.2f}, y={target['y']:.2f}")
    elif kind in {"target.lost", "source.lost"}:
        print_action("Release actuator")
