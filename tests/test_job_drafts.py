"""Job email drafts (jarvis.job_drafts): which emails get a reply, where it goes, no repeats.

Usage: python -m unittest tests.test_job_drafts (Gmail, Zoho, the calendar and Claude are faked)
"""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from jarvis import job_drafts
from jarvis.tools.clock import TZ

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)


def found(*emails):
    return {"emails": [{"id": id, "from": sender, "to": "", "subject": subject, "date": "",
                        "snippet": "", "unread": True, "account": account}
                       for id, account, sender, subject in emails]}


def reply(text):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=text)])


class JobDrafts(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(job_drafts.config, "JOB_DRAFTS", True),
                   mock.patch.object(job_drafts.config, "ZOHO_USER", "me@example.ca"),
                   mock.patch.object(job_drafts.config, "OWNER_NAME", "Me"),
                   mock.patch.object(job_drafts.zoho, "enabled", return_value=True),
                   mock.patch.object(job_drafts, "_calendar", return_value="(nothing on it)"),
                   mock.patch.object(job_drafts, "_gmail_address", "me@gmail.com"),
                   mock.patch.object(job_drafts.config, "OWNER_EMAILS", ["me@school.ca"]),
                   mock.patch.object(job_drafts.notify, "post")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.draft = mock.patch.object(job_drafts.zoho, "draft").start()
        self.addCleanup(mock.patch.stopall)
        self.originals = {}
        mock.patch.object(job_drafts, "original", side_effect=lambda id: self.originals[id]).start()
        self.picks = []

        def claude(**kwargs):
            if kwargs["model"] == job_drafts.DRAFT_MODEL:
                return reply("Hi Sam,\n\nSounds good!\n\nThanks,\nMe")
            ids = [e["id"] for e in json.loads(kwargs["messages"][0]["content"])] + self.picks
            return reply(json.dumps({"emails": [{"id": i, "why": "recruiter", "needs_reply": i in self.picks}
                                                for i in ids]}))
        self.claude = mock.patch.object(job_drafts, "_claude", side_effect=claude).start()

    def run_check(self, state, inbox):
        with mock.patch.object(job_drafts.gmail, "search_email", return_value=inbox):
            job_drafts.check(state, NOW)

    def test_first_run_only_learns(self):
        state = {}
        self.run_check(state, found(("zoho:INBOX:1", "zoho", "Sam <sam@acme.com>", "Interview")))
        self.claude.assert_not_called()
        self.assertEqual(state["job_drafts"]["accounts"], ["gmail", "zoho"])
        self.assertEqual(state["job_drafts"]["seen"], ["zoho:INBOX:1"])

    def test_drafts_reply_to_the_sender_once(self):
        state = {"job_drafts": {"accounts": ["gmail", "zoho"], "seen": []}}
        inbox = found(("g1", "gmail", "Sam Lee <sam@acme.com>", "Internship interview"),
                      ("g2", "gmail", "Shop <deals@shop.com>", "Sale"))
        self.picks = ["g1"]
        self.originals["g1"] = ({"from": "Sam Lee <sam@acme.com>",
                                 "reply-to": "Recruiting <jobs@acme.com>",
                                 "to": "Me <me@gmail.com>, Pat <pat@acme.com>",
                                 "cc": "jobs@acme.com, ME@school.ca, Kim <kim@acme.com>",
                                 "subject": "Internship interview", "message-id": "<a@acme>",
                                 "references": "<z@acme>"}, "When are you free?")
        self.run_check(state, inbox)
        self.draft.assert_called_once_with(
            "jobs@acme.com", "Re: Internship interview", "Hi Sam,\n\nSounds good!\n\nThanks,\nMe",
            "", cc="pat@acme.com, kim@acme.com", in_reply_to="<a@acme>", references="<z@acme>")
        self.run_check(state, inbox)  # nothing new: no second draft
        self.assertEqual(self.draft.call_count, 1)

    def test_zoho_email_threads_by_id(self):
        state = {"job_drafts": {"accounts": ["zoho"], "seen": []}}
        self.picks = ["zoho:INBOX:7"]
        self.originals["zoho:INBOX:7"] = ({"from": "sam@acme.com", "subject": "RE: Offer",
                                           "message-id": "<b@acme>"}, "Offer attached")
        self.run_check(state, found(("zoho:INBOX:7", "zoho", "sam@acme.com", "RE: Offer")))
        args = self.draft.call_args
        self.assertEqual(args.args[:2], ("sam@acme.com", "RE: Offer"))
        self.assertEqual(args.args[3], "zoho:INBOX:7")

    def test_no_reply_and_own_mail_skipped(self):
        state = {"job_drafts": {"accounts": ["gmail"], "seen": []}}
        self.picks = ["g1"]
        self.originals["g1"] = ({"from": "Acme <no-reply@acme.com>", "subject": "Interview"}, "")
        self.run_check(state, found(("g1", "gmail", "Acme <no-reply@acme.com>", "Interview"),
                                    ("g2", "gmail", "Me <me@example.ca>", "Fwd: resume")))
        self.draft.assert_not_called()
        listing = json.loads(self.claude.call_args.kwargs["messages"][0]["content"])
        self.assertEqual([e["id"] for e in listing], ["g1"])  # own email never shown

    def test_pick_ignores_made_up_ids(self):
        self.picks = ["g9"]
        self.assertEqual(job_drafts.pick(found(("g1", "gmail", "a@b.c", "x"))["emails"]), {})

    def test_off(self):
        state = {}
        with mock.patch.object(job_drafts.config, "JOB_DRAFTS", False):
            self.run_check(state, found(("g1", "gmail", "a@b.c", "x")))
        self.assertEqual(state, {})


if __name__ == "__main__":
    unittest.main()
