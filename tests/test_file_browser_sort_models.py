"""列表和缩略图共享排序语义，重排不丢缓存或选中状态。"""

import os

import pytest
from PyQt6.QtCore import QItemSelectionModel, QPersistentModelIndex, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from app_common.file_browser import _models
from app_common.file_browser._browser_core import (
    _FILE_TABLE_HEADERS,
    _MetaBurstGroupRole,
    _MetaRatingRole,
    _TREE_COL_BURST,
    _TREE_COL_ISO,
    _TREE_COL_NAME,
    _TREE_COL_SEQ,
    _TREE_COL_STAR,
    _ThumbPixmapRole,
    _UserRole,
)
from app_common.file_browser._models import (
    FileTableModel,
    FileTableSortProxyModel,
    ThumbnailListModel,
    file_sort_key,
)


_APP = QApplication.instance() or QApplication([])


def _rebuild(model, paths, metadata=None):
    model.rebuild(paths, meta_cache=metadata or {}, tooltip_fn=None, mismatch_fn=None)


@pytest.mark.parametrize("column", range(len(_FILE_TABLE_HEADERS) + 4))
@pytest.mark.parametrize("descending", [False, True])
def test_cached_sort_matches_table_for_all_columns_and_ties(column, descending):
    paths = [os.path.normpath(path) for path in (
        "photos/z.jpg", "photos/z/同名.jpg", "photos/a/同名.jpg", "photos/B.jpg", "photos/a.jpg",
    )]
    rich_meta = {
        "XMP-superpicky:bird_species_cn": "白鹭",
        "XMP-dc:Description": "岸边",
        "XMP-dc:Subject": ["鸟", "湿地"],
        "XMP-xmp:Rating": 3,
        "report.shutter_speed": "0.0005",
        "XMP-superpicky:aperture": "5.6",
        "EXIF:ISO": "800",
        "FocalLength": "600",
        "XMP-tiff:Model": "Alpha 1",
        "XMP-aux:LensModel": "FE 600mm F4 GM OSS",
        "EXIF:DateTimeOriginal": "2026:02:16 16:23:00",
        "report.adj_sharpness": 0.96,
        "XMP-superpicky:adj_topiq": "0.83",
        "focus_status": "BEST",
        "burst_id": 2,
        "burst_position": 3,
        "bird_sharpness_verdict": "sharp",
        "bird_sharpness_head_sigma": 0.6,
        "video_info": {"duration": 12.5, "width": 1920, "height": 1080, "fps": 25.0,
                       "video_codec": "H264"},
    }
    metadata = {
        paths[0]: rich_meta,
        paths[1]: rich_meta,
        paths[2]: rich_meta,
        paths[3]: {"XMP-xmp:Rating": -1, "EXIF:ISO": "100", "burst_id": 1,
                   "bird_sharpness_verdict": "soft"},
    }
    model = FileTableModel()
    model._video_columns = True
    # 不同初始行序确保同值排序不再依赖各视图此前的排列。
    _rebuild(model, list(reversed(paths)), metadata)
    proxy = FileTableSortProxyModel()
    proxy.setSourceModel(model)
    order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
    proxy.sort(column, order)

    expected = sorted(paths, key=lambda path: file_sort_key(path, metadata.get(path), column),
                      reverse=descending)
    assert [proxy.index(row, 0).data(_UserRole) for row in range(proxy.rowCount())] == expected
    assert expected.index(paths[2]) < expected.index(paths[1]) if not descending else (
        expected.index(paths[1]) < expected.index(paths[2])
    )


