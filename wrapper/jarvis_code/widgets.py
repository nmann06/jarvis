"""The pieces of the Jarvis window, top to bottom: the JARVIS banner, transcript entries,
spinner, task list, prompt, suggestion menu, footer, and the dialogs that take the prompt's
place when Claude Code asks for permission or has a question."""

import asyncio
import time

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Markdown, Static, TextArea

from . import theme, tools


def elapsed(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


# Banner -------------------------------------------------------------------------------------

class Banner(Static):
    """JARVIS in blue where Claude Code has its orange mascot."""

    DEFAULT_CSS = "Banner { height: auto; padding: 1 0 0 1; }"

    def __init__(self):
        super().__init__()
        self.info = {}

    def set_info(self, **info):
        self.info.update({k: v for k, v in info.items() if v is not None})
        self.refresh(layout=True)

    def render(self):
        i = self.info
        width = self.size.width or 80
        lines = []
        if width >= len(theme.JARVIS_ROWS[0]) + 2:
            for row, colour in zip(theme.JARVIS_ROWS, theme.GRADIENT):
                lines.append(Text(row, style=f"bold {colour}"))
            lines.append(Text.assemble(("Just A Rather Vaguely Intelligent System", f"{theme.LABEL}"),
                                       ("  ·  Claude Code" + (f" v{i['version']}" if i.get("version") else ""),
                                        theme.ACCENT)))
        else:
            lines.append(Text.assemble(("◆ J.A.R.V.I.S.", f"bold {theme.ACCENT}"),
                                       ("  Claude Code", theme.LABEL)))
        lines.append(Text(""))
        lines.append(Text(theme.greeting(time.localtime().tm_hour), style="bold"))
        model = " · ".join(x for x in (i.get("model"), i.get("effort") and f"{i['effort']} effort",
                                       i.get("plan")) if x)
        where = i.get("cwd", "") + (f"  ⎇ {i['branch']}" if i.get("branch") else "")
        for value in (model, where):
            if value:
                lines.append(Text.assemble(("◆ ", theme.ACCENT), (value, theme.LABEL)))
        lines.append(Text(""))
        lines.append(Text("/help for commands · shift+tab to switch modes · @ to add files · ! for shell",
                          style=theme.DIM))
        return Group(*lines)


# Transcript entries ---------------------------------------------------------------------------

class Entry(Horizontal):
    """One thing in the transcript: a glyph in a two-column gutter, then the body."""

    DEFAULT_CSS = """
    Entry { height: auto; margin-top: 1; }
    Entry.attached, Entry.after-reply { margin-top: 0; }
    Entry > .gutter { width: 2; height: auto; }
    Entry > .body { width: 1fr; height: auto; }
    """

    def __init__(self, glyph, glyph_style, body, classes=""):
        super().__init__(classes=classes)
        self.glyph_view = Static(Text(glyph, style=glyph_style), classes="gutter")
        self.body = body
        self.body.add_class("body")

    def compose(self):
        yield self.glyph_view
        yield self.body

    def set_glyph(self, glyph, style):
        self.glyph_view.update(Text(glyph, style=style))

    def plain(self):
        return ""


class UserEntry(Entry):
    DEFAULT_CSS = f"""
    UserEntry {{ background: {theme.USER_BG}; padding-right: 1; }}
    """

    def __init__(self, text, glyph=">", glyph_style=theme.DIM):
        self.text = text
        super().__init__(glyph, glyph_style, Static(Text(text), markup=False))

    def plain(self):
        return f"> {self.text}"


class AssistantEntry(Entry):
    """Jarvis's words, as Markdown, streamed in as they arrive."""

    def __init__(self, text=""):
        self.text = text
        self.markdown = Markdown(text, classes="reply")
        self._stream = None
        super().__init__(theme.BULLET, "", self.markdown)

    async def append(self, fragment):
        if not fragment:
            return
        if self._stream is None:
            self._stream = Markdown.get_stream(self.markdown)
        self.text += fragment
        await self._stream.write(fragment)

    async def finish(self, text=None):
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None
        if text is not None and text != self.text:
            self.text = text
            await self.markdown.update(text)

    def plain(self):
        return f"● {self.text}"


class ThinkingEntry(Entry):
    """∴ Thinking…, folded to one line; ctrl+o (or a click) shows what was thought."""

    def __init__(self, text="", verbose=False, done=False):
        self.text = text
        self.started = time.monotonic()
        self.duration = None
        self.done = done  # a thought from a saved conversation: how long it took isn't known
        self.expanded = verbose
        self.view = Static()
        super().__init__("∴", f"{theme.ACCENT}", self.view)

    def on_mount(self):
        self.redraw()

    def append(self, fragment):
        self.text += fragment
        if self.expanded:
            self.redraw()

    def finish(self, text=None):
        if text:
            self.text = text
        if not self.done:
            self.duration = time.monotonic() - self.started
            self.done = True
        self.redraw()

    def set_verbose(self, verbose):
        self.expanded = verbose
        self.redraw()

    def on_click(self):
        self.set_verbose(not self.expanded)

    def redraw(self):
        if not self.done:
            label = Text("Thinking…", style=f"italic {theme.ACCENT}")
        elif self.duration is None:
            label = Text("Thought", style=f"italic {theme.ACCENT}")
        else:
            label = Text(f"Thought for {max(1, round(self.duration))}s", style=f"italic {theme.ACCENT}")
        if not self.text.strip():
            self.view.update(label)
        elif self.expanded:
            self.view.update(Group(label, Text(self.text.strip(), style=f"italic {theme.DIM}")))
        else:
            self.view.update(label + Text(" (ctrl+o to expand)", style=theme.DIM))

    def plain(self):
        return f"∴ {self.text.strip()}" if self.text.strip() else ""


class ToolEntry(Entry):
    """● Tool(args) with its result under a ⎿; agents list their own tool calls as they go."""

    DEFAULT_CSS = """
    ToolEntry > .body > .progress { height: auto; }
    ToolEntry > .body > .result { height: auto; }
    """

    def __init__(self, tool_id, name, tool_input, cwd, verbose=False):
        self.tool_id, self.tool_name, self.input, self.cwd = tool_id, name, tool_input or {}, cwd
        self.verbose = verbose
        self.state = "running"
        self.result_args = None
        self.children_calls = []  # an agent's own tool calls: [id, name, input, state]
        self.activity = ""
        self.waiting = False  # for the permission dialog
        self.input_complete = bool(tool_input)
        self.header_view = Static()
        self.progress_view = Static(classes="progress")
        self.result_view = Static(classes="result")
        super().__init__(theme.BULLET, theme.DIM, Vertical(self.header_view, self.progress_view, self.result_view))

    def on_mount(self):
        self.redraw()

    def set_input(self, tool_input):
        self.input = tool_input or {}
        self.input_complete = True
        self.redraw()

    def set_result(self, content, data, is_error):
        self.result_args = (content, data, is_error)
        self.state = "error" if is_error else "done"
        self.redraw()

    def mark(self, state):
        self.state = state
        self.redraw()

    def add_child(self, tool_id, name, tool_input):
        self.children_calls.append([tool_id, name, tool_input or {}, "running"])
        self.redraw()

    def child_done(self, tool_id, is_error):
        for call in self.children_calls:
            if call[0] == tool_id:
                call[3] = "error" if is_error else "done"
        self.redraw()

    def set_activity(self, text):
        self.activity = text
        self.redraw()

    def set_verbose(self, verbose):
        self.verbose = verbose
        self.redraw()

    def set_waiting(self, waiting):
        self.waiting = waiting
        self.redraw()

    def blink(self, on):
        if self.state == "running":
            self.set_glyph(theme.BULLET, theme.ACCENT if on else theme.DIM)

    def redraw(self):
        colour = {"running": theme.DIM, "done": theme.SUCCESS, "error": theme.ERROR,
                  "rejected": theme.ERROR, "interrupted": theme.ERROR}[self.state]
        self.set_glyph(theme.BULLET, colour)
        self.header_view.update(tools.header(self.tool_name, self.input, self.cwd,
                                             pending=not self.input_complete))
        progress = self._progress()
        self.progress_view.update(progress)
        self.progress_view.display = bool(progress)
        result = self._result()
        self.result_view.update(result)
        self.result_view.display = bool(result)

    def _progress(self):
        if self.state != "running" and not self.verbose:
            return ""
        calls = self.children_calls if self.verbose else self.children_calls[-3:]
        rows = []
        for _, name, tool_input, state in calls:
            row = tools.header(name, tool_input, self.cwd)
            if state == "error":
                row.stylize(theme.ERROR)
            rows.append(row)
        hidden = len(self.children_calls) - len(calls)
        if hidden:
            rows.append(Text(f"+{hidden} more tool use{'s' if hidden != 1 else ''} (ctrl+o to expand)", style=theme.DIM))
        if self.activity and self.state == "running":
            rows.append(Text(self.activity, style=theme.DIM))
        return tools.block(rows) if rows else ""

    def _result(self):
        if self.state == "rejected":
            return tools.rejected()
        if self.state == "interrupted":
            return tools.interrupted()
        if self.result_args is None:
            if self.state == "running" and self.tool_name == "Bash" and self.input_complete and not self.waiting:
                return tools.block([Text("Running…", style=theme.DIM)])
            return ""
        content, data, is_error = self.result_args
        view = tools.result(self.tool_name, self.input, content, data, is_error, self.cwd, self.verbose)
        return view if view is not None else ""

    def plain(self):
        label, arg = tools.title(self.tool_name, self.input, self.cwd)
        out = f"● {label}({arg})"
        if self.result_args:
            body = tools.content_text(self.result_args[0]).strip()
            if body:
                out += "\n  ⎿  " + body[:2000].replace("\n", "\n     ")
        return out


class NoticeEntry(Entry):
    """Lines under a ⎿ with no bullet: command output, interruptions, errors. The body is
    a Rich renderable, or a widget (Markdown, for a command's table)."""

    def __init__(self, renderable, attached=True, glyph="", glyph_style=""):
        self.renderable = renderable
        body = renderable if isinstance(renderable, Widget) else Static(renderable)
        super().__init__(glyph, glyph_style, body, classes="attached" if attached else "")

    def plain(self):
        return str(self.renderable) if isinstance(self.renderable, (str, Text)) else ""


class DoneEntry(Static):
    """✻ Calibrated for 12s, under a turn that took a while."""

    DEFAULT_CSS = "DoneEntry { height: auto; margin-top: 1; } DoneEntry.after-reply { margin-top: 0; }"

    def __init__(self, seconds):
        super().__init__(Text(f"✻ {theme.done_verb()} for {elapsed(seconds)}", style=theme.DIM))


def notice(text, style=theme.DIM):
    return tools.block([Text(text, style=style)])


# Spinner, tasks, queue --------------------------------------------------------------------------

class Spinner(Static):
    """✻ Calibrating… (12s · ↓ 1.2k tokens · esc to interrupt), in blue with a running shimmer."""

    DEFAULT_CSS = "Spinner { height: auto; margin-top: 1; padding: 0 1; display: none; }"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.frame = 0
        self.word = "Working"
        self.message = None
        self.mode = "requesting"
        self.started = 0.0
        self.tokens = 0
        self.extra = ""

    def start(self):
        self.word = theme.verb()
        self.message = None
        self.mode = "requesting"
        self.started = time.monotonic()
        self.tokens = 0
        self.extra = ""
        self.display = True
        self.tick()

    def stop(self):
        self.display = False

    def tick(self):
        if not self.display:
            return
        self.frame += 1
        glyph = theme.SPINNER_FRAMES[self.frame // 2 % len(theme.SPINNER_FRAMES)]
        word = (self.message or self.word).rstrip("…").rstrip(".") + "…"
        text = Text()
        text.append(glyph + " ", style=f"bold {theme.ACCENT}")
        light = self.frame % (len(word) + 12) - 4
        for index, char in enumerate(word):
            text.append(char, style=f"bold {theme.SHIMMER}" if abs(index - light) <= 1 else theme.ACCENT)
        parts = [elapsed(time.monotonic() - self.started)]
        if self.tokens:
            parts.append(f"↓ {tools.tokens(self.tokens)} tokens")
        if self.mode == "thinking":
            parts.append("thinking")
        if self.extra:
            parts.append(self.extra)
        parts.append("esc to interrupt")
        text.append(f" ({' · '.join(parts)})", style=theme.DIM)
        self.update(text)


class TaskPanel(Static):
    """Claude Code's task list, under the spinner while it works."""

    DEFAULT_CSS = "TaskPanel { height: auto; padding: 0 1 0 3; display: none; }"

    def show(self, tasks, visible):
        rows = tools.todo_rows(tasks)
        self.display = bool(rows) and visible
        if rows:
            self.update(tools.block(rows))


class QueuedPanel(Static):
    DEFAULT_CSS = "QueuedPanel { height: auto; padding: 0 1; margin-top: 1; display: none; }"

    def show(self, texts):
        self.display = bool(texts)
        self.update(Group(*(Text.assemble(("> ", theme.DIM), (" ".join(t.split())[:200], theme.DIM),
                                          ("  (queued)", f"italic {theme.LABEL}")) for t in texts)))


# Prompt ---------------------------------------------------------------------------------------

class HRule(Static):
    DEFAULT_CSS = f"HRule {{ height: 1; color: {theme.LABEL}; }}"

    def render(self):
        return "─" * max(1, self.size.width)


class PromptInput(TextArea):
    """The prompt. Enter sends; \\+Enter, ctrl+j or alt+enter start a new line; the arrows walk
    the suggestion menu when it's open and the prompt history at the first or last line."""

    DEFAULT_CSS = """
    PromptInput { height: auto; max-height: 12; border: none; padding: 0; background: transparent; }
    PromptInput:focus { border: none; }
    PromptInput .text-area--cursor-line { background: transparent; }
    PromptInput:ansi .text-area--cursor { text-style: none; }  /* a solid $input-cursor-background block */
    """

    class Submitted(Message):
        def __init__(self, text):
            super().__init__()
            self.text = text

    def __init__(self, **kw):
        super().__init__(soft_wrap=True, compact=True, highlight_cursor_line=False,
                         placeholder='Ask Jarvis anything, or try "/help"', **kw)

    async def _on_key(self, event):
        app = self.app
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            row, col = self.cursor_location
            line = self.document.get_line(row)
            if col > 0 and line[:col].endswith("\\"):
                self.replace("\n", (row, col - 1), (row, col))
                return
            if app.menu_accept(run=True):
                return
            self.post_message(self.Submitted(self.text))
            return
        if key in ("ctrl+j", "alt+enter", "shift+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "tab":
            event.stop()
            event.prevent_default()
            app.menu_accept(run=False)
            return
        if key == "escape" and app.menu_close():
            event.stop()
            event.prevent_default()
        # Anything else goes on to TextArea's own _on_key: Textual calls the handler of each
        # class in the chain until one prevents the default.

    def action_cursor_up(self, select=False):
        if not select and self.app.menu_move(-1):
            return
        if not select and self.cursor_location[0] == 0 and self.app.history_move(-1):
            return
        super().action_cursor_up(select)

    def action_cursor_down(self, select=False):
        if not select and self.app.menu_move(1):
            return
        if not select and self.cursor_location[0] == self.document.line_count - 1 and self.app.history_move(1):
            return
        super().action_cursor_down(select)

    def action_delete_right(self):
        if not self.text:
            self.app.action_ctrl_d()
            return
        super().action_delete_right()

    def set_text(self, text):
        self.load_text(text)
        last = self.document.line_count - 1
        self.move_cursor((last, len(self.document.get_line(last))))


class PromptBar(Vertical):
    DEFAULT_CSS = f"""
    PromptBar {{ height: auto; margin-top: 1; }}
    PromptBar > Horizontal {{ height: auto; }}
    PromptBar #prompt-mark {{ width: 2; color: {theme.DIM}; }}
    PromptBar.bash #prompt-mark {{ color: {theme.BASH_MODE}; text-style: bold; }}
    PromptBar.bash HRule {{ color: {theme.BASH_MODE}; }}
    PromptBar.mode-plan HRule {{ color: {theme.MODE_LOOK['plan'][2]}; }}
    PromptBar.mode-acceptEdits HRule {{ color: {theme.MODE_LOOK['acceptEdits'][2]}; }}
    PromptBar.mode-auto HRule {{ color: {theme.MODE_LOOK['auto'][2]}; }}
    PromptBar.mode-bypassPermissions HRule {{ color: {theme.ERROR}; }}
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.input = PromptInput(id="prompt")
        self.mark = Static(">", id="prompt-mark")

    def compose(self):
        yield HRule()
        with Horizontal():
            yield self.mark
            yield self.input
        yield HRule()

    def set_bash(self, on):
        self.set_class(on, "bash")
        self.mark.update("!" if on else ">")

    def set_mode(self, mode):
        for name in theme.MODE_LOOK:
            self.set_class(mode == name, f"mode-{name}")


class SuggestionMenu(Static):
    """Slash commands or @-files matching what's typed, under the prompt like Claude Code's."""

    DEFAULT_CSS = "SuggestionMenu { height: auto; padding: 0 1 0 3; display: none; }"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.items = []  # (value, label, description)
        self.index = 0
        self.kind = None

    def show(self, kind, items):
        self.kind, self.items, self.index = kind, items, 0
        self.display = bool(items)
        self.redraw()

    def hide(self):
        self.items, self.kind = [], None
        self.display = False

    @property
    def open(self):
        return bool(self.display and self.items)

    def move(self, step):
        if self.items:
            self.index = (self.index + step) % len(self.items)
            self.redraw()

    def current(self):
        return self.items[self.index] if self.items else None

    def redraw(self):
        width = max((len(label) for _, label, _ in self.items), default=0)
        start = max(0, min(self.index - 4, len(self.items) - 8))
        rows = []
        for n, (_, label, description) in enumerate(self.items[start:start + 8], start):
            chosen = n == self.index
            row = Text(label.ljust(width + 2), style=f"bold {theme.ACCENT}" if chosen else "")
            if description:
                row.append(" ".join(description.split())[:120], style=theme.LABEL if chosen else theme.DIM)
            row.no_wrap = True
            row.overflow = "ellipsis"
            rows.append(row)
        self.update(Group(*rows))


class Footer(Static):
    """⏵⏵ auto mode on (shift+tab to cycle) on the left; model and context on the right."""

    DEFAULT_CSS = "Footer { height: 1; padding: 0 1 0 2; }"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.mode = "default"
        self.right = ""
        self.flash_text = None
        self.flash_until = 0.0
        self.hint = None

    def flash(self, text, seconds=1.5):
        self.flash_text, self.flash_until = text, time.monotonic() + seconds
        self.redraw()

    def redraw(self):
        if self.flash_text and time.monotonic() < self.flash_until:
            left = Text(self.flash_text, style=theme.LABEL)
        elif self.hint:
            left = Text(self.hint, style=theme.DIM)
        else:
            symbol, label, colour = theme.MODE_LOOK.get(self.mode, theme.MODE_LOOK["default"])
            if label:
                left = Text.assemble((f"{symbol} {label}", colour), (" (shift+tab to cycle)", theme.DIM))
            else:
                left = Text("? for shortcuts", style=theme.DIM)
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(left, Text(self.right, style=theme.DIM))
        self.update(grid)


SHORTCUTS = [
    ("! for shell mode", "\\ + enter for a new line"),
    ("/ for commands", "ctrl+j for a new line"),
    ("@ for file paths", "shift+tab to switch modes"),
    ("esc to interrupt", "ctrl+o to expand details"),
    ("esc esc to clear the prompt", "ctrl+t to show tasks"),
    ("ctrl+c twice to exit", "ctrl+g to edit in $EDITOR"),
]


def shortcuts():
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()
    for left, right in SHORTCUTS:
        grid.add_row(Text(left, style=theme.DIM), Text(right, style=theme.DIM))
    return grid


# Dialogs --------------------------------------------------------------------------------------

class Dialog(Vertical):
    """A box where the prompt was: a title, what it's about, and numbered choices.

    Resolves self.result with the chosen index, or None for esc. A choice marked as text
    entry opens a line to type into (the "Other" answer to a question).

    Textual runs a message handler in every class of the chain, so subclasses change the
    hooks (take_focus, dialog_key, submitted), never on_mount or on_key themselves.
    """

    can_focus = True

    DEFAULT_CSS = f"""
    Dialog {{ height: auto; max-height: 80vh; border: round {theme.PERMISSION}; padding: 0 1;
              margin-top: 1; }}
    Dialog:focus-within {{ border: round {theme.PERMISSION}; }}
    Dialog > .title {{ text-style: bold; color: {theme.PERMISSION}; height: auto; }}
    Dialog > .body {{ height: auto; max-height: 45vh; margin: 1 0 0 1; }}
    Dialog > .question {{ height: auto; margin-top: 1; }}
    Dialog > .choices {{ height: auto; }}
    Dialog > Input {{ margin: 0 0 0 3; border: none; height: 1; padding: 0; background: transparent; }}
    Dialog > .hint {{ height: auto; color: {theme.DIM}; margin-top: 1; }}
    Picker > Input {{ margin: 1 0; padding: 0 1; height: 1; background: {theme.USER_BG}; }}
    """

    def __init__(self, title, body=None, question=None, choices=(), text_choice=None,
                 hint="Enter to confirm · Esc to cancel", multi=False, colour=None):
        super().__init__()
        self.title_text = title
        self.body_widget = body
        self.question = question
        self.choices = list(choices)
        self.text_choice = text_choice  # index of the "type something" choice, if any
        self.multi = multi
        self.picked = set()
        self.index = 0
        self.hint = hint
        self.colour = colour
        self.result = asyncio.get_running_loop().create_future()
        self.choice_view = Static(classes="choices")
        self.entry = Input(placeholder="Type your answer, then Enter")
        self.entry.display = False

    def compose(self):
        yield Static(Text(self.title_text), classes="title")
        if self.body_widget is not None:
            self.body_widget.add_class("body")
            yield self.body_widget
        if self.question:
            yield Static(Text(self.question), classes="question")
        yield self.choice_view
        yield self.entry
        yield Static(Text(self.hint), classes="hint")

    def on_mount(self):
        if self.colour:
            self.styles.border = ("round", self.colour)
        self.redraw()
        self.take_focus()

    def take_focus(self):
        self.focus()

    def redraw(self):
        rows = []
        for n, label in enumerate(self.choices):
            chosen = n == self.index
            pointer = "❯ " if chosen else "  "
            if self.multi and n < len(self.choices) - 1 and n != self.text_choice:
                box = "[✔] " if n in self.picked else "[ ] "
            else:
                box = ""
            style = f"bold {theme.ACCENT}" if chosen else ""
            if isinstance(label, tuple):
                label, description = label
            else:
                description = ""
            row = Text.assemble((pointer, theme.ACCENT), (f"{n + 1}. {box}{label}", style))
            if description:
                row.append(f"  {' '.join(description.split())}", style=theme.DIM)
            rows.append(row)
        self.choice_view.update(Group(*rows))

    def finish(self, value):
        if not self.result.done():
            self.result.set_result(value)

    def choose(self, n):
        if not 0 <= n < len(self.choices):
            return
        self.index = n
        self.redraw()
        if n == self.text_choice:
            self.entry.display = True
            self.entry.focus()
            return
        if self.multi and n < len(self.choices) - 1:
            self.picked ^= {n}
            self.redraw()
            return
        self.finish(n)

    def on_key(self, event: events.Key):
        if self.entry.has_focus:
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                self.entry.display = False
                self.focus()
            return
        if self.dialog_key(event.key):
            event.stop()
            event.prevent_default()
            self.redraw()

    def dialog_key(self, key):
        """True if the key was the dialog's."""
        if key in ("up", "k", "shift+tab"):
            self.index = (self.index - 1) % len(self.choices)
        elif key in ("down", "j", "tab"):
            self.index = (self.index + 1) % len(self.choices)
        elif key == "enter" or (key == "space" and self.multi):
            self.choose(self.index)
        elif key == "escape":
            self.finish(None)
        elif key.isdigit() and key != "0":
            self.choose(int(key) - 1)
        else:
            return False
        return True

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.submitted(event.value)

    def submitted(self, value):
        if value.strip():
            self.finish(("text", value.strip()))


class Picker(Dialog):
    """A dialog listing many things (sessions, models) with a filter line on top."""

    def __init__(self, title, items, hint="Type to filter · Enter to pick · Esc to cancel"):
        self.all_items = items  # (value, label, description)
        self.filter = Input(placeholder="Type to search…", compact=True)
        super().__init__(title, choices=[(label, d) for _, label, d in items[:9]], hint=hint)
        self.shown = items[:9]
        self.applied = ""

    def compose(self):
        yield Static(Text(self.title_text), classes="title")
        yield self.filter
        yield self.choice_view
        yield Static(Text(self.hint), classes="hint")

    def take_focus(self):
        self.filter.focus()

    def apply_filter(self):
        """Match the list to the filter as typed now (its change can arrive after Enter)."""
        q = self.filter.value.lower().strip()
        if q == self.applied:
            return
        self.applied = q
        matches = [item for item in self.all_items
                   if not q or q in item[1].lower() or q in (item[2] or "").lower()]
        self.shown = matches[:9]
        self.choices = [(label, d) for _, label, d in self.shown] or [("(nothing matches)", "")]
        self.index = 0
        self.redraw()

    def on_input_changed(self, event: Input.Changed):
        self.apply_filter()

    def pick(self):
        self.apply_filter()
        if self.shown:
            self.finish(self.shown[self.index][0])

    def dialog_key(self, key):
        if key == "up":
            self.index = (self.index - 1) % len(self.choices)
        elif key == "down":
            self.index = (self.index + 1) % len(self.choices)
        elif key == "enter":
            self.pick()
        elif key == "escape":
            self.finish(None)
        else:
            return False
        return True

    def submitted(self, value):
        self.pick()


class Transcript(VerticalScroll):
    DEFAULT_CSS = "Transcript { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }"

    def entries(self):
        return [w for w in self.children if isinstance(w, (Entry, DoneEntry))]


__all__ = ["Banner", "Entry", "UserEntry", "AssistantEntry", "ThinkingEntry", "ToolEntry",
           "NoticeEntry", "DoneEntry", "Spinner", "TaskPanel", "QueuedPanel", "PromptBar",
           "PromptInput", "SuggestionMenu", "Footer", "Dialog", "Picker", "Transcript",
           "notice", "shortcuts", "elapsed"]
