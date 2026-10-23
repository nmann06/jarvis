"""Claude Code's interactive menus, drawn in Jarvis blue where the prompt was: the Settings
panel (Status, Config, Usage and Stats tabs, opened by /status, /config, /usage and /stats)
and the MCP menu (/mcp).

The data comes from the window (JarvisApp's panel_* methods, which ask Claude Code), so
these only draw and take keys. Both resolve self.result when they close.
"""

import asyncio
import datetime as dt
import os
import time
from urllib.parse import urlparse

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from . import stats as stats_mod
from . import theme
from .config_items import TERMINAL_ONLY

TRACK = "#1d2f44"      # the unfilled part of a bar
EIGHTHS = " ▏▎▍▌▋▊▉"
LEVELS = ["·", "░", "▒", "▓", "█"]
SERIES = [theme.ACCENT, "#4fd1e8", "#7aa2f7", "#96e6ff", "#0f69d7"]


def bar(percent, width=50, colour=theme.ACCENT):
    cells = max(0.0, min(100.0, percent)) / 100 * width
    full = int(cells)
    text = Text()
    text.append("█" * full, style=f"{colour} on {TRACK}")
    if full < width:
        partial = EIGHTHS[int((cells - full) * 8)]
        text.append(partial, style=f"{colour} on {TRACK}")
        text.append(" " * (width - full - 1), style=f"on {TRACK}")
    return text


def zone_name():
    """The IANA name of the local time zone (America/New_York), as Claude Code shows it."""
    try:
        path = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in path:
            return path.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return time.tzname[0]


def resets(iso):
    """Resets 4:10pm, or Resets Oct 29, 3am, in local time."""
    try:
        when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return ""
    clock = when.strftime("%-I:%M%p").lower().replace(":00", "")
    if when.date() != dt.date.today():
        clock = when.strftime("%b %-d, ") + clock
    return f"Resets {clock} ({zone_name()})"


def section(title):
    return Text(title, style="bold")


def dim(text):
    return Text(text, style=theme.DIM)


class TopRule(Static):
    DEFAULT_CSS = f"TopRule {{ height: 1; color: {theme.ACCENT}; }}"

    def render(self):
        return "▔" * max(1, self.size.width)


