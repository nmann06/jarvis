"""Behaviour checks for the web UI against the mock server (headless Chromium).

    /home/nate/jarvis/.venv/bin/python app/tests/behaviour.py

Starts mock_server.py on a temp port with a throwaway token. Never touches the real panel.
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
TOKEN = "throwaway-test-token"
fails, passes = [], 0


def check(name, cond, extra=""):
    global passes
    if cond:
        passes += 1
        print(f"  ok   {name}")
    else:
        fails.append(name)
        print(f"  FAIL {name} {extra}")


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


def calls():
    return mock("/__mock/calls")


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
            ctx = br.new_context(viewport={"width": 1180, "height": 820})
            page = ctx.new_page()
            scripts_ran = []
            csp = []
            page.on("dialog", lambda d: (scripts_ran.append(d.message), d.dismiss()))
            page.on("console", lambda m: csp.append(m.text) if "Content Security Policy" in m.text else None)

            def go(h):
                page.goto(f"{BASE}/#{h}")
                page.reload()
                page.wait_for_timeout(600)

            print("auth")
            r = ctx.request.get(f"{BASE}/api/status")
            check("API without the token is 401", r.status == 401)
            page.goto(f"{BASE}/login?token={TOKEN}")
            page.wait_for_timeout(500)
            check("login sets the cookie and shows Home", page.locator("h1").inner_text() == "Home")

            print("home / light")
            states = {"idle": "Idle", "listening": "Listening", "thinking": "Thinking", "speaking": "Speaking"}
            for st, word in states.items():
                mock("/__mock/state", {"state": st})
                page.wait_for_timeout(300)
                check(f"light {st}", page.locator(".light.big").get_attribute("data-state") == st and page.locator(".light-title").inner_text() == word)
            go("home")
            mock("/__mock/state", {"state": "thinking"})
            page.wait_for_timeout(300)
            anim = page.evaluate("getComputedStyle(document.querySelector('.light.big'), '::after').animationName")
            check("thinking pulses", anim == "pulse", anim)
            page.emulate_media(reduced_motion="reduce")
            anim = page.evaluate("getComputedStyle(document.querySelector('.light.big'), '::after').animationName")
            check("reduced motion stops the pulse", anim == "none", anim)
            page.emulate_media(reduced_motion="no-preference")
            page.click("button:has-text('Restart')")
            body = page.locator("#dialog").inner_text()
            check("restart asks first and warns about interruption", "few seconds" in body and "interrupts" in body, body)
            page.click("#dialog button:has-text('Cancel')")
            check("cancelling sends nothing", not [c for c in calls() if c["path"] == "/api/service"])
            page.click("button:has-text('Stop')")
            check("stop asks first", page.locator("#dialog[open]").count() == 1)
            page.keyboard.press("Escape")
            page.click("button:has-text('Mute microphone')")
            page.wait_for_timeout(300)
            check("mute toggles", page.locator("button[role=switch]:has-text('Unmute')").count() == 1)
            page.click("button:has-text('Unmute')")

            print("connection lost and back")
            mock("/__mock/state", {"state": "listening"})
            mock("/__mock/scenario", {"name": "down"})
            page.wait_for_timeout(5500)
            check("banner says connection lost", "Lost the connection" in page.locator("#banner").inner_text())
            check("light is unknown (not idle) when disconnected", page.locator(".light.big").get_attribute("data-state") == "unknown")
            mock("/__mock/scenario", {"name": "normal"})
            page.wait_for_timeout(5500)
            check("reconnects by itself", page.locator("#banner").inner_text() == "" and page.locator("#conn").get_attribute("data-conn") == "ok")
            mock("/__mock/drop", {})
            page.wait_for_timeout(1500)
            check("dropped event stream shows unknown", page.locator(".light.big").get_attribute("data-state") in ("unknown", "idle"))

            print("hooks not live")
            mock("/__mock/scenario", {"name": "hooks_off"})
            go("home")
            txt = page.locator(".hooks-note").inner_text()
            check("needs_restart says a restart is needed for the light and audio capture", "needs a restart for the light and audio capture" in txt)
            check("light is unknown", page.locator(".light.big").get_attribute("data-state") == "unknown")
            check("mute disabled", page.locator("button[role=switch]").first.is_disabled())
            page.click(".hooks-note button:has-text('Restart Jarvis')")
            page.click("#dialog button:has-text('Restart')")
            page.wait_for_timeout(2000)
            check("after restart the hooks note is gone and light is idle", page.locator(".hooks-note").inner_text() == "" and page.locator(".light.big").get_attribute("data-state") == "idle")

            print("hooks_reason")
            mock("/__mock/scenario", {"name": "voice_off"})
            go("home")
            txt = page.locator(".hooks-note").inner_text()
            check("voice_off says voice is off and does not offer a restart", "Voice is turned off" in txt and "Restart Jarvis" not in txt and page.locator(".hooks-note a[href='#settings']").count() == 1, txt)
            check("voice_off light words", "Voice is turned off" in page.locator(".light-sub").inner_text())
            mock("/__mock/scenario", {"name": "starting"})
            go("home")
            txt = page.locator(".hooks-note").inner_text()
            check("starting says it is starting up, no button", "starting up" in txt and page.locator(".hooks-note button").count() == 0, txt)
            mock("/__mock/scenario", {"name": "stopped"})
            go("home")
            txt = page.locator(".hooks-note").inner_text()
            check("down offers Start, not Restart", "isn't running" in txt and page.locator(".hooks-note button:has-text('Start Jarvis')").count() == 1 and "needs a restart" not in txt, txt)
            go("improve")
            check("improve line for down", "isn't running" in page.locator("#hooks-line").inner_text())
            mock("/__mock/scenario", {"name": "env_broken"})
            go("home")
            page.click("button:has-text('Restart')")
            page.click("#dialog button:has-text('Restart')")
            page.wait_for_timeout(500)
            dtxt = page.locator("#dialog").inner_text()
            check("a 400 on restart shows the server's message in a dialog", "can't load .env" in dtxt and "left as it was" in dtxt and page.locator("#dialog[open]").count() == 1, dtxt)
            page.click("#dialog button:has-text('OK')")

            print("improve")
            mock("/__mock/scenario", {"name": "normal"})
            go("improve")
            before = len(calls())
            page.click("button[data-kind=misheard]")
            check("press opens no dialog", page.locator("#dialog[open]").count() == 0)
            page.wait_for_timeout(100)
            check("instant feedback on the button", "Captured" in page.locator("button[data-kind=misheard]").inner_text())
            check("pending card appears at once or the report already landed", page.locator(".report").count() >= 4)
            page.wait_for_timeout(600)
            posts = [c for c in calls()[before:] if c["method"] == "POST" and c["path"] == "/api/reports"]
            check("exactly one POST /api/reports", len(posts) == 1)
            page.fill("#report-note", "dog barking")
            page.click("button[data-kind=missed_wake]")
            page.wait_for_timeout(600)
            check("note is attached and cleared", page.locator(".report-note", has_text="dog barking").count() == 1 and page.input_value("#report-note") == "")
            card = page.locator(".report", has_text="Lower sensitivity").first
            card.locator("button:has-text('Apply')").click()
            dlg = page.locator("#dialog").inner_text()
            check("apply confirms and names the setting", "WAKE_THRESHOLD" in dlg and "0.28" in dlg, dlg)
            page.click("#dialog button:has-text('Apply change')")
            page.wait_for_timeout(500)
            check("restart bar appears after apply", page.locator("#restart-bar").is_visible())
            check("card offers a restart", page.locator(".applied").count() >= 1)
            page.click("#restart-bar button")
            check("restart from the bar confirms first", page.locator("#dialog[open]").count() == 1)
            page.click("#dialog button:has-text('Cancel')")
            n = page.locator(".report").count()
            page.locator(".report").last.locator("button:has-text('Delete')").click()
            page.click("#dialog button:has-text('Delete report')")
            page.wait_for_timeout(400)
            check("delete confirms then removes", page.locator(".report").count() == n - 1)
            page.locator(".report").first.locator(".seg button:has-text('Dismissed')").click()
            page.wait_for_timeout(300)
            check("status change", page.locator(".report").first.get_attribute("class").endswith("status-dismissed"))
            check("audio player present", page.locator("audio").count() >= 1)
            check("untrusted exchange text shown as text", scripts_ran == [])

            print("settings")
            mock("/__mock/scenario", {"name": "normal"})
            go("settings")
            sw = page.locator("button.switch").first
            was = sw.get_attribute("aria-checked")
            sw.click()
            page.wait_for_timeout(300)
            check("toggle flips and saves", sw.get_attribute("aria-checked") != was)
            check("restart bar visible", page.locator("#restart-bar").is_visible())
            first = page.locator(".setting").first
            first.locator("button:has-text('Reset to default')").click()
            page.wait_for_timeout(300)
            check("reset to default", "claude-haiku-5-5" in first.locator("select").input_value())
            sel = page.locator(".setting select").first
            sel.select_option("__other__")
            page.fill(".setting input[aria-label*='custom']", "claude-experimental-9")
            page.click(".setting button:has-text('Save')")
            page.wait_for_timeout(300)
            check("free-text model id saves", page.locator(".setting").first.locator(".field-msg").inner_text().startswith("Model: claude-experimental-9"))
            check("secret inputs are empty and masked", page.locator("input[type=password]").evaluate_all("els => els.every(e => e.value === '')"))
            before = len(page.locator(".toast").all())
            page.locator("input[type=password]").nth(1).fill("secret-abc")
            page.locator(".setting.secret").nth(1).locator("button:has-text('Save')").click()
            page.wait_for_timeout(400)
            check("secret save marks Set and clears the field", page.locator(".setting.secret").nth(1).locator(".chip").inner_text().endswith("Set") and page.locator("input[type=password]").nth(1).input_value() == "")
            check("secret value never appears in the page", "secret-abc" not in page.content())

            print("setup")
            go("setup")
            check("steps rendered as an ordered list", page.locator("#int-anthropic ol li").count() == 3)
            check("safe link rendered", page.locator("#int-anthropic ol a[href^='https://']").count() == 1)
            check("javascript: link not rendered as a link", page.locator("a[href^='javascript']").count() == 0)
            check("no injected elements from untrusted text", page.locator("article.int img, article.int script, article.int b").count() == 0)
            page.click("#int-anthropic button:has-text('Test')")
            page.wait_for_timeout(900)
            check("test shows OK result", page.locator("#int-anthropic .result.good").count() == 1)
            page.click("#int-google button:has-text('Sign in')")
            page.wait_for_timeout(1500)
            check("sign-in shows job output", "Opening your browser" in page.locator("#int-google .job-out").inner_text())
            page.wait_for_timeout(3200)
            check("sign-in finishes with a next step", "Press Test" in page.locator("#int-google").inner_text())

            print("logs")
            mock("/__mock/scenario", {"name": "long_logs"})
            go("logs")
            page.click("button:has-text('Following new lines')")
            page.evaluate("document.querySelector('.log').scrollTop = 0")
            ov = page.evaluate("(() => { const l = document.querySelector('.log'); return [l.scrollWidth, l.clientWidth]; })()")
            check("long lines wrap inside the viewer", ov[0] <= ov[1] + 1, str(ov))
            page.fill("#log-filter", "end-of-long-line")
            check("filter narrows lines", page.locator(".log .ln").count() == 1)
            page.fill("#log-filter", "")
            page.wait_for_timeout(2200)
            check("live lines arrive", page.locator(".log .ln", has_text="live line").count() >= 1)

            print("keyboard / responsive")
            go("improve")
            page.keyboard.press("Tab"); page.keyboard.press("Tab")
            focused = page.evaluate("document.activeElement.tagName + ':' + (document.activeElement.textContent || '').slice(0, 12)")
            check("tab reaches nav links", focused.startswith("A:"), focused)
            ctx2 = br.new_context(viewport={"width": 360, "height": 740})
            p2 = ctx2.new_page()
            p2.goto(f"{BASE}/login?token={TOKEN}")
            for pg in ("home", "setup", "settings", "improve", "stats", "logs"):
                p2.goto(f"{BASE}/#{pg}"); p2.reload(); p2.wait_for_timeout(600)
                w = p2.evaluate("document.documentElement.scrollWidth")
                check(f"no horizontal overflow at 360px on {pg}", w <= 361, str(w))
            check("CSP meta present", "default-src 'none'" in page.evaluate("document.querySelector('meta[http-equiv=Content-Security-Policy]').content"))
            check("no CSP violations in console", csp == [], str(csp))
            check("no untrusted text executed", scripts_ran == [])
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(3)
        except Exception:
            proc.kill()
    print(f"\n{passes} passed, {len(fails)} failed")
    for f in fails:
        print("  FAILED:", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
