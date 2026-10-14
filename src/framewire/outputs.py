"""Event sinks and optional physical outputs."""

from __future__ import annotations

import json
import queue
import sys
import threading
from collections.abc import Callable
from urllib.request import Request, urlopen

from .model import Event, Target
from .sources import safe_label


class EventBus:
    def __init__(
        self,
        *,
        stream=None,
        webhook: str | None = None,
        token: str | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.webhook = webhook
        self.token = token
        self.warn = warn or (lambda message: None)
        self.sequence = 0
        self.pending: queue.Queue[dict | None] = queue.Queue(maxsize=64)
        self.worker: threading.Thread | None = None
        self._queue_warned = False
        if webhook:
            self.worker = threading.Thread(
                target=self._send_loop, name="framewire-webhook", daemon=True
            )
            self.worker.start()

    def emit(
        self, kind: str, source: str, target: Target | None = None, detail: str | None = None
    ) -> Event:
        self.sequence += 1
        event = Event(self.sequence, kind, source, target, detail)
        payload = event.to_dict()
        self.stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.stream.flush()
        if self.webhook:
            try:
                self.pending.put_nowait(payload)
                self._queue_warned = False
            except queue.Full:
                if kind != "target.moved":
                    try:
                        self.pending.get_nowait()
                        self.pending.put_nowait(payload)
                    except queue.Empty:
                        pass
                if not self._queue_warned:
                    self.warn(
                        "Webhook is falling behind; some events were dropped (stdout remains complete)."
                    )
                    self._queue_warned = True
        return event

    def _send_loop(self) -> None:
        assert self.webhook is not None
        while True:
            payload = self.pending.get()
            if payload is None:
                return
            headers = {"Content-Type": "application/json", "User-Agent": "framewire/0.1"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            request = Request(
                self.webhook, data=json.dumps(payload).encode(), headers=headers, method="POST"
            )
            try:
                with urlopen(request, timeout=3) as response:
                    response.read(1024)
            except Exception as exc:
                self.warn(f"Webhook {safe_label(self.webhook)} failed: {type(exc).__name__}")

    def close(self) -> None:
        if self.worker:
            try:
                self.pending.put_nowait(None)
            except queue.Full:
                pass
            self.worker.join(timeout=1)


class ControlHub:
    """GPIO state follows target presence; servo signal is released when tracking stops."""

    def __init__(
        self,
        *,
        servo_pin: int | None = None,
        gpio_pin: int | None = None,
        max_angle: float = 60,
        invert_pan: bool = False,
        deadzone: float = 0.04,
        dry_run: bool = False,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.warn = warn or (lambda message: None)
        self.max_angle = max_angle
        self.invert_pan = invert_pan
        self.deadzone = deadzone
        self.servo = None
        self.gpio = None
        self.last_angle: float | None = None
        self.active = False
        if dry_run or (servo_pin is None and gpio_pin is None):
            return
        try:
            from gpiozero import AngularServo, DigitalOutputDevice
        except ImportError as exc:
            raise RuntimeError(
                "GPIO support is missing. Install with: pip install '.[pi]'"
            ) from exc
        try:
            if servo_pin is not None:
                self.servo = AngularServo(
                    servo_pin, min_angle=-90, max_angle=90, initial_angle=None
                )
            if gpio_pin is not None:
                self.gpio = DigitalOutputDevice(gpio_pin, initial_value=False)
        except Exception:
            self.close()
            raise

    def update(self, target: Target) -> None:
        if self.gpio is not None and not self.active:
            try:
                self.gpio.on()
            except Exception as exc:
                self.warn(f"GPIO output disabled after {type(exc).__name__}")
                self._close_device(self.gpio)
                self.gpio = None
        self.active = True
        if self.servo is None:
            return
        offset = target.x - 0.5
        if abs(offset) < self.deadzone:
            offset = 0
        angle = round(((-1 if self.invert_pan else 1) * offset * 2 * self.max_angle), 1)
        if self.last_angle is not None and abs(angle - self.last_angle) < 1:
            return
        try:
            self.servo.angle = angle
            self.last_angle = angle
        except Exception as exc:
            self.warn(f"Servo output disabled after {type(exc).__name__}")
            self._close_device(self.servo)
            self.servo = None

    def release(self) -> None:
        self.active = False
        if self.gpio is not None:
            try:
                self.gpio.off()
            except Exception as exc:
                self.warn(f"GPIO off failed: {type(exc).__name__}")
                self._close_device(self.gpio)
                self.gpio = None
        if self.servo is not None:
            try:
                self.servo.angle = None
                self.last_angle = None
            except Exception as exc:
                self.warn(f"Servo release failed: {type(exc).__name__}")
                self._close_device(self.servo)
                self.servo = None

    def _close_device(self, device) -> None:
        try:
            device.close()
        except Exception as exc:
            self.warn(f"Output cleanup failed: {type(exc).__name__}")

    def close(self) -> None:
        self.release()
        for device in (self.gpio, self.servo):
            if device is not None:
                self._close_device(device)
        self.gpio = None
        self.servo = None
