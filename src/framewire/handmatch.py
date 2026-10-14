"""Small personal static-hand-pose matcher over MediaPipe landmarks."""

from __future__ import annotations

import math
from pathlib import Path

from .adapter_api import ensure_model
from .data import read_document, valid_label, valid_vector, write_document

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)
MODEL_SHA256 = "97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482"


def pose_feature(landmarks, side: str) -> list[float]:
    """Translate, rotate and scale 21 landmarks; mirror left hands."""
    wrist = landmarks[0]
    middle = landmarks[9]
    dx = middle.x - wrist.x
    dy = middle.y - wrist.y
    scale = max(math.hypot(dx, dy), 1e-5)
    # The wrist-to-middle-knuckle vector is the local up axis.
    ux, uy = dx / scale, dy / scale
    sign = -1 if side.casefold() == "left" else 1
    feature: list[float] = []
    for point in landmarks:
        px, py = point.x - wrist.x, point.y - wrist.y
        feature.extend(
            (round(sign * (px * uy - py * ux) / scale, 4), round((px * ux + py * uy) / scale, 4))
        )
    return feature


def pose_distance(left: list[float], right: list[float]) -> float:
    if len(left) != 42 or len(right) != 42:
        return float("inf")
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)) / 21)


def best_pose(
    feature: list[float],
    samples: dict[str, list[list[float]]],
    *,
    threshold: float = 0.22,
    margin: float = 0.05,
) -> tuple[str, float] | None:
    scores = sorted(
        (pose_distance(feature, sample), label)
        for label, examples in samples.items()
        for sample in examples
    )
    if not scores:
        return None
    distance, label = scores[0]
    next_label = min((score for score, name in scores if name != label), default=float("inf"))
    if distance > threshold or next_label - distance < margin:
        return None
    return label, distance


def load_samples(path: Path) -> dict[str, list[list[float]]]:
    data = read_document(path, "framewire.hands.v1")
    if data is None:
        return {}
    samples = data.get("samples")
    if not isinstance(samples, dict) or any(
        not valid_label(label)
        or not isinstance(examples, list)
        or any(not valid_vector(sample, 42) for sample in examples)
        for label, examples in samples.items()
    ):
        raise ValueError(f"Invalid hand samples in {path}. Restore a valid copy or rename it.")
    return samples


def save_samples(path: Path, samples: dict[str, list[list[float]]]) -> None:
    write_document(path, {"schema": "framewire.hands.v1", "samples": samples})


class HandFeatures:
    def __init__(self, model_dir: Path) -> None:
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError("Hand recognition needs MediaPipe: pip install mediapipe") from exc
        self.mp = mp
        model = ensure_model(model_dir / "gesture_recognizer.task", MODEL_URL, MODEL_SHA256)
        options = mp.tasks.vision.GestureRecognizerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=2,
        )
        self.detector = mp.tasks.vision.GestureRecognizer.create_from_options(options)
        self.last_ms = 0

    def read(self, frame, now: float):
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        timestamp = max(self.last_ms + 1, int(now * 1000))
        self.last_ms = timestamp
        return self.detector.recognize_for_video(image, timestamp)

    def close(self) -> None:
        self.detector.close()
