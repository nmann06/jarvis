import io
import json

from rich.console import Console

from jarvis_code import sessions, tools


def plain(renderable, width=80):
    console = Console(width=width, file=io.StringIO(), color_system=None)
    console.print(renderable)
    return console.file.getvalue()


def test_titles_read_like_claude_codes():
    cwd = "/home/nate/jarvis"
    assert tools.title("Bash", {"command": "git status"}, cwd) == ("Bash", "git status")
    assert tools.title("Edit", {"file_path": "/home/nate/jarvis/jarvis/voice.py"}, cwd) == ("Update", "jarvis/voice.py")
    assert tools.title("Read", {"file_path": "/home/nate/notes.md"}, cwd) == ("Read", "~/notes.md")
    assert tools.title("Agent", {"description": "Find it", "subagent_type": "Explore", "prompt": "x"}, cwd) == ("Explore", "Find it")
    label, arg = tools.title("mcp__claude_ai_Jarvis__get_weather", {"city": "Kingston"}, cwd)
    assert label == "Jarvis - get_weather (MCP)" and arg == 'city: "Kingston"'


def test_edit_result_is_a_numbered_diff():
    data = {"structuredPatch": [{"oldStart": 10, "oldLines": 2, "newStart": 10, "newLines": 3,
                                 "lines": [" def snooze(self):", "-    pass", "+    self.reset()", "+    return True"]}]}
    text = plain(tools.result("Edit", {"file_path": "/p/timers.py"}, "ok", data, False, "/p"))
    assert "Updated timers.py with 2 additions and 1 removal" in text
    assert "11 -     pass" in text
    assert "11 +     self.reset()" in text
    assert "12 +     return True" in text


def test_long_output_is_folded_until_ctrl_o():
    out = "\n".join(f"line {i}" for i in range(10))
    folded = plain(tools.result("Bash", {"command": "seq"}, out, {"stdout": out, "stderr": ""}, False, "/"))
    assert "line 3" in folded and "line 4" not in folded and "… +6 lines (ctrl+o to expand)" in folded
    full = plain(tools.result("Bash", {"command": "seq"}, out, {"stdout": out, "stderr": ""}, False, "/", verbose=True))
    assert "line 9" in full


def test_errors_and_rejections():
    text = plain(tools.result("Bash", {"command": "x"}, "<tool_use_error>boom</tool_use_error>", None, True, "/"))
    assert "boom" in text and "tool_use_error" not in text
    assert "User rejected" in plain(tools.result("Edit", {}, "The user doesn't want to proceed", None, True, "/"))


def test_agent_summary():
    data = {"status": "completed", "totalToolUseCount": 3, "totalTokens": 16500, "totalDurationMs": 3900}
    assert "Done (3 tool uses · 16.5k tokens · 3.9s)" in plain(tools.result("Agent", {"prompt": "p"}, "x", data, False, "/"))


def test_sessions_list_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "PROJECTS", str(tmp_path))
    cwd = "/home/nate/proj"
    folder = tmp_path / "-home-nate-proj"
    folder.mkdir()
    records = [
        {"type": "user", "message": {"role": "user", "content": "fix the timer"}, "gitBranch": "main"},
        {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"}},
        {"type": "ai-title", "aiTitle": "Fix the timer bug"},
    ]
    (folder / "abc123.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    found = sessions.list_sessions(cwd)
    assert [(s.id, s.title, s.prompts, s.branch) for s in found] == [("abc123", "Fix the timer bug", 1, "main")]
    assert sessions.find_session(cwd, "abc") == str(folder / "abc123.jsonl")
    kinds = [m["type"] for m in sessions.load_conversation(str(folder / "abc123.jsonl"))]
    assert kinds == ["user", "assistant"]


def test_history_shared_with_claude_code(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(sessions, "HISTORY", str(path))
    path.write_text(json.dumps({"display": "[Pasted text #1 +2 lines] please", "project": "/p",
                                "pastedContents": {"1": {"id": 1, "type": "text", "content": "a\nb"}}}) + "\n"
                    + json.dumps({"display": "other project", "project": "/q"}) + "\n")
    sessions.add_history("newest", "/p", "sid")
    assert sessions.read_history("/p") == ["newest", "a\nb please"]


async def test_files_offered_after_an_at(tmp_path):
    from jarvis_code.files import FileIndex
    (tmp_path / "jarvis").mkdir()
    for name in ("voice.py", "timers.py"):
        (tmp_path / "jarvis" / name).write_text("x")
    (tmp_path / "notes.md").write_text("x")
    index = FileIndex(str(tmp_path))
    await index.load()
    assert index.search("tim") == ["jarvis/timers.py"]
    assert index.search("jarvis/")[:1] == ["jarvis/"]
    assert "notes.md" in index.search("")
