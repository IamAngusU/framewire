"""The terminal is the UI; stdout stays clean JSONL for piping."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading
import time
import traceback
from importlib.util import find_spec
from urllib.parse import urlsplit

from rich.table import Table
from rich.text import Text

from . import __version__
from .adapters import (
    EXAMPLES,
    AdapterManager,
    adapter_dir,
    create_adapter,
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
from .setup import prepare
from .sources import open_source, safe_error, safe_label
from .tracking import make_tracker
from .ui import CliParser, Dashboard, NullWriter, console, header, problem


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > 60:
        raise argparse.ArgumentTypeError("expected a number above 0 and at most 60")
    return number


def pin_number(value: str) -> int:
    pin = int(value)
    if not 0 <= pin <= 27:
        raise argparse.ArgumentTypeError("use a BCM GPIO number from 0 to 27")
    return pin


def valid_network_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
        return bool(parts.hostname) and (parts.port is None or 1 <= parts.port <= 65535)
    except ValueError:
        return False


def parser() -> argparse.ArgumentParser:
    top = CliParser(prog="framewire", description="Camera in. Control events out.")
    top.add_argument("--debug", action="store_true", help="Show a traceback for unexpected errors")
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
    live.add_argument(
        "--json", action="store_true", help="Write JSONL even in an interactive terminal"
    )
    live.add_argument(
        "--plain", action="store_true", help="Use simple status lines instead of a live panel"
    )

    check = commands.add_parser("doctor", help="Check dependencies and read one frame")
    check.add_argument("--source", default="auto", metavar="SOURCE")
    check.add_argument("--snapshot", action="store_true")
    check.add_argument("--json", action="store_true", help="Machine-readable result on stdout")

    demo = commands.add_parser("demo", help="See the event stream without a camera or GPIO")
    demo.add_argument("--seconds", type=positive_float, default=3)
    demo.add_argument(
        "--json", action="store_true", help="Write JSONL even in an interactive terminal"
    )
    demo.add_argument("--plain", action="store_true", help="Use simple status lines")

    setup = commands.add_parser("setup", help="Prepare adapters, optional packages and models")
    setup.add_argument(
        "names",
        nargs="*",
        metavar="ADAPTER",
        help="hands, voice, lips, actions or your installed adapter (default: hands actions)",
    )
    setup.add_argument(
        "--no-install", action="store_true", help="Skip optional package installation"
    )
    setup.add_argument(
        "--no-download", action="store_true", help="Defer model downloads until first run"
    )

    adapters = commands.add_parser("adapters", help="Install and manage local adapters")
    adapter_actions = adapters.add_subparsers(dest="adapter_action", required=True)
    for action in ("path", "list"):
        adapter_actions.add_parser(action)
    for action in ("add", "new", "enable", "disable", "remove"):
        command = adapter_actions.add_parser(action)
        command.add_argument("name")
        if action == "add":
            command.add_argument(
                "--no-install",
                action="store_true",
                help="Copy the example without installing packages",
            )
            command.add_argument(
                "--no-download", action="store_true", help="Defer its model download"
            )

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


def overview() -> None:
    header()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="accent", no_wrap=True)
    table.add_column()
    table.add_row("framewire demo", "Try it now · no hardware needed")
    table.add_row("framewire doctor", "Find a camera and check your setup")
    table.add_row("framewire run", "Follow a face; Ctrl+C stops")
    table.add_row("framewire setup hands", "Add gestures, packages and a cached model")
    table.add_row("framewire setup voice", "Add local spoken commands")
    table.add_row("framewire gestures record lamp.on", "Teach your own hand sign")
    table.add_row('framewire phrases add "licht an" lamp.on', "Map a phrase to an action")
    console.print(table)
    console.print("\n[muted]No config required. Add --help to any command for options.[/]")
    console.print("[muted]Your adapters and samples: [/]")
    console.print(Text(str(adapter_dir().parent), style="muted"), soft_wrap=True)
    console.print()


def doctor(args: argparse.Namespace) -> int:
    report: dict = {
        "framewire": __version__,
        "opencv": None,
        "gpiozero_installed": find_spec("gpiozero") is not None,
        "picamera2_installed": find_spec("picamera2") is not None,
        "mediapipe_installed": find_spec("mediapipe") is not None,
        "voice_installed": find_spec("faster_whisper") is not None
        and find_spec("sounddevice") is not None,
        "adapters": [name for name, enabled in list_installed() if enabled],
        "source": safe_label(args.source),
        "camera_ready": False,
        "data_ready": True,
    }
    try:
        load_samples(sample_path())
        load_gesture_samples(gesture_sample_path())
        load_preferences()
    except (ValueError, OSError) as exc:
        report.update(data_ready=False, data_error=str(exc))
    source = None
    try:
        import cv2

        report["opencv"] = cv2.__version__
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
        header("Check the camera, recognition support and your command files.")
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column(style="muted")
        table.add_column()
        table.add_row(
            "Camera",
            Text(
                f"Ready · {report['source']} · {report.get('frame_size', '')}"
                if report["camera_ready"]
                else "No frame available",
                style="good" if report["camera_ready"] else "yellow",
            ),
        )
        table.add_row("OpenCV", str(report["opencv"] or "Missing · rerun the installer"))
        table.add_row(
            "Hand / lip support",
            "Installed" if report["mediapipe_installed"] else "Optional · framewire setup hands",
        )
        table.add_row(
            "Voice support",
            "Installed" if report["voice_installed"] else "Optional · framewire setup voice",
        )
        table.add_row("Personal commands", "Valid" if report["data_ready"] else "Needs attention")
        table.add_row(
            "Enabled adapters", Text(", ".join(report["adapters"]) or "None · framewire setup")
        )
        console.print(table)
        if not report["data_ready"]:
            problem(report["data_error"])
        if not report["camera_ready"]:
            problem(
                report.get("reason", "No camera is ready"),
                "Connect a camera or try framewire doctor --source 1.\nframewire demo works without hardware.",
            )
        console.print()
    return 0 if report["camera_ready"] and report["data_ready"] else 1


def demo(args: argparse.Namespace) -> int:
    json_output = args.json or not sys.stdout.isatty()
    display = Dashboard(
        source="demo",
        tracker="face",
        outputs="Observe only · no hardware",
        enabled=console.is_terminal and not json_output and not args.plain,
    )
    bus = EventBus(stream=None if json_output else NullWriter())
    bus.subscribe(display.on_event)
    with display:
        display.status("Simulating a moving target")
        try:
            bus.emit("source.connected", "demo")
            count = max(3, int(args.seconds * 12))
            for index in range(count):
                x = round(0.15 + 0.7 * index / max(1, count - 1), 4)
                target = Target(x, 0.5, 0.04, "demo")
                bus.emit("target.acquired" if index == 0 else "target.moved", "demo", target)
                display.frame()
                time.sleep(args.seconds / count)
            bus.emit("target.lost", "demo")
        except KeyboardInterrupt:
            bus.emit("target.lost", "demo", detail="interrupted")
        finally:
            bus.close()
    return 0


def live(args: argparse.Namespace, top: argparse.ArgumentParser) -> int:
    if "://" in args.source and not valid_network_url(args.source):
        top.error("source URL needs a hostname and a valid port")
    if not (
        args.source in {"auto", "picamera"}
        or args.source.isdecimal()
        or args.source.startswith(("rtsp://", "rtsps://", "http://", "https://"))
    ):
        top.error("source must be auto, a camera number, picamera or a video URL")
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
    if args.webhook and not valid_network_url(args.webhook):
        top.error("webhook URL needs a hostname and a valid port")
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

    json_output = args.json or not sys.stdout.isatty()
    outputs = []
    if not args.dry_run:
        if args.servo_pin is not None:
            outputs.append(f"Pan servo BCM {args.servo_pin}")
        if args.gpio_pin is not None:
            outputs.append(f"GPIO BCM {args.gpio_pin}")
    if args.webhook:
        outputs.append(f"Webhook {safe_label(args.webhook)}")
    display = Dashboard(
        source=safe_label(args.source),
        tracker=args.track,
        outputs=", ".join(outputs) or "Observe only · no built-in pins",
        enabled=console.is_terminal and not json_output and not args.plain,
    )
    status = display.status

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
        problem(
            f"Output setup failed: {exc}",
            "Check GPIO support and pin permissions, or add --dry-run.",
        )
        return 2
    bus = EventBus(
        stream=None if json_output else NullWriter(), webhook=args.webhook, token=token, warn=status
    )
    bus.subscribe(display.on_event)
    adapters = (
        None
        if args.no_adapters
        else AdapterManager(bus, selected=set(args.adapter) if args.adapter else None, warn=status)
    )
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        with display:
            status("Starting · Ctrl+C stops" + (" · JSONL on stdout" if json_output else ""))
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
                on_frame=display.frame,
            )
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


def setup_command(args: argparse.Namespace) -> int:
    names = args.names or ["hands", "actions"]
    header("Prepare your adapters, recognition packages and local models.")
    with console.status("Preparing Framewire…", spinner="dots", spinner_style="accent"):
        prepare(
            names,
            install=not args.no_install,
            download=not args.no_download,
            status=lambda message: console.print(Text(message)),
        )
    console.print("\n[good]Ready.[/] Run [bold]framewire run --track none[/]")
    console.print("[muted]Your existing adapter code and command samples are kept.[/]\n")
    return 0


def adapter_command(args: argparse.Namespace) -> int:
    action = args.adapter_action
    if action == "path":
        print(adapter_dir())
        return 0
    if action == "list":
        console.print(Text(f"Adapter folder: {adapter_dir()}", style="muted"), soft_wrap=True)
        table = Table(title="Framewire adapters")
        table.add_column("Name")
        table.add_column("State")
        for name, enabled in list_installed():
            table.add_row(name, "enabled" if enabled else "disabled")
        console.print(table)
        if not list_installed():
            console.print("[accent]Get started:[/] framewire setup hands")
        console.print(f"[dim]Copyable examples: {', '.join(EXAMPLES)}[/]")
        return 0
    try:
        if action == "add":
            return setup_command(
                argparse.Namespace(
                    names=[args.name], no_install=args.no_install, no_download=args.no_download
                )
            )
        elif action == "new":
            console.print(
                Text(f"Created {create_adapter(args.name)}", style="good"), soft_wrap=True
            )
        elif action == "enable":
            console.print(
                Text(f"Enabled {set_enabled(args.name, True)}", style="good"), soft_wrap=True
            )
        elif action == "disable":
            console.print(
                Text(f"Disabled {set_enabled(args.name, False)}", style="yellow"), soft_wrap=True
            )
        elif action == "remove":
            remove_adapter(args.name)
            console.print(f"Removed {args.name} code and config; recorded data was kept.")
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        problem(str(exc))
        return 2
    return 0


def lips_command(args: argparse.Namespace) -> int:
    if args.lip_action == "list":
        samples = load_samples(sample_path())
        table = Table(title="Visual commands")
        table.add_column("Label")
        table.add_column("Samples", justify="right")
        for label, examples in sorted(samples.items()):
            table.add_row(Text(label), str(len(examples)))
        console.print(table)
        if not samples:
            console.print("[accent]Teach a command:[/] framewire lips record lamp.on")
        return 0
    if args.lip_action == "remove":
        if not remove_lip_label(args.label):
            problem(f"No samples for {args.label}")
            return 2
        console.print(Text(f"Removed visual command {args.label}"))
        return 0
    try:
        count = record_lips(
            args.label,
            source_spec=args.source,
            seconds=args.seconds,
            repeats=args.repeats,
            status=lambda message: console.print(Text(message)),
        )
    except (Exception, KeyboardInterrupt) as exc:
        problem(f"Recording stopped: {type(exc).__name__}: {exc}")
        return 2
    console.print(
        Text(
            f"Recorded {args.label} ({count} total samples).\n"
            "Use framewire setup lips, then framewire run --track none.",
            style="good",
        )
    )
    return 0


def gestures_command(args: argparse.Namespace) -> int:
    if args.gesture_action == "list":
        samples = load_gesture_samples(gesture_sample_path())
        table = Table(title="Personal hand signs")
        table.add_column("Label")
        table.add_column("Samples", justify="right")
        for label, examples in sorted(samples.items()):
            table.add_row(Text(label), str(len(examples)))
        console.print(table)
        if not samples:
            console.print("[accent]Teach a sign:[/] framewire gestures record lamp.on")
        return 0
    if args.gesture_action == "remove":
        if not remove_gesture_label(args.label):
            problem(f"No samples for {args.label}")
            return 2
        console.print(Text(f"Removed hand sign {args.label}"))
        return 0
    try:
        count = record_gesture(
            args.label,
            source_spec=args.source,
            seconds=args.seconds,
            repeats=args.repeats,
            status=lambda message: console.print(Text(message)),
        )
    except (Exception, KeyboardInterrupt) as exc:
        problem(f"Recording stopped: {type(exc).__name__}: {exc}")
        return 2
    console.print(
        Text(
            f"Recorded {args.label} ({count} total samples).\n"
            "Use framewire setup hands, then framewire run --track none.",
            style="good",
        )
    )
    return 0


def phrases_command(args: argparse.Namespace) -> int:
    try:
        if args.phrase_action == "add":
            add_phrase(args.phrase, args.action)
            console.print(Text(f"Mapped {args.phrase} → {args.action}", style="good"))
        elif args.phrase_action == "remove":
            if not remove_phrase(args.phrase):
                problem(f"No mapping for {args.phrase}")
                return 2
            console.print(Text(f"Removed phrase {args.phrase}"))
        elif args.phrase_action == "language":
            set_language(args.code)
            console.print(Text(f"Language: {args.code}", style="good"))
        else:
            preferences = load_preferences()
            console.print(
                Text(f"Language: {preferences['language'] or 'voice.toml / auto'}", style="muted")
            )
            table = Table(title="Spoken phrases")
            table.add_column("Phrase")
            table.add_column("Action")
            for phrase, action in sorted(preferences["commands"].items()):
                table.add_row(Text(phrase), Text(action))
            console.print(table)
            if not preferences["commands"]:
                console.print('[accent]Add a phrase:[/] framewire phrases add "licht an" lamp.on')
    except (ValueError, OSError) as exc:
        problem(str(exc))
        return 2
    return 0


def dispatch(args: argparse.Namespace, top: argparse.ArgumentParser) -> int:
    if args.command is None:
        overview()
        return 0
    if args.command == "doctor":
        return doctor(args)
    if args.command == "demo":
        return demo(args)
    if args.command == "run":
        return live(args, top)
    if args.command == "adapters":
        return adapter_command(args)
    if args.command == "setup":
        return setup_command(args)
    if args.command == "lips":
        return lips_command(args)
    if args.command == "gestures":
        return gestures_command(args)
    if args.command == "phrases":
        return phrases_command(args)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = None
    try:
        top = parser()
        args = top.parse_args(argv)
        return dispatch(args, top)
    except KeyboardInterrupt:
        console.print("[muted]Stopped.[/]")
        return 130
    except BrokenPipeError:
        # Avoid an extra interpreter-shutdown traceback after a consumer closes its pipe.
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - retained until interpreter shutdown
        return 0
    except Exception as exc:
        if (args is not None and args.debug) or os.environ.get("FRAMEWIRE_DEBUG") == "1":
            traceback.print_exc()
        else:
            message = (
                str(exc)
                if isinstance(exc, (ValueError, OSError, RuntimeError, ImportError))
                else f"Unexpected {type(exc).__name__}"
            )
            problem(
                message,
                "Use framewire doctor to check your setup.\nUse framewire --debug COMMAND for technical details.",
            )
        return 2
