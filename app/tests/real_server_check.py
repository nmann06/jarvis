"""The web UI against the REAL panel server (jarvis/panel/), on a temp port with a temp token, data
directory and .env. systemctl, journalctl, git and tailscale are faked, check_env is stubbed, and the
voice hooks are simulated in this process (no mic, no sound). No real service or data is touched.

    /home/nate/jarvis/.venv/bin/python app/tests/real_server_check.py [outdir]

Does not press anything that would call /api/service, a sign-in or an integration test.
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
from aiohttp import web  # noqa: E402

from jarvis import events, settings  # noqa: E402
from jarvis.panel import hooks, reports, server  # noqa: E402

TOKEN = "temp-real-check-token-0123456789"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/panel-real-shots")


def serve_in_thread(port, tmp):
    started = threading.Event()
    loop = asyncio.new_event_loop()
    holder = {}

    def run():
        asyncio.set_event_loop(loop)

        async def fake_run(args, timeout=10):
            joined = " ".join(args)
            if "is-active" in joined:
                return 0, "active"
            if "journalctl" in joined and "-f" not in args:
                return 0, "\n".join(f"Nov 01 09:00:{i:02d} hyde jarvis[1]: real-server log line {i}" for i in range(30))
            if "rev-parse" in joined:
                return 0, "abc1234"
            return 0, ""

        patches = [mock.patch.object(settings, "ENV", tmp / ".env"), mock.patch.object(settings, "DND", tmp / "dnd.json"),
                   mock.patch.object(reports, "DATA", tmp), mock.patch.object(events, "SOCKET", tmp / "s.sock"),
                   mock.patch.object(server, "_run", fake_run), mock.patch.object(settings, "check_env", lambda: None)]
        for p in patches:
            p.start()

        async def main():
            events.start(lambda: False)
            app = server.create_app(TOKEN)
            runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            holder["runner"] = runner
            started.set()
            i = 0
            while not holder.get("stop"):
                i += 1
                amp = 3000 if (i // 20) % 2 else 200
                events.mic_block(np.full(events.BLOCK, amp, dtype=np.int16))
                await asyncio.sleep(0.08)
            await runner.cleanup()
            events.stop()

        loop.run_until_complete(main())
        for p in patches:
            p.stop()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert started.wait(15), "the real panel server didn't start"
    return holder, t, loop


def main():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port != 8767
    tmp = Path(tempfile.mkdtemp(prefix="real-panel-"))
    (tmp / ".env").write_text("ANTHROPIC_API_KEY=fake-secret-value-123\nWAKE_THRESHOLD=0.5\n")
    os.chmod(tmp / ".env", 0o600)
    holder, thread, loop = serve_in_thread(port, tmp)
    problems = []
    try:
        from playwright.sync_api import sync_playwright
        OUT.mkdir(parents=True, exist_ok=True)
        base = f"http://127.0.0.1:{port}"
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            ctx = br.new_context(viewport={"width": 1180, "height": 820})
            page = ctx.new_page()
            page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on("dialog", lambda d: (problems.append(f"SCRIPT RAN {d.message}"), d.dismiss()))
            page.goto(f"{base}/login?token={TOKEN}")
            page.wait_for_timeout(1500)
            print("home h1:", page.locator("h1").inner_text(), "| light:", page.locator(".light.big").get_attribute("data-state"),
                  "| hooks note:", repr(page.locator(".hooks-note").inner_text()[:60]))
            page.screenshot(path=str(OUT / "real-home.png"))
            for pg in ("setup", "settings", "improve", "stats", "logs"):
                page.goto(f"{base}/#{pg}")
                page.reload()
                page.wait_for_timeout(1200)
                page.screenshot(path=str(OUT / f"real-{pg}.png"), full_page=pg in ("setup", "stats"))
                print(pg, "h1:", page.locator("h1").inner_text())
            # an Improve press against the real server
            page.goto(f"{base}/#improve")
            page.reload()
            page.wait_for_timeout(800)
            page.click("button[data-kind=missed_wake]")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / "real-improve-press.png"))
            cards = page.locator(".report").count()
            err = page.locator(".report.failed").count()
            audio = page.locator(".report audio").count()
            print("reports after press:", cards, "failed:", err, "with audio:", audio)
            problems_ok = cards >= 1 and err == 0
            # a settings change against the real settings code (temp .env)
            page.goto(f"{base}/#settings")
            page.reload()
            page.wait_for_timeout(800)
            sw = page.locator("button.switch").first
            sw.click()
            page.wait_for_timeout(600)
            print("restart bar after a setting change:", page.locator("#restart-bar").is_visible())
            print(".env now has:", [l.split("=")[0] for l in (tmp / ".env").read_text().splitlines() if l.strip()])
            br.close()
    finally:
        holder["stop"] = True
        thread.join(10)
    real = [p for p in problems]
    print("problems:", real or "none")
    return 0 if not real else 1


if __name__ == "__main__":
    sys.exit(main())
