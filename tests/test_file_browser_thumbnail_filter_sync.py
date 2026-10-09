"""元数据流式到达时，过滤刷新只原地增删缩略图行，不清空模型（避免列表闪烁）。"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module, _permissions
from app_common.file_browser._browser_core import _ThumbPixmapRole
from app_common.file_browser._models import ThumbnailListModel
from app_common.file_browser._panel import FileListPanel


_APP = QApplication.instance() or QApplication([])


def _pixmap() -> QPixmap:
    pixmap = QPixmap(8, 8)
    pixmap.fill()
    return pixmap


def _model_paths(model: ThumbnailListModel) -> list[str]:
    return [model.path_for_row(row) for row in range(model.rowCount())]


def test_insert_in_order_and_remove_keep_existing_entries() -> None:
    model = ThumbnailListModel()
    paths = [os.path.normpath(f"/photos/IMG_{index}.jpg") for index in range(8)]
    rank = {path: index for index, path in enumerate(paths)}
    model.insert_paths_in_order(paths[1::2], rank=rank, meta_cache={}, tooltip_fn=lambda path: path, mismatch_fn=lambda path: False)
    model.set_pixmap_for_path(paths[3], _pixmap(), 128)
    resets: list[str] = []
    model.modelReset.connect(lambda: resets.append("reset"))

    inserted = model.insert_paths_in_order(
        [paths[6], paths[0], paths[2]], rank=rank, meta_cache={}, tooltip_fn=lambda path: path, mismatch_fn=lambda path: False,
    )
    removed = model.remove_paths_not_in([path for path in paths if path != paths[5]])

    assert (inserted, removed, resets) == (3, 1, [])
    assert _model_paths(model) == [paths[0], paths[1], paths[2], paths[3], paths[6], paths[7]]
    assert model.row_for_path(paths[6]) == 4
    assert model.row_for_path(paths[5]) is None
    assert model.has_current_pixmap(paths[3], 128)


@pytest.fixture
def panel(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS", superviewer_user_options.normalize_user_options(None))
    for name, value in vars(_permissions).copy().items():
        if name.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, name, value)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kwargs: None)
    widget = FileListPanel()
    widget.resize(900, 700)
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def _finish_population(panel: FileListPanel) -> None:
    deadline = time.monotonic() + 5
    while panel._thumb_model_dirty and time.monotonic() < deadline:
        panel._populate_thumb_model_batch()
    assert not panel._thumb_model_dirty


def _load_thumbnails(panel: FileListPanel, tmp_path: Path, count: int) -> list[str]:
    paths = [os.path.normpath(str(tmp_path / f"IMG_{index:04d}.jpg")) for index in range(count)]
    panel._current_dir = str(tmp_path)
    panel._loaded_directory_recursive = True
    panel._all_files = list(paths)
    panel._rebuild_views()
    _finish_population(panel)
    _APP.processEvents()
    return paths


def test_streamed_metadata_filter_refresh_never_resets_thumbnail_model(panel, tmp_path) -> None:
    paths = _load_thumbnails(panel, tmp_path, 400)
    panel._filter_min_rating = 3
    panel._begin_meta_apply_session(len(paths), paths)
    first = {path: {"rating": 3} for path in paths[:120:2]}
    panel._meta_cache.update(first)
    panel._enqueue_meta_apply(first)
    panel._flush_meta_filter_refresh()
    _finish_population(panel)
    model = panel._thumb_list_model
    model.set_pixmap_for_path(paths[10], _pixmap(), panel._thumb_size)
    resets: list[str] = []
    model.modelReset.connect(lambda: resets.append("reset"))

    for start in range(120, 400, 40):
        batch = {path: {"rating": 3 if index % 2 == 0 else 1} for index, path in enumerate(paths[start:start + 40])}
        panel._meta_cache.update(batch)
        panel._enqueue_meta_apply(batch)
        panel._flush_meta_filter_refresh()
        # 刷新后视口顶端始终有已布局的缩略图，不会出现整屏空白。
        assert panel._list_widget.indexAt(QPoint(40, 40)).isValid()
        _finish_population(panel)

    assert resets == []
    assert _model_paths(model) == paths[::2]
    assert model.index_for_path(paths[10]).data(_ThumbPixmapRole) is not None


def test_cleared_filter_stops_streamed_metadata_refresh(panel, tmp_path, monkeypatch) -> None:
    paths = _load_thumbnails(panel, tmp_path, 20)
    panel._filter_pick = True
    panel._begin_meta_apply_session(len(paths), paths)
    assert panel._meta_apply_needs_filter
    panel._filter_pick = False
    calls: list[str] = []
    monkeypatch.setattr(panel, "_apply_filter", lambda: calls.append("filter"))

    panel._flush_meta_filter_refresh()

    assert calls == []
    assert not panel._meta_apply_needs_filter


def test_new_filter_during_metadata_session_is_applied(panel, tmp_path) -> None:
    paths = _load_thumbnails(panel, tmp_path, 20)
    panel._begin_meta_apply_session(len(paths), paths)
    assert not panel._meta_apply_needs_filter
    panel._filter_pick = True
    panel._meta_cache[paths[4]] = {"pick": 1}
    panel._schedule_meta_filter_refresh()
    assert panel._meta_filter_refresh_timer.isActive()
    panel._flush_meta_filter_refresh()
    assert _model_paths(panel._thumb_list_model) == [paths[4]]


def test_filter_refresh_continues_pending_population(panel, tmp_path) -> None:
    paths = [os.path.normpath(str(tmp_path / f"IMG_{index:04d}.jpg")) for index in range(1200)]
    panel._current_dir = str(tmp_path)
    panel._loaded_directory_recursive = True
    panel._all_files = paths
    panel._rebuild_views()
    model = panel._thumb_list_model
    assert 0 < model.rowCount() < len(paths)
    model.set_pixmap_for_path(paths[0], _pixmap(), panel._thumb_size)
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    panel._filter_pick = True
    panel._meta_cache.update({path: {"pick": 1} for path in paths[::2]})
    panel._apply_filter()
    _finish_population(panel)
    assert model.all_paths() == paths[::2]
    assert model.has_current_pixmap(paths[0], panel._thumb_size)
    assert not resets


def test_list_mode_filter_does_not_populate_thumbnails(panel, tmp_path, monkeypatch) -> None:
    paths = _load_thumbnails(panel, tmp_path, 20)
    panel._view_mode = panel._MODE_LIST
    calls = []
    monkeypatch.setattr(panel, "_populate_thumb_model_batch", lambda: calls.append(True))
    panel._filter_pick = True
    panel._meta_cache[paths[0]] = {"pick": 1}
    panel._apply_filter()
    assert panel._filtered_files == paths[:1]
    assert not calls
