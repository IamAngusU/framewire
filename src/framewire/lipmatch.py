"""Personalized short visual-command matching, not open-vocabulary VSR."""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2

from .adapter_api import ensure_model

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


def mouth_feature(landmarks) -> list[float]:
    def distance(a: int, b: int) -> float:
        return math.hypot(landmarks[a].x - landmarks[b].x, landmarks[a].y - landmarks[b].y)

    mouth_width = max(distance(61, 291), 1e-6)
    face_width = max(distance(234, 454), 1e-6)
    return [round(distance(13, 14) / mouth_width, 4), round(mouth_width / face_width, 4)]


def dtw_distance(left: list[list[float]], right: list[list[float]]) -> float:
    if not left or not right:
        return float("inf")
    previous = [float("inf")] * (len(right) + 1)
    previous[0] = 0.0
    for a in left:
        current = [float("inf")] * (len(right) + 1)
        for j, b in enumerate(right, 1):
            cost = math.hypot((a[0] - b[0]) * 1.5, a[1] - b[1])
            current[j] = cost + min(previous[j], current[j - 1], previous[j - 1])
        previous = current
    return previous[-1] / max(len(left), len(right))


def variation(sequence: list[list[float]]) -> float:
    if not sequence:
        return 0.0
    values = [point[0] for point in sequence]
    return max(values) - min(values)


def best_match(
    history: list[list[float]],
    samples: dict[str, list[list[list[float]]]],
    *,
    threshold: float = 0.045,
    margin: float = 0.012,
) -> tuple[str, float] | None:
    if variation(history) < 0.025:
        return None
    scores = []
    for label, examples in samples.items():
        for sample in examples:
            if len(history) < len(sample) or len(sample) < 8:
                continue
            scores.append((dtw_distance(history[-len(sample) :], sample), label))
    if not scores:
        return None
    scores.sort()
    best_score, label = scores[0]
    other = min((score for score, name in scores if name != label), default=float("inf"))
    if best_score > threshold or other - best_score < margin:
        return None
    return label, best_score


def load_samples(path: Path) -> dict[str, list[list[list[float]]]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "framewire.lips.v1" or not isinstance(data.get("samples"), dict):
        raise ValueError("lip sample file has an unsupported schema")
    return data["samples"]


def save_samples(path: Path, samples: dict[str, list[list[list[float]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {"schema": "framewire.lips.v1", "samples": samples}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    temp.replace(path)


class MouthFeatures:
    def __init__(self, model_dir: Path) -> None:
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError(
                "Lip samples need MediaPipe: python -m pip install mediapipe"
            ) from exc
        self.mp = mp
        model = ensure_model(model_dir / "face_landmarker.task", MODEL_URL, MODEL_SHA256)
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
        )
        self.detector = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        self.last_ms = 0

    def read(self, frame, now: float) -> list[float] | None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        timestamp = max(self.last_ms + 1, int(now * 1000))
        self.last_ms = timestamp
        result = self.detector.detect_for_video(image, timestamp)
        if not result.face_landmarks:
            return None
        return mouth_feature(result.face_landmarks[0])

    def close(self) -> None:
        self.detector.close()
