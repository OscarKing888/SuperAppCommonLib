from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from app_common.file_browser import _panel as panel_module
from app_common.file_browser._panel import FileListPanel


_APP = QApplication.instance() or QApplication([])


class _MetadataEmitter(QObject):
    metadata_batch_ready = pyqtSignal(dict)
    progress_updated = pyqtSignal(int, int)


@pytest.fixture
def panel(monkeypatch):
    # These tests create no thumbnail jobs; closing their panels must not shut
    # down a disk writer used by other tests in the same application instance.
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kwargs: None)
    widget = FileListPanel(create_filter_bar=False)
    yield widget
    widget._metadata_loader = None
    widget.close()


def _connect_metadata(emitter, panel) -> None:
    emitter.metadata_batch_ready.connect(
        panel._on_metadata_batch_ready, Qt.ConnectionType.QueuedConnection
    )
    emitter.progress_updated.connect(
        panel._on_metadata_progress, Qt.ConnectionType.QueuedConnection
    )


def test_metadata_bursts_coalesce_before_model_updates(panel, monkeypatch):
    updates = []
    monkeypatch.setattr(panel._file_table_model, "set_meta_for_paths", lambda items, **kw: updates.append(list(items)) or len(items))
    panel._view_mode = panel._MODE_LIST
    paths = [f"中文/photo-{i}.jpg" for i in range(24)]
    panel._begin_meta_apply_session(len(paths), ordered_paths=paths)
    for start in range(0, len(paths), 8):
        panel._enqueue_meta_apply({p: {"rating": 4} for p in paths[start:start + 8]})
    assert updates == []  # Incoming signals must yield before touching models.
    assert panel._meta_apply_timer.isActive()
    _APP.processEvents()
    assert len(updates) == 1
    assert [p for p, _ in updates[0]] == paths
    assert panel._meta_apply_index == len(paths)
    panel._stop_pending_meta_apply()


def test_directory_change_cancels_deferred_metadata_apply(panel, monkeypatch):
    updates = []
    monkeypatch.setattr(panel._file_table_model, "set_meta_for_paths", updates.append)
    panel._begin_meta_apply_session(1)
    panel._enqueue_meta_apply({"old/photo.jpg": {"rating": 5}})
    panel._stop_pending_meta_apply()
    _APP.processEvents()
    assert updates == []


@pytest.mark.parametrize("disconnect_old_loader", [False, True])
def test_queued_metadata_from_previous_directory_is_ignored(
    panel, monkeypatch, disconnect_old_loader
) -> None:
    old_loader = _MetadataEmitter()
    new_loader = _MetadataEmitter()
    panel._metadata_loader = old_loader
    panel._meta_apply_expected_total = 7
    queued: list[dict] = []
    progress: list[dict] = []
    monkeypatch.setattr(panel, "_enqueue_meta_apply", queued.append)
    monkeypatch.setattr(
        panel, "_show_meta_progress_status", lambda *_args, **kwargs: progress.append(kwargs)
    )
    _connect_metadata(old_loader, panel)

    old_loader.metadata_batch_ready.emit({"old/photo.jpg": {"rating": 5}})
    old_loader.progress_updated.emit(99, 100)
    if disconnect_old_loader:
        old_loader.metadata_batch_ready.disconnect(panel._on_metadata_batch_ready)
        old_loader.progress_updated.disconnect(panel._on_metadata_progress)
    panel._metadata_loader = new_loader
    _APP.processEvents()

    assert panel._meta_cache == {}
    assert panel._meta_apply_expected_total == 7
    assert queued == []
    assert progress == []


@pytest.mark.parametrize("direct_call", [False, True])
def test_current_metadata_and_direct_edits_still_apply(panel, monkeypatch, direct_call) -> None:
    loader = _MetadataEmitter()
    panel._metadata_loader = loader
    panel._meta_apply_expected_total = 0
    queued: list[dict] = []
    progress: list[dict] = []
    monkeypatch.setattr(panel, "_enqueue_meta_apply", queued.append)
    monkeypatch.setattr(
        panel, "_show_meta_progress_status", lambda *_args, **kwargs: progress.append(kwargs)
    )
    batch = {"current/photo.jpg": {"comment": "中文备注", "rating": 4}}
    if direct_call:
        panel._on_metadata_batch_ready(batch)
        panel._on_metadata_progress(1, 7)
    else:
        _connect_metadata(loader, panel)
        loader.metadata_batch_ready.emit(batch)
        loader.progress_updated.emit(1, 7)
        _APP.processEvents()

    assert panel._meta_cache == batch
    assert queued == [batch]
    assert panel._meta_apply_expected_total == 7
    assert len(progress) == 1
    assert progress[0]["worker_count"] == panel._metadata_loader_workers


