"""Camera adapters. Each reconnect gets a fresh adapter instance."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import cv2


class CameraError(RuntimeError):
    pass


class FrameSource(Protocol):
    label: str

    def read(self): ...

    def close(self) -> None: ...


def safe_label(value: str) -> str:
    """Never expose URL credentials or query tokens in events/diagnostics."""
    if not value.startswith(("http://", "https://", "rtsp://", "rtsps://")):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return "invalid-url"
    host = parts.hostname or "unknown"
    if ":" in host:
        host = f"[{host}]"
    try:
        if parts.port:
            host += f":{parts.port}"
    except ValueError:
        host = "invalid-host"
    return urlunsplit((parts.scheme, host, "", "", ""))


def safe_error(exc: Exception, source_spec: str) -> str:
    """Avoid forwarding library exception strings that could contain a URL secret."""
    if isinstance(exc, CameraError):
        return str(exc)
    if source_spec.startswith(("http://", "https://", "rtsp://", "rtsps://")):
        return type(exc).__name__
    return str(exc) or type(exc).__name__


@dataclass
class OpenCVSource:
    capture: object
    label: str

    @classmethod
    def open(cls, value: int | str) -> OpenCVSource:
        label = f"camera:{value}" if isinstance(value, int) else safe_label(value)
        if isinstance(value, int):
            cap = cv2.VideoCapture(value, cv2.CAP_ANY)
        else:
            params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000]
            cap = cv2.VideoCapture(value, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            raise CameraError(f"Could not open {label}")
        return cls(cap, label)

    def read(self):
        ok, frame = self.capture.read()
        if not ok or frame is None or frame.size == 0:
            raise CameraError(f"No frame from {self.label}")
        return frame

    def close(self) -> None:
        self.capture.release()


@dataclass
class SnapshotSource:
    url: str
    label: str

    def read(self):
        import numpy as np

        request = Request(self.url, headers={"User-Agent": "framewire/0.1"})
        try:
            with urlopen(request, timeout=5) as response:
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if content_type and content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise CameraError(f"Expected an image from {self.label}, got {content_type}")
                data = response.read(8_000_001)
        except CameraError:
            raise
        except Exception as exc:
            raise CameraError(
                f"Snapshot unavailable from {self.label}: {type(exc).__name__}"
            ) from exc
        if len(data) > 8_000_000:
            raise CameraError(f"Snapshot exceeds 8 MB from {self.label}")
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise CameraError(f"Invalid image from {self.label}")
        return frame

    def close(self) -> None:
        pass


class PiCameraSource:
    label = "picamera"

    def __init__(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise CameraError(
                "Picamera2 is unavailable; see the Raspberry Pi setup in README"
            ) from exc
        try:
            self.camera = Picamera2()
            config = self.camera.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
            self.camera.configure(config)
            self.camera.start()
        except Exception as exc:
            cleanup_error = ""
            if hasattr(self, "camera"):
                try:
                    self.camera.close()
                except Exception as close_exc:
                    cleanup_error = f"; cleanup: {type(close_exc).__name__}"
            raise CameraError(
                f"Pi camera could not start: {type(exc).__name__}{cleanup_error}"
            ) from exc

    def read(self):
        try:
            rgb = self.camera.capture_array()
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception as exc:
            raise CameraError(f"Pi camera read failed: {type(exc).__name__}") from exc

    def close(self) -> None:
        try:
            self.camera.stop()
        finally:
            self.camera.close()


def open_source(spec: str, *, snapshot: bool = False) -> FrameSource:
    if snapshot:
        if not spec.startswith(("http://", "https://")):
            raise CameraError("--snapshot requires an http:// or https:// image URL")
        return SnapshotSource(spec, safe_label(spec))
    if spec == "picamera":
        return PiCameraSource()
    if spec == "auto":
        errors: list[str] = []
        for index in range(3):
            source = None
            try:
                source = OpenCVSource.open(index)
                source.read()
                return source
            except Exception as exc:
                errors.append(f"camera:{index} ({type(exc).__name__})")
                if source is not None:
                    try:
                        source.close()
                    except Exception as close_exc:
                        errors.append(f"camera:{index} cleanup ({type(close_exc).__name__})")
        if sys.platform.startswith("linux"):
            source = None
            try:
                source = PiCameraSource()
                source.read()
                return source
            except Exception as exc:
                errors.append(f"picamera ({type(exc).__name__})")
                if source is not None:
                    try:
                        source.close()
                    except Exception as close_exc:
                        errors.append(f"picamera cleanup ({type(close_exc).__name__})")
        raise CameraError("No camera ready. Tried " + ", ".join(errors))
    if spec.isdecimal():
        return OpenCVSource.open(int(spec))
    if spec.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return OpenCVSource.open(spec)
    raise CameraError("Source must be auto, a camera number, picamera, or an RTSP/HTTP URL")
