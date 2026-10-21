"""Google sign-in shared by the Gmail, Sheets and notes (Drive) tools.

One-time setup: put the OAuth client file at google_client.json, then run
    .venv/bin/python -m jarvis.google_auth
and sign in. The token (with a refresh token) is saved to .google_token.json and renewed
automatically after that.
"""

import threading

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from jarvis import config

# gmail.compose is the narrowest scope that can write drafts. It also allows sending, but no
# tool here sends; Jarvis only reads and drafts.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",  # only files Jarvis creates (notes)
]
CLIENT_FILE = config.ROOT / "google_client.json"
TOKEN_FILE = config.ROOT / ".google_token.json"
LOGIN_PORT = 8765  # forward it over SSH when signing in from another computer

_creds = None
_lock = threading.Lock()


def credentials():
    """Valid credentials, refreshed if needed. Raises if the one-time sign-in hasn't been done."""
    global _creds
    with _lock:
        if _creds is None:
            if not TOKEN_FILE.exists():
                raise RuntimeError("Google isn't set up yet (run: python -m jarvis.google_auth)")
            _creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if not _creds.valid:
            _creds.refresh(Request())
            _save(_creds)
        return _creds


def _save(creds):
    TOKEN_FILE.touch(mode=0o600)
    TOKEN_FILE.write_text(creds.to_json())


def login():
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_FILE.exists():
        raise SystemExit(f"Missing {CLIENT_FILE}: download the OAuth client JSON (see README)")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(
        port=LOGIN_PORT, open_browser=False, access_type="offline", prompt="consent",
        authorization_prompt_message="Open this link in a browser and sign in:\n{url}\n",
        success_message="Jarvis is signed in to Google. You can close this tab.")
    _save(creds)
    print(f"Saved {TOKEN_FILE}")


if __name__ == "__main__":
    login()
