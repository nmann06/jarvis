"""Cancel phrases ("Hey Jarvis, never mind"): the matcher, and _answer saying nothing for them.

Usage: python -m unittest tests.test_cancel (says nothing; Claude and the speaker are faked)
"""

import unittest
from unittest import mock

from jarvis import voice

CANCEL = ["Never mind.", "Nevermind", "Hey Jarvis, never mind.", "Jarvis, cancel.", "Cancel",
          "Cancel that.", "Oh, cancel that, sorry.", "Forget it.", "Forget about it",
          "Actually, forget it.", "Um, never mind, sir.", "Stop.", "Hey Jarvis stop",
          "Don't worry about it.", "Don't worry.", "It doesn't matter.", "Doesn't matter",
          "No, never mind.", "Oh no, never mind then.", "Sorry, never mind, Jarvis.",
          "OK Jarvis, cancel it please.", "Never mind, thanks.", "Actually I changed my mind.",
          "No.", "Nope", "Nothing.", "No, nothing.", "Nothing, sir.", "Not now.",
          "No thanks.", "Okay, never mind."]
NOT = ["Cancel my alarm.", "Cancel my 7:30 alarm", "Never mind the weather, what time is it?",
       "Never mind that, play some music", "Stop the music", "Forget it, text Hyde instead",
       "Nothing is working", "No, I said Kingston", "Don't text him", "What's the time?",
       "Hey Jarvis", "Jarvis", "Stop by the office tomorrow", "Cancel the meeting",
       "No more music", "It doesn't matter what you think", "Continue", ""]
# Bare declines answer a question Jarvis asked ("Shall I send it?" "No."); real cancels don't.
ANSWERS = ["No.", "Nope", "Nothing.", "No, nothing.", "Not now", "No thanks"]


class CancelMatcher(unittest.TestCase):
    def test_cancels(self):
        for text in CANCEL:
            with self.subTest(text=text):
                self.assertTrue(voice.is_cancel(text))

    def test_not_cancels(self):
        for text in NOT:
            with self.subTest(text=text):
                self.assertFalse(voice.is_cancel(text))
                self.assertFalse(voice.is_cancel(text, answering=True))

    def test_answers(self):
        for text in ANSWERS:
            with self.subTest(text=text):
                self.assertFalse(voice.is_cancel(text, answering=True))
        for text in ["Never mind.", "Cancel that", "Forget it", "No, never mind"]:
            with self.subTest(text=text):
                self.assertTrue(voice.is_cancel(text, answering=True))

    def test_other_matchers_unchanged(self):
        self.assertTrue(voice.is_wake_only("Hey Jarvis?"))
        self.assertFalse(voice.is_wake_only("Hey Jarvis, never mind"))
        self.assertIsNotNone(voice.local_answer("What time is it?"))
        self.assertIsNone(voice.local_answer("Never mind"))


class Utterance:
    seconds = 1.0

    def __init__(self, text):
        self.text = text

    def transcribe(self):
        return self.text, 0.0, 0.0


class Answer(unittest.TestCase):
    """_answer with Claude and the speaker faked."""

    def setUp(self):
        patches = [mock.patch.object(voice.spotify, "fix_playlists", side_effect=lambda t: t),
                   mock.patch.object(voice.extras, "fix_transcript", side_effect=lambda t: t),
                   mock.patch.object(voice.extras, "contact_name", return_value=None),
                   mock.patch.object(voice, "_speak", return_value=(None, False)),
                   mock.patch.object(voice.alarms, "ringing", return_value=False),
                   mock.patch.object(voice.alarms, "after_reply")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.brain = mock.Mock()
        for name in ("_texting", "_awaiting_please", "_last_reply", "_unfinished"):
            self.addCleanup(setattr, voice, name, getattr(voice, name))

    def answer(self, text, follow_up=False):
        turn = mock.Mock()
        return voice._answer(mock.Mock(), self.brain, Utterance(text), follow_up, turn), turn

    def test_never_mind_says_nothing(self):
        voice._texting, voice._awaiting_please = "Hyde", {"asks": 1, "request": "text Hyde"}
        answered, turn = self.answer("Hey Jarvis, never mind.", follow_up=True)
        self.assertIs(answered, False)
        self.assertEqual(turn.outcome, "cancelled")
        self.assertIsNone(voice._texting)
        self.assertIsNone(voice._awaiting_please)
        voice._speak.assert_not_called()
        self.brain.ask.assert_not_called()

    def test_no_after_a_question_goes_on(self):
        voice._last_reply = "Shall I send it, sir?"
        with mock.patch.object(voice.alarms, "local_reply", return_value="sentinel") as reply:
            answered, turn = self.answer("No.", follow_up=True)
        reply.assert_called_once()  # got past the cancel check
        self.assertNotEqual(turn.outcome, "cancelled")

    def test_not_while_an_alarm_rings(self):
        voice.alarms.ringing.return_value = True
        with mock.patch.object(voice.alarms, "local_reply", return_value="Alarm off, sir.") as r:
            answered, turn = self.answer("Cancel.")
        r.assert_called_once()
        self.assertEqual(turn.outcome, "alarm (local)")
        self.assertIs(answered, True)


if __name__ == "__main__":
    unittest.main()
