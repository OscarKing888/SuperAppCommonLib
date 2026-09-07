from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor, QPalette

from app_common.qt_theme import (
    browser_chrome_colors,
    detect_color_scheme,
    is_theme_change_event,
    scheme_from_palette,
    scheme_from_qt_value,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (0, None), (1, "light"), (2, "dark"),
     (SimpleNamespace(name="Dark"), "dark"),
     (SimpleNamespace(name=lambda: "Light"), "light")],
)
def test_color_scheme_values_support_qt5_and_qt6_enum_forms(value, expected) -> None:
    assert scheme_from_qt_value(value) == expected


def test_palette_fallback_works_without_color_scheme_style_hint() -> None:
    palette = QPalette()
    app = SimpleNamespace(styleHints=lambda: object(), palette=lambda: palette)
    palette.setColor(QPalette.ColorRole.Window, QColor("#eeeeee"))
    assert detect_color_scheme(app) == "light"
    palette.setColor(QPalette.ColorRole.Window, QColor("#252525"))
    assert detect_color_scheme(app) == "dark"
    assert scheme_from_palette(palette) == "dark"


def test_system_color_scheme_takes_priority_over_applied_palette() -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    app = SimpleNamespace(
        styleHints=lambda: SimpleNamespace(colorScheme=lambda: 2),
        palette=lambda: palette,
    )
    assert detect_color_scheme(app) == "dark"


def test_theme_events_include_application_palette_but_exclude_focus_changes() -> None:
    assert is_theme_change_event(QEvent(QEvent.Type.PaletteChange))
    assert is_theme_change_event(QEvent(QEvent.Type.ApplicationPaletteChange))
    assert not is_theme_change_event(QEvent(QEvent.Type.ApplicationStateChange))
    assert not is_theme_change_event(QEvent(QEvent.Type.FocusIn))
    assert not is_theme_change_event(None)


def test_browser_colors_cover_both_schemes() -> None:
    dark = browser_chrome_colors("dark")
    light = browser_chrome_colors("light")
    assert dark.toolbar_bg != light.toolbar_bg
    assert dark.tree_bg != light.tree_bg
    assert dark.tree_selected_text != light.tree_selected_text
