"""列表与缩略图共享排序，切换或更新元数据时保持路径与选择状态。"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import (
    _TREE_COL_COMMENT,
    _TREE_COL_TAGS,
    _TREE_COL_NAME,
    _TREE_COL_STAR,
    _ThumbPixmapRole,
)
from app_common.file_browser._panel import FileListPanel


_APP = QApplication.instance() or QApplication([])
_DESCENDING = Qt.SortOrder.DescendingOrder
_ASCENDING = Qt.SortOrder.AscendingOrder


@pytest.fixture
def panel(tmp_path: Path, monkeypatch):
    # 在构造控件之前隔离配置和缓存；这里只测试排序，不启动图片解码。
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_file_writes_allowed", lambda *a, **k: True)
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kwargs: None)
    widget = FileListPanel()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def _load(
    panel: FileListPanel, tmp_path: Path, names: list[str], metadata: list[dict],
    *, thumbnail_mode: bool = False,
) -> list[str]:
    paths = [os.path.normpath(str(tmp_path / name)) for name in names]
    for path in paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch()
    if not thumbnail_mode:
        panel._btn_list.click()
    panel._current_dir = str(tmp_path)
    panel._loaded_directory_recursive = True
    panel._requested_directory_recursive = True
    panel._all_files = paths
    panel._meta_cache = dict(zip(paths, metadata))
    panel._rebuild_views()
    return paths


def _tree_paths(panel: FileListPanel) -> list[str]:
    model = panel._tree_widget.model()
    return [panel._tree_path_from_index(model.index(row, 0)) for row in range(model.rowCount())]


def _sort_header(panel: FileListPanel, column: int, order=_DESCENDING) -> None:
    panel._tree_widget.header().setSortIndicator(column, order)


@pytest.mark.parametrize("thumbnails_already_built", [False, True])
@pytest.mark.parametrize(
    ("column", "metadata", "expected_indices"),
    [
        (_TREE_COL_NAME, [{}, {}, {}], [0, 2, 1]),
        (_TREE_COL_STAR, [{"rating": 1}, {"rating": 5}, {"rating": 3}], [1, 2, 0]),
        (_TREE_COL_COMMENT, [{"comment": "b"}, {"comment": "a"}, {"comment": "c"}], [2, 0, 1]),
        (_TREE_COL_TAGS, [{"tags": ["b"]}, {"tags": ["a"]}, {"tags": ["c"]}], [2, 0, 1]),
    ],
    ids=["filename", "rating", "comment", "tags"],
)
def test_header_sort_survives_round_trip_between_views(
    panel, tmp_path, thumbnails_already_built, column, metadata, expected_indices,
) -> None:
    paths = _load(panel, tmp_path, ["zeta.jpg", "Alpha.jpg", "middle.jpg"], metadata)
    if thumbnails_already_built:
        panel._btn_thumb.click()
        panel._btn_list.click()
    _sort_header(panel, column)
    expected = [paths[index] for index in expected_indices]
    assert _tree_paths(panel) == expected

    panel._btn_thumb.click()
    assert panel._thumb_list_model.all_paths() == expected
    panel._btn_list.click()
    assert _tree_paths(panel) == expected


def test_thumbnail_order_survives_filter_clear_and_metadata_refresh(panel, tmp_path) -> None:
    paths = _load(panel, tmp_path, ["keep-low.jpg", "other.jpg", "keep-high.jpg"],
                  [{"rating": 1}, {"rating": 3}, {"rating": 4}])
    _sort_header(panel, _TREE_COL_STAR)
    panel._btn_thumb.click()
    panel._filter_edit.setText("keep")
    assert panel._thumb_list_model.all_paths() == [paths[2], paths[0]]
    panel._filter_edit.clear()
    assert panel._thumb_list_model.all_paths() == [paths[2], paths[1], paths[0]]

    assert panel.sync_metadata_edit_for_path(paths[0], meta_updates={"rating": 5})
    expected = [paths[0], paths[2], paths[1]]
    assert panel._thumb_list_model.all_paths() == expected
    panel._btn_list.click()
    assert _tree_paths(panel) == expected


def test_duplicate_names_and_equal_metadata_keep_identical_order_in_both_views(panel, tmp_path) -> None:
    _load(panel, tmp_path, ["z/same.jpg", "a/same.jpg", "m/same.jpg", "other.jpg"],
          [{"rating": 3}, {"rating": 3}, {"rating": 3}, {"rating": 1}])
    for column in (_TREE_COL_NAME, _TREE_COL_STAR):
        for order in (_ASCENDING, _DESCENDING):
            panel._btn_list.click()
            _sort_header(panel, column, order)
            expected = _tree_paths(panel)
            panel._btn_thumb.click()
            assert panel._thumb_list_model.all_paths() == expected
            panel._filter_edit.setText("same")
            assert panel._thumb_list_model.all_paths() == [path for path in expected if Path(path).name == "same.jpg"]
            panel._filter_edit.clear()
            assert panel._thumb_list_model.all_paths() == expected


def test_resort_keeps_multiselection_current_photo_and_decoded_thumbnails(panel, tmp_path) -> None:
    paths = _load(panel, tmp_path, ["a.jpg", "b.jpg", "c.jpg"],
                  [{"rating": 1}, {"rating": 3}, {"rating": 2}])
    panel._btn_thumb.click()
    for path, color in zip(paths, ("red", "green", "blue")):
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(color))
        panel._thumb_list_model.set_pixmap_for_path(path, pixmap, 128)
    original_pixmaps = {
        path: panel._thumb_index_for_path(path).data(_ThumbPixmapRole).cacheKey()
        for path in paths
    }
    panel.set_pending_selection([paths[0], paths[2]], current_path=paths[2])
    panel._range_mark_start_path = paths[0]
    panel._update_selection_status()
    assert set(panel._thumb_selected_paths()) == {paths[0], paths[2]}

    _sort_header(panel, _TREE_COL_STAR)
    assert panel._thumb_list_model.all_paths() == [paths[1], paths[2], paths[0]]
    assert set(panel._thumb_selected_paths()) == {paths[0], paths[2]}
    assert panel._thumb_path_from_index(panel._list_widget.currentIndex()) == paths[2]
    assert "当前 2/3" in panel._selection_status_label.text()
    assert "区间起点 3" in panel._selection_status_label.text()
    assert {
        path: panel._thumb_index_for_path(path).data(_ThumbPixmapRole).cacheKey()
        for path in paths
    } == original_pixmaps
    panel._btn_list.click()
    assert set(panel._tree_selected_paths()) == {paths[0], paths[2]}
    assert panel._tree_path_from_index(panel._tree_widget.currentIndex()) == paths[2]


def test_sort_change_during_thumbnail_population_orders_all_later_batches(panel, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(panel_module, "_THUMB_MODEL_APPEND_BATCH_SIZE", 2)
    paths = _load(panel, tmp_path, [f"photo-{i}.jpg" for i in range(6)],
                  [{"rating": value} for value in (2, 5, 1, 4, 0, 3)], thumbnail_mode=True)
    assert panel._thumb_list_model.rowCount() == 2
    assert panel._thumb_model_dirty
    assert panel._tree_row_count() == 0
    _sort_header(panel, _TREE_COL_STAR)

    # 驱动实际的分批填充定时器，确保中途修改排序后后续批次没有重复或遗漏。
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.001)
        if not panel._thumb_model_dirty:
            break
    assert not panel._thumb_model_dirty
    expected = [paths[index] for index in (1, 3, 5, 0, 2, 4)]
    assert panel._thumb_list_model.all_paths() == expected
    panel._btn_list.click()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.001)
        if not panel._tree_view_dirty:
            break
    assert not panel._tree_view_dirty
    assert _tree_paths(panel) == expected


def test_async_metadata_finish_sorts_thumbnails_when_hidden_table_is_empty(panel, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(panel_module, "_META_APPLY_BATCH_SIZE", 2)
    paths = _load(panel, tmp_path, ["a.jpg", "b.jpg", "c.jpg"], [{}, {}, {}], thumbnail_mode=True)
    assert panel._tree_row_count() == 0
    _sort_header(panel, _TREE_COL_STAR)
    panel._begin_meta_apply_session(len(paths), ordered_paths=paths)
    panel._on_metadata_batch_ready({path: {"rating": rating} for path, rating in zip(paths, (4, 1, 5))})
    panel._on_metadata_loader_finished()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.001)
        if panel._meta_apply_total == 0:
            break
    assert panel._meta_apply_total == 0
    expected = [paths[2], paths[0], paths[1]]
    assert panel._thumb_list_model.all_paths() == expected
    assert panel._tree_row_count() == 0
    panel._btn_list.click()
    assert _tree_paths(panel) == expected
