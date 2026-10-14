<p align="center"><img src="docs/assets/wordmark.svg" width="760" alt="Framewire — camera in, control events out"></p>

<h1 align="center">Give a camera something useful to do.</h1>
<p align="center">A headless, local-first bridge from live video to events, GPIO and a pan servo.<br>No account. No model download. No required config file.</p>

<p align="center"><a href="#start-in-a-minute">Start</a> · <a href="#choose-an-input">Inputs</a> · <a href="#connect-an-output">Outputs</a> · <a href="#when-things-go-wrong">Recovery</a> · <a href="docs/events.md">Event contract</a></p>

<p align="center"><img src="docs/assets/flow.svg" width="860" alt="Camera or network stream flows through a tracker into JSON events, webhook, GPIO and servo interfaces"></p>

## The problem

Getting from “I have a camera” to “move this lamp when I move” often means writing the same glue again: find a video source, decode frames, detect something, smooth jitter, map coordinates to a motor, deal with unplugged USB devices and reconnect a network stream. The happy-path script is short. The script that can live on a desk is usually not.

**Framewire gives that glue one small CLI.** It discovers a local camera, follows the largest visible frontal face by default, emits newline-delimited JSON events, and keeps trying when a source disappears. You can add a Pi GPIO output, a pan servo or a webhook when you need one. Video stays on the machine unless you deliberately connect a network source; Framewire sends *coordinates and state*, not frames, to its outputs.

## Start in a minute

Python 3.10+ is required. Install from this repo:

<details open><summary><strong>macOS / Linux</strong></summary>

```sh
git clone https://github.com/IamAngusU/framewire.git
cd framewire
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
framewire doctor
framewire run
```

</details>

<details><summary><strong>Windows PowerShell</strong></summary>

```powershell
git clone https://github.com/IamAngusU/framewire.git
cd framewire
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
framewire doctor
framewire run
```

</details>

No camera handy? Run `framewire demo`. It sends the same event shape with a moving synthetic point and touches no hardware.

<p align="center"><img src="docs/assets/terminal.svg" width="860" alt="Example terminal showing the doctor, run and JSON event output"></p>

`framewire run` writes **JSONL to stdout** and human status to stderr. Stop with Ctrl+C. With no output flags, it only observes and emits events, so you can inspect the stream before connecting any hardware.

```json
{"schema":"framewire.event.v1","time":"2026-10-14T10:00:00+00:00","sequence":2,"type":"target.acquired","source":"camera:0","target":{"x":0.63,"y":0.48,"size":0.05,"kind":"face"}}
```

`x` and `y` run from 0 to 1 across the frame. `size` is the fraction of frame area covered by the detection. The face mode detects a face location; it does not identify a person.

## Choose an input

| You have | Run this | Notes |
| --- | --- | --- |
| Built-in or USB webcam | `framewire run` | `auto` tries cameras 0–2, then Picamera2 on Linux. |
| A specific webcam | `framewire run --source 1` | Camera indexes depend on the OS. |
| Raspberry Pi camera | `framewire run --source picamera` | Use the Pi setup below. |
| RTSP camera | `framewire run --source 'rtsp://host/live'` | Open/read timeouts are used with OpenCV's FFmpeg backend. |
| HTTP video / MJPEG | `framewire run --source 'http://host/stream'` | The URL must deliver a decodable video stream. |
| HTTP JPEG/PNG endpoint | `framewire run --source 'http://host/snapshot.jpg' --snapshot` | Fetches one bounded image per sample. |

For a moving object instead of a face, add `--track motion`. Motion mode learns the background for its first 12 frames. It follows the largest moving region, which may be a pet, a hand or a changing screen; it is not object recognition. Both built-in modes are intentionally lightweight and offline.

The default detector follows the **largest frontal face** if several faces are visible. It is a starter tracker for desk projects, not a multi-person identity system. To tune responsiveness, see `framewire run --help` (`--fps`, `--lost-after`, `--deadzone`).

### Raspberry Pi setup

On Raspberry Pi OS, install Picamera2 from the OS packages so it matches libcamera. Create a virtual environment that can see those packages:

