"""The menus (panels.py), against the fake Claude Code: Settings (Status, Config, Usage,
Stats) and /mcp."""

import asyncio
import datetime as dt
import io
import json
import os
import sys

import pytest
from rich.console import Console

from jarvis_code import sessions, stats
from jarvis_code.app import JarvisApp, Options
from jarvis_code.panels import McpPanel, SettingsPanel
from jarvis_code.widgets import NoticeEntry

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]
SIZE = (110, 44)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "HISTORY", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(sessions, "PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "sent.jsonl"))
    monkeypatch.setenv("FAKE_SETTINGS", str(tmp_path / "settings.json"))
    claude = tmp_path / "claude"
    (claude / "projects" / "p").mkdir(parents=True)
    today = dt.date.today()
    day = lambda n: (today - dt.timedelta(days=n)).isoformat()
    (claude / "stats-cache.json").write_text(json.dumps({
        "version": 5, "lastComputedDate": day(1),
        "dailyActivity": [{"date": day(3), "messageCount": 10, "sessionCount": 1, "toolCallCount": 2},
                          {"date": day(1), "messageCount": 40, "sessionCount": 2, "toolCallCount": 5}],
        "dailyModelTokens": [{"date": day(3), "tokensByModel": {"claude-opus-5-5": 1000}},
                             {"date": day(1), "tokensByModel": {"claude-opus-5-5": 5000, "claude-haiku-5-5": 50}}],
        "modelUsage": {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 200,
                                           "cacheReadInputTokens": 5500, "cacheCreationInputTokens": 290}},
        "totalSessions": 3, "longestSession": {"duration": 3_600_000},
        "firstSessionDate": f"{day(3)}T10:00:00Z"}))
    stamp = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    (claude / "projects" / "p" / "s9.jsonl").write_text(json.dumps({
        "type": "assistant", "timestamp": stamp, "sessionId": "s9",
        "message": {"id": "m1", "model": "claude-opus-5-5", "content": [{"type": "tool_use"}],
                    "usage": {"input_tokens": 5, "output_tokens": 95}}}) + "\n")
    monkeypatch.setattr(stats, "CLAUDE_DIR", str(claude))
    return tmp_path


def make(home):
    return JarvisApp(Options(cwd=str(home), engine_command=FAKE))


