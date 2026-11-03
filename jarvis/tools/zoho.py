"""Zoho Mail (the owner's own-domain address) over IMAP: search, read and save drafts. Never
sends or deletes.

No tools here: the email tools in gmail.py cover both inboxes, and the ids of Zoho messages
start with "zoho:". Searches take the same Gmail-style queries, turned into IMAP criteria.
"""

import email
import email.policy
import html
import imaplib
import re
import shlex
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from jarvis import config

PREFIX = "zoho:"
FOLDERS = {"inbox": "INBOX", "sent": "Sent", "drafts": "Drafts", "spam": "Spam",
           "trash": "Trash"}
SNIPPET_MAX_BYTES = 300_000  # bigger messages (attachments) are listed without a snippet
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()  # IMAP's, not the locale's
_FETCHED = re.compile(rb"UID (\d+)")


def enabled():
    return bool(config.ZOHO_USER and config.ZOHO_APP_PASSWORD)


def _connect():
    # A new connection per call, like Gmail's client: voice and Discord may both use it at once.
    imap = imaplib.IMAP4_SSL(config.ZOHO_IMAP_HOST, timeout=20)
    imap.login(config.ZOHO_USER, config.ZOHO_APP_PASSWORD)
    return imap


def _quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _date(d):
    return f"{d.day}-{MONTHS[d.month - 1]}-{d.year}"


def criteria(query):
    """(folder, IMAP SEARCH criteria) for a Gmail-style query such as 'is:unread newer_than:2d
    from:prof'. Gmail-only terms (category:, label:, has:, ...) are left out."""
    try:
        tokens = shlex.split(query or "")
    except ValueError:  # an unmatched quote, e.g. "what's"
        tokens = (query or "").split()
    folder, crit = "INBOX", []
    for token in tokens:
        negate = token.startswith("-") and len(token) > 1
        term = token[1:] if negate else token
        key, colon, value = term.partition(":")
        key = key.lower()
        if not term.isascii() or term.upper() in ("OR", "AND"):
            continue  # imaplib only sends ASCII; OR/AND are read as "all of these"
        if colon and key in ("from", "to", "cc", "subject") and value:
            c = [key.upper(), _quote(value)]
        elif colon and key == "in":
            folder = FOLDERS.get(value.lower(), "INBOX")
            continue
        elif colon and key == "is" and value.lower() in ("unread", "read", "starred"):
            c = [{"unread": "UNSEEN", "read": "SEEN", "starred": "FLAGGED"}[value.lower()]]
        elif colon and key in ("newer_than", "older_than") and re.fullmatch(r"\d+[dmy]", value):
            days = int(value[:-1]) * {"d": 1, "m": 30, "y": 365}[value[-1]]
            c = ["SINCE" if key == "newer_than" else "BEFORE",
                 _date(datetime.now() - timedelta(days=days))]
        elif colon and key in ("after", "before") and re.fullmatch(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", value):
            y, m, d = map(int, re.split(r"[/-]", value))
            c = ["SINCE" if key == "after" else "BEFORE", _date(datetime(y, m, d))]
        elif colon and re.fullmatch(r"[a-z_]+", key):
            continue  # a Gmail operator IMAP has nothing like
        else:
            c = ["TEXT", _quote(term)]
        crit += ["NOT", *c] if negate else c
    return folder, crit or ["ALL"]


def _strip_html(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()


def plain_text(msg):
    """Text of a parsed message, preferring text/plain over HTML."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):  # an unknown or wrong charset
        text = (part.get_payload(decode=True) or b"").decode(errors="replace")
    return _strip_html(text) if part.get_content_type() == "text/html" else text


def _fetch(imap, uids, what):
    """{uid: (metadata, bytes)} for a UID FETCH of one body section."""
    _, data = imap.uid("FETCH", ",".join(uids), f"(UID FLAGS RFC822.SIZE {what})")
    got = {}
    for item in data:
        if isinstance(item, tuple) and (m := _FETCHED.search(item[0])):
            got[m.group(1).decode()] = item
    return got


def _parse(raw):
    return email.message_from_bytes(raw, policy=email.policy.default)


def _header(msg, name):
    value = msg[name]
    return None if value is None else str(value)


def _id(folder, uid):
    return f"{PREFIX}{folder}:{uid}"


def _split_id(id):
    folder, _, uid = id.removeprefix(PREFIX).rpartition(":")
    if not folder or not uid.isdigit():
        raise ValueError(f"not a Zoho email id: {id}")
    return folder, uid


def search(query, max_results):
    """Newest matching messages, as dicts like gmail.search_email's."""
    folder, crit = criteria(query)
    imap = _connect()
    try:
        imap.select(_quote(folder), readonly=True)
        _, data = imap.uid("SEARCH", *crit)
        uids = data[0].decode().split()[-max_results:][::-1]  # UIDs grow with arrival
        if not uids:
            return []
        heads = _fetch(imap, uids, "BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)]")
        small = [u for u, (meta, _) in heads.items()
                 if int(re.search(rb"RFC822\.SIZE (\d+)", meta).group(1)) <= SNIPPET_MAX_BYTES]
        bodies = _fetch(imap, small, "BODY.PEEK[]") if small else {}
    finally:
        imap.logout()
    results = []
    for uid in uids:
        if uid not in heads:
            continue
        meta, raw = heads[uid]
        h = _parse(raw)
        snippet = ""
        if uid in bodies:
            snippet = re.sub(r"\s+", " ", plain_text(_parse(bodies[uid][1]))).strip()[:200]
        results.append({"id": _id(folder, uid), "from": _header(h, "from"),
                        "to": _header(h, "to"), "subject": _header(h, "subject"),
                        "date": _header(h, "date"), "unread": b"\\Seen" not in meta,
                        "snippet": snippet})
    return results


def read(id):
    """The parsed message for an id from search()."""
    folder, uid = _split_id(id)
    imap = _connect()
    try:
        imap.select(_quote(folder), readonly=True)
        got = _fetch(imap, [uid], "BODY.PEEK[]")
    finally:
        imap.logout()
    if uid not in got:
        raise LookupError(f"no Zoho email {id}")
    return _parse(got[uid][1])


def draft(to, subject, body, reply_to_id=""):
    """Save a draft in Zoho's Drafts folder, from the owner's Zoho address."""
    msg = EmailMessage()
    msg["From"] = config.ZOHO_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.ZOHO_USER.rpartition("@")[2] or None)
    if reply_to_id:
        original = read(reply_to_id)
        if msg_id := _header(original, "message-id"):
            msg["In-Reply-To"] = msg_id
            msg["References"] = f"{_header(original, 'references') or ''} {msg_id}".strip()
    msg.set_content(body)
    imap = _connect()
    try:
        status, data = imap.append(_quote(FOLDERS["drafts"]), r"(\Draft \Seen)",
                                   imaplib.Time2Internaldate(time.time()),
                                   msg.as_bytes(policy=email.policy.SMTP))
    finally:
        imap.logout()
    if status != "OK":
        raise RuntimeError(f"Zoho didn't save the draft: {data}")