def test_sort_retains_pick_numeric_burst_and_video_semantics():
    key = lambda meta, column: file_sort_key("photos/a.jpg", meta, column)[0]
    assert key({"rating": 5, "pick": 1}, _TREE_COL_STAR) == 10
    assert key({"XMP-xmp:Rating": -1}, _TREE_COL_STAR) == -1
    assert key({"rating": 3}, _TREE_COL_STAR) == 3
    assert key({"EXIF:ISO": "800"}, _TREE_COL_ISO) == (0, 800)
    assert key({}, _TREE_COL_ISO) == (1, "")
    assert key({"burst_id": 2, "burst_position": 3}, _TREE_COL_BURST) == (0, 2, 3, "a.jpg")
    assert key({"burst_id": 0}, _TREE_COL_BURST) == (1, 10**12, 10**12, "a.jpg")
    assert key({"video_info": {"width": 1920, "height": 1080}}, len(_FILE_TABLE_HEADERS) + 1) == 2073600
    assert key({}, len(_FILE_TABLE_HEADERS)) == -1
    paths = ["photos/z.jpg", "photos/a.jpg"]
    assert sorted(paths, key=lambda path: file_sort_key(path, {}, _TREE_COL_SEQ)) == paths


def test_sort_reads_only_selected_metadata_column_and_never_file_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("排序不应读取文件或解析其它列")

    for field in _models._SORT_METADATA_GETTERS:
        if field != "iso":
            monkeypatch.setitem(_models._SORT_METADATA_GETTERS, field, forbidden)
    monkeypatch.setattr(os.path, "isfile", forbidden)
    assert file_sort_key("photos/a.jpg", {"EXIF:ISO": 640}, _TREE_COL_ISO)[0] == (0, 640)
    assert file_sort_key("photos/a.jpg", {}, _TREE_COL_NAME)[0] == "a.jpg"


def test_thumbnail_reorder_preserves_pixmap_metadata_selection_and_burst_groups():
    paths = [os.path.normpath(f"photos/{name}.jpg") for name in "abcd"]
    metadata = {path: {"rating": index + 1, "burst_id": 1 if index < 2 else 2}
                for index, path in enumerate(paths)}
    model = ThumbnailListModel()
    _rebuild(model, paths, metadata)
    entries = {entry.path: entry for entry in model._entries}
    pixmap = QPixmap(8, 8)
    pixmap.fill(QColor("red"))
    model.set_pixmap_for_path(paths[1], pixmap, 512)
    assert model.data(model.index(0), _MetaBurstGroupRole) == (0, True, False)

    selection = QItemSelectionModel(model)
    selection.select(model.index(1), QItemSelectionModel.SelectionFlag.Select)
    selection.select(model.index(3), QItemSelectionModel.SelectionFlag.Select)
    selection.setCurrentIndex(model.index(1), QItemSelectionModel.SelectionFlag.NoUpdate)
    persistent = QPersistentModelIndex(model.index(1))
    signals = []
    model.layoutAboutToBeChanged.connect(lambda: signals.append("before"))
    model.layoutChanged.connect(lambda: signals.append("after"))
    model.modelReset.connect(lambda: signals.append("reset"))

    ordered = [paths[2], paths[3], paths[1], paths[0]]
    assert model.reorder_paths(ordered)
    assert signals == ["before", "after"]
    assert model.all_paths() == ordered
    assert all(model._entries[model.row_for_path(path)] is entries[path] for path in paths)
    assert persistent.row() == 2 and persistent.data(_UserRole) == paths[1]
    assert selection.currentIndex().data(_UserRole) == paths[1]
    assert {index.data(_UserRole) for index in selection.selectedIndexes()} == {paths[1], paths[3]}
    assert model.data(model.index(2), _MetaRatingRole) == 2
    assert model.data(model.index(2), _ThumbPixmapRole).cacheKey() == pixmap.cacheKey()
    assert model.has_current_pixmap(paths[1], 512)
    assert model.data(model.index(0), _MetaBurstGroupRole) == (0, True, False)
    assert model.data(model.index(2), _MetaBurstGroupRole) == (1, True, False)
    assert model.data(model.index(3), _MetaBurstGroupRole) == (1, False, True)
    signals.clear()
    assert not model.reorder_paths(ordered)
    assert signals == []


def test_thumbnail_incomplete_reorder_keeps_every_existing_entry():
    paths = [os.path.normpath(f"photos/{name}.jpg") for name in "abc"]
    model = ThumbnailListModel()
    _rebuild(model, paths)
    assert model.reorder_paths([paths[2], "photos/unknown.jpg", paths[2]])
    assert model.all_paths() == [paths[2], paths[0], paths[1]]
    assert not model.reorder_paths([])
