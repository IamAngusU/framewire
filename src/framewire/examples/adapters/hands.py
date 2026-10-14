"""Two hands, seven built-in gestures and your recorded static poses."""

from __future__ import annotations

from framewire.handmatch import HandFeatures, best_pose, load_samples, pose_feature


def create(context):
    class Hands:
        def start(self):
            self.features = HandFeatures(context.data_dir / "models")
            self.samples = load_samples(context.data_dir / "gestures.json")
            self.threshold = float(context.settings.get("threshold", 0.22))
            self.margin = float(context.settings.get("margin", 0.05))
            self.pending = {}
            self.active = {}
            self.seen = {}
            self.gesture_seen = {}
            self.last_position = {}

        def on_frame(self, frame, now):
            result = self.features.read(frame, now)
            for index, landmarks in enumerate(result.hand_landmarks):
                side = result.handedness[index][0].category_name
                self.seen[side] = now
                palm = landmarks[9]
                x, y = round(float(palm.x), 4), round(float(palm.y), 4)
                if now - self.last_position.get(side, 0) >= 0.2:
                    context.emit("hand.position", side, hand=side, x=x, y=y)
                    self.last_position[side] = now
                categories = result.gestures[index] if index < len(result.gestures) else []
                category = categories[0] if categories else None
                custom = (
                    best_pose(
                        pose_feature(landmarks, side),
                        self.samples,
                        threshold=self.threshold,
                        margin=self.margin,
                    )
                    if self.samples
                    else None
                )
                if custom:
                    gesture, distance = custom
                    confidence = round(max(0.0, 1 - distance / self.threshold), 3)
                    is_custom = True
                elif category and category.score >= 0.6:
                    gesture = category.category_name
                    confidence = round(float(category.score), 3)
                    is_custom = False
                else:
                    gesture = None
                if gesture in (None, "None", "Unknown"):
                    self.pending.pop(side, None)
                    continue
                self.gesture_seen[side] = now
                candidate, count = self.pending.get(side, (None, 0))
                count = count + 1 if candidate == gesture else 1
                self.pending[side] = (gesture, count)
                if count >= 3 and self.active.get(side) != gesture:
                    self._end(side)
                    self.active[side] = gesture
                    context.emit(
                        "hand.gesture",
                        gesture,
                        hand=side,
                        phase="start",
                        confidence=confidence,
                        custom=is_custom,
                        x=x,
                        y=y,
                    )
            for side, last in tuple(self.seen.items()):
                if now - last > 0.5:
                    self._end(side)
                    self.seen.pop(side, None)
                    self.pending.pop(side, None)
            for side, last in tuple(self.gesture_seen.items()):
                if now - last > 0.5:
                    self._end(side)
                    self.gesture_seen.pop(side, None)

        def _end(self, side):
            previous = self.active.pop(side, None)
            if previous:
                context.emit("hand.gesture", previous, hand=side, phase="end")

        def on_event(self, event):
            if event["type"] == "source.lost":
                for side in tuple(self.active):
                    self._end(side)
                self.seen.clear()
                self.gesture_seen.clear()
                self.pending.clear()

        def close(self):
            if hasattr(self, "features"):
                self.features.close()

    return Hands()
