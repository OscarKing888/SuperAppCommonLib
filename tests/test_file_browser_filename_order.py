import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._browser_core import _TREE_COL_NAME, _UserRole
from app_common.file_browser._models import (
    FileTableModel,
    FileTableSortProxyModel,
    ThumbnailListModel,
)
from app_common.file_browser._panel import FileListPanel
from app_common.file_browser._workers import DirectoryScanWorker
from app_common.report_db import ReportDB


_APP = QApplication.instance() or QApplication([])


def _scan(directory, *, include_videos, report_root=None):
    results = []
    worker = DirectoryScanWorker(
        str(directory),
        # 报告模式本身按子树列出照片，普通目录显式递归。
        recursive=report_root is None,
        report_root=str(report_root) if report_root else None,
        use_report_db=report_root is not None,
        include_videos=include_videos,
    )
    worker.scan_finished.connect(lambda _path, files, *_: results.append(files))
    worker.run()
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize("include_videos", [False, True])
@pytest.mark.parametrize("use_report", [False, True])
@pytest.mark.parametrize("selected_child", [False, True])
def test_camera_filename_order_across_classification_directories(
    tmp_path, include_videos, use_report, selected_child
):
    relative_paths = [
        "今日/z鸟种/DSC0001.ARW",
        "今日/m保留/dsc0002.HIF",
        "今日/a精选/DSC0003.JPG",
        "今日/a精选/DSC0004.MP4",
        "昨日/DSC0000.JPG",
    ]
    for relative in relative_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"directory scan only")

    db_path = tmp_path / ".superpicky" / "report.db"
    before = None
    if use_report:
        db = ReportDB(str(tmp_path))
        try:
            for relative in relative_paths:
                path = Path(relative)
                if path.suffix == ".MP4":
                    continue
                current = relative
                if path.stem == "DSC0001":
                    # 已归类的源图应修复旧路径，排序仍按相机文件名。
                    current = "今日/旧目录/DSC0001.ARW"
                elif path.stem == "DSC0003":
                    current = str(path.with_suffix(".xmp"))
                db.insert_photo({
                    "filename": path.stem,
                    "original_path": "DCIM/" + path.name,
                    "current_path": current.replace("/", "\\"),
                })
        finally:
            db.close()
        before = db_path.read_bytes()

    directory = tmp_path / "今日" if selected_child else tmp_path
    files = _scan(
        directory,
        include_videos=include_videos,
        report_root=tmp_path if use_report else None,
    )
    expected = [str(tmp_path / relative) for relative in relative_paths[:3]]
    if include_videos:
        expected.append(str(tmp_path / relative_paths[3]))
    if not selected_child:
        expected.insert(0, str(tmp_path / relative_paths[4]))
    assert files == expected
    if use_report:
        assert db_path.read_bytes() == before

    # 真实列表/缩略图模型必须保持同一默认顺序，包含筛选重建。
    panel = FileListPanel.__new__(FileListPanel)
    panel._all_files = files
    panel._meta_cache = {path: {"pick": 1} for path in files if "0002" not in path}
    panel._filter_edit = None
    panel._filter_pick = True
    panel._filter_reject = False
    panel._filter_min_rating = 0
    panel._filter_focus_status = ""
    filtered = panel._compute_filtered_files()
    assert filtered == [path for path in expected if "0002" not in path]

    for visible in (files, filtered):
        table = FileTableModel()
        thumbs = ThumbnailListModel()
        for model in (table, thumbs):
            model.rebuild(visible, meta_cache={}, tooltip_fn=None, mismatch_fn=None)
        proxy = FileTableSortProxyModel()
        proxy.setSourceModel(table)
        proxy.sort(_TREE_COL_NAME, Qt.SortOrder.AscendingOrder)
        assert [proxy.data(proxy.index(i, _TREE_COL_NAME), _UserRole)
                for i in range(proxy.rowCount())] == visible
        assert [thumbs.path_for_row(i) for i in range(thumbs.rowCount())] == visible
        proxy.sort(_TREE_COL_NAME, Qt.SortOrder.DescendingOrder)
        assert [proxy.data(proxy.index(i, _TREE_COL_NAME), _UserRole)
                for i in range(proxy.rowCount())] == list(reversed(visible))


@pytest.mark.parametrize("include_videos", [False, True])
def test_same_filename_uses_path_only_as_tiebreaker(tmp_path, include_videos):
    for relative in ("z鸟种/DSC0001.JPG", "a精选/DSC0002.JPG", "a精选/DSC0001.JPG"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    expected = [str(tmp_path / relative) for relative in (
        "a精选/DSC0001.JPG", "z鸟种/DSC0001.JPG", "a精选/DSC0002.JPG",
    )]
    assert _scan(tmp_path, include_videos=include_videos) == expected
