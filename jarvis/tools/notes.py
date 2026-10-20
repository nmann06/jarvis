from datetime import datetime

from jarvis import config
from jarvis.tools import tool
from jarvis.tools.clock import TZ

NOTES = config.ROOT / "data" / "notes.md"


@tool("Save a note for the owner.", text={"type": "string", "description": "The note"})
def add_note(text):
    NOTES.parent.mkdir(exist_ok=True)
    with NOTES.open("a") as f:
        f.write(f"- {datetime.now(TZ):%Y-%m-%d %H:%M} {text}\n")
    return "Saved."


@tool("Read back all saved notes, oldest first.")
def read_notes():
    return NOTES.read_text() if NOTES.exists() and NOTES.stat().st_size else "No notes."
