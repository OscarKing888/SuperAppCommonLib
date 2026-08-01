from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


class _SignalProbe:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit(self, *args) -> None:
        self.calls.append(tuple(args))


class _UncachedPreviewProbe:
    enable_in_memory_fast_preview = True
    skip_uncached_fast_preview = True

    def __init__(self, pixmap=None) -> None:
        self.file_fast_preview_requested = _SignalProbe()
        self.file_fast_preview_pixmap_requested = _SignalProbe()
        self._selected_display_path = ""
        self._thumb_size = 128
        self._pixmap = pixmap
        self.prioritized: list[str] = []

    def _current_thumbnail_fast_preview_pixmap(self, _path: str):
        return self._pixmap

    def resolve_preview_path(self, path: str, prefer_fast_preview: bool = False) -> str:
        return path

    def _prioritize_fast_preview_thumbnail(self, path: str) -> None:
        self.prioritized.append(path)

    def _resolve_source_path_for_action(self, _path: str) -> str:
        raise AssertionError("cache miss must not synchronously resolve/decode the source")

    def _materialize_current_thumbnail_fast_preview(self, _path: str) -> str:
        raise AssertionError("cache miss must not materialize a temporary JPEG")


class _PlaybackPanel(FileListPanel):
    enable_key_navigation_playback = True
    enable_in_memory_fast_preview = True
    skip_uncached_fast_preview = True

    def __init__(self) -> None:
        self.full_paths: list[str] = []
        self.fast_paths: list[str] = []
        super().__init__(create_filter_bar=False)

    def _emit_file_selected_for_path(self, path: str) -> None:
        self.full_paths.append(os.path.normpath(path))

    def _emit_fast_preview_for_path(self, path: str) -> None:
        self.fast_paths.append(os.path.normpath(path))


def _key_event(event_type, key, *, auto_repeat: bool = False, modifiers=None) -> QKeyEvent:
    if modifiers is None:
        modifiers = Qt.KeyboardModifier.NoModifier
    return QKeyEvent(event_type, key, modifiers, "", auto_repeat, 1)


def _make_thumb_panel() -> tuple[_PlaybackPanel, list[str]]:
    panel = _PlaybackPanel()
    paths = [os.path.normpath(f"folder/photo-{index}.jpg") for index in range(6)]
    panel._thumb_list_model.rebuild(
        paths,
        meta_cache={},
        tooltip_fn=lambda _path: "",
        mismatch_fn=lambda _path: False,
    )
    panel._set_view_mode(panel._MODE_THUMB)
    panel._list_widget.resize(900, 600)
    panel._update_thumb_display()
    panel._list_widget.setCurrentIndex(panel._thumb_index_for_row(0))
    return panel, paths


def _make_tree_panel() -> tuple[_PlaybackPanel, list[str]]:
    panel = _PlaybackPanel()
    paths = [os.path.normpath(f"folder/photo-{index}.jpg") for index in range(6)]
    panel._file_table_model.rebuild(
        paths,
        meta_cache={},
        tooltip_fn=lambda _path: "",
        mismatch_fn=lambda _path: False,
    )
    panel._set_view_mode(panel._MODE_LIST)
    panel._tree_widget.setCurrentIndex(panel._tree_index_for_path(paths[0]))
    panel.full_paths.clear()
    panel.fast_paths.clear()
    return panel, paths


def test_file_list_panel_defaults_to_native_key_navigation() -> None:
    assert FileListPanel.enable_key_navigation_playback is False
    assert FileListPanel.enable_in_memory_fast_preview is False
    assert FileListPanel.skip_uncached_fast_preview is False


@pytest.mark.parametrize(
    ("fps", "expected_interval_ms"),
    [(8, 125), (24, 42), (60, 17)],
)
def test_application_playback_timer_tracks_configured_fps(
    fps: int,
    expected_interval_ms: int,
) -> None:
    panel, _paths = _make_thumb_panel()
    try:
        panel._set_key_navigation_fps(fps, persist=False)
        panel._ensure_key_navigation_playback_timer()

        timer = panel._key_navigation_playback_timer
        assert timer is not None
        assert timer.timerType() == Qt.TimerType.PreciseTimer
        assert timer.interval() == expected_interval_ms
    finally:
        panel.stop_key_navigation_playback()
        panel.close()


