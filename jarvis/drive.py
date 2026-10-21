"""Course files in the owner's Google Drive, for onq_get_file.

"Saved from Chrome" is where the owner's browser saves downloads; Jarvis only looks in it, which
needs the drive.metadata.readonly scope (names, checksums and links, never contents). "Saved from
Jarvis" is Jarvis's own folder, made and filled under drive.file.
"""

import hashlib
import threading

from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from jarvis import google_auth

CHROME = "Saved from Chrome"
JARVIS = "Saved from Jarvis"
FOLDER_TYPE = "application/vnd.google-apps.folder"
FIELDS = "id,name,mimeType,md5Checksum,webViewLink"

_jarvis_folder = None
_lock = threading.Lock()  # so two saves at once don't make two folders


def _drive():
    # New client per call; see gmail._gmail.
    return build("drive", "v3", credentials=google_auth.credentials(), cache_discovery=False)


def _quote(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _list(drive, q):
    files, page = [], None
    while True:
        r = drive.files().list(q=q + " and trashed = false", fields=f"nextPageToken,files({FIELDS})",
                               pageSize=1000, pageToken=page).execute()
        files += r["files"]
        page = r.get("nextPageToken")
        if not page:
            return files


def _tree(drive, folder_id):
    """Every file in a folder and its subfolders."""
    files = []
    for f in _list(drive, f"{_quote(folder_id)} in parents"):
        files += _tree(drive, f["id"]) if f["mimeType"] == FOLDER_TYPE else [f]
    return files


def _folder(drive, name):
    found = _list(drive, f"name = {_quote(name)} and mimeType = '{FOLDER_TYPE}' and 'root' in parents")
    return found[0]["id"] if found else None


def find(data, name):
    """Where a copy of these bytes already is: (folder name, file, note), or (None, None, note).
    The note says when Saved from Chrome couldn't be checked, or only has a different version."""
    md5 = hashlib.md5(data).hexdigest()
    drive = _drive()
    note = ""
    for folder in (CHROME, JARVIS):
        if folder == CHROME and not google_auth.credentials().has_scopes([google_auth.DRIVE_METADATA]):
            note += (f"(Couldn't check {CHROME}: Jarvis needs to be signed in to Google again, "
                     "with `.venv/bin/python -m jarvis.google_auth` in ~/jarvis.) ")
            continue
        folder_id = _jarvis_id(drive) if folder == JARVIS else _folder(drive, folder)
        files = _tree(drive, folder_id) if folder_id else []
        for f in files:
            if f.get("md5Checksum") == md5:
                return folder, f, note
        if any(f["name"] == name for f in files):
            note += (f"({folder} has a file called {name} too, but it's a different version "
                     "from the one on the course site now.) ")
    return None, None, note


def _jarvis_id(drive):
    global _jarvis_folder
    with _lock:
        if _jarvis_folder is None:
            _jarvis_folder = _folder(drive, JARVIS) or drive.files().create(
                body={"name": JARVIS, "mimeType": FOLDER_TYPE}, fields="id").execute()["id"]
        return _jarvis_folder


def save(data, name, mime):
    """Upload a file into Saved from Jarvis; returns the new file."""
    drive = _drive()
    return drive.files().create(
        body={"name": name, "parents": [_jarvis_id(drive)]},
        media_body=MediaInMemoryUpload(data, mimetype=mime or "application/octet-stream"),
        fields=FIELDS).execute()
