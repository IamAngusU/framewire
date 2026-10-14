"""Install optional recognition support into Framewire's own environment."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from importlib.util import find_spec
from pathlib import Path

from .adapter_api import ensure_model, home_dir
from .adapters import EXAMPLES, adapter_dir, install_example, list_installed, tomllib, validate_name
from .data import write_text_atomic

SUPPORT = {
    "hands": {"mediapipe": "mediapipe>=1.0,<2"},
    "lips": {"mediapipe": "mediapipe>=1.0,<2"},
    "voice": {"faster_whisper": "faster-whisper>=1.2,<2", "sounddevice": "sounddevice>=0.5,<1"},
    "actions": {},
}


def find_uv() -> str | None:
    executable = "uv.exe" if sys.platform == "win32" else "uv"
    candidates = [
        os.environ.get("FRAMEWIRE_UV"),
        shutil.which("uv"),
        str(home_dir() / "runtime" / "uv" / executable),
        str(Path.home() / ".local" / "bin" / executable),
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


def ensure_support(names: list[str], status: Callable[[str], None]) -> None:
    requirements = {spec for name in names for spec in SUPPORT.get(name, {}).values()}
    for name in names:
        settings_path = adapter_dir() / f"{name}.toml"
        if settings_path.exists():
            settings = tomllib.loads(settings_path.read_text(encoding="utf-8-sig"))
            extra = settings.get("requirements", [])
            if not isinstance(extra, list) or any(
                not isinstance(spec, str)
                or not spec.strip()
                or spec.startswith("-")
                or len(spec) > 500
                or any(ord(c) < 32 for c in spec)
                for spec in extra
            ):
                raise ValueError(
                    f"{name}.toml: requirements must be a list of package requirement strings"
                )
            requirements.update(extra)
    if not requirements:
        return
    if sys.prefix == sys.base_prefix:
        raise RuntimeError(
            "Optional support needs an isolated environment. Re-run the Framewire installer, then framewire setup."
        )
    uv = find_uv()
    if uv:
        command = [uv, "pip", "install", "--python", sys.executable, "--only-binary", ":all:"]
    elif find_spec("pip"):
        command = [sys.executable, "-m", "pip", "install", "--only-binary", ":all:"]
    else:
        raise RuntimeError(
            "No package installer was found. Re-run the Framewire installer to repair its environment."
        )
    status("Installing optional support into Framewire's environment…")
    try:
        result = subprocess.run(
            [*command, *sorted(requirements)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env={**os.environ, "UV_CACHE_DIR": str(home_dir() / "runtime" / "cache")},
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Support download timed out. Check your connection and run setup again."
        ) from exc
    if result.returncode:
        # Keep package-manager details out of the main UI; provide an actionable failure.
        detail = result.stderr or result.stdout
        log = home_dir() / "data" / "setup.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(detail, encoding="utf-8")
        raise RuntimeError(
            f"Optional packages could not be installed. Check your connection and platform support. Details: {log}"
        )
    manifest = home_dir() / "data" / "runtime-requirements.txt"
    saved = set(manifest.read_text(encoding="utf-8").splitlines()) if manifest.exists() else set()
    write_text_atomic(manifest, "\n".join(sorted(saved | requirements)) + "\n")


def prepare(
    names: list[str],
    *,
    install: bool = True,
    download: bool = True,
    status: Callable[[str], None] = print,
) -> list[str]:
    names = list(dict.fromkeys(names))
    installed = dict(list_installed())
    if not names or any(name not in EXAMPLES and name not in installed for name in names):
        raise ValueError(f"Choose adapters from: {', '.join(EXAMPLES)}")
    for name in names:
        validate_name(name)
    if install:
        ensure_support(names, status)
        importlib.invalidate_caches()
    if "voice" in names and install:
        try:
            importlib.import_module("sounddevice")
        except OSError as exc:
            raise RuntimeError(
                "The microphone backend is unavailable. On Linux install libportaudio2 with your OS package manager, then run framewire setup voice again."
            ) from exc
    for name in names:
        if name not in installed:
            install_example(name)
            status(f"Added {name} adapter")
        else:
            status(f"Kept your existing {name} adapter")
    if download and "hands" in names:
        from .handmatch import MODEL_SHA256, MODEL_URL

        status("Preparing the hand model (8 MB)…")
        ensure_model(
            home_dir() / "data" / "hands" / "models" / "gesture_recognizer.task",
            MODEL_URL,
            MODEL_SHA256,
        )
    if download and "lips" in names:
        from .lipmatch import MODEL_SHA256, MODEL_URL

        status("Preparing the face landmark model (4 MB)…")
        ensure_model(
            home_dir() / "data" / "lips" / "models" / "face_landmarker.task",
            MODEL_URL,
            MODEL_SHA256,
        )
    if download and "voice" in names:
        try:
            from faster_whisper.utils import download_model
        except ImportError as exc:
            raise RuntimeError(
                "Voice support is missing. Run framewire setup voice without --no-install."
            ) from exc
        status("Preparing the multilingual voice model…")
        download_model("tiny", cache_dir=str(home_dir() / "data" / "voice" / "models"))
    return names