class Panel(Vertical):
    """What the two menus share: the frame, the hint line and closing."""

    can_focus = True

    DEFAULT_CSS = f"""
    Panel {{ height: auto; max-height: 92vh; margin-top: 1; }}
    Panel > .head {{ height: auto; margin: 0 0 1 2; }}
    Panel > VerticalScroll {{ height: auto; max-height: 72vh; padding: 0 1 0 3;
                             scrollbar-size-vertical: 1; }}
    Panel .content {{ height: auto; }}
    Panel > .hint {{ height: auto; color: {theme.DIM}; margin: 1 0 0 3; }}
    """

    def __init__(self):
        super().__init__()
        self.result = asyncio.get_running_loop().create_future()
        self.head = Static(classes="head")
        self.content = Static(classes="content")
        self.scroller = VerticalScroll(self.content)
        self.hint = Static(classes="hint")
        self.message = ""

    def compose(self):
        yield TopRule()
        yield self.head
        yield self.scroller
        yield self.hint

    def on_mount(self):
        self.focus()
        self.redraw()

    def finish(self, value=None):
        if not self.result.done():
            self.result.set_result(value)

    def on_key(self, event: events.Key):
        if self.press(event.key, event.character):
            event.stop()
            event.prevent_default()
            self.redraw()

    def press(self, key, char):
        return False

    def redraw(self):
        pass

    def say(self, text):
        self.message = text
        self.redraw()

    def scroll_by(self, rows):
        self.scroller.scroll_relative(y=rows, animate=False)

    def window(self, rows, selected, height):
        """The rows around the selected one that fit, with '↑ n more' / '↓ n more' marks."""
        if len(rows) <= height:
            return rows
        start = max(0, min(selected - height // 2, len(rows) - height))
        shown = rows[start:start + height]
        if start:
            shown = [dim(f"↑ {start} more")] + shown[1:]
        if start + height < len(rows):
            shown = shown[:-1] + [dim(f"↓ {len(rows) - start - height + 1} more")]
        return shown

    def list_height(self, reserve=14):
        return max(6, (self.app.size.height if self.app else 40) - reserve)


class SettingsPanel(Panel):
    """Claude Code's Settings panel: Status, Config, Usage and Stats."""

    TABS = [("status", "Status"), ("config", "Config"), ("usage", "Usage"), ("stats", "Stats")]

    def __init__(self, tab="usage"):
        super().__init__()
        self.tab = tab
        self.in_tabs = tab not in ("config",)  # the keys go to the tab row or to the tab
        self.data = {}
        self.errors = {}
        self.fetching = set()
        # Config
        self.search = ""
        self.searching = True
        self.selected = 0
        self.choosing = None      # the item whose options are open
        self.choice = 0
        self.typing = None        # the item a value is being typed for
        self.typed = ""
        # Usage
        self.period = "day"
        # Stats
        self.stats_view = "overview"
        self.stats_range = "all"

    def on_mount(self):
        super().on_mount()
        self.load(self.tab)

    def load(self, tab, again=False):
        if (tab in self.data and not again) or tab in self.fetching:
            return
        self.fetching.add(tab)
        self.run_worker(self.fetch(tab), group=f"panel-{tab}")

    async def fetch(self, tab):
        try:
            self.data[tab] = await getattr(self.app, f"panel_{tab}")()
            self.errors.pop(tab, None)
        except Exception as e:
            self.errors[tab] = str(e) or type(e).__name__
        finally:
            self.fetching.discard(tab)
        self.redraw()

    # Keys -------------------------------------------------------------------------------------

    def switch(self, step):
        keys = [k for k, _ in self.TABS]
        self.tab = keys[(keys.index(self.tab) + step) % len(keys)]
        self.in_tabs = self.tab != "config"
        self.choosing = self.typing = None
        self.message = ""
        self.scroller.scroll_home(animate=False)
        self.load(self.tab)

    def press(self, key, char):
        if key == "escape":
            return self.escape()
        if self.tab == "config" and not self.in_tabs:
            return self.config_key(key, char)
        if key in ("left", "shift+tab"):
            if self.tab == "stats" and not self.in_tabs:
                self.stats_view = "overview"
            else:
                self.switch(-1)
            return True
        if key in ("right", "tab"):
            if self.tab == "stats" and not self.in_tabs and key == "right":
                self.stats_view = "models"
            else:
                self.switch(1)
            return True
        if key == "down":
            if self.in_tabs and self.tab in ("stats", "config"):
                self.in_tabs = False
            else:
                self.scroll_by(3)
            return True
        if key == "up":
            if not self.in_tabs and self.scroller.scroll_y <= 0:
                self.in_tabs = True
            else:
                self.scroll_by(-3)
            return True
        if key in ("pagedown", "pageup"):
            self.scroll_by(10 if key == "pagedown" else -10)
            return True
        if self.tab == "usage":
            return self.usage_key(key)
        if self.tab == "stats":
            return self.stats_key(key)
        return False

    def escape(self):
        if self.tab == "config":
            if self.choosing or self.typing:
                self.choosing = self.typing = None
                return True
            if self.search:
                self.search = ""
                self.selected = 0
                return True
        self.finish()
        return True

    # Drawing ----------------------------------------------------------------------------------

    def redraw(self):
        tabs = Text.assemble(("Settings", f"bold {theme.ACCENT}"))
        tabs.append("  ")
        for key, label in self.TABS:
            if key == self.tab:
                tabs.append(f" {label} ", style=f"bold black on {theme.ACCENT}" if self.in_tabs or key != "config"
                            else f"bold {theme.ACCENT} on {TRACK}")
            else:
                tabs.append(f" {label} ")
            tabs.append(" ")
        self.head.update(tabs)
        if self.tab in self.errors:
            body = Text(f"Couldn't get this from Claude Code: {self.errors[self.tab]}", style=theme.ERROR)
        elif self.tab not in self.data:
            body = dim("Loading…")
        else:
            body = getattr(self, f"draw_{self.tab}")(self.data[self.tab])
        self.content.update(body)
        self.hint.update(Text(self.hint_text()))

    def hint_text(self):
        tabs = "←/→ to switch tabs"
        if self.message:
            prefix = self.message + " · "
        else:
            prefix = ""
        if self.tab == "config" and not self.in_tabs:
            if self.choosing:
                return prefix + "↑/↓ to choose · Enter to set · Esc to go back"
            if self.typing:
                return prefix + "Type a value · Enter to set · Esc to go back"
            if self.searching:
                return prefix + "Type to filter · Enter/↓ to select · ↑ to tabs · Esc to clear"
            return prefix + "Enter/Space to change · / to search · ↑ at the top for tabs · Esc to close"
        if self.tab == "usage":
            data = self.data.get("usage") or {}
            fix = ((data.get("behaviors") or {}).get(self.period) or {}).get("fix") or {}
            recs = ((data.get("behaviors") or {}).get(self.period) or {}).get("recommendations") or []
            parts = []
            if fix.get("messages"):
                parts.append(f"f to fix {len(fix.get('keys') or [1])} of {len(recs)}")
            parts += ["d to day", "w to week", tabs, "Esc to close"]
            return prefix + " · ".join(parts)
        if self.tab == "stats":
            if self.in_tabs:
                return prefix + f"{tabs} · ↓ for stats · Esc to close"
            return prefix + "←/→ Overview/Models · r to cycle dates · ctrl+s to copy · ↑ to tabs · Esc to close"
        if self.tab == "config":
            return prefix + f"{tabs} · ↓ to settings · Esc to close"
        return prefix + f"{tabs} · Esc to close"

    # Status -----------------------------------------------------------------------------------

    def draw_status(self, rows):
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True)
        grid.add_column()
        for label, value in rows:
            grid.add_row(Text(f"{label}:", style=theme.LABEL), Text(str(value)))
        return grid

    # Config -----------------------------------------------------------------------------------

    def config_rows(self):
        items = self.data.get("config") or []
        q = self.search.lower()
        return [i for i in items if not q or q in i.label.lower() or q in i.id.lower()]

    def config_key(self, key, char):
        items = self.config_rows()
        if self.choosing:
            options = self.choosing.options
            if key == "up":
                self.choice = (self.choice - 1) % len(options)
            elif key == "down":
                self.choice = (self.choice + 1) % len(options)
            elif key == "enter":
                item, value = self.choosing, options[self.choice]
                self.choosing = None
                self.commit(item, value)
            else:
                return key in ("left", "right")
            return True
        if self.typing:
            if key == "enter":
                item, value = self.typing, self.typed.strip() or "default"
                self.typing = None
                self.commit(item, value)
            elif key == "backspace":
                self.typed = self.typed[:-1]
            elif char and char.isprintable():
                self.typed += char
            return True
        if self.searching:
            if key in ("down", "enter"):
                self.searching = False
                self.selected = 0
            elif key == "up":
                self.in_tabs = True
            elif key in ("left", "right", "tab", "shift+tab") and not self.search:
                self.switch(-1 if key in ("left", "shift+tab") else 1)
            elif key == "backspace":
                self.search = self.search[:-1]
                self.selected = 0
            elif char and char.isprintable() and key != "space":
                self.search += char
                self.selected = 0
            elif key == "space" and self.search:
                self.search += " "
            else:
                return False
            return True
        if key == "up":
            if self.selected == 0:
                self.searching = True
            else:
                self.selected -= 1
        elif key == "down":
            self.selected = min(self.selected + 1, max(0, len(items) - 1))
        elif key in ("enter", "space") and items:
            self.change(items[self.selected])
        elif key == "/":
            self.searching = True
        elif key in ("left", "right", "tab", "shift+tab"):
            self.switch(-1 if key in ("left", "shift+tab") else 1)
        elif char and char.isprintable():
            self.searching = True
            self.search += char
            self.selected = 0
        else:
            return False
        return True

    def change(self, item):
        if item.kind == "bool":
            self.commit(item, "false" if item.value else "true")
        elif item.kind == "enum" and len(item.options) <= 3:
            current = item.as_option()
            options = item.options
            index = options.index(current) if current in options else -1
            self.commit(item, options[(index + 1) % len(options)])
        elif item.kind == "enum":
            self.choosing = item
            current = item.as_option()
            self.choice = item.options.index(current) if current in item.options else 0
        else:
            self.typing = item
            self.typed = "" if item.value is None else str(item.value)

    def commit(self, item, value):
        self.say(f"Setting {item.label}…")
        self.run_worker(self.save(item, value), group="panel-save")

    async def save(self, item, value):
        try:
            said = await self.app.panel_set_config(item.id, value)
        except Exception as e:
            said = f"Couldn't change {item.label}: {e}"
        note = ""
        if item.id in TERMINAL_ONLY:
            note = " (for Claude Code's own screen)"
        self.say((said or f"Set {item.label} to {value}") + note)
        self.load("config", again=True)

    def draw_config(self, items):
        rows = []
        if self.choosing:
            item = self.choosing
            rows.append(Text(item.label, style=f"bold {theme.ACCENT}"))
            for n, option in enumerate(item.options):
                chosen = n == self.choice
                current = option == item.as_option()
                row = Text.assemble(("❯ " if chosen else "  ", theme.ACCENT))
                row.append(option, style=f"bold {theme.ACCENT}" if chosen else "")
                if current:
                    row.append("  ✔", style=theme.SUCCESS)
                rows.append(row)
            return Group(self.search_box(), Text(""), *rows)
        if self.typing:
            row = Text(f"{self.typing.label}: ", style=f"bold {theme.ACCENT}")
            row.append(self.typed)
            row.append("▏", style=theme.ACCENT)
            return Group(self.search_box(), Text(""), row,
                         dim("Any language name or ISO code (e.g. 'ja'); 'default' for English."))
        shown = self.config_rows()
        if not shown:
            return Group(self.search_box(), Text(""), dim("No settings match."))
        width = max(len(i.label) for i in shown) + 4
        lines = []
        for n, item in enumerate(shown):
            chosen = n == self.selected and not self.searching and not self.in_tabs
            line = Text.assemble(("❯ " if chosen else "  ", theme.ACCENT))
            line.append(item.label.ljust(width), style=f"bold {theme.ACCENT}" if chosen else "")
            value = item.shown()
            line.append(value, style=theme.ACCENT if chosen else theme.LABEL)
            if item.kind == "enum" and len(item.options) > 3 or item.kind == "text":
                line.append(" ›", style=theme.DIM)
            lines.append(line)
        return Group(self.search_box(), *self.window(lines, self.selected, self.list_height(16)))

    def search_box(self):
        active = self.searching and not self.in_tabs and not self.choosing and not self.typing
        colour = theme.ACCENT if active else theme.LABEL
        text = Text.assemble(("⌕ ", colour))
        if self.search:
            text.append(self.search)
        else:
            text.append("Search settings…", style=theme.DIM)
        if active:
            text.append("▏", style=theme.ACCENT)
        width = max(30, (self.app.size.width if self.app else 100) - 10)
        top = Text("╭" + "─" * (width - 2) + "╮", style=colour)
        middle = Text.assemble(("│ ", colour))
        middle.append_text(text)
        middle.append(" " * max(0, width - 3 - text.cell_len))
        middle.append("│", style=colour)
        bottom = Text("╰" + "─" * (width - 2) + "╯", style=colour)
        return Group(top, middle, bottom)

    # Usage ------------------------------------------------------------------------------------

    LIMIT_NAMES = {"session": "Current session", "weekly_all": "Current week (all models)",
                   "weekly_opus": "Current week (Opus)", "weekly_sonnet": "Current week (Sonnet)",
                   "weekly_fable": "Current week (Fable)"}

    def usage_key(self, key):
        if key in ("d", "w"):
            self.period = "day" if key == "d" else "week"
            return True
        if key == "f":
            data = self.data.get("usage") or {}
            fix = ((data.get("behaviors") or {}).get(self.period) or {}).get("fix") or {}
            if fix.get("messages"):
                self.app.fill_prompt(fix["messages"][0])
                self.finish()
            return True
        return False

    def draw_usage(self, data):
        rows = []
        cost = data.get("cost_text")
        if cost:
            rows += [section("Session"), Text(""), Text(cost), Text("")]
        limits = (data.get("rate_limits") or {}).get("limits") or []
        if not data.get("rate_limits_available", True) or not limits:
            if data.get("subscription_type"):
                rows += [dim("Plan usage isn't available right now."), Text("")]
        width = max(20, min(50, (self.app.size.width if self.app else 100) - 30))
        for limit in limits:
            name = self.LIMIT_NAMES.get(limit.get("kind"), (limit.get("kind") or "").replace("_", " ").capitalize())
            percent = limit.get("percent") or 0
            colour = theme.ERROR if percent >= 95 else theme.WARNING if percent >= 85 else theme.ACCENT
            line = bar(percent, width, colour)
            line.append(f"  {percent}% used")
            rows += [section(name), line, dim(resets(limit.get("resets_at") or ""))]
            if limit.get("kind") == "weekly_all":
                shares = ((data.get("rate_limits") or {}).get("seven_day_breakdown") or {}).get("rows") or []
                shares = [f"{r['display_name']} {r['percent']}%" for r in shares if r.get("percent")]
                if len(shares) > 1:
                    rows.append(dim("This week: " + " · ".join(shares)))
            rows.append(Text(""))
        behaviors = (data.get("behaviors") or {}).get(self.period)
        if behaviors:
            rows += [section("What's contributing to your limits usage?"),
                     dim("Approximate · sessions on this machine only"), Text("")]
            label = "Last 24h" if self.period == "day" else "Last 7d"
            other = "w to week" if self.period == "day" else "d to day"
            head = Text(f"{label} · {behaviors.get('request_count', 0)} requests · "
                        f"{behaviors.get('session_count', 0)} sessions · shares overlap", style=theme.LABEL)
            head.append(f"   ({other})", style=theme.DIM)
            rows += [head, Text("")]
            for rec in behaviors.get("recommendations") or []:
                line = Text.assemble((f"{rec.get('pct', 0):>3}%  ", f"bold {theme.ACCENT}"))
                line.append(rec.get("title", ""))
                if rec.get("detail"):
                    line.append(f" · {rec['detail']}", style=theme.DIM)
                rows.append(line)
            for heading, key in (("Skills", "skills"), ("Agents", "agents"), ("Plugins", "plugins"),
                                 ("MCP servers", "mcp_servers")):
                entries = behaviors.get(key) or []
                if not entries:
                    continue
                grid = Table.grid(padding=(0, 2))
                grid.add_column(min_width=28)
                grid.add_column(justify="right")
                grid.add_row(Text(heading, style="bold"), Text("% of usage", style=theme.DIM))
                for entry in entries:
                    name = entry.get("name", "")
                    if key == "skills" and not name.startswith("/"):
                        name = "/" + name
                    grid.add_row(Text(name), Text(f"{entry.get('pct', 0)}%"))
                rows += [Text(""), grid]
            rows.append(Text(""))
        spend = (data.get("rate_limits") or {}).get("spend") or {}
        if spend:
            rows.append(section("Usage credits"))
            if spend.get("enabled"):
                used = (spend.get("used") or {}).get("amount_minor", 0) / 10 ** (spend.get("used") or {}).get("exponent", 2)
                rows.append(Text(f"${used:.2f} used" + (f" of {spend.get('limit')}" if spend.get("limit") else "")))
            else:
                rows.append(dim("Usage credits are off · /usage-credits to turn them on"))
        return Group(*rows) if rows else dim("No usage to show yet.")

    # Stats ------------------------------------------------------------------------------------

    def stats_key(self, key):
        if key == "r":
            keys = [k for k, _, _ in stats_mod.RANGES]
            self.stats_range = keys[(keys.index(self.stats_range) + 1) % len(keys)]
            return True
        if key == "ctrl+s":
            data = self.data.get("stats")
            if data is not None:
                self.app.copy_to_clipboard(self.stats_text(data))
                self.message = "Copied the stats"
            return True
        return False

    def range_line(self):
        line = Text()
        for n, (key, label, _) in enumerate(stats_mod.RANGES):
            if n:
                line.append(" · ", style=theme.DIM)
            line.append(label, style=f"bold {theme.ACCENT}" if key == self.stats_range else theme.DIM)
        return line

    def model_name(self, model):
        return self.app.pretty_model(model) if self.app else model

    def draw_stats(self, data):
        views = Text()
        for key, label in (("overview", "Overview"), ("models", "Models")):
            views.append(f" {label} ", style=f"bold black on {theme.ACCENT}" if key == self.stats_view
                         else theme.LABEL)
            views.append("  ")
        body = self.stats_overview(data) if self.stats_view == "overview" else self.stats_models(data)
        return Group(views, Text(""), *body)

    def stats_overview(self, data):
        s = stats_mod.summary(data, self.stats_range)
        grid, months = stats_mod.heatmap_levels(data, weeks=min(52, max(10, ((self.app.size.width if self.app else 100) - 20))))
        rows = []
        head = Text("      ")
        column = 0
        for week, month in enumerate(months):
            if month and week >= column:
                head.append(" " * (week - column) + month)
                column = week + len(month)
        rows.append(Text(head.plain, style=theme.DIM))
        shades = [theme.DIM, "#1f6fae", "#2a92d6", theme.ACCENT, "#a8e4ff"]
        for weekday in range(7):
            label = {1: "Mon", 3: "Wed", 5: "Fri"}.get(weekday, "")
            line = Text(f"{label:<4}  ", style=theme.DIM)
            for level in grid[weekday]:
                if level is None:
                    line.append(" ")
                else:
                    line.append(LEVELS[level], style=shades[level])
            rows.append(line)
        legend = Text("      Less ", style=theme.DIM)
        for level in range(1, 5):
            legend.append(LEVELS[level] + " ", style=shades[level])
        legend.append("More", style=theme.DIM)
        rows += [legend, Text(""), self.range_line(), Text("")]
        pairs = [
            ("Favorite model", self.model_name(s["favorite"]) if s["favorite"] else "—",
             "Total tokens", stats_mod.human_tokens(s["tokens"])),
            ("Sessions", str(s["sessions"]), "Longest session", stats_mod.human_duration(s["longest_ms"])),
            ("Active days", f"{s['active_days']}/{s['total_days']}", "Longest streak", plural_days(s["longest_streak"])),
            ("Most active day", s["busiest"].strftime("%b %-d") if s["busiest"] else "—",
             "Current streak", plural_days(s["current_streak"])),
        ]
        table = Table.grid(padding=(0, 4))
        table.add_column()
        table.add_column()
        for left_label, left, right_label, right in pairs:
            table.add_row(Text.assemble((f"{left_label}: ", theme.LABEL), (left, f"bold {theme.ACCENT}")),
                          Text.assemble((f"{right_label}: ", theme.LABEL), (right, f"bold {theme.ACCENT}")))
        rows.append(table)
        if self.stats_range == "all":
            totals = {k: sum(m[k] for m in data.models.values()) for k in ("input", "output", "cache_read", "cache_write")}
            rows += [Text(""), dim(f"Input {stats_mod.human_tokens(totals['input'])} · Output "
                                   f"{stats_mod.human_tokens(totals['output'])} · Cache read "
                                   f"{stats_mod.human_tokens(totals['cache_read'])} · Cache write "
                                   f"{stats_mod.human_tokens(totals['cache_write'])}")]
            fact = stats_mod.fun_fact(data)
            if fact:
                rows.append(Text(fact, style=f"italic {theme.LABEL}"))
        return rows

    def stats_models(self, data):
        span = dict((k, n) for k, _, n in stats_mod.RANGES).get(self.stats_range)
        since = data.today - dt.timedelta(days=(span or 10_000) - 1)
        dates = sorted(k for k in data.days if dt.date.fromisoformat(k) >= since)
        models = {}
        for date in dates:
            for model, n in data.days[date].tokens.items():
                models[model] = models.get(model, 0) + n
        order = sorted(models, key=models.get, reverse=True)[:len(SERIES)]
        rows = [section("Tokens per Day")]
        if dates and order:
            series = [[data.days[d].tokens.get(m, 0) for d in dates] for m in order]
            width = max(20, min(60, (self.app.size.width if self.app else 100) - 30))
            rows += chart(series, dates, width, height=8)
            legend = Text()
            for n, model in enumerate(order):
                if n:
                    legend.append(" · ", style=theme.DIM)
                legend.append("● ", style=SERIES[n])
                legend.append(self.model_name(model))
            rows.append(legend)
        else:
            rows.append(dim("No tokens in this range."))
        rows += [Text(""), self.range_line(), Text("")]
        total = sum(models.values()) or 1
        grid = Table.grid(padding=(0, 6))
        grid.add_column()
        grid.add_column()
        cells = []
        for n, model in enumerate(order):
            usage = data.models.get(model, {})
            cell = [Text.assemble(("● ", SERIES[n]), (self.model_name(model), "bold"),
                                  (f" ({models[model] / total * 100:.1f}%)", theme.DIM))]
            if self.stats_range == "all" and usage:
                cell.append(dim(f"  In: {stats_mod.human_tokens(usage['input'])} · Out: "
                                f"{stats_mod.human_tokens(usage['output'])}"))
                cell.append(dim(f"  Cache: {stats_mod.human_tokens(usage['cache_read'])} read · "
                                f"{stats_mod.human_tokens(usage['cache_write'])} write"))
            else:
                cell.append(dim(f"  {stats_mod.human_tokens(models[model])} tokens"))
            cells.append(Group(*cell))
        for i in range(0, len(cells), 2):
            grid.add_row(cells[i], cells[i + 1] if i + 1 < len(cells) else Text(""))
        rows.append(grid)
        return rows

    def stats_text(self, data):
        s = stats_mod.summary(data, self.stats_range)
        label = dict((k, l) for k, l, _ in stats_mod.RANGES)[self.stats_range]
        return "\n".join([
            f"Claude Code stats ({label})",
            f"Favorite model: {self.model_name(s['favorite']) if s['favorite'] else '-'}",
            f"Total tokens: {stats_mod.human_tokens(s['tokens'])}",
            f"Sessions: {s['sessions']}",
            f"Longest session: {stats_mod.human_duration(s['longest_ms'])}",
            f"Active days: {s['active_days']}/{s['total_days']}",
            f"Longest streak: {plural_days(s['longest_streak'])}",
            f"Current streak: {plural_days(s['current_streak'])}",
        ])


def plural_days(n):
    return f"{n} day{'s' if n != 1 else ''}"


def chart(series, dates, width, height=8):
    """A line chart of one or more series over the same days (box-drawing lines, one colour
    a series), with a value axis on the left and dates under it."""
    peak = max(max(s) for s in series) or 1
    columns = width
    # Spread the days over the columns; a column shows the day it lands on.
    def at(values, column):
        if len(values) == 1:
            return values[0]
        position = column * (len(values) - 1) / max(1, columns - 1)
        return values[round(position)]
    grid = [[(" ", "")] * columns for _ in range(height + 1)]
    for index, values in enumerate(reversed(series)):
        colour = SERIES[len(series) - 1 - index]
        levels = [round(at(values, c) / peak * height) for c in range(columns)]
        for c in range(columns):
            here = levels[c]
            before = levels[c - 1] if c else here
            if here == before:
                grid[height - here][c] = ("─", colour)
            else:
                low, high = sorted((before, here))
                for level in range(low + 1, high):
                    grid[height - level][c] = ("│", colour)
                grid[height - here][c] = ("╭" if here > before else "╰", colour)
                grid[height - before][c] = ("╯" if here > before else "╮", colour)
    rows = []
    for r in range(height + 1):
        value = peak * (height - r) / height
        label = stats_mod.human_tokens(value).rjust(7) if r % 2 == 0 or r == height else " " * 7
        line = Text(label + (" ┼" if r in (0, height) else " ┤"), style=theme.DIM)
        for char, colour in grid[r]:
            line.append(char, style=colour)
        rows.append(line)
    first, last = dates[0], dates[-1]
    axis = Text(" " * 9, style=theme.DIM)
    start = dt.date.fromisoformat(first).strftime("%b %-d")
    end = dt.date.fromisoformat(last).strftime("%b %-d")
    axis.append(start + " " * max(1, columns - len(start) - len(end)) + (end if last != first else ""), style=theme.DIM)
    rows.append(axis)
    return rows


class McpPanel(Panel):
    """Claude Code's /mcp menu: the servers, one server's details and actions, its tools."""

    MARKS = {"connected": ("✔", theme.SUCCESS), "failed": ("✗", theme.ERROR),
             "needs-auth": ("△", theme.WARNING), "pending": ("◯", theme.DIM),
             "connecting": ("◯", theme.DIM), "disabled": ("⊘", theme.DIM)}
    PLACES = {"claudeai": "claude.ai", "user": "User MCPs (~/.claude.json)",
              "project": "Project MCPs (.mcp.json)", "local": "Local MCPs", "plugin": "Plugin MCPs",
              "dynamic": "Added by flags", "enterprise": "Managed MCPs"}

    def __init__(self):
        super().__init__()
        self.servers = None
        self.error = None
        self.view = "list"
        self.selected = 0
        self.server = None
        self.action = 0
        self.tries = 0

    def on_mount(self):
        super().on_mount()
        self.refresh_servers()

    def refresh_servers(self):
        self.run_worker(self.fetch(), group="panel-mcp")

    async def fetch(self):
        try:
            self.servers = await self.app.panel_mcp()
            self.error = None
        except Exception as e:
            self.error = str(e)
        if self.server is not None and self.servers:
            self.server = next((s for s in self.servers if s.get("name") == self.server.get("name")), self.server)
        # Claude Code connects its servers a few seconds after it starts: look again for a while.
        if not self.servers and not self.error and self.tries < 15:
            self.tries += 1
            self.set_timer(1.5, self.refresh_servers)
        self.redraw()

    def actions(self):
        server = self.server or {}
        status = server.get("status")
        items = []
        if server.get("tools"):
            items.append(("tools", "View tools"))
        if status == "disabled":
            items.append(("enable", "Enable"))
        else:
            items.append(("reconnect", "Reconnect"))
            items.append(("disable", "Disable"))
        if status == "needs-auth":
            items.insert(0, ("auth", "Authenticate"))
        elif (server.get("config") or {}).get("type") in ("http", "sse", "claudeai-proxy"):
            items.append(("clear-auth", "Clear authentication"))
        return items

    def press(self, key, char):
        if key == "escape":
            if self.view == "tools":
                self.view = "server"
            elif self.view == "server":
                self.view, self.message = "list", ""
            else:
                self.finish()
            return True
        if self.view == "list":
            count = len(self.servers or [])
            if key == "up" and count:
                self.selected = (self.selected - 1) % count
            elif key == "down" and count:
                self.selected = (self.selected + 1) % count
            elif key == "enter" and count:
                self.server = self.servers[self.selected]
                self.view, self.action, self.message = "server", 0, ""
            else:
                return False
            return True
        if self.view == "server":
            actions = self.actions()
            if key == "up":
                self.action = (self.action - 1) % len(actions)
            elif key == "down":
                self.action = (self.action + 1) % len(actions)
            elif key == "enter" or (char and char.isdigit() and 0 < int(char) <= len(actions)):
                if key != "enter":
                    self.action = int(char) - 1
                self.act(actions[self.action][0])
            else:
                return False
            return True
        if self.view == "tools":
            if key in ("up", "down", "pageup", "pagedown"):
                self.scroll_by({"up": -1, "down": 1, "pageup": -10, "pagedown": 10}[key])
                return True
        return False

    def act(self, action):
        server = self.server or {}
        name = server.get("name", "")
        if action == "tools":
            self.view = "tools"
            self.scroller.scroll_home(animate=False)
            return
        if action == "auth":
            if server.get("source") == "claudeai" or server.get("scope") == "claudeai":
                self.message = "Connect it on claude.ai: Settings → Connectors"
            else:
                self.message = "Sign in from regular Claude Code: /claude, then /mcp"
            return
        self.message = {"reconnect": f"Reconnecting {name}…", "disable": f"Disabling {name}…",
                        "enable": f"Enabling {name}…", "clear-auth": f"Clearing {name}'s sign-in…"}[action]
        self.run_worker(self.perform(name, action), group="panel-mcp-action")

    async def perform(self, name, action):
        try:
            said = await self.app.panel_mcp_action(name, action)
        except Exception as e:
            said = f"Couldn't {action.replace('-', ' ')} {name}: {e}"
        self.say(said)
        await self.fetch()

    def redraw(self):
        if self.view == "list":
            self.head.update(Text("Manage MCP servers", style=f"bold {theme.ACCENT}"))
            self.content.update(self.draw_list())
            hint = "↑/↓ to navigate · Enter to confirm · Esc to cancel"
        elif self.view == "server":
            name = (self.server or {}).get("name", "")
            self.head.update(Text(f"{name[:1].upper()}{name[1:]} MCP Server", style=f"bold {theme.ACCENT}"))
            self.content.update(self.draw_server())
            hint = "↑/↓ to navigate · Enter to select · Esc to go back"
        else:
            name = (self.server or {}).get("name", "")
            self.head.update(Text(f"Tools for {name}", style=f"bold {theme.ACCENT}"))
            self.content.update(self.draw_tools())
            hint = "↑/↓ to scroll · Esc to go back"
        self.hint.update(Text((self.message + " · " if self.message else "") + hint))

    def mark(self, status):
        return self.MARKS.get(status, ("?", theme.DIM))

    def draw_list(self):
        if self.error:
            return Text(f"Couldn't get the servers from Claude Code: {self.error}", style=theme.ERROR)
        if self.servers is None:
            return dim("Loading…")
        if not self.servers:
            if self.tries < 15:
                return dim("Connecting to MCP servers…")
            return dim("No MCP servers configured. They're added with `claude mcp add`, or on claude.ai "
                       "as connectors.")
        rows = [dim(f"{len(self.servers)} server{'s' if len(self.servers) != 1 else ''}"), Text("")]
        width = max(len(s.get("name", "")) for s in self.servers) + 3
        place = None
        for n, server in enumerate(self.servers):
            where = self.PLACES.get(server.get("scope") or server.get("source"), server.get("scope") or "")
            if where != place:
                if place is not None:
                    rows.append(Text(""))
                rows.append(Text(f"  {where}", style=f"bold {theme.LABEL}"))
                place = where
            chosen = n == self.selected
            mark, colour = self.mark(server.get("status"))
            row = Text.assemble(("❯ " if chosen else "  ", theme.ACCENT))
            row.append(f"{mark} ", style=colour)
            row.append(server.get("name", "").ljust(width), style=f"bold {theme.ACCENT}" if chosen else "")
            tools = server.get("tools")
            if server.get("status") == "connected" and tools is not None:
                row.append(f"{len(tools)} tool{'s' if len(tools) != 1 else ''}", style=theme.DIM)
            else:
                row.append((server.get("status") or "").replace("-", " "), style=colour)
            rows.append(row)
        rows += [Text(""), dim("https://code.claude.com/docs/en/mcp for help")]
        return Group(*rows)

    def draw_server(self):
        server = self.server or {}
        config = server.get("config") or {}
        mark, colour = self.mark(server.get("status"))
        info = server.get("serverInfo") or {}
        rows = [("Status", Text.assemble((f"{mark} ", colour), (server.get("status") or "unknown").replace("-", " ")))]
        if info.get("name"):
            rows.append(("Server", Text(f"{info['name']} {info.get('version', '')}".strip())))
        if config.get("url"):
            url = urlparse(config["url"])
            rows.append(("URL", Text(f"{url.scheme}://{url.netloc}")))  # the path can hold a secret
        if config.get("command"):
            rows.append(("Command", Text(" ".join([config["command"]] + list(config.get("args") or []))[:200])))
        rows.append(("Config location", Text(self.PLACES.get(server.get("scope") or server.get("source"), "—"))))
        tools = server.get("tools")
        if tools is not None:
            rows.append(("Capabilities", Text("tools")))
            rows.append(("Tools", Text(f"{len(tools)} tool{'s' if len(tools) != 1 else ''}")))
        if server.get("error"):
            rows.append(("Error", Text(str(server["error"])[:300], style=theme.ERROR)))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True)
        grid.add_column()
        for label, value in rows:
            grid.add_row(Text(f"{label}:", style=theme.LABEL), value)
        choices = []
        for n, (_, label) in enumerate(self.actions()):
            chosen = n == self.action
            choices.append(Text.assemble(("❯ " if chosen else "  ", theme.ACCENT),
                                         (f"{n + 1}. {label}", f"bold {theme.ACCENT}" if chosen else "")))
        return Group(grid, Text(""), *choices)

    def draw_tools(self):
        tools = (self.server or {}).get("tools") or []
        rows = []
        for tool in tools:
            notes = (tool.get("annotations") or {})
            row = Text.assemble(("· ", theme.DIM))
            row.append(tool.get("name", ""), style="bold")
            if notes.get("readOnly"):
                row.append("  read-only", style=theme.DIM)
            if notes.get("destructive"):
                row.append("  destructive", style=theme.ERROR)
            rows.append(row)
        return Group(*rows) if rows else dim("No tools.")
