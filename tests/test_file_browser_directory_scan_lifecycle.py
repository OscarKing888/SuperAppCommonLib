from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._workers import DirectoryScanWorker


_APP = QApplication.instance() or QApplication([])


class _ControlledScan(DirectoryScanWorker):
    """Emit a real queued result separately from native thread completion."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_event = threading.Event()
        self.send_result = threading.Event()
        self.result_emitted = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.started_event.set()
        if self.send_result.wait(3):
            # An already queued signal can still arrive after disconnect.
            self.scan_progress.emit(self._path, 99, 1, self._path)
            self.report_scope_progress.emit(self._path, 99, 100, self._path)
            self.scan_finished.emit(self._path, [self._path + "/photo.jpg"], {}, None, {})
            self.result_emitted.set()
        self.release.wait(3)


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    assert predicate(), "queued scan lifecycle did not complete"


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(panel_module, "DirectoryScanWorker", _ControlledScan)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **kwargs: None)
    widget = panel_module.FileListPanel(create_filter_bar=False)
    widget._use_report_db = False
    widget._use_preview_cache = False
    monkeypatch.setattr(widget, "_rebuild_views", lambda: None)
    yield widget
    workers = tuple(widget._directory_scan_workers)
    widget._request_background_shutdown()
    for worker in workers:
        worker.send_result.set()
        worker.release.set()
        assert worker.wait(3000)
    _wait_until(lambda: not widget.has_pending_directory_scans())
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def _start_scan(panel, directory):
    panel.load_directory(str(directory), force_reload=True)
    worker = panel._directory_scan_worker
    assert worker.started_event.wait(1)
    return worker


def test_scan_result_retains_owner_until_actual_finished_slot(panel, tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(panel, "_apply_directory_listing_result", lambda *args, **kwargs: applied.append(args))
    worker = _start_scan(panel, tmp_path)
    worker.send_result.set()
    assert worker.result_emitted.wait(1)
    _wait_until(lambda: bool(applied))

    assert worker.isRunning()
    assert panel._directory_scan_worker is worker
    assert panel.has_pending_directory_scans()
    worker.release.set()
    assert worker.wait(1000)
    assert panel._directory_scan_worker is worker
    assert panel.has_pending_directory_scans()
    _wait_until(lambda: not panel.has_pending_directory_scans())
    assert panel._directory_scan_worker is None


def test_same_path_queued_old_result_and_finished_do_not_steal_new_scan(panel, tmp_path, monkeypatch):
    applied = []
    progress = []
    monkeypatch.setattr(panel, "_apply_directory_listing_result", lambda *args, **kwargs: applied.append(args))
    monkeypatch.setattr(panel, "_show_meta_progress_status", lambda *args, **kwargs: progress.append(args))
    first = _start_scan(panel, tmp_path / "A")
    first.send_result.set()
    assert first.result_emitted.wait(1)

    second = _start_scan(panel, tmp_path / "B")
    latest = _start_scan(panel, tmp_path / "A")
    assert first.isInterruptionRequested()
    assert second.isInterruptionRequested()
    assert len(panel._directory_scan_workers) == 3
    progress.clear()
    _APP.processEvents()
    assert applied == []
    assert progress == []
    assert panel._directory_scan_worker is latest

    first.release.set()
    assert first.wait(1000)
    _wait_until(lambda: first not in panel._directory_scan_workers)
    assert panel._directory_scan_worker is latest
    latest.send_result.set()
    assert latest.result_emitted.wait(1)
    _wait_until(lambda: bool(applied))
    assert len(applied) == 1
    assert applied[0][0] == str(tmp_path / "A")
    assert any("正在匹配报告" in str(args) for args in progress)


def test_shutdown_retains_canceled_scans_and_rejects_late_results(panel, tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(panel, "_apply_directory_listing_result", lambda *args, **kwargs: applied.append(args))
    first = _start_scan(panel, tmp_path / "A")
    latest = _start_scan(panel, tmp_path / "B")
    started = time.monotonic()
    panel._request_background_shutdown()
    assert time.monotonic() - started < 0.4
    assert panel._directory_scan_worker is None
    assert panel.has_pending_directory_scans()
    for worker in (first, latest):
        assert worker.isInterruptionRequested()
        worker.send_result.set()
        assert worker.result_emitted.wait(1)
    _APP.processEvents()
    assert applied == []
    assert panel.has_pending_directory_scans()

