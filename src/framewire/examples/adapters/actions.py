"""Example action mapping. Replace the print with your own actuator SDK."""

from __future__ import annotations

import sys


def create(context):
    class Actions:
        def start(self):
            rules = context.settings.get("rules", [])
            if not isinstance(rules, list) or any(
                not isinstance(rule, dict)
                or any(
                    not isinstance(rule.get(key), str) or not rule[key]
                    for key in ("signal", "value", "action")
                )
                for rule in rules
            ):
                raise ValueError("actions.toml: each rule needs a signal, value and action string")

        def on_event(self, event):
            if event["type"] != "adapter.signal":
                return
            data = event.get("data", {})
            if data.get("adapter") == "actions":
                return
            for rule in context.settings.get("rules", []):
                if data.get("signal") != rule.get("signal"):
                    continue
                if data.get("value") != rule.get("value"):
                    continue
                if "phase" in rule and data.get("phase") != rule["phase"]:
                    continue
                action = rule["action"]
                print(f"framewire action: {action}", file=sys.stderr, flush=True)
                context.emit("action.triggered", action, caused_by=data.get("signal"))

    return Actions()
