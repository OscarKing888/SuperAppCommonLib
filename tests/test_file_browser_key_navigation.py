from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


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
    first = panel._thumb_index_for_row(0)
    panel._list_widget.setCurrentIndex(first)
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


def test_first_physical_step_is_full_then_playback_owns_cadence_and_release() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        physical_press = _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right)
        assert panel.eventFilter(widget, physical_press) is True
        assert panel.full_paths == [paths[1]]
        assert panel.fast_paths == []

        first_repeat = _key_event(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            auto_repeat=True,
        )
        assert panel.eventFilter(widget, first_repeat) is True
        assert panel._key_navigation_playback_active is True
        assert panel.fast_paths == [paths[2]]
        assert panel._key_navigation_playback_timer is not None
        assert panel._key_navigation_playback_timer.timerType() == Qt.TimerType.PreciseTimer

        # Later OS repeats do not advance; one application timer tick advances
        # exactly one frame and never performs catch-up work.
        assert panel.eventFilter(widget, first_repeat) is True
        assert panel.fast_paths == [paths[2]]
        panel._on_key_navigation_playback_tick()
        assert panel.fast_paths == [paths[2], paths[3]]

        auto_release = _key_event(
            QEvent.Type.KeyRelease,
            Qt.Key.Key_Right,
            auto_repeat=True,
        )
        assert panel.eventFilter(widget, auto_release) is True
        assert panel._key_navigation_playback_active is True
        assert panel.full_paths == [paths[1]]

        physical_release = _key_event(QEvent.Type.KeyRelease, Qt.Key.Key_Right)
        assert panel.eventFilter(widget, physical_release) is True
        assert panel._key_navigation_playback_active is False
        assert panel.full_paths == [paths[1], paths[3]]
    finally:
        panel.stop_key_navigation_playback()
        panel.close()


def test_focus_loss_cancels_playback_without_full_commit() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        panel.eventFilter(
            widget,
            _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right),
        )
        panel.eventFilter(
            widget,
            _key_event(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Right,
                auto_repeat=True,
            ),
        )
        assert panel.fast_paths == [paths[2]]

        panel.eventFilter(widget, QEvent(QEvent.Type.FocusOut))

        assert panel._key_navigation_playback_active is False
        assert panel.full_paths == [paths[1]]
        assert panel._deferred_file_selected_path == ""
    finally:
        panel.close()


def test_public_stop_is_idempotent_and_never_commits_by_default() -> None:
    panel, paths = _make_thumb_panel()
    widget = panel._list_widget
    try:
        panel.eventFilter(
            widget,
            _key_event(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Right,
                auto_repeat=True,
            ),
        )
        assert panel.fast_paths == [paths[1]]

        panel.stop_key_navigation_playback()
        panel.stop_key_navigation_playback()

        assert panel._key_navigation_playback_active is False
        assert panel.full_paths == []
    finally:
        panel.close()
