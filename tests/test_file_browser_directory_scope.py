"""Directory scope stays consistent for filesystem and explicit report callers."""
from pathlib import Path
from contextlib import closing
import sqlite3

import pytest
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._directory_browser import DirectoryBrowserWidget
from app_common.file_browser._workers import DirectoryScanWorker
from app_common.report_db import ReportDB


_APP = QApplication.instance() or QApplication([])


def _report_paths(path):
    # This branch retains schema maintenance on open; verify photo paths, not DB bytes.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute(
            "SELECT filename, original_path, current_path, bird_species_cn FROM photos ORDER BY filename"
        ).fetchall()


@pytest.mark.parametrize("recursive", [False, True])
@pytest.mark.parametrize("report_mode", ["none", "rows", "empty"])
@pytest.mark.parametrize("selected_child", [False, True])
def test_scan_scope_including_report_repair_and_fallback(tmp_path, recursive, report_mode, selected_child):
    photos = ["根图.JPG", "今日/本层.ARW", "今日/移动.JPG", "今日/子目录/深层.HIF"]
    for relative in photos + ["今日/.cache/隐藏.JPG"]:
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
        before = _report_paths(tmp_path / ".superpicky/report.db")
    directory = tmp_path / "今日" if selected_child else tmp_path
    results = []
    worker = DirectoryScanWorker(
        str(directory), recursive, report_root=str(tmp_path) if before is not None else None,
        use_report_db=report_mode != "none",
    )
    worker.scan_finished.connect(lambda _path, files, *_: results.append(files))
    worker.run()
    expected = {
        str(tmp_path / relative) for relative in photos
        if (directory in (tmp_path / relative).parents if recursive else (tmp_path / relative).parent == directory)
    }
    assert len(results) == 1
    assert set(results[0]) == expected
    if before is not None:
        assert _report_paths(tmp_path / ".superpicky/report.db") == before


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
        default.deleteLater()
        viewer.deleteLater()
