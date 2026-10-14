"""The terminal is the UI; stdout stays clean JSONL for piping."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from importlib.util import find_spec

import cv2
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .adapters import (
    EXAMPLES,
    AdapterManager,
    adapter_dir,
    create_adapter,
    install_example,
    list_installed,
    remove_adapter,
    set_enabled,
)
from .engine import RunOptions, run
from .gestures_cli import record as record_gesture
from .gestures_cli import remove as remove_gesture_label
from .gestures_cli import sample_path as gesture_sample_path
from .handmatch import load_samples as load_gesture_samples
from .lipmatch import load_samples
from .lips_cli import record as record_lips
from .lips_cli import remove as remove_lip_label
from .lips_cli import sample_path
from .model import Target
from .outputs import ControlHub, EventBus
from .phrases import add_phrase, load_preferences, remove_phrase, set_language
from .sources import open_source, safe_error, safe_label
from .tracking import make_tracker

console = Console(stderr=True)


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0 or number > 60:
        raise argparse.ArgumentTypeError("expected a number above 0 and at most 60")
    return number


def pin_number(value: str) -> int:
    pin = int(value)
    if not 0 <= pin <= 27:
        raise argparse.ArgumentTypeError("use a BCM GPIO number from 0 to 27")
    return pin


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="framewire", description="Camera in. Control events out.")
    top.add_argument("--version", action="version", version=f"framewire {__version__}")
    commands = top.add_subparsers(dest="command")

    live = commands.add_parser("run", help="Track a target and emit events continuously")
    live.add_argument(
        "--source",
        default="auto",
        metavar="SOURCE",
        help="auto, camera number, picamera, RTSP or HTTP video URL",
    )
    live.add_argument(
        "--snapshot", action="store_true", help="Treat HTTP URL as a repeated JPEG/PNG snapshot"
    )
    live.add_argument("--track", choices=("face", "motion", "none"), default="face")
    live.add_argument(
        "--fps", type=positive_float, default=12, help="Maximum sampling rate (default: 12)"
    )
    live.add_argument("--lost-after", type=positive_float, default=0.8, metavar="SECONDS")
    live.add_argument("--servo-pin", type=pin_number, metavar="BCM", help="Pan servo signal pin")
    live.add_argument(
        "--gpio-pin", type=pin_number, metavar="BCM", help="HIGH while target is present"
    )
    live.add_argument(
        "--servo-range",
        type=float,
        default=60,
        metavar="DEGREES",
        help="Max angle on each side of center (default: 60)",
    )
    live.add_argument("--invert-pan", action="store_true")
    live.add_argument("--deadzone", type=float, default=0.04, metavar="FRACTION")
    live.add_argument("--webhook", metavar="URL", help="POST each JSON event to this URL")
    live.add_argument(
        "--webhook-token-env",
        metavar="NAME",
        help="Read webhook bearer token from this environment variable",
    )
    live.add_argument("--dry-run", action="store_true", help="Emit events without driving pins")
    live.add_argument("--no-adapters", action="store_true", help="Ignore installed adapters")
    live.add_argument(
        "--adapter",
        action="append",
        metavar="NAME",
        help="Run only this installed adapter (repeatable)",
    )
    live.add_argument("--max-frames", type=int, metavar="N", help="Stop after N received frames")

    check = commands.add_parser("doctor", help="Check dependencies and read one frame")
    check.add_argument("--source", default="auto", metavar="SOURCE")
    check.add_argument("--snapshot", action="store_true")
    check.add_argument("--json", action="store_true", help="Machine-readable result on stdout")

    demo = commands.add_parser("demo", help="See the event stream without a camera or GPIO")
    demo.add_argument("--seconds", type=positive_float, default=3)

    adapters = commands.add_parser("adapters", help="Install and manage local adapters")
    adapter_actions = adapters.add_subparsers(dest="adapter_action", required=True)
    for action in ("path", "list"):
        adapter_actions.add_parser(action)
    for action in ("add", "new", "enable", "disable", "remove"):
        command = adapter_actions.add_parser(action)
        command.add_argument("name")

    lips = commands.add_parser("lips", help="Train short visual commands from your own examples")
    lip_actions = lips.add_subparsers(dest="lip_action", required=True)
    lip_record = lip_actions.add_parser("record")
    lip_record.add_argument("label", help="Command label, for example lamp.on")
    lip_record.add_argument("--source", default="auto")
    lip_record.add_argument("--seconds", type=float, default=2.0)
    lip_record.add_argument("--repeats", type=int, default=3)
    lip_actions.add_parser("list")
    lip_remove = lip_actions.add_parser("remove")
    lip_remove.add_argument("label")

    gestures = commands.add_parser("gestures", help="Teach and manage static hand signs")
    gesture_actions = gestures.add_subparsers(dest="gesture_action", required=True)
    gesture_record = gesture_actions.add_parser("record")
    gesture_record.add_argument("label", help="Your sign name, for example lamp.on")
    gesture_record.add_argument("--source", default="auto")
    gesture_record.add_argument("--seconds", type=float, default=2.0)
    gesture_record.add_argument("--repeats", type=int, default=3)
    gesture_actions.add_parser("list")
    gesture_remove = gesture_actions.add_parser("remove")
    gesture_remove.add_argument("label")

    phrases = commands.add_parser("phrases", help="Manage spoken commands and language")
    phrase_actions = phrases.add_subparsers(dest="phrase_action", required=True)
    phrase_add = phrase_actions.add_parser("add")
    phrase_add.add_argument("phrase")
    phrase_add.add_argument("action")
    phrase_actions.add_parser("list")
    phrase_remove = phrase_actions.add_parser("remove")
    phrase_remove.add_argument("phrase")
    phrase_language = phrase_actions.add_parser("language")
    phrase_language.add_argument("code", help="auto or a language code such as de")
    return top


def overview(top: argparse.ArgumentParser) -> None:
    console.print(
        Panel.fit(
            "[bold cyan]FRAMEWIRE[/]\n[dim]camera in  /  control events out[/]", border_style="cyan"
        )
    )
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[bold]framewire doctor[/]", "Find a camera and check the local setup")
    table.add_row("[bold]framewire run[/]", "Follow a face; JSON events on stdout")
    table.add_row("[bold]framewire demo[/]", "Try the event stream without hardware")
    table.add_row("[bold]framewire adapters[/]", "Add hands, voice, lip or action adapters")
    table.add_row("[bold]framewire gestures[/]", "Record your own hand signs")
    table.add_row("[bold]framewire phrases[/]", "Map spoken phrases and set a language")
    table.add_row("[bold]framewire lips[/]", "Record short visual commands")
    console.print(table)
    console.print("[dim]Add --help to any command for all options.[/]")


def doctor(args: argparse.Namespace) -> int:
    report: dict = {
        "framewire": __version__,
        "opencv": cv2.__version__,
        "gpiozero_installed": find_spec("gpiozero") is not None,
        "picamera2_installed": find_spec("picamera2") is not None,
        "mediapipe_installed": find_spec("mediapipe") is not None,
        "voice_installed": find_spec("faster_whisper") is not None
        and find_spec("sounddevice") is not None,
        "adapters": [name for name, enabled in list_installed() if enabled],
        "source": safe_label(args.source),
        "camera_ready": False,
    }
    source = None
    try:
        make_tracker("face")
        source = open_source(args.source, snapshot=args.snapshot)
        frame = source.read()
        report.update(
            camera_ready=True,
            source=source.label,
            frame_size=[int(frame.shape[1]), int(frame.shape[0])],
        )
    except Exception as exc:
        report["reason"] = safe_error(exc, args.source)
    finally:
        if source is not None:
            try:
                source.close()
            except Exception as exc:
                report["cleanup_error"] = type(exc).__name__
    if args.json:
        print(json.dumps(report))
    else:
        table = Table(title="Framewire · doctor", show_header=False)
        for name, value in report.items():
            table.add_row(name.replace("_", " "), str(value))
        console.print(table)
        if not report["camera_ready"]:
            console.print(
                "[yellow]No frame yet.[/] Connect a camera, choose --source 1, or try "
                "--source URL. [bold]framewire demo[/] works without hardware."
            )
    return 0 if report["camera_ready"] else 1


def demo(args: argparse.Namespace) -> int:
    console.print("[cyan]Simulating a moving target.[/] Events are JSONL on stdout.")
    bus = EventBus()
    try:
        bus.emit("source.connected", "demo")
        count = max(3, int(args.seconds * 5))
        for index in range(count):
            x = round(0.15 + 0.7 * index / max(1, count - 1), 4)
            target = Target(x, 0.5, 0.04, "demo")
            bus.emit("target.acquired" if index == 0 else "target.moved", "demo", target)
            time.sleep(args.seconds / count)
        bus.emit("target.lost", "demo")
    except KeyboardInterrupt:
        bus.emit("target.lost", "demo", detail="interrupted")
    finally:
        bus.close()
    return 0


def live(args: argparse.Namespace, top: argparse.ArgumentParser) -> int:
    if args.snapshot and not args.source.startswith(("http://", "https://")):
        top.error("--snapshot requires an HTTP image URL")
    if not 1 <= args.servo_range <= 90:
        top.error("--servo-range must be between 1 and 90 degrees")
    if not 0 <= args.deadzone < 0.5:
        top.error("--deadzone must be at least 0 and below 0.5")
    if args.servo_pin is not None and args.servo_pin == args.gpio_pin:
        top.error("--servo-pin and --gpio-pin must be different")
    if args.max_frames is not None and args.max_frames < 1:
        top.error("--max-frames must be positive")
    if args.webhook and not args.webhook.startswith(("http://", "https://")):
        top.error("--webhook requires an HTTP(S) URL")
    if args.no_adapters and args.adapter:
        top.error("--no-adapters and --adapter cannot be combined")
    installed = {name for name, enabled in list_installed() if enabled}
    if args.adapter and not set(args.adapter).issubset(installed):
        top.error("requested adapter is not installed and enabled; use 'framewire adapters list'")
    token = None
    if args.webhook_token_env:
        token = os.environ.get(args.webhook_token_env)
        if not token:
            top.error(f"environment variable {args.webhook_token_env} is empty or missing")

    def status(message: str) -> None:
        console.print(f"[dim]framewire[/]  {message}")

    if args.dry_run and (args.servo_pin is not None or args.gpio_pin is not None):
        status("Dry run: physical outputs are disabled")
    try:
        control = ControlHub(
            servo_pin=args.servo_pin,
            gpio_pin=args.gpio_pin,
            max_angle=args.servo_range,
            invert_pan=args.invert_pan,
            deadzone=args.deadzone,
            dry_run=args.dry_run,
            warn=status,
        )
    except Exception as exc:
        console.print(f"[red]Output setup failed:[/] {exc}")
        return 2
    bus = EventBus(webhook=args.webhook, token=token, warn=status)
    adapters = (
        None
        if args.no_adapters
        else AdapterManager(bus, selected=set(args.adapter) if args.adapter else None, warn=status)
    )
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    status("Starting; Ctrl+C stops. JSON events are written to stdout.")
    try:
        run(
            RunOptions(
                source=args.source,
                snapshot=args.snapshot,
                track=args.track,
                fps=args.fps,
                lost_after=args.lost_after,
                max_frames=args.max_frames,
            ),
            bus,
            control,
            source_factory=lambda: open_source(args.source, snapshot=args.snapshot),
            adapters=adapters,
            stop=stop,
            status=status,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


def adapter_command(args: argparse.Namespace) -> int:
    action = args.adapter_action
    if action == "path":
        print(adapter_dir())
        return 0
    if action == "list":
        console.print(f"[dim]Adapter folder:[/] {adapter_dir()}")
        table = Table(title="Framewire adapters")
        table.add_column("Name")
        table.add_column("State")
        for name, enabled in list_installed():
            table.add_row(name, "enabled" if enabled else "disabled")
        console.print(table)
        console.print(f"[dim]Copyable examples: {', '.join(EXAMPLES)}[/]")
        return 0
    try:
        if action == "add":
            path = install_example(args.name)
            console.print(f"[green]Installed[/] {path}")
            if args.name in {"hands", "lips", "voice"}:
                packages = (
                    "mediapipe" if args.name in {"hands", "lips"} else "faster-whisper sounddevice"
                )
                console.print(
                    f"Install optional recognition support: [bold]python -m pip install {packages}[/]"
                )
        elif action == "new":
            console.print(f"[green]Created[/] {create_adapter(args.name)}")
        elif action == "enable":
            console.print(f"[green]Enabled[/] {set_enabled(args.name, True)}")
        elif action == "disable":
            console.print(f"[yellow]Disabled[/] {set_enabled(args.name, False)}")
        elif action == "remove":
            remove_adapter(args.name)
            console.print(f"Removed {args.name} code and config; recorded data was kept.")
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/]")
        return 2
    return 0


def lips_command(args: argparse.Namespace) -> int:
    if args.lip_action == "list":
        samples = load_samples(sample_path())
        table = Table(title="Visual commands")
        table.add_column("Label")
        table.add_column("Samples", justify="right")
        for label, examples in sorted(samples.items()):
            table.add_row(label, str(len(examples)))
        console.print(table)
        return 0
    if args.lip_action == "remove":
        if not remove_lip_label(args.label):
            console.print(f"[red]No samples for {args.label}[/]")
            return 2
        console.print(f"Removed visual command {args.label}")
        return 0
    try:
        count = record_lips(
            args.label,
            source_spec=args.source,
            seconds=args.seconds,
            repeats=args.repeats,
            status=lambda message: console.print(message),
        )
    except (Exception, KeyboardInterrupt) as exc:
        console.print(f"[red]Recording stopped:[/] {type(exc).__name__}: {exc}")
        return 2
    console.print(
        f"[green]Recorded[/] {args.label} ({count} total samples). "
        "Install/enable the lips adapter to recognize it during run."
    )
    return 0


def gestures_command(args: argparse.Namespace) -> int:
    if args.gesture_action == "list":
        samples = load_gesture_samples(gesture_sample_path())
        table = Table(title="Personal hand signs")
        table.add_column("Label")
        table.add_column("Samples", justify="right")
        for label, examples in sorted(samples.items()):
            table.add_row(label, str(len(examples)))
        console.print(table)
        return 0
    if args.gesture_action == "remove":
        if not remove_gesture_label(args.label):
            console.print(f"[red]No samples for {args.label}[/]")
            return 2
        console.print(f"Removed hand sign {args.label}")
        return 0
    try:
        count = record_gesture(
            args.label,
            source_spec=args.source,
            seconds=args.seconds,
            repeats=args.repeats,
            status=lambda message: console.print(message),
        )
    except (Exception, KeyboardInterrupt) as exc:
        console.print(f"[red]Recording stopped:[/] {type(exc).__name__}: {exc}")
        return 2
    console.print(
        f"[green]Recorded[/] {args.label} ({count} total samples). "
        "Install/enable the hands adapter to recognize it during run."
    )
    return 0


def phrases_command(args: argparse.Namespace) -> int:
    try:
        if args.phrase_action == "add":
            add_phrase(args.phrase, args.action)
            console.print(f"[green]Mapped[/] {args.phrase} → {args.action}")
        elif args.phrase_action == "remove":
            if not remove_phrase(args.phrase):
                console.print(f"[red]No mapping for {args.phrase}[/]")
                return 2
            console.print(f"Removed phrase {args.phrase}")
        elif args.phrase_action == "language":
            set_language(args.code)
            console.print(f"[green]Language[/] {args.code}")
        else:
            preferences = load_preferences()
            console.print(f"[dim]Language:[/] {preferences['language'] or 'voice.toml / auto'}")
            table = Table(title="Spoken phrases")
            table.add_column("Phrase")
            table.add_column("Action")
            for phrase, action in sorted(preferences["commands"].items()):
                table.add_row(phrase, action)
            console.print(table)
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/]")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    top = parser()
    args = top.parse_args(argv)
    if args.command is None:
        overview(top)
        return 0
    if args.command == "doctor":
        return doctor(args)
    if args.command == "demo":
        return demo(args)
    if args.command == "run":
        return live(args, top)
    if args.command == "adapters":
        return adapter_command(args)
    if args.command == "lips":
        return lips_command(args)
    if args.command == "gestures":
        return gestures_command(args)
    if args.command == "phrases":
        return phrases_command(args)
    return 2
