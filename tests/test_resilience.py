import argparse
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from framewire import setup as runtime_setup
from framewire.adapter_api import AdapterContext
from framewire.adapters import AdapterManager, LoadedAdapter, create_adapter
from framewire.cli import main, positive_float, valid_network_url
from framewire.handmatch import load_samples as hand_samples
from framewire.lipmatch import load_samples as lip_samples
from framewire.outputs import EventBus
from framewire.setup import prepare
from framewire.sources import CameraError, IsolatedSource


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "61"])
def test_nonfinite_and_out_of_range_cli_numbers_are_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        positive_float(value)


def test_invalid_network_configuration_fails_before_reconnecting():
    assert not valid_network_url("http://")
    assert not valid_network_url("rtsp://camera:99999/live")
    with pytest.raises(SystemExit) as stopped:
        main(["run", "--source", "http://", "--no-adapters"])
    assert stopped.value.code == 2


def test_damaged_command_file_gets_a_repair_hint_and_is_preserved(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    path = tmp_path / "data" / "lips" / "samples.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    assert main(["lips", "list"]) == 2
    output = capsys.readouterr()
    assert "Restore a valid JSON" in output.err
    assert "Traceback" not in output.err
    assert path.read_text() == "{broken"


@pytest.mark.parametrize(
    "loader,schema,invalid",
    [
        (hand_samples, "framewire.hands.v1", {"a": [[1, 2]]}),
        (lip_samples, "framewire.lips.v1", {"a": [[[float("nan"), 0]] * 8]}),
    ],
)
def test_recognition_rejects_corrupted_sample_shapes(tmp_path, loader, schema, invalid):
    path = tmp_path / "samples.json"
    path.write_text(json.dumps({"schema": schema, "samples": invalid}), encoding="utf-8-sig")
    with pytest.raises(ValueError, match="Invalid"):
        loader(path)


def test_concurrent_events_keep_complete_lines_and_sequence_order():
    output = io.StringIO()
    bus = EventBus(stream=output)
    threads = [
        threading.Thread(target=lambda: [bus.emit("test", "worker") for _ in range(50)])
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["sequence"] for event in events] == list(range(1, 101))


def test_audio_event_and_frame_hook_do_not_invert_locks(tmp_path):
    held = threading.Event()
    audio_written = threading.Event()
    bus = EventBus(stream=io.StringIO())
    bus.subscribe(lambda event: audio_written.set() if event["source"] == "audio" else None)
    manager = AdapterManager(bus, folder=tmp_path)
    context = AdapterContext("custom", tmp_path, {}, manager._publish)

    class Hook:
        def on_frame(self, frame, now):
            held.set()
            assert audio_written.wait(timeout=2)
            context.emit("frame.signal", "ok")

        def on_event(self, event):
            pass

    manager.loaded["custom"] = LoadedAdapter("custom", Hook(), context)
    camera = threading.Thread(target=lambda: manager.on_frame(None, 1), daemon=True)
    camera.start()
    assert held.wait(timeout=2)
    audio = threading.Thread(target=lambda: bus.emit("adapter.signal", "audio"), daemon=True)
    audio.start()
    for thread in (camera, audio):
        thread.join(timeout=2)
        assert not thread.is_alive()
    manager.close()


def test_help_does_not_require_opencv(monkeypatch, capsys):
    import builtins

    original = builtins.__import__

    def without_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("OpenCV is unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_cv2)
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    assert "Framewire" in capsys.readouterr().err


def test_user_text_is_rendered_as_literal_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    assert main(["phrases", "add", "[lamp] on", "lamp[on]"]) == 0
    assert "lamp[on]" in capsys.readouterr().err


def test_recursive_adapter_signals_disable_the_adapter_without_killing_events(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    folder = tmp_path / "adapters"
    path = create_adapter("cycle", folder)
    path.write_text(
        "def create(context):\n"
        "    class Cycle:\n"
        "        def on_event(self, event):\n"
        "            if event['type'] == 'adapter.signal':\n"
        "                context.emit('loop.signal', 'again')\n"
        "    return Cycle()\n",
        encoding="utf-8",
    )
    output = io.StringIO()
    bus = EventBus(stream=output)
    manager = AdapterManager(bus, folder=folder)
    manager.start()
    bus.emit("adapter.signal", "test", data={"signal": "start"})
    bus.emit("source.connected", "camera:0")
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(events) < 25
    assert any(event["type"] == "adapter.error" for event in events)
    assert events[-1]["type"] == "source.connected"
    assert not manager.loaded
    manager.close()


def test_adapters_cannot_spoof_reserved_fields_or_emit_nonfinite_data(tmp_path):
    context = AdapterContext("custom", tmp_path, {}, lambda *args: None)
    with pytest.raises(ValueError, match="reserved"):
        context.emit("test.signal", adapter="other")
    with pytest.raises(ValueError):
        context.emit("test.signal", x=float("nan"))


def test_setup_keeps_edited_adapter_code(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    prepare(["actions"], install=False, download=False, status=lambda _: None)
    path = tmp_path / "adapters" / "actions.py"
    path.write_text("# my edited adapter", encoding="utf-8")
    prepare(["actions"], install=False, download=False, status=lambda _: None)
    assert path.read_text() == "# my edited adapter"


def test_custom_adapter_requirements_are_kept_for_updates(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env"))
    path = create_adapter("my_lamp")
    path.with_suffix(".toml").write_text(
        'requirements = ["gpiozero>=2,<3"]\n', encoding="utf-8-sig"
    )
    calls = []

    def installed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime_setup, "find_uv", lambda: "uv")
    monkeypatch.setattr(runtime_setup.subprocess, "run", installed)
    prepare(["my_lamp"], download=False, status=lambda _: None)
    assert "gpiozero>=2,<3" in calls[0]
    assert (tmp_path / "data" / "runtime-requirements.txt").read_text() == "gpiozero>=2,<3\n"
    assert path.exists()


def test_capture_timeout_stops_a_worker_blocked_on_a_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    requested = threading.Event()

    class StalledImage(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"incomplete")
            self.wfile.flush()
            requested.set()
            time.sleep(5)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), StalledImage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = IsolatedSource(
        f"http://127.0.0.1:{server.server_port}/image", snapshot=True, read_timeout=1.5
    )
    started = time.monotonic()
    try:
        with pytest.raises(CameraError, match="Timed out"):
            source.read()
        assert requested.is_set()
    finally:
        source.close()
        server.shutdown()
        server.server_close()
    assert time.monotonic() - started < 4.5
    assert not source.process.is_alive()
