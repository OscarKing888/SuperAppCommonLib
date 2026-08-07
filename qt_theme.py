# -*- coding: utf-8 -*-
"""Shared Qt dark/light chrome helpers for app_common widgets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

try:
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QPalette
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QPalette


ColorSchemeName = Literal["dark", "light"]


@dataclass(frozen=True)
class BrowserChromeColors:
    """Semantic colors for titled browser chrome (directory tree, etc.)."""

    toolbar_bg: str
    title_text: str
    button_text: str
    button_hover_text: str
    button_hover_bg: str
    tree_bg: str
    tree_selected_bg: str
    tree_selected_text: str
    tree_hover_bg: str
    muted_text: str


_DARK_BROWSER = BrowserChromeColors(
    toolbar_bg="#252525",
    title_text="#aaaaaa",
    button_text="#aaaaaa",
    button_hover_text="#ffffff",
    button_hover_bg="#333333",
    tree_bg="#2a2a2a",
    tree_selected_bg="#3a5a8a",
    tree_selected_text="#ffffff",
    tree_hover_bg="#333333",
    muted_text="#aaaaaa",
)

_LIGHT_BROWSER = BrowserChromeColors(
    toolbar_bg="#f1f3f4",
    title_text="#5f6368",
    button_text="#5f6368",
    button_hover_text="#202124",
    button_hover_bg="#e8eaed",
    tree_bg="#ffffff",
    tree_selected_bg="#d2e3fc",
    tree_selected_text="#174ea6",
    tree_hover_bg="#f1f3f4",
    muted_text="#70757a",
)


def _role(name: str):
    roles = getattr(QPalette, "ColorRole", QPalette)
    return getattr(roles, name)


def _scheme_from_qt_value(value) -> ColorSchemeName | None:
    if value is None:
        return None
    name_attr = getattr(value, "name", None)
    if callable(name_attr):
        text = str(name_attr())
    elif name_attr is not None:
        text = str(name_attr)
    else:
        text = str(value)
    lowered = text.lower()
    if "dark" in lowered:
        return "dark"
    if "light" in lowered:
        return "light"
    try:
        numeric = int(value)
    except Exception:
        return None
    if numeric == 2:
        return "dark"
    if numeric == 1:
        return "light"
    return None


def scheme_from_palette(palette: QPalette | None) -> ColorSchemeName:
    if palette is None:
        return "dark"
    try:
        color = palette.color(_role("Window"))
        luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255.0
        return "dark" if luminance < 0.5 else "light"
    except Exception:
        return "dark"


def detect_color_scheme(app: QApplication | None = None) -> ColorSchemeName:
    """Detect preferred color scheme from Qt style hints or current palette."""
    application = app or QApplication.instance()
    if application is not None:
        style_hints = getattr(application, "styleHints", None)
        if callable(style_hints):
            hints = style_hints()
            color_scheme = getattr(hints, "colorScheme", None)
            if callable(color_scheme):
                detected = _scheme_from_qt_value(color_scheme())
                if detected is not None:
                    return detected
        return scheme_from_palette(application.palette())
    return "dark"


def browser_chrome_colors(scheme: ColorSchemeName | None = None) -> BrowserChromeColors:
    resolved = scheme or detect_color_scheme()
    return _LIGHT_BROWSER if resolved == "light" else _DARK_BROWSER


def is_theme_change_event(event) -> bool:
    """Return True for Qt ThemeChange / PaletteChange events."""
    if event is None:
        return False
    event_type = event.type()
    type_value = getattr(event_type, "value", event_type)
    watched = {214, 39}  # ThemeChange, PaletteChange numeric fallbacks
    for name in ("ThemeChange", "PaletteChange"):
        enum_val = getattr(QEvent, name, None)
        if enum_val is None and hasattr(QEvent, "Type"):
            enum_val = getattr(QEvent.Type, name, None)
        if enum_val is not None:
            watched.add(getattr(enum_val, "value", enum_val))
            watched.add(enum_val)
    return type_value in watched or event_type in watched


__all__ = [
    "BrowserChromeColors",
    "ColorSchemeName",
    "browser_chrome_colors",
    "detect_color_scheme",
    "is_theme_change_event",
    "scheme_from_palette",
]
