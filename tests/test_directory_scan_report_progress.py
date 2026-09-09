from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app_common.file_browser import _workers as workers


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    assert predicate(), "directory worker did not finish"


def test_report_matching_progress_is_separate_from_filesystem_scan(tmp_path, monkeypatch):
    photo = tmp_path / "照片.HIF"
    photo.write_bytes(b"scan only; no image decode")
    progress = []
    results = []
    done = []
    worker = workers.DirectoryScanWorker(str(tmp_path), True, use_report_db=True)

    def build_scope(files, selected_dir, *, should_cancel, progress_callback):
        assert files == [str(photo)]
        assert selected_dir == str(tmp_path)
        assert not should_cancel()
        progress_callback(1, 1, str(tmp_path))
        for _ in range(1000):
            progress_callback(1, 1, str(tmp_path))
        return {}, {}, {}

    monkeypatch.setattr(workers, "_build_report_scope_maps_for_files", build_scope)
    worker.scan_progress.connect(lambda *_: progress.append("scan"))
    worker.report_scope_progress.connect(lambda *args: progress.append(args))
    worker.scan_finished.connect(lambda *args: results.append(args))
    worker.finished.connect(lambda: done.append(True))
    try:
        worker.start()
        _wait_until(lambda: done)
        assert results[0][1] == [str(photo)]
        first_report = next(i for i, value in enumerate(progress) if isinstance(value, tuple))
        assert first_report > 0
        assert all(isinstance(value, tuple) for value in progress[first_report:])
        assert progress[first_report] == (str(tmp_path), 0, 1, str(tmp_path))
        assert progress[-1] == (str(tmp_path), 1, 1, str(tmp_path))
        assert len(progress[first_report:]) == 2
    finally:
        worker.requestInterruption()
        worker.wait(4000)


def test_cancel_during_report_matching_discards_partial_listing(tmp_path, monkeypatch):
    (tmp_path / "照片.HIF").write_bytes(b"scan only")
    entered = threading.Event()
    release = threading.Event()
    results = []
    warnings = []
    done = []
    worker = workers.DirectoryScanWorker(str(tmp_path), True, use_report_db=True)

    def build_scope(files, selected_dir, *, should_cancel, progress_callback):
        entered.set()
        assert release.wait(4)
        assert should_cancel()
        raise workers._ReportScopeBuildCancelled()

    monkeypatch.setattr(workers, "_build_report_scope_maps_for_files", build_scope)
    monkeypatch.setattr(workers._log, "warning", lambda *args: warnings.append(args))
    worker.scan_finished.connect(lambda *args: results.append(args))
    worker.finished.connect(lambda: done.append(True))
    try:
        worker.start()
        _wait_until(entered.is_set)
        worker.requestInterruption()
        release.set()
        _wait_until(lambda: done)
        assert results == []
        assert warnings == []
    finally:
        release.set()
        worker.requestInterruption()
        worker.wait(4000)
