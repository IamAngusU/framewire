"""Local microphone commands with faster-whisper; audio is never stored."""

from __future__ import annotations

import queue
import threading

from framewire.commands import match_command, normalize_phrase
from framewire.phrases import load_preferences


def create(context):
    class Voice:
        def start(self):
            try:
                import sounddevice as sd
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "Install voice support: python -m pip install faster-whisper sounddevice"
                ) from exc
            self.sd = sd
            preferences = load_preferences(context.data_dir / "phrases.json")
            self.commands = dict(context.settings.get("commands", {}))
            for phrase, action in preferences["commands"].items():
                for existing in tuple(self.commands):
                    if normalize_phrase(existing) == normalize_phrase(phrase):
                        del self.commands[existing]
                self.commands[phrase] = action
            self.language = preferences["language"] or context.settings.get("language", "auto")
            self.min_rms = float(context.settings.get("min_rms", 0.018))
            model_name = context.settings.get("model", "tiny")
            model_dir = context.data_dir / "models"
            model_dir.mkdir(parents=True, exist_ok=True)
            self.model = WhisperModel(
                model_name, device="cpu", compute_type="int8", download_root=str(model_dir)
            )
            self.blocks = queue.Queue(maxsize=120)
            self.stop = threading.Event()
            self.stream = None
            self.thread = threading.Thread(target=self._listen, name="framewire-voice", daemon=True)
            self.thread.start()

        def _callback(self, indata, frames, timing, status):
            if self.stop.is_set():
                return
            block = indata[:, 0].copy()
            try:
                self.blocks.put_nowait(block)
            except queue.Full:
                try:
                    self.blocks.get_nowait()
                    self.blocks.put_nowait(block)
                except queue.Empty:
                    return

        def _listen(self):
            import numpy as np

            retry = 1.0
            while not self.stop.is_set():
                try:
                    with self.sd.InputStream(
                        samplerate=16000,
                        channels=1,
                        dtype="float32",
                        blocksize=1600,
                        callback=self._callback,
                    ) as stream:
                        self.stream = stream
                        retry = 1.0
                        speaking = False
                        samples = []
                        silence = 0
                        while not self.stop.is_set():
                            try:
                                block = self.blocks.get(timeout=0.2)
                            except queue.Empty:
                                continue
                            loud = float(np.sqrt(np.mean(block * block))) >= self.min_rms
                            if loud and not speaking:
                                speaking = True
                                samples = []
                                silence = 0
                            if not speaking:
                                continue
                            samples.append(block)
                            silence = 0 if loud else silence + 1
                            if silence >= 6 or len(samples) >= 50:
                                audio = np.concatenate(samples)
                                if len(audio) >= 6400:
                                    self._transcribe(audio)
                                speaking = False
                                samples = []
                                # Inference may take longer than capture. Drop stale blocks.
                                while not self.blocks.empty():
                                    try:
                                        self.blocks.get_nowait()
                                    except queue.Empty:
                                        break
                except Exception as exc:
                    if not self.stop.is_set():
                        context.emit("speech.error", type(exc).__name__, retry_in=retry)
                finally:
                    self.stream = None
                if self.stop.wait(retry):
                    return
                retry = min(retry * 2, 15.0)

        def _transcribe(self, audio):
            language = None if self.language == "auto" else self.language
            segments, info = self.model.transcribe(
                audio,
                language=language,
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            phrase = " ".join(segment.text.strip() for segment in segments).strip()
            if not phrase:
                return
            detected_language = getattr(info, "language", None) or language or "unknown"
            context.emit("speech.transcript", phrase, language=detected_language)
            action = match_command(phrase, self.commands)
            if action:
                context.emit("speech.command", action, phrase=phrase, language=detected_language)

        def close(self):
            if hasattr(self, "stop"):
                self.stop.set()
            if getattr(self, "stream", None) is not None:
                try:
                    self.stream.abort()
                except Exception as exc:
                    if not self.stop.is_set():
                        context.emit("speech.error", type(exc).__name__)
            if hasattr(self, "thread"):
                self.thread.join(timeout=2)

    return Voice()
