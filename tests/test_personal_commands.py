import sys
import threading
from types import SimpleNamespace

from framewire.adapter_api import AdapterContext
from framewire.examples.adapters import hands, voice
from framewire.handmatch import best_pose, load_samples, pose_distance, pose_feature, save_samples
from framewire.phrases import (
    add_phrase,
    load_preferences,
    remove_phrase,
    set_language,
)


def test_hand_pose_normalizes_scale_orientation_and_left_hand():
    points = [SimpleNamespace(x=i * 0.02, y=-i * 0.03) for i in range(21)]
    points[0] = SimpleNamespace(x=0.2, y=0.7)
    points[9] = SimpleNamespace(x=0.2, y=0.5)
    right = pose_feature(points, "Right")
    mirrored = [SimpleNamespace(x=1 - p.x, y=p.y) for p in points]
    left = pose_feature(mirrored, "Left")
    assert pose_distance(right, left) < 0.001
    assert best_pose(right, {"lamp.on": [right]}) == ("lamp.on", 0.0)
    assert best_pose(right, {"a": [right], "b": [right]}) is None


def test_recorded_hand_pose_triggers_custom_event(tmp_path, monkeypatch):
    points = [SimpleNamespace(x=0.2 + i * 0.01, y=0.7 - i * 0.01) for i in range(21)]
    pose = pose_feature(points, "Right")
    path = tmp_path / "gestures.json"
    save_samples(path, {"lamp.on": [pose]})
    assert load_samples(path) == {"lamp.on": [pose]}

    class FakeFeatures:
        def __init__(self, model_dir):
            pass

        def read(self, frame, now):
            return SimpleNamespace(
                hand_landmarks=[points],
                handedness=[[SimpleNamespace(category_name="Right")]],
                gestures=[[]],
            )

        def close(self):
            pass

    monkeypatch.setattr(hands, "HandFeatures", FakeFeatures)
    events = []
    context = AdapterContext("hands", tmp_path, {}, lambda name, signal, data: events.append(data))
    adapter = hands.create(context)
    adapter.start()
    for now in (1.0, 1.1, 1.2):
        adapter.on_frame(None, now)
    adapter.close()
    gestures = [event for event in events if event["signal"] == "hand.gesture"]
    assert len(gestures) == 1
    assert gestures[0]["value"] == "lamp.on"
    assert gestures[0]["custom"] is True
    assert gestures[0]["phase"] == "start"


def test_phrase_cli_data_manages_unicode_and_language(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWIRE_HOME", str(tmp_path))
    add_phrase("Licht an!", "lamp.on")
    add_phrase("LICHT AN", "lamp.start")
    set_language("de")
    saved = load_preferences()
    assert saved == {"language": "de", "commands": {"LICHT AN": "lamp.start"}}
    assert remove_phrase("licht, an") is True
    assert remove_phrase("licht an") is False


def test_voice_adapter_waits_after_missing_microphone(tmp_path, monkeypatch):
    def missing_input(**kwargs):
        raise OSError("no microphone")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=missing_input))
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *args, **kwargs: object()),
    )
    seen = threading.Event()
    events = []

    def publish(name, signal, data):
        events.append(data)
        seen.set()

    context = AdapterContext("voice", tmp_path, {}, publish)
    adapter = voice.create(context)
    adapter.start()
    assert seen.wait(timeout=2)
    assert adapter.thread.is_alive()
    adapter.close()
    assert events[0]["signal"] == "speech.error"
    assert events[0]["retry_in"] == 1.0
