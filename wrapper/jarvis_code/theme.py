"""Jarvis's look: Claude Code's layout and glyphs, in arc-reactor blue where Claude Code is orange.

Red and green keep their meaning (diffs, errors, a tool that worked); everything Claude Code
draws in its own orange, and the extra UI around it (thinking, modes, dialogs), is blue here.
"""

import random

LETTERS = {
    "J": ["     ██╗", "     ██║", "     ██║", "██   ██║", "╚█████╔╝", " ╚════╝ "],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
}
JARVIS_ROWS = ["".join(LETTERS[c][row] for c in "JARVIS") for row in range(6)]

# Light at the top of the letters to deep at the bottom.
GRADIENT = ["#96e6ff", "#5acdff", "#28afff", "#148cf0", "#0f69d7", "#0a4bb4"]

ACCENT = "#3cbeff"        # Claude Code's orange: spinner, thinking, the J.A.R.V.I.S. mark
SHIMMER = "#c2efff"       # the light that runs across the spinner's word
LABEL = "#6e96be"         # labels, hints and the prompt's rules
DIM = "#8592a6"           # dim text
CODE = "#8ccfff"          # inline code
USER_BG = "#16202e"       # behind your own messages
PERMISSION = "#5aa9ff"    # permission and question dialogs
SUCCESS = "#4eba65"
ERROR = "#ff6b80"
WARNING = "#e8b75a"       # usage-limit warnings only

DIFF_ADDED = "#1d3a28"
DIFF_REMOVED = "#45202a"

# Permission modes, in the order shift+tab cycles them.
MODES = ["default", "acceptEdits", "plan", "auto"]
MODE_LOOK = {
    # mode: (symbol, footer label, colour)
    "default": ("", "", LABEL),
    "acceptEdits": ("⏵⏵", "accept edits on", "#7aa2f7"),
    "plan": ("⏸", "plan mode on", "#4fd1e8"),
    "auto": ("⏵⏵", "auto mode on", ACCENT),
    "bypassPermissions": ("⏵⏵", "bypass permissions on", ERROR),
    "dontAsk": ("⏵⏵", "don't ask on", ERROR),
}
BASH_MODE = "#4fd1e8"  # the prompt after a leading !

BULLET = "●"
RESULT = "⎿"
SPINNER_FRAMES = ["·", "✢", "✳", "✶", "✻", "✽", "✽", "✻", "✶", "✳", "✢", "·"]

VERBS = [
    "Analysing", "Assembling", "Calculating", "Calibrating", "Charging the arc reactor",
    "Compiling", "Computing", "Consulting the archives", "Cross-referencing", "Deliberating",
    "Extrapolating", "Fabricating", "Indexing", "Modelling", "Optimising", "Pondering",
    "Polishing the armour", "Reconfiguring", "Rendering", "Running diagnostics", "Scanning",
    "Simulating", "Synthesising", "Triangulating", "Working",
]
DONE_VERBS = ["Analysed", "Assembled", "Calculated", "Calibrated", "Computed", "Deliberated",
              "Synthesised", "Worked"]


def verb():
    return random.choice(VERBS)


def done_verb():
    return random.choice(DONE_VERBS)


def greeting(hour):
    if hour < 5:
        return "Burning the midnight oil, sir?"
    if hour < 12:
        return "Good morning, sir."
    if hour < 18:
        return "Good afternoon, sir."
    return "Good evening, sir."
