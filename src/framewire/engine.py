"""Reconnect loop and target lifecycle. No source or output owns the process."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .adapters import AdapterManager
from .model import Target
from .outputs import ControlHub, EventBus
from .sources import FrameSource, safe_error, safe_label
from .tracking import make_tracker


@dataclass
class RunOptions:
    source: str = "auto"
    snapshot: bool = False
    track: str = "face"
    fps: float = 12
    lost_after: float = 0.8
    max_frames: int | None = None


class TargetState:
    def __init__(self, *, lost_after: float = 0.8) -> None:
        self.lost_after = lost_after
        self.current: Target | None = None
        self.last_seen = 0.0
        self.last_emit = 0.0
        self.last_sent: Target | None = None
        self.candidate = 0

    def step(self, detected: Target | None, now: float) -> tuple[str | None, Target | None]:
        if detected is None:
            self.candidate = 0
            if self.current is not None and now - self.last_seen >= self.lost_after:
                self.current = None
                self.last_sent = None
                return "target.lost", None
            return None, self.current

        self.last_seen = now
        if self.current is None:
            self.candidate += 1
            if self.candidate < 2:
                return None, None
            self.current = detected
            self.last_sent = detected
            self.last_emit = now
            return "target.acquired", detected

        self.candidate = 0
        alpha = 0.35
        self.current = Target(
            round(self.current.x * (1 - alpha) + detected.x * alpha, 4),
            round(self.current.y * (1 - alpha) + detected.y * alpha, 4),
            detected.size,
            detected.kind,
        )
        prior = self.last_sent or self.current
        moved = abs(self.current.x - prior.x) >= 0.025 or abs(self.current.y - prior.y) >= 0.025
        if moved and now - self.last_emit >= 0.2:
            self.last_sent = self.current
            self.last_emit = now
            return "target.moved", self.current
        return None, self.current

    def reset(self) -> bool:
        was_active = self.current is not None
        self.current = None
        self.last_sent = None
        self.candidate = 0
        return was_active


def run(
    options: RunOptions,
    bus: EventBus,
    control: ControlHub,
    *,
    source_factory: Callable[[], FrameSource],
    tracker_factory: Callable[[str], object] = make_tracker,
    adapters: AdapterManager | None = None,
    stop: threading.Event | None = None,
    status: Callable[[str], None] | None = None,
) -> None:
    stop = stop or threading.Event()
    status = status or (lambda message: None)
    source_label = safe_label(options.source)
    frames = 0
    retry = 1.0
    state: TargetState | None = None
    try:
        if adapters is not None:
            adapters.start()
        while not stop.is_set():
            source: FrameSource | None = None
            state = TargetState(lost_after=options.lost_after)
            try:
                source = source_factory()
                source_label = source.label
                tracker = tracker_factory(options.track)
                frame = source.read()
                bus.emit("source.connected", source_label)
                status(f"Connected to {source_label}")
                retry = 1.0
                while not stop.is_set():
                    started = time.monotonic()
                    detected = tracker.detect(frame)
                    kind, target = state.step(detected, started)
                    if adapters is not None:
                        adapters.on_frame(frame, started)
                    if kind:
                        bus.emit(kind, source_label, target)
                    if target is not None:
                        control.update(target)
                    else:
                        control.release()
                    frames += 1
                    if options.max_frames is not None and frames >= options.max_frames:
                        return
                    remaining = 1 / options.fps - (time.monotonic() - started)
                    if remaining > 0 and stop.wait(remaining):
                        return
                    frame = source.read()
            except KeyboardInterrupt:
                return
            except BrokenPipeError:
                return
            except Exception as exc:
                control.release()
                if state.reset():
                    bus.emit("target.lost", source_label, detail="source unavailable")
                detail = safe_error(exc, options.source)
                bus.emit("source.lost", source_label, detail=detail)
                status(f"{detail}. Retry in {retry:g}s")
            finally:
                control.release()
                if source is not None:
                    try:
                        source.close()
                    except Exception as exc:
                        status(f"Camera cleanup: {type(exc).__name__}")
            if stop.wait(retry):
                return
            retry = min(retry * 2, 15)
    finally:
        try:
            if state is not None and state.reset():
                try:
                    bus.emit("target.lost", source_label, detail="stopped")
                except BrokenPipeError:
                    pass
        finally:
            control.close()
            if adapters is not None:
                adapters.close()
            bus.close()
