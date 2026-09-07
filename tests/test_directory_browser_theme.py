from __future__ import annotations

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem

from app_common.file_browser import DirectoryBrowserWidget
from app_common.qt_theme import browser_chrome_colors


_APP = QApplication.instance() or QApplication([])


def _palette(scheme):
    palette = QPalette(_APP.palette())
    dark = scheme == "dark"
    background = QColor("#252525" if dark else "#f5f5f5")
    foreground = QColor("#dddddd" if dark else "#202124")
    palette.setColor(QPalette.ColorRole.Window, background)
    palette.setColor(QPalette.ColorRole.WindowText, foreground)
    palette.setColor(QPalette.ColorRole.Base, background)
    palette.setColor(QPalette.ColorRole.Text, foreground)
    return palette


class _ThemeBrowser(DirectoryBrowserWidget):
    def __init__(self, path):
        self.root_path = str(path)
        self.populate_count = 0
        self.expand_count = 0
        super().__init__()

    def _populate_roots(self):
        self.populate_count += 1
        self.root_item = QTreeWidgetItem(["root"])
        self.root_item.setData(0, Qt.ItemDataRole.UserRole, self.root_path)
        self.child_item = QTreeWidgetItem(["child"])
        self.child_item.setData(0, Qt.ItemDataRole.UserRole, self.root_path)
        self.root_item.addChild(self.child_item)
        self._tree.addTopLevelItem(self.root_item)

    def _on_expanded(self, _item):
        self.expand_count += 1


@pytest.fixture
def browser(tmp_path):
    previous = QPalette(_APP.palette())
    _APP.setPalette(_palette("dark"))
    widget = _ThemeBrowser(tmp_path)
    yield widget
    widget.close()
    _APP.setPalette(previous)
    _APP.processEvents()


def test_directory_theme_preserves_dark_colors_and_never_changes_host_palette(browser) -> None:
    host_palette = QPalette(_APP.palette())
    assert "#252525" in browser._toolbar_widget.styleSheet()
    assert "#2a2a2a" in browser._tree.styleSheet()
    assert "#3a5a8a" in browser._tree.styleSheet()

    browser.apply_theme("light")
    assert "#f1f3f4" in browser._toolbar_widget.styleSheet()
    assert "#ffffff" in browser._tree.styleSheet()
    assert "#5f6368" in browser._title_label.styleSheet()
    assert _APP.palette() == host_palette

    custom = replace(browser_chrome_colors("dark"), toolbar_bg="#123456")
    browser.apply_theme(custom)
    assert "#123456" in browser._toolbar_widget.styleSheet()
    assert _APP.palette() == host_palette


def test_palette_changes_restyle_without_reloading_or_changing_directory_selection(browser) -> None:
    browser.root_item.setExpanded(True)
    browser._tree.setCurrentItem(browser.child_item)
    selected_paths = []
    browser.directory_selected.connect(selected_paths.append)
    before_counts = (browser.populate_count, browser.expand_count)
    model = browser._tree.model()
    resets = []
    model.modelReset.connect(lambda: resets.append(True))

    _APP.setPalette(_palette("light"))
    _APP.processEvents()
    assert "#f1f3f4" in browser._toolbar_widget.styleSheet()
    assert "#ffffff" in browser._tree.styleSheet()
    _APP.setPalette(_palette("dark"))
    _APP.processEvents()
    assert "#252525" in browser._toolbar_widget.styleSheet()
    assert "#2a2a2a" in browser._tree.styleSheet()

    assert (browser.populate_count, browser.expand_count) == before_counts
    assert browser.root_item.isExpanded()
    assert browser._tree.currentItem() is browser.child_item
    assert browser._tree.topLevelItem(0) is browser.root_item
    assert resets == []
    assert selected_paths == []
    # Existing clicks still emit the same directory after palette changes.
    browser._on_clicked(browser.child_item, 0)
    assert selected_paths == [browser.root_path]


def test_repeated_palette_and_focus_events_do_not_reapply_identical_styles(browser, monkeypatch) -> None:
    styles = []
    set_style = browser._tree.setStyleSheet

    def record_style(value):
        styles.append(value)
        set_style(value)

    monkeypatch.setattr(browser._tree, "setStyleSheet", record_style)
    for event_type in (
        QEvent.Type.PaletteChange,
        QEvent.Type.ApplicationPaletteChange,
        QEvent.Type.ApplicationStateChange,
    ):
        QApplication.sendEvent(browser, QEvent(event_type))
    assert styles == []

    browser.setPalette(_palette("light"))
    _APP.processEvents()
    assert len(styles) == 1
    assert "#ffffff" in styles[0]
