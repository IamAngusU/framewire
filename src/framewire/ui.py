"""A small terminal instrument panel. Machine output never passes through Rich."""

from __future__ import annotations

import argparse
import threading
import time
from collections import deque

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from . import __version__

console = Console(
    stderr=True,
    theme=Theme({"accent": "#7bbcff", "good": "#6ad9bc", "muted": "#8a99ad"}),
)


def header(subtitle: str = "Camera in. Control events out.") -> None:
    title = Text("Framewire", style="bold accent")
    title.append(f"  {__version__}", style="muted")
    console.print()
    console.print(title)
    console.print(Text(subtitle, style="muted"))
    console.print()


def problem(message: str, hint: str = "") -> None:
    text = Text(message, style="red")
    if hint:
        text.append(f"\n\n{hint}", style="muted")
    console.print(Panel(text, title="Needs attention", title_align="left", border_style="red"))


class CliParser(argparse.ArgumentParser):
    def print_help(self, file=None) -> None:
        header(self.description or "Camera in. Control events out.")
        console.print(Text(self.format_usage().strip(), style="muted"))
        for group in self._action_groups:
            table = Table(box=None, show_header=False, padding=(0, 2), expand=False)
            table.add_column(style="accent", no_wrap=True)
            table.add_column()
            for action in group._group_actions:
                if action.help is argparse.SUPPRESS:
                    continue
                if isinstance(action, argparse._SubParsersAction):
                    for choice in action._choices_actions:
                        table.add_row(choice.dest, choice.help or "")
                else:
                    label = ", ".join(action.option_strings) or action.dest
                    if action.option_strings and action.nargs != 0:
                        label += f" {action.metavar or action.dest.upper()}"
                    table.add_row(label, action.help or "")
            if table.row_count:
                console.print()
                console.print(table)
        console.print()

    def error(self, message: str) -> None:
        problem(message, f"See {self.prog} --help for examples and options.")
        self.exit(2)


class NullWriter:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        pass


class Dashboard:
    def __init__(self, *, source: str, tracker: str, outputs: str, enabled: bool) -> None:
        self.enabled = enabled
        self.source = source
        self.tracker = tracker
        self.outputs = outputs
        self.state = "Looking for a camera"
        self.target = None
        self.events = 0
        self.frames = 0
        self.started = time.monotonic()
        self.adapters: dict[str, str] = {}
        self.signals: dict[str, str] = {}
        self.activity: deque[str] = deque(maxlen=3)
        self.lock = threading.RLock()
        self.live = Live(self.render(), console=console, refresh_per_second=4) if enabled else None

    def __enter__(self):
        if self.live:
            self.live.start()
        return self

    def __exit__(self, *args):
        if self.live:
            with self.lock:
                self.state = "Stopped · outputs released"
            self.live.update(self.render())
            self.live.stop()

    def status(self, message: str) -> None:
        with self.lock:
            self.activity.append(message)
        if not self.enabled:
            console.print(Text(f"framewire  {message}"))

    def frame(self) -> None:
        with self.lock:
            self.frames += 1
        if self.live:
            self.live.update(self.render())

    def on_event(self, event: dict) -> None:
        kind = event["type"]
        with self.lock:
            self.events += 1
            if kind == "source.connected":
                self.source = event["source"]
                self.state = "Connected"
            elif kind == "source.lost":
                self.state = "Waiting for camera · retrying"
                self.target = None
            elif kind.startswith("target."):
                self.target = event.get("target")
            elif kind.startswith("adapter."):
                data = event.get("data", {})
                name = str(data.get("adapter", "unknown"))
                if kind == "adapter.loading":
                    self.adapters[name] = "loading"
                elif kind == "adapter.ready":
                    self.adapters[name] = "ready"
                elif kind == "adapter.error":
                    self.adapters[name] = "needs attention"
                elif data.get("signal") != "hand.position":
                    signal = str(data.get("signal", "signal"))
                    label = {
                        "speech.transcript": "Heard",
                        "speech.command": "Command",
                        "hand.gesture": "Hand sign",
                        "lip.command": "Visual cmd",
                        "action.triggered": "Action",
                        "speech.error": "Microphone",
                    }.get(signal, signal)
                    value = str(data.get("value", ""))
                    if signal == "speech.error":
                        value = "Waiting for microphone · retrying"
                    elif signal == "hand.gesture":
                        phase = "released" if data.get("phase") == "end" else "held"
                        value = f"{data.get('hand', '')} {value} · {phase}"
                    self.signals[label] = value[:120]
                    self.signals = dict(list(self.signals.items())[-3:])
        if self.live:
            self.live.update(self.render())

    def render(self):
        with self.lock:
            title = Text("Framewire", style="bold accent")
            title.append(f"  {__version__}", style="muted")
            title.append(f"\n{self.state}", style="good" if self.state == "Connected" else "muted")
            table = Table(box=None, show_header=False, padding=(0, 2), expand=True)
            table.add_column(style="muted", width=10)
            table.add_column()
            table.add_row("Camera", Text(self.source))
            target = "Waiting for a target" if self.tracker != "none" else "Adapters only"
            if self.target:
                target = (
                    f"{self.target['kind']} · x {self.target['x']:.2f} · y {self.target['y']:.2f}"
                )
            table.add_row("Tracking", Text(target))
            table.add_row("Outputs", Text(self.outputs))
            table.add_row(
                "Adapters",
                Text(
                    ", ".join(f"{name}: {state}" for name, state in self.adapters.items())
                    or "None installed · framewire setup"
                ),
            )
            elapsed = max(0.01, time.monotonic() - self.started)
            table.add_row(
                "Activity", f"{self.frames / elapsed:.1f} frames/s  ·  {self.events} events"
            )
            for signal, value in self.signals.items():
                table.add_row(Text(signal), Text(value))
            log = Text("\n".join(self.activity), style="muted")
            footer = Text("Ctrl+C to stop  ·  pipe stdout or add --json for JSONL", style="muted")
            return Panel(
                Group(title, Text(""), table, Text(""), log, Text(""), footer),
                border_style="accent",
                box=box.ROUNDED,
                padding=(1, 2),
            )
