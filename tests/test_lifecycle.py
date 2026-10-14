import io

import numpy as np

from framewire.engine import RunOptions, TargetState, run
from framewire.model import Target
from framewire.outputs import ControlHub, EventBus
from framewire.sources import CameraError, safe_error, safe_label
from framewire.tracking import make_tracker


def point(x):
    return Target(x, 0.5, 0.05, "face")


def test_target_needs_two_frames_and_expires_after_grace_period():
    state = TargetState(lost_after=0.8)
    assert state.step(point(0.2), 0)[0] is None
    assert state.step(point(0.2), 0.1)[0] == "target.acquired"
    assert state.step(None, 0.5) == (None, point(0.2))
    assert state.step(None, 1.0) == ("target.lost", None)
    assert state.step(None, 2.0) == (None, None)


def test_secret_url_parts_never_enter_source_label():
    assert safe_label("rtsp://alice:secret@example.org:554/live?token=private") == (
        "rtsp://example.org:554"
    )
    secret = "rtsp://alice:secret@example.org/live?token=private"
    assert safe_error(RuntimeError(f"failed {secret}"), secret) == "RuntimeError"


class FakeStop:
    def is_set(self):
        return False

    def wait(self, seconds):
        return False


class FakeSource:
    label = "camera:0"

    def __init__(self, good_reads):
        self.good_reads = good_reads
        self.closed = False

    def read(self):
        if self.good_reads <= 0:
            raise CameraError("cable unplugged")
        self.good_reads -= 1
        return np.zeros((20, 20, 3), dtype=np.uint8)

    def close(self):
        self.closed = True


class FakeTracker:
    def detect(self, frame):
        return point(0.4)


def test_disconnect_releases_outputs_emits_loss_and_recovers():
    first, second = FakeSource(2), FakeSource(2)
    sources = iter([first, second])
    output = io.StringIO()
    bus = EventBus(stream=output)
    control = ControlHub()
    run(
        RunOptions(fps=60, max_frames=4),
        bus,
        control,
        source_factory=lambda: next(sources),
        tracker_factory=lambda kind: FakeTracker(),
        stop=FakeStop(),
    )
    import json

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["type"] for event in events] == [
        "source.connected",
        "target.acquired",
        "target.lost",
        "source.lost",
        "source.connected",
        "target.acquired",
        "target.lost",
    ]
    assert first.closed and second.closed
    assert not control.active
    assert [event["sequence"] for event in events] == list(range(1, 8))


def test_servo_and_gpio_release_on_loss():
    class Servo:
        angle = None

        def close(self):
            pass

    class Gpio:
        active = False

        def on(self):
            self.active = True

        def off(self):
            self.active = False

        def close(self):
            pass

    control = ControlHub()
    control.servo = Servo()
    control.gpio = Gpio()
    control.update(point(1.0))
    assert control.servo.angle == 60
    assert control.gpio.active
    control.release()
    assert control.servo.angle is None
    assert not control.gpio.active


def test_broken_output_pipe_still_releases_active_gpio():
    class BrokenStream:
        writes = 0

        def write(self, value):
            self.writes += 1
            if self.writes >= 3:
                raise BrokenPipeError()

        def flush(self):
            pass

    class Gpio:
        active = False
        closed = False

        def on(self):
            self.active = True

        def off(self):
            self.active = False

        def close(self):
            self.closed = True

    gpio = Gpio()
    control = ControlHub()
    control.gpio = gpio
    bus = EventBus(stream=BrokenStream())
    run(
        RunOptions(fps=60, max_frames=3),
        bus,
        control,
        source_factory=lambda: FakeSource(3),
        tracker_factory=lambda kind: FakeTracker(),
        stop=FakeStop(),
    )
    assert not gpio.active
    assert gpio.closed


def test_adapter_only_mode_skips_builtin_tracking_and_receives_frames():
    class Adapter:
        frames = 0
        closed = False

        def start(self):
            pass

        def on_frame(self, frame, now):
            self.frames += 1

        def close(self):
            self.closed = True

    adapter = Adapter()
    source = FakeSource(2)
    output = io.StringIO()
    bus = EventBus(stream=output)
    run(
        RunOptions(track="none", fps=60, max_frames=2),
        bus,
        ControlHub(),
        source_factory=lambda: source,
        adapters=adapter,
        stop=FakeStop(),
    )
    assert make_tracker("none").detect(None) is None
    assert adapter.frames == 2 and adapter.closed
    assert source.closed
    assert '"type":"target.' not in output.getvalue()
