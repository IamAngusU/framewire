"""Small built-in detectors; no cloud, model download or identity recognition."""

from __future__ import annotations

from .model import Target


class FaceTracker:
    def __init__(self) -> None:
        import cv2

        self.cv2 = cv2
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.cascade = cv2.CascadeClassifier(path)
        if self.cascade.empty():
            raise RuntimeError("OpenCV's bundled face classifier is unavailable")

    def detect(self, frame) -> Target | None:
        cv2 = self.cv2
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.cascade.detectMultiScale(
            gray, scaleFactor=1.12, minNeighbors=5, minSize=(35, 35)
        )
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
        return Target(
            round((x + w / 2) / width, 4),
            round((y + h / 2) / height, 4),
            round(w * h / (width * height), 4),
            "face",
        )


class MotionTracker:
    def __init__(self) -> None:
        import cv2

        self.cv2 = cv2
        self.background = cv2.createBackgroundSubtractorMOG2(
            history=100, varThreshold=32, detectShadows=True
        )
        self.frames = 0

    def detect(self, frame) -> Target | None:
        cv2 = self.cv2
        self.frames += 1
        height, width = frame.shape[:2]
        mask = self.background.apply(frame)
        if self.frames <= 12:
            return None
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) >= width * height * 0.008]
        if not contours:
            return None
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        if w * h > width * height * 0.8:
            return None
        return Target(
            round((x + w / 2) / width, 4),
            round((y + h / 2) / height, 4),
            round(w * h / (width * height), 4),
            "motion",
        )


class NoTracker:
    def detect(self, frame) -> None:
        return None


def make_tracker(kind: str):
    if kind == "face":
        return FaceTracker()
    if kind == "motion":
        return MotionTracker()
    if kind == "none":
        return NoTracker()
    raise ValueError(f"unknown tracker: {kind}")
