# Event contract

Framewire emits one compact JSON object per stdout line. Diagnostic text goes to stderr. A configured webhook receives the same object as an HTTP POST with `Content-Type: application/json`. Each process starts `sequence` at 1 and increments it for every emitted event.

```json
{
  "schema": "framewire.event.v1",
  "time": "2026-10-14T10:00:00+00:00",
  "sequence": 4,
  "type": "target.moved",
  "source": "camera:0",
  "target": {"x": 0.63, "y": 0.48, "size": 0.05, "kind": "face"}
}
```

| Field | Meaning |
| --- | --- |
| `schema` | Exact contract identifier. Reject unknown major versions. |
| `time` | UTC ISO 8601 time at emission. |
| `sequence` | Monotonic within this process, useful for detecting missed lines. Resets on restart. |
| `type` | One of the lifecycle events below. |
| `source` | Camera index or URL origin without user info, path, query or fragment. |
| `target` | Present on acquisition and movement events. Normalized center `x`, `y`; detected area fraction `size`; `kind` is `face`, `motion` or `demo`. |
| `detail` | Optional human-readable reason. Treat as diagnostic text, not a stable error code. |
| `data` | Optional JSON object for adapter lifecycle and signals. |

## Lifecycle

```text
source.connected
  target.acquired  -> target.moved* -> target.lost
source.lost
  (retry) -> source.connected
```

- `source.connected`: one frame was successfully read after opening the source.
- `target.acquired`: a detection persisted for two frames. GPIO becomes active and the servo receives a position.
- `target.moved`: the smoothed target changed by at least 0.025 of frame width or height; emission is limited to at most once per 0.2 seconds. The servo may update between emitted movement events.
- `target.lost`: no detection for the grace period, or the camera failed. GPIO goes LOW and servo PWM is released.
- `source.lost`: open/read/detection failed. The process waits and retries. It may be the first event if no source was ready.
- `adapter.ready`: an installed adapter loaded successfully.
- `adapter.error`: an adapter failed and was disabled for this process run. `data.error` is the exception class; `data.message` is diagnostic text.
- `adapter.signal`: an adapter emitted a signal. `data.adapter`, `data.signal` and `data.value` are always present; adapter-specific JSON fields follow. Example signals include `hand.position`, `hand.gesture`, `speech.transcript`, `speech.command`, `lip.command` and `action.triggered`.

The built-in face/motion tracker follows **at most one target**; the hand adapter can emit signals for two hands. Target transitions are intentionally sparse; do not use the event stream as a full video or position telemetry feed. Sources are local or user-supplied URLs. Framewire does not upload frames.

Adapter signals can arrive from background threads (for example a microphone). The event bus serializes sequence assignment and stdout writes, so lines remain ordered by `sequence`. An adapter may emit a new event while reacting to another event. On a camera dropout, the hand and lip examples clear their transient state; the voice adapter can still hear commands while the video source reconnects. If the microphone disappears, it emits `speech.error` and retries with bounded backoff.

## Delivery

Stdout writes synchronously in order. A broken stdout pipe can terminate the process, as with ordinary Unix CLI tools. Webhook delivery is asynchronous, bounded to 64 queued events, with a 3-second request timeout. It does not retry failed POSTs, acknowledge downstream processing or persist a queue. Under backlog pressure, movement events may be dropped; lifecycle events are prioritized when possible. Consumers needing durable delivery should persist stdout JSONL or bridge it to their own queue.

An optional bearer token is read from an environment variable at startup with `--webhook-token-env NAME`. Never put credentials in an example URL. Source labels retain only URL scheme, host and port; outbound connections still use the full supplied source URL.

Transcripts and action names are event data. They are sent to a configured webhook, even though raw microphone audio and video frames are not. Avoid forwarding events to a receiver that should not see the recognized text.
