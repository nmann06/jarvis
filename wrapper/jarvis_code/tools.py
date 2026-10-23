"""How a tool call reads in the transcript, the way Claude Code draws it:

    ● Update(jarvis/timers.py)
      ⎿  Updated jarvis/timers.py with 2 additions and 1 removal
           10      def snooze(self):
           11 -        pass
           11 +        self.reset()

title() gives the "Update(jarvis/timers.py)" part; result() the lines under the ⎿.
Everything here returns Rich renderables and is pure, so it can be tested on its own.
"""

import json
import os

from rich.console import Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import theme

COLLAPSED_LINES = 4      # output lines shown before "… +N lines (ctrl+o to expand)"
COLLAPSED_DIFF = 40
COLLAPSED_WRITE = 8
EXPANDED_LINES = 400     # even ctrl+o stops somewhere

HOME = os.path.expanduser("~")


def short_path(path, cwd):
    """Relative to the project when inside it, ~/... when under home."""
    if not path:
        return ""
    if cwd and (path == cwd or path.startswith(cwd.rstrip("/") + "/")):
        return os.path.relpath(path, cwd)
    if path == HOME or path.startswith(HOME + "/"):
        return "~" + path[len(HOME):]
    return path


def one_line(text, limit=120):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def mcp_name(name):
    """mcp__claude_ai_Jarvis__get_weather -> ("claude_ai_Jarvis", "get_weather")."""
    parts = name.split("__")
    if len(parts) >= 3 and parts[0] == "mcp":
        return parts[1], "__".join(parts[2:])
    return None


def short_args(tool_input, limit=100):
    if not tool_input:
        return ""
    parts = []
    for key, value in tool_input.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, str) else value}")
    return one_line(", ".join(parts), limit)


def title(name, tool_input, cwd):
    """(label, argument) for the tool's header line: Bash, "git status"."""
    i = tool_input or {}
    path = short_path(i.get("file_path") or i.get("notebook_path") or i.get("path") or "", cwd)
    if name == "Bash":
        return "Bash", one_line(i.get("command", ""), 160)
    if name == "Read":
        extra = ""
        if i.get("offset") or i.get("limit"):
            start = i.get("offset") or 1
            extra = f" · lines {start}-{start + i['limit'] - 1}" if i.get("limit") else f" · from line {start}"
        return "Read", path + extra
    if name == "Write":
        return "Write", path
    if name in ("Edit", "MultiEdit"):
        return "Update", path
    if name == "NotebookEdit":
        return "Edit Notebook", path
    if name in ("Glob", "Grep"):
        arg = f'pattern: "{i.get("pattern", "")}"'
        if i.get("path"):
            arg += f', path: "{path}"'
        return "Search", arg
    if name == "WebFetch":
        return "Fetch", i.get("url", "")
    if name == "WebSearch":
        return "Web Search", f'"{i.get("query", "")}"'
    if name in ("Agent", "Task") and ("prompt" in i or "subagent_type" in i):
        return i.get("subagent_type") or "Agent", i.get("description", "")
    if name == "TodoWrite":
        return "Update Todos", ""
    if name == "TaskCreate":
        return "Add Task", one_line(i.get("subject", ""), 100)
    if name == "TaskUpdate":
        return "Update Task", f"#{i.get('taskId', '?')} → {i.get('status', '')}".rstrip(" →")
    if name == "Skill":
        return "Skill", i.get("skill") or i.get("command") or ""
    if name == "ToolSearch":
        return "Load Tools", one_line(i.get("query", "").removeprefix("select:"), 100)
    if name == "AskUserQuestion":
        return "Question", one_line("; ".join(q.get("question", "") for q in i.get("questions", [])), 100)
    if name == "ExitPlanMode":
        return "Plan", "ready for review"
    mcp = mcp_name(name)
    if mcp:
        server, tool = mcp
        server = server.removeprefix("claude_ai_").replace("_", " ")
        return f"{server} - {tool} (MCP)", short_args(i)
    return name, short_args(i)


def header(name, tool_input, cwd, pending=False):
    label, arg = title(name, tool_input, cwd)
    text = Text(label, style="bold")
    if arg or pending:
        text.append("(")
        text.append(arg if arg else "…", style="" if arg else theme.DIM)
        text.append(")")
    return text


# Results ----------------------------------------------------------------------------------

def block(rows, first="⎿  "):
    """Rows under the ⎿, each row a renderable; wrapped lines stay indented."""
    grid = Table.grid(padding=0, expand=True)
    grid.add_column(width=len(first), no_wrap=True)
    grid.add_column(ratio=1)
    for index, row in enumerate(rows):
        grid.add_row(Text(first if index == 0 else "", style=theme.DIM), row)
    return grid


def more(n):
    return Text(f"… +{n} line{'s' if n != 1 else ''} (ctrl+o to expand)", style=theme.DIM)


