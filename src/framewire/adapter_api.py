"""Small public API for local adapters. See docs/adapters.md."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import __version__

ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SIGNAL_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,79}$")


def home_dir() -> Path:
    override = os.environ.get("FRAMEWIRE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Framewire"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Framewire"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "framewire"


@dataclass
class AdapterContext:
    name: str
    data_dir: Path
    settings: dict[str, Any]
    _publish: Callable[[str, str, dict[str, Any]], None] = field(repr=False)
    _alive: bool = field(default=True, repr=False)

    def number(self, name: str, default: float, *, minimum: float, maximum: float) -> float:
        try:
            value = float(self.settings.get(name, default))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{self.name}.toml: {name} must be a number") from exc
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{self.name}.toml: {name} must be between {minimum} and {maximum}")
        return value

    def emit(self, signal: str, value: str | float | bool | None = None, **metadata: Any) -> None:
        """Publish a JSON-safe signal. Calls may come from a background audio thread."""
        if not self._alive:
            return
        if not SIGNAL_NAME.fullmatch(signal):
            raise ValueError("signal must be a lowercase dotted name, e.g. hand.gesture")
        if {"adapter", "signal", "value"}.intersection(metadata):
            raise ValueError("adapter, signal and value are reserved event fields")
        data = {"adapter": self.name, "signal": signal, "value": value, **metadata}
        json.dumps(data, allow_nan=False)
        self._publish(self.name, signal, data)


def ensure_model(path: Path, url: str, sha256: str, *, max_bytes: int = 30_000_000) -> Path:
    """Fetch a pinned HTTPS model once, atomically, and verify its digest."""
    if not url.startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("model URL must be HTTPS and SHA-256 must be pinned")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size <= max_bytes:
        digest = hashlib.sha256()
        with path.open("rb") as cached:
            while chunk := cached.read(64 * 1024):
                digest.update(chunk)
        if digest.hexdigest() == sha256:
            return path
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        request = Request(url, headers={"User-Agent": f"framewire/{__version__}"})
        for attempt in range(3):
            try:
                digest = hashlib.sha256()
                size = 0
                started = time.monotonic()
                with urlopen(request, timeout=15) as response, temp.open("wb") as output:
                    if not response.geturl().startswith("https://"):
                        raise ValueError("model download redirected to an insecure URL")
                    while chunk := response.read(64 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("model download exceeds size limit")
                        if time.monotonic() - started > 90:
                            raise TimeoutError("model download took too long")
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != sha256:
                    raise ValueError(
                        "model checksum mismatch; run setup again to retry the download"
                    )
                temp.replace(path)
                return path
            except (URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise RuntimeError(
                        "Model download failed. Check your connection and run framewire setup again."
                    ) from exc
                time.sleep(attempt + 1)
        raise RuntimeError("Model download did not finish")
    finally:
        temp.unlink(missing_ok=True)
