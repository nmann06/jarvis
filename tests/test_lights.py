"""Lights on/off without Claude: the phrase matcher, _match's "all", and switch_all's replies.

Usage: python -m unittest tests.test_lights (switches nothing; the switches are faked)
"""

import unittest
from unittest import mock

from jarvis.tools import kasa

ON = ["Lights on.", "lights on", "Light on", "Turn the lights on.", "Turn on the lights.",
      "Jarvis, lights on please.", "Hey Jarvis, turn on the lights", "Switch the lights on",
      "Can you turn the lights on for me, please?", "All lights on", "Turn all the lights on",
      "Turn on all of the lights", "OK Jarvis, lights on, sir.", "Put the lights on",
      "Turn my lights on"]
OFF = ["Lights off.", "lights out", "Turn off the lights.", "Turn the lights off, please.",
       "Could you turn off the lights?", "Kill the lights", "Turn the lights out",
       "Jarvis, lights off", "Switch off all the lights", "All the lights off now"]
NOT = ["Are the lights on?", "Turn off the bedroom light", "Turn light 1 off",
       "Turn the lights on at seven", "Lights on in five minutes", "Which lights are on?",
       "Turn off the lights and play some music", "Turn on the lamp", "Is light two on?",
       "Turn the music off", "Turn it off", "I left the lights on", "Leave the lights on",
       "Don't turn the lights off", "Turn the lights down", ""]


class Device:
    def __init__(self, alias):
        self.alias = alias

    def get_alias(self):
        return self.alias


DEVICES = {name: Device(name) for name in ("Light 1", "Light 2", "Light 3")}


class Phrases(unittest.TestCase):
    def test_on(self):
        for text in ON:
            self.assertEqual(kasa.all_lights_request(text), "on", text)

    def test_off(self):
        for text in OFF:
            self.assertEqual(kasa.all_lights_request(text), "off", text)

    def test_toggle(self):
        for text in ("Hey Jarvis, lights.", "lights", "Lights please.", "toggle the lights",
                     "Jarvis, the lights."):
            self.assertEqual(kasa.all_lights_request(text), "toggle", text)

    def test_not_bare(self):
        for text in NOT:
            self.assertIsNone(kasa.all_lights_request(text), text)


class Match(unittest.TestCase):
    def names(self, name):
        return [d.get_alias() for d in kasa._match(name, DEVICES)]

    def test_all(self):
        for name in ("all", "everything", "", "lights", "the lights", "all the lights",
                     "all three", "all 3 lights", "all of them", "every light", "each light"):
            self.assertEqual(self.names(name), list(DEVICES), name)

    def test_one(self):
        self.assertEqual(self.names("light 1"), ["Light 1"])
        self.assertEqual(self.names("the light two"), ["Light 2"])
        self.assertEqual(self.names("bedroom"), [])


class SwitchAll(unittest.TestCase):
    def switch(self, action, results):
        with mock.patch.object(kasa, "_devices", DEVICES), \
             mock.patch.object(kasa, "_act_retrying", return_value=results) as act:
            reply = kasa.switch_all(action)
        self.assertEqual(act.call_args.args, (list(DEVICES.values()), action))
        return reply

    def test_all_answer(self):
        self.assertEqual(self.switch("on", [True, True, True]), "Lights on, sir.")
        self.assertEqual(self.switch("off", [False, False, False]), "Lights off, sir.")

    def test_some_unreachable(self):
        self.assertEqual(self.switch("off", [False, None, False]),
                         "Light 2 didn't answer, I'm afraid; the rest are off.")
        self.assertEqual(self.switch("on", [None, True, OSError()]),
                         "Light 1 and Light 3 didn't answer, I'm afraid; the rest are on.")

    def test_none_answer(self):
        self.assertEqual(self.switch("on", [None, None, None]),
                         "The lights didn't answer, I'm afraid.")

    def test_toggle(self):
        for states, action in (([False, True, False], "off"), ([False, False, None], "on")):
            with mock.patch.object(kasa, "_devices", DEVICES), \
                 mock.patch.object(kasa, "_act_retrying",
                                   side_effect=[states, [action == "on"] * 3]) as act:
                self.assertEqual(kasa.switch_all("toggle"), f"Lights {action}, sir.")
            self.assertEqual([c.args[1] for c in act.call_args_list], ["status", action])

    def test_no_cloud(self):
        with mock.patch.object(kasa, "_devices", None), \
             mock.patch.object(kasa, "_list", side_effect=RuntimeError(kasa.NOT_LOGGED_IN)):
            self.assertEqual(kasa.switch_all("on"), "I couldn't reach the lights, I'm afraid.")


if __name__ == "__main__":
    unittest.main()