async def until(pilot, condition, timeout=8.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await pilot.pause(0.05)


async def ready(pilot):
    await until(pilot, lambda: pilot.app.engine_state == "running" and pilot.app.model)


async def send(pilot, text):
    pilot.app.prompt.set_text(text)
    await pilot.press("enter")


def text_of(renderable, width=104):
    console = Console(width=width, file=io.StringIO(), color_system=None)
    console.print(renderable)
    return console.file.getvalue()


def shown(app):
    panel = app.dialog
    return text_of(panel.head.content) + text_of(panel.content.content) + text_of(panel.hint.content)


async def open_panel(pilot, command, kind=SettingsPanel, loaded=None):
    await send(pilot, command)
    await until(pilot, lambda: isinstance(pilot.app.dialog, kind))
    if loaded:
        await until(pilot, lambda: loaded in shown(pilot.app))
    return pilot.app.dialog


def settings(home):
    path = home / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


async def test_usage_tab(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        panel = await open_panel(pilot, "/usage", loaded="Current session")
        text = shown(app)
        for words in ("Total cost:            $0.1200", "62% used", "Current week (all models)",
                      "This week: Claude Code 98% · Chats 2%", "Last 24h · 750 requests · 17 sessions",
                      "Start fresh when you switch tasks · run /clear", "/plugin-authoring",
                      "claude.ai Jarvis", "f to fix 1 of 2", "Usage credits are off"):
            assert words in text, words
        assert panel.tab == "usage" and "▶" not in text
        await pilot.press("w")
        assert "Last 7d · 2007 requests" in shown(app) and "/claude-api" in shown(app)
        await pilot.press("d", "f")
        await until(pilot, lambda: app.dialog is None)
        assert app.prompt.text == "Make the plugin-authoring skill cheaper." and app.prompt.has_focus


async def test_tabs_and_status(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        panel = await open_panel(pilot, "/status", loaded="Version:")
        text = shown(app)
        for words in ("9.9.9", "Jarvis:", "Email:", "test@example.com", "Haiku 5.5 · high effort",
                      "MCP servers:", "1 connected, 1 need auth · /mcp", "Setting sources:"):
            assert words in text, words
        seen = []
        for _ in range(4):
            await pilot.press("right")
            seen.append(panel.tab)
        assert seen == ["config", "usage", "stats", "status"]
        await pilot.press("left")
        assert panel.tab == "stats"
        await pilot.press("escape")
        await until(pilot, lambda: app.dialog is None)
        assert app.prompt.has_focus


async def test_config_tab_toggles_and_chooses(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        panel = await open_panel(pilot, "/config", loaded="Auto-compact")
        await pilot.press(*"compact")
        assert "Auto-compact" in shown(app) and "Theme" not in shown(app)
        await pilot.press("down", "enter")
        await until(pilot, lambda: settings(home).get("autoCompactEnabled") is False)
        await until(pilot, lambda: "Set autoCompact to false" in shown(app))
        await until(pilot, lambda: any(i.id == "autoCompact" and i.value is False for i in panel.data.get("config", [])))
        await pilot.press("escape")  # clears the search; a second one would close the panel
        assert panel.search == "" and app.dialog is panel
        await pilot.press(*"theme", "down", "enter")
        assert panel.choosing is not None and panel.choosing.id == "theme"
        await pilot.press("down", "enter")  # from dark to light
        await until(pilot, lambda: settings(home).get("theme") == "light")
        await pilot.press("escape", "escape", "escape")
        await until(pilot, lambda: app.dialog is None)


async def test_config_command_with_a_value(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "/config verbose=true")
        await until(pilot, lambda: settings(home).get("verbose") is True)
        await until(pilot, lambda: any("Set verbose to true" in text_of(n.renderable) for n in app.transcript.children
                                       if isinstance(n, NoticeEntry)))
        assert not any(m.get("type") == "user" and "/config" in str(m["message"]["content"])
                       for m in map(json.loads, (home / "sent.jsonl").read_text().splitlines())), \
            "a /config change never goes into the conversation"


async def test_stats_tab(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        panel = await open_panel(pilot, "/stats", loaded="Favorite model")
        text = shown(app)
        for words in ("Overview", "Models", "Less", "More", "All time · Last 7 days · Last 30 days",
                      "Favorite model: Opus 5.5", "Sessions: 4", "Longest session: 1h 0m 0s",
                      "Active days: 3/4", "Current streak: 2 days", "Input 15 · Output 295"):
            assert words in text, words
        await pilot.press("down", "r")
        assert panel.stats_range == "7d"
        await pilot.press("right")
        assert panel.stats_view == "models" and "Tokens per Day" in shown(app)
        assert "Haiku 5.5" in shown(app)


async def test_mcp_menu(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        panel = await open_panel(pilot, "/mcp", kind=McpPanel, loaded="claude.ai Jarvis")
        text = shown(app)
        assert "2 servers" in text and "2 tools" in text and "needs auth" in text
        await pilot.press("enter")
        text = shown(app)
        assert "Claude.ai Jarvis MCP Server" in text and "https://hyde.example.ts.net" in text
        assert "SECRET-TOKEN" not in text, "the URL's path can hold a secret"
        assert "1. View tools" in text and "2. Reconnect" in text and "3. Disable" in text
        await pilot.press("1")
        assert panel.view == "tools" and "get_weather" in shown(app) and "read-only" in shown(app)
        await pilot.press("escape", "2")
        await until(pilot, lambda: "Reconnected claude.ai Jarvis" in shown(app))
        await pilot.press("3")
        await until(pilot, lambda: (panel.server or {}).get("status") == "disabled")
        sent = [json.loads(line) for line in (home / "sent.jsonl").read_text().splitlines()]
        requests = [m["request"] for m in sent if m.get("type") == "control_request"]
        assert {"subtype": "mcp_reconnect", "serverName": "claude.ai Jarvis"} in requests
        assert {"subtype": "mcp_toggle", "serverName": "claude.ai Jarvis", "enabled": False} in requests
        await pilot.press("escape", "escape")
        await until(pilot, lambda: app.dialog is None)


async def test_mcp_menu_waits_for_servers_to_connect(home, monkeypatch):
    monkeypatch.setenv("FAKE_MCP_LATE", "2.5")
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await open_panel(pilot, "/mcp", kind=McpPanel, loaded="Connecting to MCP servers")
        await until(pilot, lambda: "claude.ai Jarvis" in shown(app), timeout=10)
