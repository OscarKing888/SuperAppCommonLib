# -*- coding: utf-8 -*-
from __future__ import annotations

from app_common.file_browser import DirectoryBrowserWidget
from app_common.qt_theme import browser_chrome_colors

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    from PyQt5.QtWidgets import QApplication


def test_browser_chrome_colors_differ() -> None:
    dark = browser_chrome_colors("dark")
    light = browser_chrome_colors("light")
    assert dark.toolbar_bg != light.toolbar_bg
    assert dark.tree_bg != light.tree_bg
    assert dark.title_text != light.title_text


def test_directory_browser_apply_theme_updates_stylesheets() -> None:
    app = QApplication.instance() or QApplication([])
    widget = DirectoryBrowserWidget()
    try:
        widget.apply_theme("light")
        assert "f1f3f4" in widget._toolbar_widget.styleSheet().lower()
        assert "ffffff" in widget._tree.styleSheet().lower() or "#fff" in widget._tree.styleSheet().lower()
        assert "5f6368" in widget._title_label.styleSheet().lower()

        widget.apply_theme("dark")
        assert "252525" in widget._toolbar_widget.styleSheet().lower()
        assert "2a2a2a" in widget._tree.styleSheet().lower()
        assert "aaaaaa" in widget._title_label.styleSheet().lower().replace(" ", "") or "#aaa" in widget._title_label.styleSheet().lower()
    finally:
        widget.close()
