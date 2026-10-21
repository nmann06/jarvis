"""Notes: each note is a Google Doc in a "Jarvis Notes" folder in the owner's Google Drive.

Drive access is the drive.file scope, so Jarvis only sees files it made: the folder is one
Jarvis creates (and finds again by name), and docs moved into it by hand won't show up.
"""

import threading

from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from jarvis import google_auth, notify
from jarvis.tools import tool

FOLDER = "Jarvis Notes"
FOLDER_TYPE = "application/vnd.google-apps.folder"
DOC_TYPE = "application/vnd.google-apps.document"
MAX_TEXT = 20000  # characters of one note passed to Claude

_folder_id = None
_lock = threading.Lock()  # so voice and Discord saving at once don't make two folders


def _drive():
    # New client per call; see gmail._gmail.
    return build("drive", "v3", credentials=google_auth.credentials(), cache_discovery=False)


def _quote(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _folder(drive):
    global _folder_id
    with _lock:
        if _folder_id is None:
            found = drive.files().list(
                q=f"name = {_quote(FOLDER)} and mimeType = '{FOLDER_TYPE}' and trashed = false",
                fields="files(id)").execute()["files"]
            _folder_id = found[0]["id"] if found else drive.files().create(
                body={"name": FOLDER, "mimeType": FOLDER_TYPE}, fields="id").execute()["id"]
        return _folder_id


@tool("Save a note as a new Google Doc in the owner's Jarvis Notes folder in Google Drive. Write "
      "the note out in full, as long as it needs to be: the short-reply rule is for speaking, not "
      "for notes.",
      title={"type": "string", "description": "A short title for the note"},
      text={"type": "string", "description": "The note. Markdown headings and lists are kept."})
def add_note(title, text):
    drive = _drive()
    doc = drive.files().create(
        body={"name": title, "mimeType": DOC_TYPE, "parents": [_folder(drive)]},
        media_body=MediaInMemoryUpload(text.encode(), mimetype="text/markdown"),
        fields="webViewLink").execute()
    notify.record(f"📝 Note saved in Google Drive: [{title}]({doc['webViewLink']})")
    return f"Saved '{title}' in the Jarvis Notes folder in Google Drive."


@tool("List the owner's saved notes (Google Docs in Jarvis Notes), newest first.")
def list_notes():
    drive = _drive()
    files = drive.files().list(
        q=f"{_quote(_folder(drive))} in parents and trashed = false", orderBy="modifiedTime desc",
        pageSize=30, fields="files(name,modifiedTime)").execute()["files"]
    return [{"title": f["name"], "modified": f["modifiedTime"][:16].replace("T", " ") + " UTC"}
            for f in files] or "No notes."


@tool("Read one saved note from Jarvis Notes.",
      title={"type": "string", "description": "The note's title, or part of it"})
def read_note(title):
    drive = _drive()
    files = drive.files().list(
        q=f"{_quote(_folder(drive))} in parents and trashed = false and name contains {_quote(title)}",
        orderBy="modifiedTime desc", pageSize=1, fields="files(id,name)").execute()["files"]
    if not files:
        return f"No note titled '{title}'."
    text = drive.files().export(fileId=files[0]["id"], mimeType="text/plain").execute()
    return f"{files[0]['name']}:\n{text.decode(errors='replace').lstrip('\ufeff')[:MAX_TEXT]}"
