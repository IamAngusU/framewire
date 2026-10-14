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
from .engine import RunOptions, run
from .model import Target
from .outputs import ControlHub, EventBus
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
    live.add_argument("--track", choices=("face", "motion"), default="face")
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
    live.add_argument("--max-frames", type=int, metavar="N", help="Stop after N received frames")

    check = commands.add_parser("doctor", help="Check dependencies and read one frame")
    check.add_argument("--source", default="auto", metavar="SOURCE")
    check.add_argument("--snapshot", action="store_true")
    check.add_argument("--json", action="store_true", help="Machine-readable result on stdout")

    demo = commands.add_parser("demo", help="See the event stream without a camera or GPIO")
    demo.add_argument("--seconds", type=positive_float, default=3)
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
    console.print(table)
    console.print("[dim]Add --help to any command for all options.[/]")


def doctor(args: argparse.Namespace) -> int:
    report: dict = {
        "framewire": __version__,
        "opencv": cv2.__version__,
        "gpiozero_installed": find_spec("gpiozero") is not None,
        "picamera2_installed": find_spec("picamera2") is not None,
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
            stop=stop,
            status=status,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
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
    return 2
