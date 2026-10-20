"""Wake word detection with openWakeWord's hey_jarvis model.

Run: python -m jarvis.wakeword  to print WAKE and the captured utterance on each detection.
"""

import time

import numpy as np
from openwakeword.model import Model
from openwakeword.utils import download_models

from jarvis import audio, config

MODEL = "hey_jarvis"


class WakeWord:
    def __init__(self):
        download_models([MODEL])  # no-op once the model files are downloaded
        self._model = Model(wakeword_models=[MODEL], inference_framework="onnx")

    def wait(self, mic):
        """Block until the wake word is heard; returns the detection score."""
        while True:
            score = self._model.predict(mic.read())[MODEL]
            if score >= config.WAKE_THRESHOLD:
                # Clear the model's rolling buffer so one "Hey Jarvis" can't trigger twice.
                self._model.reset()
                return score


if __name__ == "__main__":
    from jarvis import stt

    wake = WakeWord()
    stt.get_model()
    print(f"Listening for 'Hey Jarvis' (threshold {config.WAKE_THRESHOLD}). Ctrl+C to stop.")
    with audio.Mic() as mic:
        try:
            while True:
                score = wake.wait(mic)
                woke = time.perf_counter()
                print(f"WAKE (score {score:.2f})", flush=True)
                utterance = audio.record_utterance(mic)
                if utterance is None:
                    print("  (no speech after wake word)", flush=True)
                    continue
                ended = time.perf_counter()
                text = stt.transcribe(utterance)
                print(f"  utterance {len(utterance) / audio.SAMPLE_RATE:.1f}s, "
                      f"recorded in {ended - woke:.1f}s, transcribed in "
                      f"{time.perf_counter() - ended:.2f}s, peak {np.abs(utterance).max():.2f}",
                      flush=True)
                print(f"  > {text}", flush=True)
        except KeyboardInterrupt:
            pass
