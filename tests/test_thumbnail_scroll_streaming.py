"""Continuous scroll scheduling and viewport-bounded display image maintenance."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, QRect, QTimer, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._models import ThumbnailListModel, ThumbViewportEntry, ThumbViewportRange
from app_common.file_browser._panel import FileListPanel
from app_common.file_browser._thumbnail import ThumbnailMemoryCache


_APP = QApplication.instance() or QApplication([])


class _ScrollProbe(QObject):
    _MODE_THUMB = 1
    _MODE_LIST = 0
    _view_mode = _MODE_THUMB
    _background_shutdown_requested = False
    _thumb_viewport_timer = None
    _ensure_thumb_viewport_timer = FileListPanel._ensure_thumb_viewport_timer
    _schedule_visible_thumbnail_update = FileListPanel._schedule_visible_thumbnail_update

    def __init__(self):
        super().__init__()
        self.position = 0
        self.seen = []

    def _thumb_profile_add(self, *_args):
        pass

    def _update_visible_thumbnail_range(self):
        self.seen.append(self.position)


def test_continuous_scroll_updates_before_release_and_keeps_latest_position():
    probe = _ScrollProbe()
    ticker = QTimer()
    ticker.setTimerType(Qt.TimerType.PreciseTimer)
    updates_before_stop = []

    def scroll():
        probe.position += 1
        probe._schedule_visible_thumbnail_update()
        if probe.position == 40:
            updates_before_stop.extend(probe.seen)
            ticker.stop()

    ticker.timeout.connect(scroll)
    ticker.start(5)
    try:
        for _ in range(200):
            QTest.qWait(5)
            if not ticker.isActive():
                break
        assert probe.position == 40
        QTest.qWait(100)
        assert len(updates_before_stop) >= 3
        assert probe.seen[-1] == 40
        assert len(probe.seen) < probe.position  # bursts are still coalesced
        before_idle = list(probe.seen)
        QTest.qWait(80)
        assert probe.seen == before_idle
    finally:
        ticker.stop()
        if probe._thumb_viewport_timer is not None:
            probe._thumb_viewport_timer.stop()


@pytest.mark.parametrize("state", ["list", "shutdown"])
def test_scroll_does_not_schedule_in_list_mode_or_after_shutdown(state):
    probe = _ScrollProbe()
    if state == "list":
        probe._view_mode = probe._MODE_LIST
    else:
        probe._background_shutdown_requested = True
    probe._schedule_visible_thumbnail_update()
    assert probe._thumb_viewport_timer is None


def _paths(count):
    return [os.path.normpath(f"/photos/IMG_{index:06d}.jpg") for index in range(count)]


def _populate(model, paths):
    model.append_paths(paths, meta_cache={}, tooltip_fn=None, mismatch_fn=None)


def _pixmap():
    pixmap = QPixmap(16, 12)
    pixmap.fill(Qt.GlobalColor.green)
    return pixmap


def _resident_paths(model):
    return {model.path_for_row(row) for row in model.pixmap_rows()}


def test_pixmap_tracking_survives_sort_filter_insert_refresh_and_reset():
    model = ThumbnailListModel()
    paths = _paths(12)
    _populate(model, paths[::2])
    model.set_pixmap_for_path(paths[2], _pixmap(), 128)
    model.set_pixmaps_for_paths([(paths[4], _pixmap(), 256), (paths[8], _pixmap(), 128)])
    assert _resident_paths(model) == {paths[2], paths[4], paths[8]}

    model.insert_paths_in_order(
        paths[1::2], rank={path: row for row, path in enumerate(paths)},
        meta_cache={}, tooltip_fn=None, mismatch_fn=None,
    )
    model.reorder_paths(list(reversed(paths)))
    assert _resident_paths(model) == {paths[2], paths[4], paths[8]}
    assert model.has_current_pixmap(paths[4], 256)
    assert not model.has_current_pixmap(paths[4], 128)

    model.remove_paths_not_in([path for path in paths if path != paths[4]])
    assert _resident_paths(model) == {paths[2], paths[8]}
    model.clear_pixmap_for_path(paths[2])  # explicit thumbnail refresh
    model.set_pixmaps_for_paths([(paths[8], QPixmap(), 128), (paths[6], _pixmap(), 512)])
    assert _resident_paths(model) == {paths[6]}
    assert model.clear_all_pixmaps() == 1  # changing thumbnail tier
    assert model.pixmap_rows() == ()
    assert model.clear_all_pixmaps() == 0
    model.set_pixmap_for_path(paths[6], _pixmap(), 128)
    model.rebuild(paths[:2], meta_cache={}, tooltip_fn=None, mismatch_fn=None)
    assert model.pixmap_rows() == ()
    model.set_pixmap_for_path(paths[0], _pixmap(), 128)
    model.clear()
    assert model.pixmap_rows() == ()
    _populate(model, paths[:2])
    assert model.pixmap_rows() == ()


class _NoFullScan(list):
    def __iter__(self):
        raise AssertionError("viewport maintenance scanned the whole model")


def test_large_directory_maintenance_only_visits_visible_and_resident_images(monkeypatch):
    model = ThumbnailListModel()
    paths = _paths(20000)
    _populate(model, paths)
    visible_rows = range(100, 112)
    near_row, far_row = 96, 15000
    model.set_pixmaps_for_paths([(paths[near_row], _pixmap(), 128), (paths[far_row], _pixmap(), 128)])
    cache = ThumbnailMemoryCache(max_bytes=1024 * 1024)
    image = QImage(16, 12, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.green)
    for row in (*visible_rows, far_row):
        cache.put(paths[row], 128, image)
    visible = ThumbViewportRange(
        128, visible_rows.start, visible_rows.stop - 1, 128, 180, len(paths),
        tuple(ThumbViewportEntry(paths[row], row) for row in visible_rows),
    )
    panel = SimpleNamespace(
        _thumb_list_model=model, _thumb_size=128, _thumb_memory_cache=cache,
        _thumb_row_count=model.rowCount, _meta_cache={},
        _apply_thumb_meta_to_path=lambda *_args: None,
        _list_widget=SimpleNamespace(viewport=lambda: SimpleNamespace(rect=lambda: QRect(0, 0, 512, 600))),
    )
    checked_rows = []
    clear = model.clear_pixmap_for_row

    def counted_clear(row):
        checked_rows.append(row)
        return clear(row)

    monkeypatch.setattr(model, "clear_pixmap_for_row", counted_clear)
    monkeypatch.setattr(model, "_entries", _NoFullScan(model._entries))
    assert FileListPanel._evict_offscreen_item_pixmaps(panel, visible) == 1
    assert checked_rows == [far_row]
    assert FileListPanel._apply_cached_thumbs_to_items(panel, visible) == len(visible_rows)
    assert _resident_paths(model) == {paths[row] for row in (*visible_rows, near_row)}
    assert cache.get(paths[far_row], 128) is not None  # eviction retains decoded cache
    assert FileListPanel._apply_cached_thumbs_to_items(panel, visible) == 0


def test_cache_fill_keeps_exact_tier_and_pre_layout_fallback():
    model = ThumbnailListModel()
    paths = _paths(2)
    _populate(model, paths)
    cache = ThumbnailMemoryCache(max_bytes=1024 * 1024)
    image = QImage(16, 12, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    cache.put(paths[0], 128, image)
    cache.put(paths[1], 256, image)
    panel = SimpleNamespace(
        _thumb_list_model=model, _thumb_size=128, _thumb_memory_cache=cache,
        _thumb_row_count=model.rowCount, _meta_cache={},
        _apply_thumb_meta_to_path=lambda *_args: None,
        _build_visible_thumbnail_data_source=lambda: None,
    )
    assert FileListPanel._apply_cached_thumbs_to_items(panel) == 1
    assert _resident_paths(model) == {paths[0]}
