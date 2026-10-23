"""The window against a fake Claude Code (fake_claude.py): each feature, end to end."""

import asyncio
import io
import json
import os
import sys
import time

import pytest
from rich.console import Console

from jarvis_code import sessions
from jarvis_code.app import JarvisApp, Options
from jarvis_code.widgets import AssistantEntry, NoticeEntry, ThinkingEntry, ToolEntry, UserEntry

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]
SIZE = (110, 40)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "HISTORY", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(sessions, "PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "sent.jsonl"))
    (tmp_path / "notes.txt").write_text("alpha\n")
    return tmp_path


def make(home, **kw):
    return JarvisApp(Options(cwd=str(home), engine_command=FAKE, **kw))


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


def sent(home):
    path = home / "sent.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def entries(app, kind):
    return [w for w in app.transcript.children if isinstance(w, kind)]


def text_of(renderable, width=100):
    console = Console(width=width, file=io.StringIO(), color_system=None)
    console.print(renderable)
    return console.file.getvalue()


def replies(app):
    return [e.text.strip() for e in entries(app, AssistantEntry)]


async def test_banner_and_hello(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        banner = text_of(app.banner.render())
        assert "╚█████╔╝" in banner and "Haiku 5.5 · high effort · Claude Pro" in banner
        await send(pilot, "hello")
        await until(pilot, lambda: replies(app) == ["Hello, sir. How may I help?"] and not app.busy)
        assert [e.text for e in entries(app, UserEntry)] == ["hello"]
        await until(pilot, lambda: "3% context" in app.footer.right)
        assert not app.spinner.display
        assert sessions.read_history(str(home)) == ["hello"]


async def test_permission_yes_and_dont_ask_again(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "edit the notes")
        await until(pilot, lambda: app.dialog is not None)
        shown = text_of(app.dialog.choice_view.content)
        assert "Yes, allow all edits during this session" in shown
        await pilot.press("2")
        await until(pilot, lambda: not app.busy and replies(app))
        tool = entries(app, ToolEntry)[0]
        assert tool.state == "done" and app.mode == "acceptEdits"
        response = [m for m in sent(home) if m.get("type") == "control_response"][0]["response"]["response"]
        assert response["behavior"] == "allow" and response["updatedPermissions"][0]["mode"] == "acceptEdits"
        assert "Updated notes.txt with 1 addition and 1 removal" in text_of(tool.result_view.content)


async def test_permission_rejected_with_esc(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "edit the notes")
        await until(pilot, lambda: app.dialog is not None)
        await pilot.press("escape")
        await until(pilot, lambda: not app.busy)
        assert entries(app, ToolEntry)[0].state == "rejected"
        response = [m for m in sent(home) if m.get("type") == "control_response"][0]["response"]["response"]
        assert response["behavior"] == "deny" and response["interrupt"] is True
        assert app.prompt.has_focus


async def test_question_answered(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "ask me a question")
        await until(pilot, lambda: app.dialog is not None)
        await pilot.press("down", "enter")
        await until(pilot, lambda: "You chose Coffee." in replies(app))


async def test_question_typed_answer(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "ask me a question")
        await until(pilot, lambda: app.dialog is not None)
        await pilot.press("3")
        await until(pilot, lambda: app.dialog.entry.has_focus)
        await pilot.press(*"Water", "enter")
        await until(pilot, lambda: "You chose Water." in replies(app))


async def test_plan_approval_switches_mode(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "make a plan")
        await until(pilot, lambda: app.dialog is not None)
        await pilot.press("1")
        await until(pilot, lambda: "Starting on the plan." in replies(app))
        assert app.mode == "auto"


async def test_shift_tab_cycles_modes(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        seen = []
        for _ in range(4):
            await pilot.press("shift+tab")
            seen.append(app.mode)
        assert seen == ["acceptEdits", "plan", "auto", "default"]
        await until(pilot, lambda: [m["request"]["mode"] for m in sent(home)
                                    if m.get("request", {}).get("subtype") == "set_permission_mode"] == seen)


async def test_footer_shows_usage_effort_and_when_the_5_hour_session_ends(home):
    app = make(home)
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        clock = lambda t: time.strftime("%-I:%M%p", time.localtime(t)).lower().replace(":00", "")
        await until(pilot, lambda: "weekly" in app.footer.right)  # from get_usage
        assert app.session_ends - time.time() == pytest.approx(7200, abs=60)
        right = app.footer.right
        assert f"5-hour 62% ends {clock(app.session_ends)}" in right and "weekly 23%" in right
        assert "Haiku 5.5 · high effort" in right
        later = int(time.time()) + 3 * 3600
        app.on_rate_limit({"rate_limit_info": {"status": "allowed", "resetsAt": later, "rateLimitType": "five_hour",
                                               "unifiedWindows": {"five_hour": {"utilization": 0.1, "resetsAt": later},
                                                                  "seven_day": {"utilization": 0.3, "resetsAt": later}}}})
        assert f"5-hour 10% ends {clock(later)} · weekly 30%" in app.footer.right
        app.session_ends = time.time() - 1  # past: just the usage until the next turn reports one
        app.update_footer()
        assert "5-hour 10% · weekly 30%" in app.footer.right and "ends" not in app.footer.right


async def test_slash_menu_completes_and_runs(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        app.prompt.set_text("/mo")
        await pilot.pause(0.1)
        assert app.menu.open and app.menu.current()[0] == "model"
        await pilot.press("tab")
        assert app.prompt.text == "/model "
        await pilot.press(*"sonnet", "enter")
        await until(pilot, lambda: any("Set model to" in text_of(n.renderable) for n in entries(app, NoticeEntry)))
        assert app.model_override == "sonnet"


async def test_queued_prompt_runs_after(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "slow")
        await until(pilot, lambda: app.busy and replies(app))
        await send(pilot, "hello")
        assert app.queued == ["hello"] and app.queued_panel.display
        await until(pilot, lambda: "Hello, sir. How may I help?" in replies(app) and not app.busy, timeout=15)
        assert [e.text for e in entries(app, UserEntry)] == ["slow", "hello"]
        assert not app.queued_panel.display


async def test_ctrl_x_s_sends_now(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "slow")
        await until(pilot, lambda: app.busy and replies(app))
        app.prompt.set_text("hello")
        await pilot.press("ctrl+x")
        assert app.prompt.text == "hello"  # not cut
        await pilot.press("s")
        await until(pilot, lambda: "Hello, sir. How may I help?" in replies(app) and not app.busy, timeout=15)
        assert any("Interrupted" in text_of(n.renderable) for n in entries(app, NoticeEntry))
        assert [e.text for e in entries(app, UserEntry)] == ["slow", "hello"]
        assert not app.queued and app.prompt.text == ""


async def test_ctrl_x_then_another_key_types_it(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        app.prompt.set_text("hi")
        app.prompt.move_cursor((0, 2))
        await pilot.press("ctrl+x", "a")
        assert app.prompt.text == "hia" and not entries(app, UserEntry)


def test_starts_in_auto_mode(monkeypatch):
    from jarvis_code.__main__ import parse
    monkeypatch.delenv("JARVIS_MODE", raising=False)
    assert parse([]).mode == "auto"
    assert parse(["--mode", "default"]).mode == "default"


async def test_escape_interrupts(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "slow")
        await until(pilot, lambda: replies(app))
        await pilot.press("escape")
        await until(pilot, lambda: not app.busy)
        assert any("Interrupted" in text_of(n.renderable) for n in entries(app, NoticeEntry))


async def test_up_arrow_walks_history(home):
    for text in ("first", "second"):
        sessions.add_history(text, str(home))
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        app.prompt.set_text("draft")
        await pilot.press("up")
        assert app.prompt.text == "second"
        await pilot.press("up")
        assert app.prompt.text == "first"
        await pilot.press("down", "down")
        assert app.prompt.text == "draft"


async def test_shell_output_goes_with_next_prompt(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "!echo from-the-shell")
        await until(pilot, lambda: any("from-the-shell" in text_of(n.renderable) for n in entries(app, NoticeEntry)))
        await send(pilot, "hello")
        await until(pilot, lambda: not app.busy and replies(app))
        prompt = [m for m in sent(home) if m.get("type") == "user"][0]["message"]["content"]
        assert prompt.startswith("<bash-input>echo from-the-shell</bash-input>") and prompt.endswith("hello")


async def test_agent_and_thinking(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "use an agent")
        await until(pilot, lambda: not app.busy and replies(app))
        agent = entries(app, ToolEntry)[0]
        assert agent.children_calls[0][1] == "Bash"
        assert "Done (1 tool use · 1.5k tokens · 2.1s)" in text_of(agent.result_view.content)
        await send(pilot, "think hard")
        await until(pilot, lambda: not app.busy and len(replies(app)) == 2)
        thought = entries(app, ThinkingEntry)[0]
        assert thought.text == "Seventeen times twenty-three is 391."
        await pilot.press("ctrl+o")
        assert thought.expanded and "391" in text_of(thought.view.content)


async def test_bash_permission_and_folded_output(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "run bash")
        await until(pilot, lambda: app.dialog is not None)
        assert "Yes, and don't ask again for ls:* commands in this project" in text_of(app.dialog.choice_view.content)
        await pilot.press("enter")
        await until(pilot, lambda: not app.busy and replies(app))
        out = text_of(entries(app, ToolEntry)[0].result_view.content)
        assert "file3.txt" in out and "… +8 lines (ctrl+o to expand)" in out


async def test_clear_starts_fresh(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "hello")
        await until(pilot, lambda: replies(app) and not app.busy)
        await send(pilot, "/clear")
        await until(pilot, lambda: not replies(app) and not app.busy)
        assert any("new conversation" in text_of(n.renderable) for n in entries(app, NoticeEntry))


async def test_resume_shows_saved_conversation(home):
    cwd = str(home)
    folder = home / "projects" / sessions.project_dir(cwd).split("/")[-1]
    folder.mkdir(parents=True)
    records = [
        {"type": "user", "message": {"role": "user", "content": "what's in notes?"}},
        {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": f"{cwd}/notes.txt"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1\talpha"}]},
         "toolUseResult": {"type": "text", "file": {"numLines": 1}}},
        {"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [{"type": "text", "text": "It says alpha."}]}},
    ]
    (folder / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    app = make(home, resume="s1")
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        assert [e.text for e in entries(app, UserEntry)] == ["what's in notes?"]
        assert replies(app) == ["It says alpha."]
        assert "Read 1 line" in text_of(entries(app, ToolEntry)[0].result_view.content)
        assert ["--resume", "s1"] == next(m["argv"] for m in sent(home) if "argv" in m)[-2:] or \
            "--resume" in next(m["argv"] for m in sent(home) if "argv" in m)


async def test_ctrl_c_twice_exits(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await pilot.press("ctrl+c")
        assert "again to exit" in (app.footer.flash_text or "")
        await pilot.press("ctrl+c")
        await until(pilot, lambda: app.engine is None)


async def test_engine_that_dies_is_reported(home):
    app = JarvisApp(Options(cwd=str(home), engine_command=[sys.executable, "-c", "import sys; sys.exit(3)"]))
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.engine_state == "stopped")
        assert any("Couldn't start Claude Code" in text_of(n.renderable) for n in entries(app, NoticeEntry))


async def test_model_picker_from_a_command(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "/model")
        await until(pilot, lambda: app.dialog is not None)
        await pilot.press(*"sonn", "enter")
        await until(pilot, lambda: any("Set model to" in text_of(n.renderable) for n in entries(app, NoticeEntry)))
        assert app.model_override == "sonnet"


async def test_resume_picker_switches_conversation(home):
    cwd = str(home)
    folder = home / "projects" / sessions.project_dir(cwd).split("/")[-1]
    folder.mkdir(parents=True)
    for sid, prompt in (("old1", "first chat"), ("old2", "second chat")):
        (folder / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "/resume")
        await until(pilot, lambda: app.dialog is not None)
        await pilot.press(*"first", "enter")
        await until(pilot, lambda: app.dialog is None and app.engine_state == "running"
                    and [e.text for e in entries(app, UserEntry)] == ["first chat"])
        assert app.session_id == "old1"


async def test_stale_menu_does_not_run_a_command(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        app.prompt.set_text("/")
        await pilot.pause(0.1)
        app.prompt.set_text("hello")
        await pilot.press("enter")
        await until(pilot, lambda: replies(app) == ["Hello, sir. How may I help?"])


async def test_permission_prompt_runs_notification_hooks(home, monkeypatch):
    out = home / "hook.json"
    monkeypatch.setenv("FAKE_HOOK_OUT", str(out))
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "edit the notes")
        await until(pilot, lambda: app.dialog is not None)
        await until(pilot, lambda: out.exists() and out.read_text())
        event = json.loads(out.read_text())
        assert event["hook_event_name"] == "Notification" and event["notification_type"] == "permission_prompt"
        assert event["cwd"] == str(home) and "Edit" in event["message"]
        await pilot.press("1")


async def test_task_list_without_bookkeeping_entries(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "make tasks")
        await until(pilot, lambda: not app.busy and replies(app))
        assert app.task_list() == [{"content": "Write the tests", "status": "in_progress", "activeForm": "Writing the tests"}]
        quiet = [e for e in entries(app, ToolEntry) if e.tool_name in ("TaskCreate", "TaskUpdate")]
        assert len(quiet) == 2 and not any(e.display for e in quiet)
        await pilot.press("ctrl+o")
        assert all(e.display for e in quiet)


async def test_background_tasks_in_footer(home):
    app = make(home)
    async with app.run_test(size=SIZE) as pilot:
        await ready(pilot)
        await send(pilot, "start background server")
        await until(pilot, lambda: not app.busy and "1 background task" in app.footer.right)
        await send(pilot, "stop background server")
        await until(pilot, lambda: not app.busy and "background" not in app.footer.right)
