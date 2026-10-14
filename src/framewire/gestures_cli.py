"""Record and manage personal static hand signs without saving images."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .adapter_api import home_dir
from .handmatch import HandFeatures, load_samples, pose_distance, pose_feature, save_samples
from .sources import open_source


def sample_path() -> Path:
    return home_dir() / "data" / "hands" / "gestures.json"


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
    recorded: list[list[float]] = []
    try:
        source = open_source(source_spec)
        source.read()
        features = HandFeatures(home_dir() / "data" / "hands" / "models")
        for number in range(1, repeats + 1):
            for remaining in (3, 2, 1):
                status(f"Sample {number}/{repeats}: hold '{label}' in {remaining}…")
                time.sleep(1)
            status("Recording now — hold one hand steady and visible.")
            sequence: list[list[float]] = []
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                started = time.monotonic()
                result = features.read(source.read(), started)
                if len(result.hand_landmarks) == 1 and result.handedness:
                    side = result.handedness[0][0].category_name
                    sequence.append(pose_feature(result.hand_landmarks[0], side))
                remaining = 1 / 12 - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
            if len(sequence) < 8:
                raise RuntimeError("too few single-hand frames; improve light and retry")
            average = [round(sum(row[i] for row in sequence) / len(sequence), 4) for i in range(42)]
            spread = sum(pose_distance(row, average) for row in sequence) / len(sequence)
            if spread > 0.18:
                raise RuntimeError("hand moved too much; hold a static sign and retry")
            recorded.append(average)
            status(f"Captured sample {number}/{repeats} ({len(sequence)} frames).")
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
