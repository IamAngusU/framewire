# Adapters: a file you can understand and replace

Run `framewire adapters path` to locate the active folder. Drop a `name.py` file there, then start `framewire run`. Enabled files load automatically. The four [shipped examples](../src/framewire/examples/adapters) are **copies** of ordinary adapter code. Setup prepares optional packages and models and keeps your existing files:

```sh
framewire setup hands voice lips actions
framewire adapters list
```

`framewire adapters new my_detector` creates a minimal file. `disable`, `enable` and `remove` manage its code. `remove` leaves model caches and recorded samples in the data folder. Use `--adapter NAME` to run only selected installed adapters or `--no-adapters` to run the camera core alone. Use `--track none` to skip the built-in face/motion tracker when your adapter handles all recognition. Set `FRAMEWIRE_HOME` to move the whole folder, including models and samples, to another location.

## The API

```python
def create(context):
    class MyAdapter:
        def start(self):
            pass  # optional: load a model or start a microphone worker

        def on_frame(self, frame, now):
            # frame: OpenCV BGR numpy array; now: monotonic seconds
            context.emit("my.signal", "hello", confidence=0.9)

        def on_event(self, event):
            if event["type"] == "adapter.signal":
                print(event["data"])

        def close(self):
            pass  # release workers, models and devices

    return MyAdapter()
```

All four methods are optional except `create`. `context.name` is the file's name, `context.settings` is its sibling TOML file parsed into a dict, and `context.data_dir` is a persistent per-adapter directory for models or calibration. `context.emit(signal, value, **metadata)` accepts JSON-safe values and can be called from a background thread. It rejects non-finite numbers and attempts to overwrite reserved fields. `context.number("threshold", 0.2, minimum=0.01, maximum=1)` reads and validates a numeric setting. Events use `framewire.event.v1` with `type = "adapter.signal"`, `source = "adapter:NAME"`, and a `data` object containing `adapter`, `signal`, `value` and any metadata.

The loader catches import, setup, frame-processing and event-handler failures. It emits `adapter.error`, closes that adapter and leaves the others running. Hooks are serialized across camera and audio event threads. Recursive signal chains are bounded. **An adapter is local Python code with your user's permissions.** Install only code you trust. Slow or blocking `on_frame` or `on_event` methods still slow the camera loop; put heavier inference or I/O in a worker thread and use a bounded queue. The [voice example](../src/framewire/examples/adapters/voice.py) shows one approach.

If your adapter needs an actuator SDK, put its package requirements in the sibling TOML file:

```toml
# my_lamp.toml
requirements = ["gpiozero>=2,<3"]
```

Then run `framewire setup my_lamp`. Framewire installs those requirements into its environment and remembers them for installer updates. The declaration is optional; adapters using only the standard library need no config file. Prebuilt packages are required by automatic setup. Package-manager failures keep your code and provide a local diagnostic log.

## Included examples

| File | Reads | Emits | Edit this to… |
| --- | --- | --- | --- |
| [`hands.py`](../src/framewire/examples/adapters/hands.py) | Camera frames and your samples | `hand.position`, `hand.gesture` | Record a static sign, tune thresholds or plug in your own model. |
| [`voice.py`](../src/framewire/examples/adapters/voice.py) | Local microphone | `speech.transcript`, `speech.command`, `speech.error` | Add phrase mappings and set a language/model in `voice.toml`. |
| [`lips.py`](../src/framewire/examples/adapters/lips.py) | Camera frames and your samples | `lip.command` | Change matching thresholds or replace the matcher with a visual-speech model. |
| [`actions.py`](../src/framewire/examples/adapters/actions.py) | Framewire events | `action.triggered` | Replace `print` with your actuator's API. Rules are in `actions.toml`. |

`hands` and `lips` need MediaPipe (`python -m pip install mediapipe`). Their official model bundles are fetched from pinned version URLs, verified against SHA-256 and cached under `context.data_dir/models`. Recognition is local and offline after the first successful download. The models are not committed to this repo. The hand model recognizes seven predefined signs. You can also teach a static personal sign:

```sh
framewire gestures record lamp.on --source 0 --seconds 2 --repeats 3
framewire gestures list
framewire gestures remove lamp.on
```

Hold one hand still and fully visible for each repeat. Only 21 normalized hand points per example are saved, never images. The example matches shape across left and right hands and emits the label as `hand.gesture` with `custom = true`. Recorded signs are approximate: similar poses can be confused, and moving or two-hand gestures need another adapter or a custom model. Tune `threshold` and `margin` in an optional sibling `hands.toml` to adjust recognition. [MediaPipe gesture task](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/python) · [Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python).

`voice` needs `faster-whisper` and `sounddevice`. It downloads the multilingual `tiny` model on first use into its data folder. Manage exact normalized phrase mappings and the recognition language from the terminal:

```sh
framewire phrases add "licht an" lamp.on
framewire phrases add "light off" lamp.off
framewire phrases language de
framewire phrases list
framewire phrases remove "light off"
```

The language setting applies when the voice adapter next starts. Use `auto` for automatic detection, or a fixed code such as `de`, `en`, `es` or `fr` for short commands. `voice.toml` contains starter mappings and model settings; CLI mappings override matching phrases. Raw audio stays in memory, but recognized **transcript text is an event** and goes to stdout and any configured webhook. [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

`lips` uses a small, personal template matcher over mouth landmarks. Record several silent examples of each short command:

```sh
framewire lips record lamp.on --source 0 --seconds 2 --repeats 3
framewire lips list
framewire lips remove lamp.on
```

The label is arbitrary Unicode text, so your commands need no preselected language. Recognition is experimental and limited to your trained short commands. It cannot transcribe an unseen sentence. Similar visemes may match the wrong label; use `lips.toml` to tighten `threshold` and `margin`, record more distinct examples, or implement a stronger visual-speech adapter. Samples are JSON mouth-landmark sequences, not saved face images or video.

## Actions stay yours

The example action adapter maps signals to `action.triggered` without driving a device. To use a new actuator, edit the copied `actions.py` or create a fresh adapter with `on_event`. You can also consume the same events via JSONL stdout or `--webhook`, with no Python plugin at all. Keep hardware fail-safe behavior in your own driver; `--dry-run` only disables Framewire's built-in GPIO and servo outputs, not arbitrary code inside installed adapters.
