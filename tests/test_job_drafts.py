"""Job email drafts (jarvis.job_drafts): which emails get a reply, where it goes, no repeats;
and draft_email's replies.

Usage: python -m unittest tests.test_job_drafts (Gmail, Zoho, the calendar and Claude are faked)
"""

import base64
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from jarvis import job_drafts
from jarvis.tools import gmail, zoho
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
                                 "date": "Wed, 04 Nov 2026 09:05:00 -0500",
                                 "references": "<z@acme>"}, "When are you free?")
        self.run_check(state, inbox)
        self.draft.assert_called_once_with(
            "", "Re: Internship interview",
            "To: jobs@acme.com\nCc: pat@acme.com, kim@acme.com\n\n"
            "Hi Sam,\n\nSounds good!\n\nThanks,\nMe\n\nOn Wed, Nov 4, 2026 at 9:05 AM, "
            "Sam Lee <sam@acme.com> wrote:\n> When are you free?\n",
            "", in_reply_to="<a@acme>", references="<z@acme>")
        self.run_check(state, inbox)  # nothing new: no second draft
        self.assertEqual(self.draft.call_count, 1)

    def test_zoho_email_threads_by_id(self):
        state = {"job_drafts": {"accounts": ["zoho"], "seen": []}}
        self.picks = ["zoho:INBOX:7"]
        self.originals["zoho:INBOX:7"] = ({"from": "sam@acme.com", "subject": "RE: Offer",
                                           "message-id": "<b@acme>"}, "Offer attached")
        self.run_check(state, found(("zoho:INBOX:7", "zoho", "sam@acme.com", "RE: Offer")))
        args = self.draft.call_args
        self.assertEqual(args.args[:2], ("", "RE: Offer"))
        self.assertTrue(args.args[2].startswith("To: sam@acme.com\n\nHi Sam,"))
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


class DraftEmail(unittest.TestCase):
    """draft_email (jarvis/tools/gmail.py): replies are threaded, quoted and addressed."""

    def setUp(self):
        for p in [mock.patch.object(job_drafts.config, "ZOHO_USER", "me@example.ca"),
                  mock.patch.object(job_drafts, "_gmail_address", "me@gmail.com"),
                  mock.patch.object(job_drafts.config, "OWNER_EMAILS", []),
                  mock.patch.object(gmail.zoho, "enabled", return_value=True),
                  mock.patch.object(gmail.notify, "record"),
                  mock.patch.object(job_drafts, "polish", side_effect=lambda d, h, b: d.upper())]:
            p.start()
            self.addCleanup(p.stop)
        self.original = ({"from": "Mike <mike@gf.com>", "to": "me@example.ca", "cc": "ann@gf.com",
                          "subject": "RE: Internship", "message-id": "<" + "x" * 80 + "@outlook.com>",
                          "references": "<r1@gf> <r2@gf>"}, "Happy to help.\n\nMike")
        p = mock.patch.object(job_drafts, "original", return_value=self.original)
        p.start()
        self.addCleanup(p.stop)

    def test_zoho_reply_goes_to_the_sender_with_the_original_quoted(self):
        with mock.patch.object(gmail.zoho, "draft") as draft:
            gmail.draft_email("", "Thanks!", "Hi Mike, thanks", "zoho:INBOX:30")
        draft.assert_called_once_with(
            "", "RE: Internship", "To: mike@gf.com\nCc: ann@gf.com\n\nHI MIKE, THANKS\n\n"
            "Mike <mike@gf.com> wrote:\n> Happy to help.\n>\n> Mike\n",
            in_reply_to=self.original[0]["message-id"], references="<r1@gf> <r2@gf>")

    def test_gmail_reply_leaves_the_to_box_empty(self):
        with mock.patch.object(gmail, "_gmail") as client:
            api = client.return_value.users.return_value
            api.messages.return_value.get.return_value.execute.return_value = {"threadId": "t1"}
            gmail.draft_email("", "Thanks!", "Hi Mike, thanks", "g1", account="gmail")
        message = api.drafts.return_value.create.call_args.kwargs["body"]["message"]
        head, _, body = base64.urlsafe_b64decode(message["raw"]).decode().partition("\r\n\r\n")
        self.assertEqual(message["threadId"], "t1")
        names = [line.partition(":")[0] for line in head.split("\r\n")]
        self.assertNotIn("To", names)
        self.assertNotIn("Cc", names)
        self.assertTrue(body.startswith("To: mike@gf.com\r\nCc: ann@gf.com\r\n\r\nHI MIKE, THANKS"))

    def test_new_email_leaves_the_to_box_empty_too(self):
        with mock.patch.object(gmail.zoho, "draft") as draft:
            gmail.draft_email("sam@acme.com", "Hello", "Hi Sam", "")
        self.assertEqual(draft.call_args.args[:3], ("", "Hello", "To: sam@acme.com\n\nHI SAM"))

    def test_empty_to_leaves_the_header_out(self):
        with mock.patch.object(zoho, "_connect") as connect:
            connect.return_value.append.return_value = ("OK", [b""])
            zoho.draft("", "Re: x", "hi")
        raw = connect.return_value.append.call_args.args[3].decode()
        self.assertNotIn("\r\nTo:", raw)

    def test_long_message_ids_stay_readable(self):
        with mock.patch.object(zoho, "_connect") as connect:
            connect.return_value.append.return_value = ("OK", [b""])
            zoho.draft("a@b.c", "Re: x", "hi", in_reply_to=self.original[0]["message-id"],
                       references="<r1@gf>")
        raw = connect.return_value.append.call_args.args[3].decode()
        self.assertIn(f"In-Reply-To: {self.original[0]['message-id']}\r\n", raw)
        self.assertIn(f"References: <r1@gf> {self.original[0]['message-id']}\r\n", raw)

    def test_reference_chain_is_trimmed(self):
        chain = zoho.reference_chain(" ".join(f"<{i}@a>" for i in range(20)), "<new@a>").split()
        self.assertEqual(chain, ["<0@a>"] + [f"<{i}@a>" for i in range(11, 20)] + ["<new@a>"])


if __name__ == "__main__":
    unittest.main()
