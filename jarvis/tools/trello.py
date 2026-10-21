"""The owner's Trello todos: list, add, move and finish cards. Finishing = moving to the board's Done list."""

from datetime import datetime

import requests

from jarvis import config
from jarvis.tools import tool
from jarvis.tools.clock import TZ

API = "https://api.trello.com/1"


def _get(path, **params):
    if not (config.TRELLO_KEY and config.TRELLO_TOKEN):
        raise RuntimeError("Trello isn't set up (TRELLO_KEY and TRELLO_TOKEN in .env)")
    r = requests.get(API + path, timeout=10,
                     params={"key": config.TRELLO_KEY, "token": config.TRELLO_TOKEN, **params})
    r.raise_for_status()
    return r.json()


def _send(method, path, **params):
    r = requests.request(method, API + path, timeout=10,
                         params={"key": config.TRELLO_KEY, "token": config.TRELLO_TOKEN, **params})
    r.raise_for_status()
    return r.json()


def _board(name):
    """The open board called name (case-insensitive), or the default board if name is empty."""
    name = (name or config.TRELLO_BOARD).strip().lower()
    boards = _get("/members/me/boards", filter="open", fields="name", lists="open", list_fields="name")
    for b in boards:
        if b["name"].strip().lower() == name:
            return b
    raise ValueError(f"No board called {name!r}. Boards: " + ", ".join(b["name"] for b in boards))


def _list(board, name):
    for l in board["lists"]:
        if l["name"].strip().lower() == name.strip().lower():
            return l
    raise ValueError(f"No list called {name!r} on {board['name']}. Lists: "
                     + ", ".join(l["name"] for l in board["lists"]))


def _card(board, name):
    """The open card on board whose title best matches name: exact, else the only one containing it."""
    cards = _get(f"/boards/{board['id']}/cards", filter="open", fields="name,idList")
    exact = [c for c in cards if c["name"].strip().lower() == name.strip().lower()]
    partial = [c for c in cards if name.strip().lower() in c["name"].lower()]
    found = exact or partial
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(f"No card matching {name!r} on {board['name']}")
    raise ValueError(f"Several cards match {name!r}: " + "; ".join(c["name"] for c in found[:10]))


def _due(text):
    """'2026-10-24' or '2026-10-24 17:00' in local time -> ISO for Trello; '' -> no due date."""
    if not text:
        return None
    when = datetime.fromisoformat(text)
    if len(text) <= 10:
        when = when.replace(hour=23, minute=59)
    return when.replace(tzinfo=TZ).isoformat()


BOARD = {"type": "string", "description": f"Board name, or empty for the main todo board "
         f"({config.TRELLO_BOARD!r})"}


@tool("List the owner's Trello boards and the lists on each.")
def trello_boards():
    return [{"board": b["name"], "lists": [l["name"] for l in b["lists"]]}
            for b in _get("/members/me/boards", filter="open", fields="name", lists="open",
                          list_fields="name")]


@tool("Read the open cards on a Trello board, grouped by list, with due dates.",
      board=BOARD,
      list={"type": "string", "description": "Only this list, or empty for every list"})
def trello_cards(board, list):
    b = _board(board)
    lists = {l["id"]: l["name"] for l in b["lists"]}
    cards = _get(f"/boards/{b['id']}/cards", filter="open", fields="name,idList,due,dueComplete,desc")
    out = {}
    for c in cards:
        where = lists.get(c["idList"], "?")
        if list and where.lower() != list.strip().lower():
            continue
        entry = c["name"]
        if c["due"]:
            due = datetime.fromisoformat(c["due"].replace("Z", "+00:00")).astimezone(TZ)
            entry += f" (due {due:%a %b} {due.day} {due:%I:%M %p}{', done' if c['dueComplete'] else ''})"
        if c["desc"]:
            entry += f" - {c['desc'][:200]}"
        out.setdefault(where, []).append(entry)
    return out or "No cards."


@tool("Add a card to a Trello list.",
      board=BOARD,
      list={"type": "string", "description": "List name, e.g. 'todo'"},
      title={"type": "string"},
      due={"type": "string", "description": "Due date as YYYY-MM-DD or YYYY-MM-DD HH:MM "
           "(local time), or empty for none"})
def trello_add_card(board, list, title, due):
    b = _board(board)
    params = {"idList": _list(b, list)["id"], "name": title, "pos": "bottom"}
    if due:
        params["due"] = _due(due)
    _send("POST", "/cards", **params)
    return f"Added {title!r} to {b['name']} / {_list(b, list)['name']}."


@tool("Move a Trello card to another list on the same board.",
      board=BOARD,
      card={"type": "string", "description": "The card's title, or a distinctive part of it"},
      list={"type": "string", "description": "List to move it to"})
def trello_move_card(board, card, list):
    b = _board(board)
    c, l = _card(b, card), _list(b, list)
    _send("PUT", f"/cards/{c['id']}", idList=l["id"], pos="top")
    return f"Moved {c['name']!r} to {l['name']}."


@tool("Mark a Trello todo as finished by moving it to its board's Done list.",
      board=BOARD,
      card={"type": "string", "description": "The card's title, or a distinctive part of it"})
def trello_finish_card(board, card):
    b = _board(board)
    c, l = _card(b, card), _list(b, "done")
    _send("PUT", f"/cards/{c['id']}", idList=l["id"], pos="top")
    return f"Done: {c['name']!r} moved to {l['name']}."
