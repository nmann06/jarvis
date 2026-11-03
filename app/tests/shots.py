"""Screenshot every page of the web UI against the mock server, and fail on console errors, CSP
violations or injected script. Run with /home/nate/jarvis/.venv/bin/python (it has playwright).

    python app/tests/shots.py [outdir]

Never talks to the real panel: starts mock_server.py on a temp port with a throwaway token.
"""

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/panel-shots")
OUT.mkdir(parents=True, exist_ok=True)
TOKEN = "throwaway-test-token"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def mock(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req))


problems = []


def main():
    proc = subprocess.Popen([sys.executable, str(HERE / "mock_server.py"), "--port", str(PORT), "--token", TOKEN],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", PORT), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            for name, vp in (("wide", {"width": 1180, "height": 820}), ("narrow", {"width": 390, "height": 800})):
                ctx = br.new_context(viewport=vp)
                page = ctx.new_page()

                def on_console(m, tag=name):
                    if m.type in ("error", "warning"):
                        problems.append(f"[{tag}] console {m.type}: {m.text}")

                page.on("console", on_console)
                page.on("pageerror", lambda e, tag=name: problems.append(f"[{tag}] pageerror: {e}"))
                page.on("dialog", lambda d, tag=name: (problems.append(f"[{tag}] SCRIPT RAN: dialog {d.message}"), d.dismiss()))

                def go(h, wait=700):
                    page.goto(f"{BASE}/#{h}")
                    page.reload()
                    page.wait_for_timeout(wait)

                def shot(n):
                    page.screenshot(path=str(OUT / f"{name}-{n}.png"), full_page=n.split("-")[0] in ("stats", "setup"))

                mock("/__mock/scenario", {"name": "normal"})
                page.goto(f"{BASE}/login?token={TOKEN}")
                for st in ("idle", "listening", "thinking", "speaking"):
                    mock("/__mock/state", {"state": st})
                    go("home")
                    mock("/__mock/state", {"state": st})
                    page.wait_for_timeout(500)
                    shot(f"home-{st}")
                for pg in ("setup", "settings", "improve", "stats", "logs"):
                    go(pg, 900)
                    shot(pg)
                    if pg == "stats":
                        page.click("text=Table")
                        shot("stats-table")
                    if pg == "settings":
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        shot("settings-bottom")

                # a report press and a hover on a chart
                go("improve")
                page.click("button[data-kind=missed_wake]")
                page.wait_for_timeout(120)
                shot("improve-pressed")
                page.wait_for_timeout(800)
                shot("improve-after")
                go("stats", 900)
                page.hover(".hit >> nth=5")
                shot("stats-hover")

                # hooks not live
                mock("/__mock/scenario", {"name": "hooks_off"})
                for pg in ("home", "improve"):
                    go(pg)
                    shot(f"hooksoff-{pg}")

                for sc in ("voice_off", "starting", "stopped"):
                    mock("/__mock/scenario", {"name": sc})
                    go("home")
                    shot(f"reason-{sc}")
                mock("/__mock/scenario", {"name": "env_broken"})
                go("home")
                page.click("button:has-text('Restart')")
                page.click("#dialog button:has-text('Restart')")
                page.wait_for_timeout(500)
                shot("env-broken-notice")
                page.click("#dialog button:has-text('OK')")

                # empty
                mock("/__mock/scenario", {"name": "empty"})
                for pg in ("improve", "stats", "logs", "setup"):
                    go(pg)
                    shot(f"empty-{pg}")

                # failed integration test
                mock("/__mock/scenario", {"name": "failing"})
                go("setup")
                page.click("#int-discord button:has-text('Test')")
                page.wait_for_timeout(900)
                page.locator("#int-discord").scroll_into_view_if_needed()
                shot("setup-failed-test")

                # long log lines
                mock("/__mock/scenario", {"name": "long_logs"})
                go("logs")
                shot("logs-long")
                page.click("button:has-text('Wrapping long lines')")
                shot("logs-long-nowrap")

                # connection lost
                mock("/__mock/scenario", {"name": "normal"})
                go("home")
                mock("/__mock/scenario", {"name": "down"})
                page.wait_for_timeout(5200)
                shot("home-lost")
                ctx.close()
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(3)
        except Exception:
            proc.kill()
    print("\n".join(problems) if problems else "no console errors, CSP violations or injected scripts")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