```sh
sudo apt update
sudo apt install python3-picamera2 python3-gpiozero python3-lgpio
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e '.[pi]'
framewire doctor --source picamera
```

See the [official Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf) if your camera module needs OS-level setup. `framewire doctor` reads one frame and reports the actual result. It does not move pins.

## Connect an output

### A pan servo for a desk lamp

```sh
framewire run --servo-pin 18 --servo-range 55
```

Pin numbers are **BCM GPIO** numbers. The servo angle maps the camera's horizontal coordinate across the configured range; `--invert-pan` flips direction. `--deadzone 0.04` ignores tiny shifts near center. Start with `--dry-run` to inspect events first. A stable camera mounted separately from the lamp is the intended geometry; camera-on-servo feedback needs a different controller.

Power a servo from a suitable separate supply and share ground with the Pi; the GPIO pin carries only its control signal. Read [hardware notes](docs/hardware.md) before connecting a motor.

### A trigger output

```sh
framewire run --gpio-pin 23
```

GPIO 23 is HIGH while a target is present and LOW when it is lost, when the camera fails and on shutdown. Use this logic signal to feed an appropriate driver or controller. You can combine it with `--servo-pin` using a different BCM pin.

### A webhook or your own actuator

```sh
framewire run --webhook 'http://127.0.0.1:8080/events'
```

Framewire POSTs each event as JSON. To add a bearer token without placing it in shell history, set an environment variable and use `--webhook-token-env MY_TOKEN`. Webhooks run on a bounded background queue; if the receiver is slow or offline, camera tracking continues and stdout remains complete. The webhook is **best effort**, not a durable message queue.

Or pipe stdout into your own process:

```sh
framewire run | python examples/consume_events.py
```

The example prints actions for `target.acquired`, `target.moved` and `target.lost`. Replace those functions with your actuator SDK. [Event fields and delivery behavior](docs/events.md) are documented so you can integrate without importing Framewire internals.

## When things go wrong

| Situation | What Framewire does | What to try |
| --- | --- | --- |
| No camera on startup | Emits `source.lost`, waits, and retries with bounded backoff. Outputs stay inactive. | `framewire doctor`; try `--source 1`. |
| USB unplugged / stream drops | Releases servo signal, sets GPIO LOW, emits loss events, closes the source, reconnects. | Check cable, power or stream URL. No restart needed. |
| No face for a moment | Holds the current target for `--lost-after` seconds; then emits `target.lost` and releases outputs. | Increase `--lost-after` for brief occlusion. |
| Webhook is down | Warns on stderr and drops undeliverable webhook events; JSONL stdout still carries them. | Reconnect receiver; use a durable consumer if every event matters. |
| GPIO library or pin fails | Reports the output error. A failing output is disabled so camera/events continue. | Check permissions, pin conflict and Pi packages. |

OpenCV's FFmpeg timeouts apply to supported backends; a faulty camera driver can still block below Python. `source.lost` reports observed failure, not a guarantee of a fixed recovery time. JSONL is the complete local event stream while the process runs; it is not persisted automatically.

## Interfaces and scope

```text
FrameSource.read() -> BGR frame
Tracker.detect(frame) -> Target | None
EventBus.emit(type, source, target?) -> versioned JSONL + optional webhook
ControlHub.update(target) / release() -> optional GPIO and pan servo
```

Sources and trackers are small Python adapters in [`src/framewire`](src/framewire). `FrameSource` is a protocol, so another capture device can be added without changing the event contract. [The contract](docs/events.md) describes transition ordering, normalized coordinates and sink limits.

Framewire currently controls one pan axis and one digital output. It does not include object identity, tilt, calibration for arbitrary optics, recording, a GUI or a hardware safety system. The GPIO output is a logic signal, not a power stage or an interlock. **Do not connect a laser directly or use person-following with a laser without independent hardware safety.**

## Develop and verify

```sh
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
ruff format --check src tests
```

Tests cover target transitions, camera loss and recovery, output release, credential redaction and a real local HTTP snapshot. Hardware behavior must also be checked on the exact Pi, camera and actuator you use. See [hardware notes](docs/hardware.md).

MIT licensed. Early 0.1 release; event schema is versioned so integrations can reject an incompatible future format. [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md).
