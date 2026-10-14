"""Capture and manage personalized visual command examples."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .adapter_api import home_dir
from .lipmatch import MouthFeatures, load_samples, save_samples, variation
from .sources import open_source


def sample_path() -> Path:
    return home_dir() / "data" / "lips" / "samples.json"


def record(
    label: str,
    *,
    source_spec: str = "auto",
    seconds: float = 2.0,
    repeats: int = 3,
    status: Callable[[str], None] = print,
) -> int:
    if not label or len(label) > 80:
        raise ValueError("label must contain 1–80 characters")
    if not 0.8 <= seconds <= 5 or not 1 <= repeats <= 10:
        raise ValueError("seconds must be 0.8–5 and repeats 1–10")
    source = None
    features = None
    recorded: list[list[list[float]]] = []
    try:
        source = open_source(source_spec)
        source.read()
        features = MouthFeatures(home_dir() / "data" / "lips" / "models")
        for number in range(1, repeats + 1):
            for remaining in (3, 2, 1):
                status(f"Sample {number}/{repeats}: say '{label}' silently in {remaining}…")
                time.sleep(1)
            status("Recording now — face the camera and mouth the whole command once.")
            sequence = []
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                started = time.monotonic()
                feature = features.read(source.read(), started)
                if feature is not None:
                    sequence.append(feature)
                remaining = 1 / 12 - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
            if len(sequence) < 8:
                raise RuntimeError("too few face frames; improve light, face the camera and retry")
            if variation(sequence) < 0.025:
                raise RuntimeError("little mouth motion was seen; speak more clearly and retry")
            recorded.append(sequence)
            status(f"Saved sample {number}/{repeats} in memory ({len(sequence)} frames).")
        path = sample_path()
        samples = load_samples(path)
        samples.setdefault(label, []).extend(recorded)
        save_samples(path, samples)
        return len(samples[label])
    finally:
        if source is not None:
            try:
                source.close()
            finally:
                if features is not None:
                    features.close()


def remove(label: str) -> bool:
    path = sample_path()
    samples = load_samples(path)
    if label not in samples:
        return False
    del samples[label]
    save_samples(path, samples)
    return True
