from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QSignalBlocker
from PyQt6.QtGui import QColor, QPalette, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common.file_browser import _panel as panel_module
from app_common.file_browser._panel import FileListPanel


_APP = QApplication.instance() or QApplication([])


def _palette(scheme):
    palette = QPalette()
    dark = scheme == "dark"
    for name, value in {
        "Window": "#2d2d2d" if dark else "#f5f5f5",
        "Base": "#232323" if dark else "#ffffff",
        "AlternateBase": "#323232" if dark else "#eeeeee",
        "Text": "#dcdcdc" if dark else "#202124",
        "WindowText": "#dcdcdc" if dark else "#202124",
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, name), QColor(value))
    return palette


@pytest.fixture
def app(monkeypatch):
    palette = QPalette(_APP.palette())
    style_name = _APP.style().objectName()
    _APP.setStyle("Fusion")
    _APP.setPalette(_palette("light"))
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kwargs: None)
    yield _APP
    _APP.setStyle(style_name)
    _APP.setPalette(palette)
    _APP.processEvents()


def _assert_widget_colors(panel, palette):
    for view in (panel._tree_widget, panel._list_widget):
        # The viewport is what actually paints the large list background;
        # checking QApplication.palette() alone missed this regression.
        for widget in (view, view.viewport()):
            for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Text):
                assert widget.palette().color(role) == palette.color(role)
    if panel._filter_edit is not None:
        for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Text):
            assert panel._filter_edit.palette().color(role) == palette.color(role)
    for label in (panel._size_label, panel._selection_status_label):
        assert label.palette().color(QPalette.ColorRole.WindowText) == palette.color(QPalette.ColorRole.Text)


@pytest.mark.parametrize("create_filter_bar", [True, False])
def test_palette_changes_restyle_real_viewports_without_resetting_photo_state(
    app, tmp_path, monkeypatch, create_filter_bar
):
    panel = FileListPanel(create_filter_bar=create_filter_bar)
    path = os.path.normpath(str(tmp_path / "existing-thumbnail.jpg"))
    panel._all_files = [path]
    panel._filtered_files = [path]
    panel._meta_cache = {path: {"rating": 4, "comment": "cached metadata"}}
    model = panel._thumb_list_model
    model.rebuild([path], meta_cache=panel._meta_cache, tooltip_fn=None, mismatch_fn=None)
    pixmap = QPixmap(32, 24)
    pixmap.fill(QColor("#39aaff"))
    model.set_pixmap_for_path(path, pixmap, panel._thumb_size)
    panel._thumb_memory_cache.put(path, panel._thumb_size, pixmap.toImage())
    with QSignalBlocker(panel._list_widget.selectionModel()):
        panel._list_widget.setCurrentIndex(model.index(0, 0))
    panel._selected_display_path = path
    panel.resize(900, 600)
    panel.show()
    QTest.qWait(40)
    changes, reads = [], []
    model.modelReset.connect(lambda: changes.append("reset"))
    model.dataChanged.connect(lambda *_args: changes.append("data"))
    panel.file_selected.connect(lambda *_args: changes.append("selected"))
    monkeypatch.setattr(panel, "load_directory", lambda *_args, **_kwargs: reads.append("directory"))
    monkeypatch.setattr(panel, "_start_thumbnail_loader", lambda *_args, **_kwargs: reads.append("thumbnail"))
    original_entry = model._entries[0]
    original_meta = panel._meta_cache
    cache_key = original_entry.pixmap.cacheKey()
    try:
        for scheme in ("dark", "light", "dark"):
            palette = _palette(scheme)
            app.setPalette(palette)
            QTest.qWait(40)
            _assert_widget_colors(panel, palette)
            assert panel._list_widget.currentIndex() == model.index(0, 0)
            assert panel._list_widget.selectionModel().selectedIndexes() == [model.index(0, 0)]
            assert panel.get_selected_display_path() == path
            assert model._entries[0] is original_entry
            assert original_entry.pixmap.cacheKey() == cache_key
            assert panel._meta_cache is original_meta
            assert panel._thumb_memory_cache.get(path, panel._thumb_size) is not None
            assert changes == []
            assert reads == []
    finally:
        panel.close()


def test_duplicate_palette_events_do_not_repolish_again(app, monkeypatch):
    panel = FileListPanel(create_filter_bar=False)
    calls = []
    original = panel._list_widget.setStyleSheet

    def record(stylesheet):
        calls.append(stylesheet)
        original(stylesheet)
        # Exercise synchronous reentry during a style application as well.
        QApplication.sendEvent(panel, QEvent(QEvent.Type.PaletteChange))

    monkeypatch.setattr(panel._list_widget, "setStyleSheet", record)
    try:
        app.setPalette(_palette("dark"))
        app.processEvents()
        for _ in range(5):
            QApplication.sendEvent(panel, QEvent(QEvent.Type.PaletteChange))
            QApplication.sendEvent(panel, QEvent(QEvent.Type.ApplicationPaletteChange))
        QTest.qWait(1)
        assert len(calls) == 1
        _assert_widget_colors(panel, _palette("dark"))
    finally:
        panel.close()


def test_shutdown_stops_queued_style_refresh(app, monkeypatch):
    panel = FileListPanel(create_filter_bar=False)
    calls = []
    monkeypatch.setattr(panel._list_widget, "setStyleSheet", calls.append)
    try:
        panel.setPalette(_palette("dark"))
        assert panel._theme_refresh_timer.isActive()
        panel._request_background_shutdown()
        assert not panel._theme_refresh_timer.isActive()
        app.processEvents()
        QApplication.sendEvent(panel, QEvent(QEvent.Type.ApplicationPaletteChange))
        assert not panel._theme_refresh_timer.isActive()
        assert calls == []
    finally:
        panel.close()
