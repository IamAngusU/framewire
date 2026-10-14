"""Small public API for local adapters. See docs/adapters.md."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

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

    def emit(self, signal: str, value: str | float | bool | None = None, **metadata: Any) -> None:
        """Publish a JSON-safe signal. Calls may come from a background audio thread."""
        if not self._alive:
            return
        if not SIGNAL_NAME.fullmatch(signal):
            raise ValueError("signal must be a lowercase dotted name, e.g. hand.gesture")
        data = {"adapter": self.name, "signal": signal, "value": value, **metadata}
        json.dumps(data)
        self._publish(self.name, signal, data)


def ensure_model(path: Path, url: str, sha256: str, *, max_bytes: int = 30_000_000) -> Path:
    """Fetch a pinned HTTPS model once, atomically, and verify its digest."""
    if not url.startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("model URL must be HTTPS and SHA-256 must be pinned")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
        return path
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        request = Request(url, headers={"User-Agent": "framewire/0.2"})
        digest = hashlib.sha256()
        size = 0
        with urlopen(request, timeout=15) as response, temp.open("wb") as output:
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("model download exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError("model checksum mismatch")
        temp.replace(path)
        return path
    finally:
        temp.unlink(missing_ok=True)
