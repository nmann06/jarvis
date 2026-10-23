"""The rows of Claude Code's Config tab: what each setting is called, where its value lives
in the settings files, and its default, in the order Claude Code lists them.

The choices each one takes come from Claude Code itself (`claude -p /config`), so a new
version's options show up here without a change; a setting this table doesn't know yet is
listed under its own name.
"""

import re
from dataclasses import dataclass, field

# /config id, label, settings key (dots for nesting), default
ITEMS = [
    ("autoCompact", "Auto-compact", "autoCompactEnabled", True),
    ("tips", "Show tips", "spinnerTipsEnabled", True),
    ("reduceMotion", "Reduce motion", "prefersReducedMotion", False),
    ("thinking", "Thinking mode", "alwaysThinkingEnabled", True),
    ("promptSuggestionEnabled", "Prompt suggestions", "promptSuggestionEnabled", True),
    ("recap", "Session recap", "awaySummaryEnabled", True),
    ("checkpoints", "Rewind code (checkpoints)", "fileCheckpointingEnabled", True),
    ("workflows", "Dynamic workflows", "enableWorkflows", False),
    ("workflowKeywordTriggerEnabled", "Ultracode keyword trigger", "workflowKeywordTriggerEnabled", True),
    ("workflowSizeGuideline", "Dynamic workflow size", "workflowSizeGuideline", "small"),
    ("verbose", "Verbose output", "verbose", False),
    ("progressBar", "Terminal progress bar", "terminalProgressBarEnabled", True),
    ("turnDuration", "Show turn duration", "showTurnDuration", True),
    ("timeFormat", "Time format", "timeFormat", "auto"),
    ("permissionMode", "Default permission mode", "permissions.defaultMode", "default"),
    ("worktreeBaseRef", "Worktree base ref", "worktree.baseRef", "fresh"),
    ("useAutoModeDuringPlan", "Use auto mode during plan", "useAutoModeDuringPlan", True),
    ("gitignore", "Respect .gitignore in file picker", "respectGitignore", True),
    ("copyFullResponse", "Skip the /copy picker", "copyFullResponse", False),
    ("copyOnSelect", "Copy on select", "copyOnSelect", True),
    ("autoScroll", "Auto-scroll", "autoScrollEnabled", True),
    ("defaultToAgentsView", "Open agents view by default", "defaultToAgentsView", False),
    ("leftArrowOpensAgents", "← opens agents", "leftArrowOpensAgents", True),
    ("theme", "Theme", "theme", "dark"),
    ("notifChannel", "Local notifications", "preferredNotifChannel", "auto"),
    ("inputNeededNotifEnabled", "Push when actions required", "inputNeededNotifEnabled", False),
    ("agentPushNotifEnabled", "Push when Claude decides", "agentPushNotifEnabled", False),
    ("outputStyle", "Output style", "outputStyle", "default"),
    ("language", "Language", "language", None),
    ("editor", "Editor mode", "editorMode", "normal"),
    ("externalEditorContext", "Show last response in external editor", "externalEditorContext", False),
    ("prStatus", "Show PR status footer", "prStatusFooterEnabled", True),
    ("model", "Model", "model", None),
    ("autoConnectIde", "Auto-connect to IDE (external terminal)", "autoConnectIde", False),
    ("remoteControl", "Enable Remote Control for all sessions", "remoteControlAtStartup", "default"),
    ("switchModelsOnFlag", "Switch models when a message is flagged", "switchModelsOnFlag", None),
]

# How Claude Code words some values.
VALUE_NAMES = {
    "permissionMode": {"default": "Manual", "acceptEdits": "Accept edits", "plan": "Plan",
                       "auto": "Auto", "dontAsk": "Don't ask"},
    "theme": {"auto": "Auto (match terminal)", "dark": "Dark mode", "light": "Light mode",
              "dark-daltonized": "Dark mode (colorblind-friendly)",
              "light-daltonized": "Light mode (colorblind-friendly)",
              "dark-ansi": "Dark mode (ANSI colors only)", "light-ansi": "Light mode (ANSI colors only)"},
    "notifChannel": {"auto": "Auto"},
    "model": {None: "Default (recommended)", "default": "Default (recommended)"},
    "language": {None: "Default (English)"},
    "switchModelsOnFlag": {None: "Not asked yet"},
    "workflowSizeGuideline": {"small": "small (default)"},
}

# Changes that only matter to Claude Code's own terminal screen, not to the Jarvis window.
TERMINAL_ONLY = {"tips", "reduceMotion", "progressBar", "turnDuration", "copyFullResponse",
                 "copyOnSelect", "autoScroll", "defaultToAgentsView", "leftArrowOpensAgents",
                 "theme", "editor", "prStatus", "timeFormat"}


@dataclass
class Item:
    id: str
    label: str
    key: str
    default: object
    options: list = field(default_factory=list)  # empty: free text
    value: object = None

    @property
    def kind(self):
        if self.options in (["true", "false"], ["false", "true"]):
            return "bool"
        return "enum" if self.options else "text"

    def shown(self):
        value = self.value
        if self.kind == "bool":
            return "true" if value else "false"
        names = VALUE_NAMES.get(self.id, {})
        if value in names:
            return names[value]
        return "default" if value is None else str(value)

    def as_option(self):
        """The value as /config takes it."""
        if self.kind == "bool":
            return "true" if self.value else "false"
        return "" if self.value is None else str(self.value)


def parse_options(text):
    """`/config` with no arguments lists every key: `  autoCompact=true|false`."""
    options = {}
    for line in text.splitlines():
        match = re.match(r"\s+([A-Za-z][\w.]*)=(.+)$", line)
        if not match:
            continue
        values = match.group(2).strip()
        options[match.group(1)] = [] if values.startswith("<") else values.split("|")
    return options


def lookup(settings, key):
    value = settings
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def build(options, settings, global_config=None):
    """Every setting /config can change, with its value now (settings first, then the
    older ~/.claude.json, then the default)."""
    known = {i[0] for i in ITEMS}
    rows = []
    for item_id, label, key, default in ITEMS + [(k, k, k, None) for k in options if k not in known]:
        if options and item_id not in options:
            continue
        value = lookup(settings, key)
        if value is None and global_config:
            value = lookup(global_config, key)
        if item_id == "workflows" and settings.get("disableWorkflows") is True:
            value = False
        rows.append(Item(item_id, label, key, default, list(options.get(item_id, [])),
                         default if value is None else value))
    return rows