def test_shutdown_stops_timers_and_rejects_queued_work(panel, monkeypatch) -> None:
    timer_names = (
        "_thumb_viewport_timer",
        "_thumb_apply_timer",
        "_persistent_thumb_cache_timer",
        "_meta_apply_timer",
        "_meta_filter_refresh_timer",
        "_tree_model_populate_timer",
        "_thumb_model_populate_timer",
        "_deferred_file_selected_timer",
        "_probe_heartbeat_timer",
    )
    timers = []
    for name in timer_names:
        timer = QTimer(panel)
        timer.start(60_000)
        setattr(panel, name, timer)
        timers.append(timer)
    panel._view_mode = panel._MODE_THUMB
    panel._current_dir = os.path.normpath("current")
    panel._deferred_file_selected_path = os.path.normpath("current/photo.jpg")
    panel._pending_directory_listing_result = ("current", [], {}, None, True, {})
    panel._thumb_pending_batch["current/photo.jpg"] = QImage(8, 8, QImage.Format.Format_RGB32)
    loader = _MetadataEmitter()
    panel._metadata_loader = loader
    _connect_metadata(loader, panel)
    selections = []
    panel.file_selected.connect(selections.append)

    def unexpected_worker(*_args, **_kwargs):
        raise AssertionError("a shutdown callback must not create a worker")

    for name in ("ThumbnailLoader", "MetadataLoader", "PersistentThumbCacheWorker", "PathLookupWorker"):
        monkeypatch.setattr(panel_module, name, unexpected_worker)
    loader.metadata_batch_ready.emit({"current/photo.jpg": {"rating": 5}})
    loader.progress_updated.emit(1, 10)
    QTimer.singleShot(0, panel._schedule_visible_thumbnail_update)
    QTimer.singleShot(0, lambda: panel._start_thumbnail_loader(["current/photo.jpg"]))
    QTimer.singleShot(0, lambda: panel._start_metadata_loader(["current/photo.jpg"]))
    QTimer.singleShot(0, lambda: panel._schedule_persistent_thumb_cache_build(["current/photo.jpg"]))
    QTimer.singleShot(0, lambda: panel._request_actual_path_lookup("current/photo.jpg"))
    QTimer.singleShot(0, lambda: panel._on_directory_scan_finished("current", [], {}, None))
    QTimer.singleShot(0, lambda: panel._on_thumbnail_ready(0, "current/photo.jpg", QImage()))
    QTimer.singleShot(0, panel._apply_pending_directory_listing_result)
    QTimer.singleShot(0, panel._commit_deferred_file_selected)
    panel._request_background_shutdown()
    _APP.processEvents()

    assert panel._background_shutdown_requested
    assert not panel._background_shutdown_started
    assert all(not timer.isActive() for timer in timers)
    assert panel._meta_cache == {}
    assert panel._thumb_pending_batch == {}
    assert panel._pending_directory_listing_result is None
    assert selections == []


def test_shutdown_request_preserves_final_cleanup_once(panel, monkeypatch) -> None:
    waits = []
    writer_shutdowns = []

    class _PendingWorker:
        def isRunning(self):
            return True

        def wait(self, timeout):
            waits.append(timeout)
            return True

    panel._pending_loaders = [_PendingWorker()]
    monkeypatch.setattr(
        panel_module, "_shutdown_thumb_disk_writer", lambda wait: writer_shutdowns.append(wait)
    )
    panel._request_background_shutdown()
    panel._request_background_shutdown()
    assert waits == []
    assert writer_shutdowns == []

    panel._shutdown_background_work()
    panel._shutdown_background_work()

    assert panel._background_shutdown_started
    assert waits == [2500]
    assert writer_shutdowns == [True]
