from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    from app_common import superviewer_user_options
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS", superviewer_user_options.normalize_user_options(None))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *a, **k: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))


class _RangePanel(FileListPanel):
    enable_range_mark_shortcuts = True

    def __init__(self) -> None:
        self.full_paths: list[str] = []
        super().__init__(create_filter_bar=False)

    def _emit_file_selected_for_path(self, path: str, **kwargs) -> None:
        self.full_paths.append(os.path.normpath(path))


class _NativePanel(_RangePanel):
    enable_range_mark_shortcuts = False


def _make_panel(mode: str, panel_cls=_RangePanel) -> tuple[FileListPanel, list[str], object]:
    panel = panel_cls()
    paths = [os.path.normpath(f"folder/photo-{index:02}.jpg") for index in range(10)]
    model = panel._thumb_list_model if mode == "thumb" else panel._file_table_model
    model.rebuild(paths, meta_cache={}, tooltip_fn=lambda _path: "", mismatch_fn=lambda _path: False)
    panel._filtered_files = list(paths)
    panel._set_view_mode(panel._MODE_THUMB if mode == "thumb" else panel._MODE_LIST)
    view = panel._list_widget if mode == "thumb" else panel._tree_widget
    return panel, paths, view


def _set_current(view, row: int) -> None:
    view.setCurrentIndex(view.model().index(row, 0))


def _press(panel: FileListPanel, view, key, *, text: str = "", modifiers=None, auto_repeat: bool = False) -> bool:
    if modifiers is None:
        modifiers = Qt.KeyboardModifier.NoModifier
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text, auto_repeat, 1)
    return panel.eventFilter(view, event)


def _close(panel: FileListPanel) -> None:
    panel.stop_key_navigation_playback()
    panel.close()
    _APP.processEvents()


def test_range_mark_shortcuts_default_off_for_shared_panel() -> None:
    assert FileListPanel.enable_range_mark_shortcuts is False


@pytest.mark.parametrize("mode", ["thumb", "tree"])
def test_bracket_marks_select_every_path_between_start_and_end(mode: str) -> None:
    panel, paths, view = _make_panel(mode)
    try:
        _set_current(view, 2)
        assert _press(panel, view, Qt.Key.Key_BracketLeft, text="[")
        _set_current(view, 6)
        panel.full_paths.clear()
        assert _press(panel, view, Qt.Key.Key_BracketRight, text="]")

        assert panel._active_view_selected_paths() == paths[2:7]
        assert view.currentIndex().row() == 6
        # 当前图不变，区间选择不能重新加载预览。
        assert panel.full_paths == []
        assert "已选 5" in panel._selection_status_label.text()
        assert "区间起点 3" in panel._selection_status_label.text()

        # 起点保留：在其它图上再按 "]" 调整区间终点，也支持反向区间。
        _set_current(view, 0)
        assert _press(panel, view, Qt.Key.Key_BracketRight, text="]")
        assert panel._active_view_selected_paths() == paths[0:3]
        assert view.currentIndex().row() == 0
    finally:
        _close(panel)


@pytest.mark.parametrize("mode", ["thumb", "tree"])
def test_end_mark_without_start_keeps_selection_and_shows_hint(mode: str) -> None:
    panel, paths, view = _make_panel(mode)
    try:
        _set_current(view, 4)
        assert _press(panel, view, Qt.Key.Key_BracketRight, text="]")
        assert panel._active_view_selected_paths() == [paths[4]]
        assert "[" in panel._selection_status_label.text()
    finally:
        _close(panel)


def test_full_width_ime_brackets_count_as_marks() -> None:
    panel, paths, view = _make_panel("thumb")
    try:
        _set_current(view, 1)
        assert _press(panel, view, Qt.Key.Key_unknown, text="【")
        _set_current(view, 3)
        assert _press(panel, view, Qt.Key.Key_unknown, text="】")
        assert panel._active_view_selected_paths() == paths[1:4]
    finally:
        _close(panel)


def test_modified_or_disabled_brackets_stay_native() -> None:
    panel, paths, view = _make_panel("thumb")
    try:
        _set_current(view, 1)
        assert not _press(
            panel,
            view,
            Qt.Key.Key_BracketLeft,
            text="[",
            modifiers=Qt.KeyboardModifier.ControlModifier,
        )
        assert panel._range_mark_start_path == ""
    finally:
        _close(panel)

    native, _paths, native_view = _make_panel("thumb", _NativePanel)
    try:
        _set_current(native_view, 1)
        assert not _press(native, native_view, Qt.Key.Key_BracketLeft, text="[")
        assert native._range_mark_start_path == ""
    finally:
        _close(native)


def test_start_mark_hidden_by_filter_does_not_select_range() -> None:
    panel, paths, view = _make_panel("thumb")
    try:
        _set_current(view, 2)
        assert _press(panel, view, Qt.Key.Key_BracketLeft, text="[")
        visible = paths[4:]
        panel._thumb_list_model.rebuild(
            visible, meta_cache={}, tooltip_fn=lambda _path: "", mismatch_fn=lambda _path: False
        )
        _set_current(view, 3)
        assert _press(panel, view, Qt.Key.Key_BracketRight, text="]")
        assert panel._active_view_selected_paths() == [visible[3]]
    finally:
        _close(panel)
