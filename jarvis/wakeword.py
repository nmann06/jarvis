"""Wake word detection with openWakeWord's hey_jarvis model.

Run: python -m jarvis.wakeword  to print WAKE and the captured utterance on each detection.
"""

import time
from collections import deque

import numpy as np
from openwakeword.model import Model
from openwakeword.utils import download_models

from jarvis import audio, config

MODEL = "hey_jarvis"
PREROLL_BLOCKS = 5  # 400 ms: the model fires late in "Jarvis", so keep the audio just before


class WakeWord:
    def __init__(self):
        download_models([MODEL])  # no-op once the model files are downloaded
        self._model = Model(wakeword_models=[MODEL], inference_framework="onnx")
        self.preroll = deque(maxlen=PREROLL_BLOCKS)

    def wait(self, mic):
        """Block until the wake word is heard; returns the detection score.

        Afterwards self.preroll holds the last blocks heard, which may contain the start of
        the request if it followed "Hey Jarvis" without a pause.
        """
        self.preroll.clear()
        while True:
            block = mic.read()
            self.preroll.append(block)
            score = self._model.predict(block)[MODEL]
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
                utterance = audio.record_utterance(mic, stt.transcribe, preroll=wake.preroll)
                if utterance is None:
                    print("  (no speech after wake word)", flush=True)
                    continue
                text, _, _ = utterance.transcribe()
                print(f"  utterance {utterance.seconds:.1f}s, recorded in "
                      f"{utterance.ended - woke:.1f}s ({utterance.endpoint} end), text ready "
                      f"{time.perf_counter() - utterance.ended:.2f}s after, "
                      f"peak {np.abs(utterance.audio).max():.2f}", flush=True)
                print(f"  > {text}", flush=True)
        except KeyboardInterrupt:
            pass