def plain_lines(text, verbose, style="", limit=COLLAPSED_LINES):
    lines = str(text).rstrip("\n").split("\n") if str(text).strip() else []
    shown = lines[:EXPANDED_LINES if verbose else limit]
    rows = [Text(line.expandtabs(4), style=style) for line in shown]
    if len(lines) > len(shown):
        rows.append(more(len(lines) - len(shown)))
    return rows


def content_text(content):
    """A tool_result's content, which is a string or a list of blocks, as text."""
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "image":
                parts.append("[image]")
            elif item.get("type") == "tool_reference":
                parts.append(item.get("tool_name", ""))
    return "\n".join(parts)


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def tokens(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def seconds(ms):
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m {int(s % 60)}s"


class DiffView:
    """A unified diff from Claude Code's structuredPatch, numbered and highlighted, with full
    width red and green rows."""

    def __init__(self, hunks, path, verbose=False):
        self.hunks, self.path, self.verbose = hunks, path, verbose

    def counts(self):
        added = sum(1 for h in self.hunks for line in h.get("lines", []) if line.startswith("+"))
        removed = sum(1 for h in self.hunks for line in h.get("lines", []) if line.startswith("-"))
        return added, removed

    def _highlighter(self):
        try:
            lexer = Syntax.guess_lexer(self.path or "", code="")
        except Exception:
            lexer = "text"

        def highlight(code):
            try:
                text = Syntax(code, lexer, theme="monokai", background_color="default").highlight(code)
                text.rstrip()
                return text
            except Exception:
                return Text(code)
        return highlight

    def __rich_console__(self, console, options):
        width = max(20, options.max_width)
        highlight = self._highlighter()
        last = max((h.get("newStart", 1) + h.get("newLines", 0) for h in self.hunks), default=1)
        num_width = max(3, len(str(last)))
        shown, total = 0, sum(len(h.get("lines", [])) for h in self.hunks)
        limit = EXPANDED_LINES if self.verbose else COLLAPSED_DIFF
        for index, hunk in enumerate(self.hunks):
            if index:
                yield Text(" " * num_width + " ...", style=theme.DIM)
            old, new = hunk.get("oldStart", 1), hunk.get("newStart", 1)
            for line in hunk.get("lines", []):
                if shown >= limit:
                    yield more(total - shown)
                    return
                shown += 1
                sign, code = line[:1], line[1:].expandtabs(4)
                if sign == "-":
                    number, bg = old, theme.DIFF_REMOVED
                    old += 1
                elif sign == "+":
                    number, bg = new, theme.DIFF_ADDED
                    new += 1
                else:
                    number, bg, sign = new, None, " "
                    old += 1
                    new += 1
                row = Text.assemble((f"{number:>{num_width}} ", theme.DIM))
                row.append(sign + " ", style="bold" if bg else "")
                row.append_text(highlight(code))
                if not bg:
                    yield row
                    continue
                for wrapped in row.wrap(console, width):
                    wrapped.pad_right(max(0, width - wrapped.cell_len))
                    wrapped.stylize(f"on {bg}")
                    yield wrapped


def numbered(text, path, verbose, limit=COLLAPSED_WRITE):
    lines = text.rstrip("\n").split("\n") if text else []
    shown = lines[:EXPANDED_LINES if verbose else limit]
    try:
        lexer = Syntax.guess_lexer(path or "", code="")
    except Exception:
        lexer = "text"
    width = max(3, len(str(len(shown))))
    rows = []
    for n, line in enumerate(shown, 1):
        row = Text.assemble((f"{n:>{width}} ", theme.DIM))
        line = line.expandtabs(4)
        try:
            code = Syntax(line, lexer, theme="monokai", background_color="default").highlight(line)
            code.rstrip()
            row.append_text(code)
        except Exception:
            row.append(line)
        rows.append(row)
    if len(lines) > len(shown):
        rows.append(more(len(lines) - len(shown)))
    return rows


def todo_rows(todos):
    rows = []
    for todo in todos:
        status = todo.get("status")
        label = todo.get("content") or todo.get("subject") or ""
        if status == "completed":
            rows.append(Text.assemble(("☒ ", theme.DIM), (label, f"{theme.DIM} strike")))
        elif status == "in_progress":
            rows.append(Text.assemble(("◼ ", f"bold {theme.ACCENT}"), (label, "bold")))
        else:
            rows.append(Text("☐ ") + Text(label))
    return rows


def result(name, tool_input, content, data, is_error, cwd, verbose=False):
    """The renderable under the ⎿ for a finished tool call, or None to show nothing.

    content is the tool_result content the model sees; data is Claude Code's structured
    tool_use_result (a dict for most tools, a string for errors).
    """
    i = tool_input or {}
    text = content_text(content)
    d = data if isinstance(data, dict) else {}
    path = short_path(i.get("file_path") or i.get("notebook_path") or "", cwd)

    if is_error:
        message = text.replace("<tool_use_error>", "").replace("</tool_use_error>", "").strip()
        if "doesn't want to proceed" in message or "rejected" in message.lower():
            return block([Text("User rejected this action", style=theme.ERROR)])
        return block(plain_lines(message or "Error", verbose, style=theme.ERROR))

    if name == "Bash":
        out = d.get("stdout", text) if d else text
        err = d.get("stderr", "") if d else ""
        rows = plain_lines(out, verbose) + plain_lines(err, verbose, style=theme.ERROR)
        if d.get("interrupted"):
            rows.append(Text("Interrupted", style=theme.ERROR))
        if d.get("backgroundTaskId"):
            rows.append(Text(f"Running in the background ({d['backgroundTaskId']})", style=theme.DIM))
        return block(rows or [Text("(No output)", style=theme.DIM)])

    if name == "Read":
        f = d.get("file") or {}
        if d.get("type") == "image":
            return block([Text("Read image", style=theme.DIM)])
        lines = f.get("numLines") or (text.count("\n") + 1 if text else 0)
        return block([Text.assemble("Read ", (str(lines), "bold"), f" line{'s' if lines != 1 else ''}")])

    if name in ("Edit", "MultiEdit") or (name == "Write" and d.get("structuredPatch")):
        diff = DiffView(d.get("structuredPatch") or [], i.get("file_path", ""), verbose)
        added, removed = diff.counts()
        summary = Text.assemble("Updated ", (path, "bold"))
        if added or removed:
            summary.append(" with ")
            parts = []
            if added:
                parts.append(f"{plural(added, 'addition')}")
            if removed:
                parts.append(f"{plural(removed, 'removal')}")
            summary.append(" and ".join(parts))
        return block([summary, diff] if diff.hunks else [summary])

    if name == "Write":
        body = i.get("content", d.get("content", ""))
        lines = body.count("\n") + (0 if body.endswith("\n") else 1) if body else 0
        rows = [Text.assemble("Wrote ", (str(lines), "bold"), f" line{'s' if lines != 1 else ''} to ", (path, "bold"))]
        return block(rows + numbered(body, i.get("file_path", ""), verbose))

    if name in ("Glob", "Grep"):
        count = d.get("numFiles") or d.get("numLines") or len(d.get("filenames", []))
        unit = "line" if d.get("mode") == "content" else "file"
        return block([Text.assemble("Found ", (str(count or 0), "bold"), f" {unit}{'s' if count != 1 else ''}")])

    if name == "WebFetch":
        size = d.get("bytes")
        status = f"{d.get('code', '')} {d.get('codeText', '')}".strip()
        if size:
            return block([Text(f"Received {size / 1024:.1f}KB" + (f" ({status})" if status else ""), style=theme.DIM)])

    if name == "WebSearch" and d:
        searches = d.get("searchCount") or 1
        took = d.get("durationSeconds")
        return block([Text(f"Did {plural(searches, 'search')}" + (f" in {took:.0f}s" if took else ""), style=theme.DIM)])

    if name in ("Agent", "Task") and ("totalToolUseCount" in d or "status" in d):
        parts = [plural(d.get("totalToolUseCount", 0), "tool use")]
        if d.get("totalTokens"):
            parts.append(f"{tokens(d['totalTokens'])} tokens")
        if d.get("totalDurationMs"):
            parts.append(seconds(d["totalDurationMs"]))
        word = "Done" if d.get("status", "completed") == "completed" else d.get("status", "").capitalize()
        rows = [Text(f"{word} ({' · '.join(parts)})", style=theme.DIM)]
        if verbose:
            rows += [Text("")] + plain_lines(content_text(d.get("content")) or text, True)
        return block(rows)

    if name == "TodoWrite":
        return block(todo_rows(i.get("todos", [])) or [Text("(no todos)", style=theme.DIM)])

    if name in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return None if not verbose else block(plain_lines(text, True, style=theme.DIM))

    if name == "ToolSearch":
        matches = d.get("matches")
        return block([Text(", ".join(matches) if matches else "No tools found", style=theme.DIM)])

    if name == "Skill":
        return block([Text("Loaded", style=theme.DIM)])

    if name == "AskUserQuestion":
        answers = d.get("answers") or {}
        if not answers:
            return block([Text("No answer", style=theme.DIM)])
        return block([Text.assemble("· ", q, " → ", (a, f"bold {theme.ACCENT}")) for q, a in answers.items()])

    if name == "ExitPlanMode":
        return block([Text("Plan approved", style=theme.DIM)])

    return block(plain_lines(text, verbose) or [Text("(No output)", style=theme.DIM)])


def rejected():
    return block([Text("User rejected this action", style=theme.ERROR)])


def interrupted():
    return block([Text("Interrupted · What should Jarvis do instead?", style=theme.ERROR)])


__all__ = ["title", "header", "result", "block", "DiffView", "todo_rows", "short_path",
           "content_text", "rejected", "interrupted", "Group"]
