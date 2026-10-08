"""目录范围在普通扫描、报告兼容与媒体补充中保持一致。"""
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._directory_browser import DirectoryBrowserWidget
from app_common.file_browser._workers import DirectoryScanWorker
from app_common.report_db import ReportDB


_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("recursive", [False, True])
@pytest.mark.parametrize("report_mode", ["none", "rows", "empty"])
@pytest.mark.parametrize("selected_child", [False, True])
def test_scan_scope_including_report_repair_and_fallback(tmp_path, recursive, report_mode, selected_child):
    photos = ["根图.JPG", "今日/本层.ARW", "今日/移动.JPG", "今日/子目录/深层.HIF"]
    videos = ["根视频.MP4", "今日/本层视频.MOV", "今日/子目录/深层视频.mp4"]
    for relative in photos + videos + ["今日/._资源.JPG", "今日/.cache/隐藏.JPG"]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    before = None
    if report_mode != "none":
        db = ReportDB(str(tmp_path))
        try:
            if report_mode == "rows":
                for relative in photos:
                    path = Path(relative)
                    current = "旧目录/移动.JPG" if path.stem == "移动" else str(path.with_suffix(".xmp"))
                    db.insert_photo({
                        "filename": path.stem,
                        "original_path": "DCIM/" + path.name,
                        "current_path": current.replace("/", "\\"),
                        "bird_species_cn": "白鹭",
                    })
        finally:
            db.close()
        before = (tmp_path / ".superpicky/report.db").read_bytes()
    directory = tmp_path / "今日" if selected_child else tmp_path
    results = []
    worker = DirectoryScanWorker(
        str(directory), recursive, report_root=str(tmp_path) if before else None,
        use_report_db=report_mode != "none", include_videos=True,
    )
    worker.scan_finished.connect(lambda _path, files, *_: results.append(files))
    worker.run()
    expected = {
        str(tmp_path / relative) for relative in photos + videos
        if (directory in (tmp_path / relative).parents if recursive else (tmp_path / relative).parent == directory)
    }
    assert len(results) == 1
    assert set(results[0]) == expected
    if before:
        assert (tmp_path / ".superpicky/report.db").read_bytes() == before


def test_directory_checkbox_is_opt_in_and_emits_scope_only():
    default = DirectoryBrowserWidget()
    viewer = DirectoryBrowserWidget(include_subdirectories=True)
    try:
        assert default._include_subdirectories_checkbox is None
        checkbox = viewer._include_subdirectories_checkbox
        scopes, directories = [], []
        viewer.include_subdirectories_changed.connect(scopes.append)
        viewer.directory_selected.connect(directories.append)
        assert checkbox.isChecked()
        checkbox.click()
        checkbox.click()
        assert scopes == [False, True]
        assert directories == []
    finally:
        default.close()
        viewer.close()

