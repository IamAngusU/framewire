# What Framewire handles, and what has been checked

Framewire is suitable for developing desk automation and custom camera-driven interfaces. It is an early release, with recovery logic tested more thoroughly than recognition accuracy on a range of people and hardware.

## Recovery behavior

- Camera capture and HTTP snapshot decoding run in a separate process. Opening waits up to eight seconds; reading waits up to five. A worker that stalls is stopped, the built-in outputs are released, and the source is retried with backoff.
- A lost target releases the built-in GPIO and servo signal after the configured grace period. Shutdown also releases them.
- A failing adapter is closed and disabled for that run. Other adapters and the camera continue. Recursive adapter signal chains are bounded and reported.
- Audio capture retries after a missing or failed microphone. A bounded queue discards stale blocks instead of accumulating audio while transcription is busy.
- Webhooks have a bounded queue and request timeout. A slow receiver does not pause tracking. Delivery is best effort.
- Command files are validated before matching. Damaged JSON, invalid dimensions and non-finite values produce a repair hint. Writes use atomic replacement; incomplete new writes do not replace a valid old file.
- CLI usage and ordinary runtime errors have a concise message and next step. `--debug` makes technical tracebacks available.

The event bus serializes complete JSON lines across threads, protects reserved adapter fields, rejects non-finite JSON numbers and isolates failed event listeners. Event callbacks run outside the stdout lock; adapter hooks are serialized separately to avoid lock inversion between camera and microphone workers.

## Evidence for this release

Automated tests cover camera loss/recovery, physical-output release, broken pipes, damaged command files, concurrent events, recursive adapter signals, a stalled HTTP capture worker, sample matching and missing microphone recovery. The Windows installer was exercised in a fresh private directory with no uv or managed Python present, then used to prepare hand recognition and run against a local virtual camera. Hand/face model loading and the multilingual voice model were also checked locally.

The CI workflow includes Python 3.10/3.12 checks plus installer checks for Windows, Linux and macOS. A configured workflow is only a future verification gate until its jobs actually run; current results are available on the repository's Actions page.

## Practical limits

No program can promise that it will never crash. OS failures, memory exhaustion, interrupted power and native libraries can stop a process. Camera isolation limits driver failures; arbitrary user adapters still run as trusted Python code in the main process. Slow or blocking adapter hooks can delay tracking. Hardware drivers need their own watchdog or interlock if losing the process would matter.

The largest-frontal-face tracker does not identify people. Hand presets and recorded static poses need testing on your camera. Personalized mouth-motion commands are experimental and limited to your trained labels. There is no measured accuracy claim for arbitrary lighting, viewpoints, microphones or languages.

Physical Raspberry Pi GPIO/servos, CSI camera configurations and long unattended runs across platforms have not all been validated on hardware. Keep those checks part of your project before relying on an actuator.
