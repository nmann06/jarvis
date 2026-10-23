"""Textual walks a widget's whole class chain and treats some names as its own: an attribute
or method of ours with one of those names breaks the widget in odd ways (`loading` covers it
with a spinner and swallows keys). Catch that here rather than on screen."""

import inspect
import re

from textual.app import App
from textual.widget import Widget

from jarvis_code import app as app_module, panels, widgets

INTENDED = {"display", "render", "compose", "theme", "text"}  # set or overridden on purpose


def our_names(cls):
    names = set()
    for klass in cls.__mro__:
        if not klass.__module__.startswith("jarvis_code"):
            break
        source = inspect.getsource(klass)
        names |= set(re.findall(r"self\.([a-z_]+)\s*=", source))
        names |= {n for n, v in vars(klass).items() if callable(v) and not n.startswith(("_", "on_", "action_"))}
    return names


def test_no_widget_shadows_textual():
    for module in (widgets, panels):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Widget) and cls.__module__ == module.__name__:
                base = next(k for k in cls.__mro__ if not k.__module__.startswith("jarvis_code"))
                clashes = {n for n in our_names(cls) if hasattr(base, n) or hasattr(Widget, n)} - INTENDED
                assert not clashes, f"{cls.__name__} shadows Textual's {sorted(clashes)}"


def test_app_does_not_shadow_textual():
    clashes = {n for n in our_names(app_module.JarvisApp) if hasattr(App, n)} - INTENDED
    assert not clashes, f"JarvisApp shadows Textual's {sorted(clashes)}"
