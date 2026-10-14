"""Personalized short silent commands from mouth landmarks (experimental)."""

from __future__ import annotations

from collections import deque

from framewire.lipmatch import MouthFeatures, best_match, load_samples


def create(context):
    class Lips:
        def start(self):
            self.threshold = context.number("threshold", 0.045, minimum=0.001, maximum=1)
            self.margin = context.number("margin", 0.012, minimum=0, maximum=1)
            self.cooldown = context.number("cooldown", 2.0, minimum=0.5, maximum=60)
            self.features = MouthFeatures(context.data_dir / "models")
            self.samples = load_samples(context.data_dir / "samples.json")
            self.history = deque(maxlen=80)
            self.last_check = 0.0
            self.last_trigger = 0.0

        def on_frame(self, frame, now):
            feature = self.features.read(frame, now)
            if feature is None:
                self.history.clear()
                return
            self.history.append(feature)
            if (
                not self.samples
                or now - self.last_check < 0.4
                or now - self.last_trigger < self.cooldown
            ):
                return
            self.last_check = now
            match = best_match(
                list(self.history), self.samples, threshold=self.threshold, margin=self.margin
            )
            if match:
                label, distance = match
                self.last_trigger = now
                self.history.clear()
                context.emit(
                    "lip.command", label, distance=round(distance, 4), method="personal_template"
                )

        def on_event(self, event):
            if event["type"] == "source.lost":
                self.history.clear()

        def close(self):
            if hasattr(self, "features"):
                self.features.close()

    return Lips()