def test_key_hold_commits_only_final_path_on_physical_release() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        physical_press = _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right)
        assert panel.eventFilter(widget, physical_press) is True
        assert panel.full_paths == []
        assert panel.fast_paths == [paths[1]]

        first_repeat = _key_event(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            auto_repeat=True,
        )
        assert panel.eventFilter(widget, first_repeat) is True
        assert panel._key_navigation_playback_active is True
        assert panel.fast_paths == [paths[1], paths[2]]

        # 后续系统重复事件不推进；应用定时器每 tick 只推进一帧。
        assert panel.eventFilter(widget, first_repeat) is True
        assert panel.fast_paths == [paths[1], paths[2]]
        panel._on_key_navigation_playback_tick()
        assert panel.fast_paths == [paths[1], paths[2], paths[3]]

        auto_release = _key_event(
            QEvent.Type.KeyRelease,
            Qt.Key.Key_Right,
            auto_repeat=True,
        )
        assert panel.eventFilter(widget, auto_release) is True
        assert panel._key_navigation_playback_active is True
        assert panel.full_paths == []

        physical_release = _key_event(QEvent.Type.KeyRelease, Qt.Key.Key_Right)
        assert panel.eventFilter(widget, physical_release) is True
        assert panel._key_navigation_playback_active is False
        assert panel.full_paths == [paths[3]]
    finally:
        panel.stop_key_navigation_playback()
        panel.close()


def test_focus_loss_commits_final_path_when_key_release_is_lost() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        panel.eventFilter(widget, _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right))
        panel.eventFilter(
            widget,
            _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right, auto_repeat=True),
        )
        assert panel.fast_paths == [paths[1], paths[2]]

        panel.eventFilter(widget, QEvent(QEvent.Type.FocusOut))

        assert panel._key_navigation_playback_active is False
        assert panel.full_paths == [paths[2]]
        assert panel._deferred_file_selected_path == ""
    finally:
        panel.close()


def test_public_stop_is_idempotent_and_never_commits_by_default() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        panel.eventFilter(
            widget,
            _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right, auto_repeat=True),
        )
        assert panel.fast_paths == [paths[1]]

        panel.stop_key_navigation_playback()
        panel.stop_key_navigation_playback()

        assert panel._key_navigation_playback_active is False
        assert panel.full_paths == []
    finally:
        panel.close()


def test_short_key_tap_commits_once_on_release() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        assert panel.eventFilter(
            widget,
            _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right),
        ) is True
        assert panel.fast_paths == [paths[1]]
        assert panel.full_paths == []

        assert panel.eventFilter(
            widget,
            _key_event(QEvent.Type.KeyRelease, Qt.Key.Key_Right),
        ) is True
        assert panel.full_paths == [paths[1]]
    finally:
        panel.close()


def test_tree_short_key_tap_also_commits_only_on_release() -> None:
    panel, paths = _make_tree_panel()
    widget = panel._tree_widget
    try:
        QApplication.sendEvent(
            widget,
            _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Down),
        )
        assert panel.fast_paths == [paths[1]]
        assert panel.full_paths == []

        QApplication.sendEvent(
            widget,
            _key_event(QEvent.Type.KeyRelease, Qt.Key.Key_Down),
        )
        assert panel.full_paths == [paths[1]]
    finally:
        panel.close()


def test_uncached_fast_preview_only_prioritizes_background_thumbnail(tmp_path) -> None:
    source = os.path.normpath(str(tmp_path / "photo.ARW"))
    probe = _UncachedPreviewProbe()

    FileListPanel._emit_fast_preview_for_path(probe, source)

    assert probe.prioritized == [source]
    assert probe.file_fast_preview_requested.calls == []
    assert probe.file_fast_preview_pixmap_requested.calls == []


def test_decoded_current_tier_pixmap_wins_before_path_fallback(tmp_path) -> None:
    source = os.path.normpath(str(tmp_path / "photo.ARW"))
    pixmap = QPixmap(128, 96)
    pixmap.fill(QColor(10, 20, 30))
    probe = _UncachedPreviewProbe(pixmap)

    FileListPanel._emit_fast_preview_for_path(probe, source)

    assert probe.prioritized == []
    assert probe.file_fast_preview_requested.calls == []
    assert probe.file_fast_preview_pixmap_requested.calls == [
        (source, pixmap, 128),
    ]


def test_thumbnail_size_change_invalidates_old_tier_pending_results() -> None:
    panel = _PlaybackPanel()
    try:
        panel._thumb_size = 128
        old_token = panel._thumb_request_token
        stale = QImage(128, 96, QImage.Format.Format_ARGB32)
        stale.fill(QColor(10, 20, 30))
        panel._thumb_pending_batch[os.path.normpath("folder/stale.jpg")] = stale

        size_index = panel._size_slider.maximum()
        panel._on_size_slider_changed(size_index)

        assert panel._thumb_size > 128
        assert panel._thumb_request_token == old_token + 1
        assert panel._thumb_pending_batch == {}
    finally:
        panel.close()


def test_background_thumbnail_arrival_replays_current_fast_preview() -> None:
    panel, paths = _make_thumb_panel()
    try:
        target = paths[1]
        image = QImage(128, 96, QImage.Format.Format_ARGB32)
        image.fill(QColor(10, 20, 30))
        panel._selected_display_path = target
        panel._key_navigation_playback_active = True
        panel._thumb_pending_batch[target] = image

        panel._flush_thumb_pending_batch()

        assert panel.fast_paths == [target]
        assert panel.full_paths == []
    finally:
        panel.stop_key_navigation_playback()
        panel.close()
