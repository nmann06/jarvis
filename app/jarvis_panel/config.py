"""Where the app keeps its settings and token, and how it finds the panel.

On the Jarvis machine it reads data/panel-token itself and connects to 127.0.0.1:8767. Anywhere
else it connects to the host `hyde` (over Tailscale) with a token the owner pastes once; that token
is kept in the per-user config directory, readable by the user only (on Windows it sits in the
user's AppData folder, which other accounts can't read by default).

Environment overrides, mostly for tests:
    JARVIS_PANEL_CONFIG_DIR   use this directory (and never look for the real token file)
    JARVIS_PANEL_TOKEN_FILE   read the token from this file
    JARVIS_PANEL_HOST / JARVIS_PANEL_PORT
    JARVIS_HOME               the Jarvis folder, if it isn't ~/jarvis
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "hyde"
DEFAULT_PORT = 8767
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,200}$")


def config_dir() -> Path:
    env = os.environ.get("JARVIS_PANEL_CONFIG_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "JarvisPanel"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jarvis-panel"


def _private_write(path: Path, text: str) -> None:
    """Write text so only the current user can read it (mode 600 where the OS has modes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


@dataclass
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    float_visible: bool = True
    float_pos: list = field(default_factory=list)   # [x, y] of the floating light, or empty
    geometry: str = ""                               # main window, base64 from saveGeometry()

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        s = cls()
        for k, v in raw.items():
            if hasattr(s, k) and isinstance(v, type(getattr(s, k))):
                setattr(s, k, v)
        return s

    def save(self) -> None:
        _private_write(config_dir() / "settings.json", json.dumps(self.__dict__, indent=2))


def saved_token() -> str:
    try:
        return (config_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_token(token: str) -> None:
    _private_write(config_dir() / "token", token.strip() + "\n")


def clear_token() -> None:
    try:
        (config_dir() / "token").unlink()
    except OSError:
        pass


def local_token_file() -> Path | None:
    """data/panel-token if this machine is the Jarvis machine."""
    env = os.environ.get("JARVIS_PANEL_TOKEN_FILE")
    if env:
        return Path(env)
    if os.environ.get("JARVIS_PANEL_CONFIG_DIR"):
        return None
    homes = []
    if os.environ.get("JARVIS_HOME"):
        homes.append(Path(os.environ["JARVIS_HOME"]))
    if not getattr(sys, "frozen", False):
        homes.append(Path(__file__).resolve().parents[2])       # app/jarvis_panel -> the Jarvis folder
    else:
        homes.append(Path(sys.executable).resolve().parents[3])  # app/dist/jarvis-panel/<exe>
    homes.append(Path.home() / "jarvis")
    for home in homes:
        p = home / "data" / "panel-token"
        if p.is_file():
            return p
    return None


@dataclass
class Connection:
    host: str
    port: int
    token: str
    source: str   # "local" (this is the Jarvis machine), "saved", or "none" (the owner must paste a token)

    @property
    def base(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"http://{host}:{self.port}"


def resolve() -> Connection:
    s = Settings.load()
    host = os.environ.get("JARVIS_PANEL_HOST")
    port = int(os.environ["JARVIS_PANEL_PORT"]) if os.environ.get("JARVIS_PANEL_PORT") else None
    tf = local_token_file()
    if tf:
        try:
            token = tf.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            return Connection(host or "127.0.0.1", port or DEFAULT_PORT, token, "local")
    token = saved_token()
    return Connection(host or s.host, port or s.port, token, "saved" if token else "none")


def parse_host(text: str) -> tuple[str, int | None]:
    """'hyde', 'hyde:8767', 'http://100.64.0.1:8767/' -> (host, port or None)."""
    t = re.sub(r"^https?://", "", text.strip()).split("/")[0]
    if t.startswith("["):                       # [::1]:8767
        host, _, rest = t[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else None
    if t.count(":") == 1:
        host, _, p = t.partition(":")
        return host, int(p) if p.isdigit() else None
    return t, None
