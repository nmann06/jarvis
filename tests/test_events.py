"""The voice hooks (jarvis.events) and the small calls to them in the voice loop.

Usage: python -m unittest tests.test_events (simulated audio: no mic, no speaker, no real socket
path; Claude, Vosk and Whisper are faked)
"""

import ast
import json
import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from jarvis import audio, events, tts, voice, wakeword

ROOT = Path(__file__).resolve().parent.parent


def tone(level=1000, n=events.BLOCK):
    return np.full(n, level, dtype=np.int16)


class Hooks(unittest.TestCase):
    """start() on a temp socket for each test, stop() after."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ev")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "s.sock"
        patcher = mock.patch.object(events, "SOCKET", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(events.stop)
        events.stop()
        self.alarm = False
        events.start(lambda: self.alarm)

    def connect(self, request):
        conn = socket.socket(socket.AF_UNIX)
        conn.settimeout(3)
        conn.connect(str(self.path))
        self.addCleanup(conn.close)
        conn.sendall(json.dumps(request).encode() + b"\n")
        return conn


class RingBuffer(Hooks):
    def test_keeps_the_last_30_seconds_only(self):
        self.assertEqual(events.BUFFER_BLOCKS, 30 * audio.SAMPLE_RATE // audio.BLOCK)
        self.assertEqual((events.SAMPLE_RATE, events.BLOCK), (audio.SAMPLE_RATE, audio.BLOCK))
        for i in range(events.BUFFER_BLOCKS + 50):
            events.mic_block(tone(i % 30000))
        samples = events.audio()
        self.assertEqual(len(samples), 30 * events.SAMPLE_RATE)
        self.assertEqual(samples[-1], (events.BUFFER_BLOCKS + 49) % 30000)
        self.assertEqual(samples[0], 50)

    def test_write_is_cheap_even_with_a_stalled_subscriber(self):
        stalled = self.connect({"cmd": "subscribe"})  # never reads
        time.sleep(0.1)
        block, started = tone(), time.perf_counter()
        for _ in range(2000):
            events.mic_block(block)
        each = (time.perf_counter() - started) / 2000
        self.assertLess(each, 0.001)  # a block is 80 ms
        stalled.close()

    def test_mic_callback_queues_what_the_hook_returns(self):
        mic = audio.Mic.__new__(audio.Mic)  # no stream: nothing opens the mic
        mic._queue, mic.captured = audio.queue.Queue(), None
        mic._callback(tone(7).reshape(-1, 1), audio.BLOCK, None, None)
        self.assertEqual(mic.read(timeout=0)[0], 7)
        self.assertEqual(events.audio()[0], 7)


class Mute(Hooks):
    def test_silence_while_muted_and_nothing_kept(self):
        events.set_muted(True)
        self.assertFalse(events.mic_block(tone(5000)).any())
        self.assertFalse(events.audio().any())

    def test_alarm_is_heard_through_mute(self):
        events.set_muted(True)
        self.alarm = True
        self.assertTrue(events.mic_block(tone(5000)).all())

    def test_listens_if_alarm_check_fails(self):
        events.set_muted(True)
        with mock.patch.object(events, "_alarm_ringing", side_effect=RuntimeError("boom")):
            with self.assertLogs("jarvis.events", "ERROR"):
                self.assertTrue(events.mic_block(tone(5000)).all())


class NeverBreaks(Hooks):
    def test_every_hook_swallows_its_failure(self):
        calls = [(events.set_state, ("thinking",)), (events.speaking, (True,)),
                 (events.wake_score, (0.1, 0.5)), (events.wake_fired, (0.9,)),
                 (events.wake_ignored, ("why",)), (events.whisper_heard, ("x",)),
                 (events.stop_event, ("echo", "stop", "stop"))]
        boom = mock.Mock(side_effect=RuntimeError("boom"))
        events._complained.clear()
        with mock.patch.object(events, "_publish", boom), mock.patch.object(events, "_states", None), \
                mock.patch.object(events, "_scores", None), mock.patch.object(events, "_wakes", None), \
                mock.patch.object(events, "_stops", None), self.assertLogs("jarvis.events", "ERROR"):
            for hook, args in calls:
                hook(*args)  # no exception

    def test_bad_arguments_are_swallowed(self):
        events._complained.clear()
        with self.assertLogs("jarvis.events", "ERROR"):
            events.wake_score("not a number", None)
            events.stop_event("stopped", score="nope")

    def test_mic_block_hands_back_the_block_on_failure(self):
        block = tone(3)
        events._complained.clear()
        with mock.patch.object(events, "_ring", None), self.assertLogs("jarvis.events", "ERROR"):
            self.assertIs(events.mic_block(block), block)

    def test_a_persistent_failure_is_logged_once_a_minute(self):
        events._complained.clear()
        with mock.patch.object(events, "_ring", None), mock.patch.object(events.log, "exception") as logged:
            for _ in range(100):
                events.mic_block(tone())
        self.assertEqual(logged.call_count, 1)

    def test_start_failure_is_swallowed(self):
        events.stop()
        with self.assertLogs("jarvis.events", "ERROR"):
            events.start(path="/proc/no/such/place/s.sock")
        self.assertIs(type(events.mic_block(tone())), np.ndarray)

    def test_no_hook_name_is_shadowed(self):
        # An earlier change shadowed a name in voice.py and crashed every voice turn: anything
        # assigned to "events" in a function makes it a local there.
        for name in ("voice", "audio", "wakeword", "tts"):
            tree = ast.parse((ROOT / "jarvis" / f"{name}.py").read_text())
            for node in ast.walk(tree):
                bound = isinstance(node, ast.Name) and node.id == "events" \
                    and isinstance(node.ctx, (ast.Store, ast.Del))
                arg = isinstance(node, ast.arg) and node.arg == "events"
                self.assertFalse(bound or arg, f"{name}.py rebinds events at line {getattr(node, 'lineno', 0)}")


class SocketServer(Hooks):
    def lines(self, conn, n):
        data = b""
        while data.count(b"\n") < n:
            more = conn.recv(65536)
            if not more:
                break
            data += more
        return [json.loads(line) for line in data.splitlines()[:n]]

    def test_socket_is_owner_only(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_status(self):
        status = self.lines(self.connect({"cmd": "status"}), 1)[0]
        self.assertEqual((status["state"], status["muted"]), ("idle", False))

    def test_mute(self):
        status = self.lines(self.connect({"cmd": "mute", "muted": True}), 1)[0]
        self.assertTrue(status["muted"])
        self.assertTrue(events._muted)

    def test_snapshot_returns_buffer_and_context(self):
        for i in range(5):
            events.mic_block(tone(i + 1))
            events.wake_score(0.1 * i, 0.5)
        events.wake_fired(0.81)
        events.whisper_heard("the stamp")
        events.stop_event("echo", "stop", "stop")
        conn = self.connect({"cmd": "snapshot"})
        data = b""
        while (more := conn.recv(65536)):
            data += more
        line, pcm = data.split(b"\n", 1)
        header = json.loads(line)
        self.assertEqual(header["samples"], 5 * events.BLOCK)
        self.assertEqual(len(pcm), 2 * header["samples"])
        self.assertEqual(np.frombuffer(pcm, "<i2")[0], 1)
        context = header["context"]
        self.assertAlmostEqual(context["peak_wake_score"], 0.4)
        self.assertEqual(context["threshold"], 0.5)
        self.assertEqual(context["wakes"][-1]["score"], 0.81)
        self.assertEqual(context["stop_events"][-1]["whisper"], "the stamp")
        self.assertEqual(context["stop_events"][-1]["decision"], "echo")

    def test_subscribe_streams_state_and_level(self):
        conn = self.connect({"cmd": "subscribe"})
        self.assertEqual(self.lines(conn, 1)[0]["type"], "status")
        events.wake_score(0.25, 0.5)
        events.set_state("listening")
        events.mic_block(tone(3277))
        state, level = self.lines(conn, 2)
        self.assertEqual((state["type"], state["state"]), ("state", "listening"))
        self.assertEqual(set(level), {"type", "rms", "peak", "wake_score", "t"})
        self.assertAlmostEqual(level["rms"], 0.1, places=2)
        self.assertAlmostEqual(level["peak"], 0.1, places=2)
        self.assertEqual(level["wake_score"], 0.25)

    def test_full_scale_negative_peak(self):
        conn = self.connect({"cmd": "subscribe"})
        self.lines(conn, 1)
        events.mic_block(tone(-32768))
        self.assertEqual(self.lines(conn, 1)[0]["peak"], 1.0)

    def test_stalled_subscriber_drops_messages_not_the_mic(self):
        conn = self.connect({"cmd": "subscribe"})
        self.lines(conn, 1)
        time.sleep(0.1)
        for _ in range(events.CLIENT_QUEUE * 3):
            events.mic_block(tone())
        self.assertLessEqual(len(events._clients[0]), events.CLIENT_QUEUE)

    def test_garbage_request_is_harmless(self):
        conn = socket.socket(socket.AF_UNIX)
        self.addCleanup(conn.close)
        conn.connect(str(self.path))
        conn.sendall(b"not json\n")
        conn.settimeout(3)
        self.assertEqual(conn.recv(100), b"")
        self.assertEqual(self.lines(self.connect({"cmd": "status"}), 1)[0]["type"], "status")


class States(Hooks):
    def history(self):
        return [s["state"] for s in events._states]

    def test_speaking_goes_back_to_what_it_was(self):
        events.set_state("listening")
        events.speaking(True)
        events.speaking(True)
        events.speaking(False)
        self.assertEqual(self.history(), ["listening", "speaking", "listening"])

    def test_speaking_finished_after_state_moved_on_changes_nothing(self):
        events.speaking(True)
        events.set_state("thinking")
        events.speaking(False)
        self.assertEqual(events._state, "thinking")

    def test_record_utterance_nobody_spoke(self):
        mic = SimpleNamespace(read=lambda timeout=None: tone(0), captured=0.0)
        with mock.patch.object(audio, "speech_probability", return_value=0.0):
            self.assertIsNone(audio.record_utterance(mic, lambda a: "", wait_seconds=0.3))
        self.assertEqual(self.history(), ["listening", "idle"])

    def test_handle_thinking_then_idle(self):
        utterance = SimpleNamespace(speech_end=time.perf_counter(), ended=time.perf_counter(),
                                    endpoint="pause", seconds=1.0)
        seen = []

        def answer(*args):
            seen.append(events._state)
            return True
        with mock.patch.object(voice, "_answer", answer), \
                mock.patch.object(voice.latency.Turn, "finish"):
            self.assertTrue(voice.handle(None, None, utterance))
        self.assertEqual(seen, ["thinking"])
        self.assertEqual(events._state, "idle")

    def test_handle_goes_idle_after_a_failure(self):
        utterance = SimpleNamespace(speech_end=time.perf_counter(), ended=time.perf_counter(),
                                    endpoint="pause", seconds=1.0)
        with mock.patch.object(voice, "_answer", side_effect=KeyError("x")), \
                mock.patch.object(voice.latency.Turn, "finish"), self.assertRaises(KeyError):
            voice.handle(None, None, utterance)
        self.assertEqual(events._state, "idle")

    def test_speak_all_marks_speaking_only_once_sound_starts(self):
        chunk = SimpleNamespace(audio_int16_array=tone(1, 2205))
        seen = []
        out = mock.MagicMock(latency=0.0)
        out.__enter__.return_value = out
        out.write.side_effect = lambda piece: seen.append(events._state)
        fake = SimpleNamespace(config=SimpleNamespace(sample_rate=22050),
                               synthesize=lambda text, syn_config=None: [chunk])
        events.set_state("thinking")
        with mock.patch.object(tts, "get_voice", return_value=fake), \
                mock.patch.object(tts.sd, "OutputStream", return_value=out) as stream:
            self.assertIsNotNone(tts.speak_all(["Hello."]))
        stream.assert_called_once()  # the fake: nothing reaches the speaker
        self.assertEqual(set(seen), {"speaking"})
        self.assertEqual(self.history(), ["thinking", "speaking", "thinking"])


class FakeRecognizer:
    """Vosk saying text once, then nothing."""

    def __init__(self, text):
        self.text = text

    def AcceptWaveform(self, data):
        return bool(self.text)

    def Result(self):
        text, self.text = self.text, ""
        return json.dumps({"text": text, "result": [
            {"word": w, "start": 0.0, "end": 0.1} for w in text.split()]})

    def PartialResult(self):
        return json.dumps({"partial": ""})

    def FinalResult(self):
        return json.dumps({"text": ""})


class StopDetection(Hooks):
    """voice._listen_for_stop, the real thing, with Vosk and Whisper faked."""

    def listen(self, vosk, whisper="", saying="All done.", reply="All done.", wake=None):
        stop, done = threading.Event(), threading.Event()
        blocks = iter([tone()] * 3)

        def read(timeout=None):
            block = next(blocks, None)
            if block is None:
                done.set()
            return block
        mic = SimpleNamespace(read=read, captured=0.0)
        recognizers = iter([FakeRecognizer(vosk), FakeRecognizer(""), FakeRecognizer("")])
        with mock.patch.object(voice, "_stop_recognizer", lambda: next(recognizers)), \
                mock.patch.object(voice.stt, "transcribe", return_value=whisper), \
                mock.patch.object(voice.config, "SAVE_STOP_AUDIO", False):
            voice._listen_for_stop(mic, stop, done, lambda: saying, lambda: reply, wake)
        return stop

    def last(self):
        return events._stops[-1]

    def test_real_stop(self):
        stop = self.listen("it yes sir stop", whisper="Stop!")
        self.assertTrue(stop.is_set())
        event = self.last()
        self.assertEqual((event["decision"], event["vosk"], event["phrase"], event["whisper"]),
                         ("stopped", "it yes sir stop", "stop", "Stop!"))

    def test_echo(self):
        stop = self.listen("stop", whisper="all done", reply="All done now.")
        self.assertFalse(stop.is_set())
        self.assertEqual((self.last()["decision"], self.last()["whisper"]), ("echo", "all done"))

    def test_own_words(self):
        stop = self.listen("stop", saying="Stop by the office.")
        self.assertFalse(stop.is_set())
        self.assertEqual(self.last()["decision"], "own_words")

    def test_not_a_stop(self):
        self.assertFalse(self.listen("yes sir").is_set())
        self.assertEqual((self.last()["decision"], self.last()["vosk"]), ("not_a_stop", "yes sir"))

    def test_wake_stops_it(self):
        wake = mock.Mock()
        wake.heard.return_value = 0.7
        stop = self.listen("", wake=wake)
        self.assertTrue(stop.woken)
        self.assertEqual((self.last()["decision"], self.last()["wake_score"]), ("wake_stopped", 0.7))

    def test_stop_still_works_with_broken_hooks(self):
        events._complained.clear()
        with mock.patch.object(events, "_stops", None), self.assertLogs("jarvis.events", "ERROR"):
            self.assertTrue(self.listen("stop", whisper="Stop!").is_set())


class Wake(Hooks):
    def word(self, scores):
        word = wakeword.WakeWord.__new__(wakeword.WakeWord)
        word._model = mock.Mock()
        word._model.predict.side_effect = [{wakeword.MODEL: s} for s in scores]
        word.preroll = wakeword.deque(maxlen=5)
        return word

    def test_scores_and_firing_recorded(self):
        word = self.word([np.float32(0.1), np.float32(0.3), np.float32(0.9)])
        with mock.patch.object(wakeword.config, "WAKE_THRESHOLD", 0.5):
            results = [word.heard(tone()) for _ in range(3)]
        self.assertIsNone(results[0])
        self.assertAlmostEqual(float(results[2]), 0.9, places=5)
        context = events.context()
        self.assertAlmostEqual(context["peak_wake_score"], 0.9, places=5)
        self.assertEqual(context["threshold"], 0.5)
        self.assertEqual(len(context["wakes"]), 1)
        json.dumps(context)  # plain numbers, not numpy's

    def test_wake_still_fires_with_broken_hooks(self):
        word = self.word([0.9])
        events._complained.clear()
        with mock.patch.object(wakeword.config, "WAKE_THRESHOLD", 0.5), \
                mock.patch.object(events, "_scores", None), mock.patch.object(events, "_wakes", None), \
                self.assertLogs("jarvis.events", "ERROR"):
            self.assertEqual(word.heard(tone()), 0.9)

    def test_runs_without_start(self):
        events.stop()  # python -m jarvis.wakeword
        block = tone()
        self.assertIs(events.mic_block(block), block)
        self.assertEqual(len(events._ring), 0)


class LoopDone(Exception):
    pass


class RunLoop(Hooks):
    """voice.run's loop, with everything around it faked: a wake, nothing said, end chime."""

    def test_wake_listening_idle(self):
        events.stop()  # run() starts them itself
        seen = []
        wake = mock.Mock()
        wake.wait.side_effect = [0.9, LoopDone()]
        wake.preroll = ()
        mic = mock.MagicMock()
        mic.__enter__.return_value = mic
        mic.captured = time.perf_counter()

        def record(*args, **kwargs):
            seen.append(events._state)
            return None
        with mock.patch.object(voice, "WakeWord", return_value=wake), \
                mock.patch.object(voice.llm, "Brain"), mock.patch.object(voice.tools, "warm_up"), \
                mock.patch.object(voice.stt, "warm_up"), mock.patch.object(voice.tts, "get_voice"), \
                mock.patch.object(voice, "_stop_recognizer"), mock.patch.object(voice.notify, "register"), \
                mock.patch.object(voice.spotify, "duck"), mock.patch.object(voice.extras, "listeners", return_value=[]), \
                mock.patch.object(voice, "play"), \
                mock.patch.object(voice.audio, "Mic", return_value=mic), \
                mock.patch.object(voice.audio, "record_utterance", record), \
                mock.patch.object(voice.alarms, "on_stop", []), self.assertRaises(LoopDone):
            voice.run()
        self.assertEqual(seen, ["listening"])
        self.assertEqual([s["state"] for s in events._states], ["listening", "idle"])
        self.assertTrue(self.path.exists())


class VoiceStillWorks(unittest.TestCase):
    def test_matchers(self):
        self.assertTrue(voice.is_wake_only("Hey Jarvis?"))
        self.assertFalse(voice.is_wake_only("Hey Jarvis, what time is it?"))
        self.assertTrue(voice.local_answer("What time is it?").startswith("It's "))
        self.assertTrue(voice.local_answer("What's the date?").startswith("It's "))
        self.assertIsNone(voice.local_answer("What time is my class?"))


if __name__ == "__main__":
    unittest.main()
