from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module, _permissions
from app_common.file_browser._panel import FileListPanel
from app_common.file_browser._work_action import CallableAction
from app_common.file_browser._work_policy import WorkKind
from app_common.file_browser._workers import MetadataLoader


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        threading.Event().wait(.001)
    assert predicate()


class _Panel(FileListPanel):
    create_filter_bar = False
    use_unified_worker_pool = True


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS",
                        superviewer_user_options.normalize_user_options(None))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("SuperViewer_THUMB_WORKERS", "6")
    monkeypatch.setattr(panel_module, "_metadata_loader_worker_count", lambda: 2)
    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "1")
    for name, value in vars(_permissions).copy().items():
        if name.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, name, value)
    widget = _Panel()
    widget._use_preview_cache = False
    if widget._thumb_viewport_timer is not None:
        widget._thumb_viewport_timer.stop()
    try:
        yield widget
    finally:
        widget._request_background_shutdown()
        widget._stop_all_loaders()
        widget._stop_persistent_thumb_cache_worker()
        widget._request_worker_pool_shutdown()
        pool = widget._browser_work_pool
        _wait_until(lambda: not widget.has_pending_pool_work())
        widget.close()
        widget.deleteLater()
        _APP.processEvents()
        if pool is not None:
            assert pool.is_finished()


def test_cached_thumbnail_view_lends_all_idle_workers_to_metadata(panel):
    pool = panel._get_browser_work_pool()
    gate = threading.Event()
    all_started = threading.Event()
    lock = threading.Lock()
    started = 0

    def read():
        nonlocal started
        with lock:
            started += 1
            if started == pool.max_workers:
                all_started.set()
        assert gate.wait(3)

    futures = []
    try:
        for _ in range(pool.max_workers):
            futures.append(pool.submit_action(CallableAction(read), kind=WorkKind.METADATA))
        assert all_started.wait(1), pool.snapshot()
    finally:
        gate.set()
        for future in futures:
            future.result(timeout=3)


def test_metadata_apply_budget_includes_model_and_signal_cost(panel, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    applied_table, applied_thumbs = [], []

    class Model:
        def __init__(self, output):
            self.output = output

        def set_meta_for_paths(self, items):
            # Deterministic model + synchronous dataChanged cost, 1 ms per row.
            clock.now += len(items) * .001
            self.output.extend(path for path, _meta in items)
            return len(items)

    monkeypatch.setattr(panel_module, "_time", SimpleNamespace(perf_counter=lambda: clock.now))
    monkeypatch.setattr(panel._file_table_model, "set_meta_for_paths", Model(applied_table).set_meta_for_paths)
    monkeypatch.setattr(panel._thumb_list_model, "set_meta_for_paths", Model(applied_thumbs).set_meta_for_paths)
    monkeypatch.setattr(panel, "_show_meta_progress_status", lambda *_a, **_k: None)
    items = [(f"photo-{i}.jpg", {"rating": 1}) for i in range(96)]
    panel._meta_apply_items = items
    panel._meta_apply_total = len(items)
    panel._meta_apply_loader_finished = False
    panel._ensure_meta_apply_timer()
    panel._apply_meta_batch_tick()
    assert 0 < panel._meta_apply_index < 64
    assert clock.now < .025
    first_tick = panel._meta_apply_index
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(panel._meta_apply_index))
    panel._meta_apply_timer.start(1)
    _wait_until(lambda: panel._meta_apply_index == len(items))
    assert heartbeat and first_tick <= heartbeat[0] < len(items)
    assert applied_table == applied_thumbs == [path for path, _ in items]


def test_empty_viewport_releases_reservation_before_metadata_progress(panel):
    panel._schedule_visible_thumbnail_update()
    pool = panel._get_browser_work_pool()
    gate = threading.Event()
    reserved_started = threading.Event()
    all_started = threading.Event()
    lock = threading.Lock()
    started = 0

    def read():
        nonlocal started
        with lock:
            started += 1
            if started == pool.metadata_workers:
                reserved_started.set()
            if started == pool.max_workers:
                all_started.set()
        assert gate.wait(3)

    futures = []
    try:
        for _ in range(pool.max_workers):
            futures.append(pool.submit_action(CallableAction(read), kind=WorkKind.METADATA))
        assert reserved_started.wait(1)
        assert pool.snapshot()["metadata_active"] == pool.metadata_workers
        # No read completes or emits progress. The viewport timer finds no work
        # and must release its reservation itself, waking all spare workers.
        _wait_until(all_started.is_set)
        assert pool.snapshot()["metadata_active"] == pool.max_workers
    finally:
        gate.set()
        for future in futures:
            future.result(timeout=3)


def test_idle_persistent_coordinator_does_not_reserve_thumbnail_capacity(panel, monkeypatch):
    monkeypatch.setattr(panel, "_persistent_thumb_cache_worker", SimpleNamespace(isRunning=lambda: True))
    panel._persistent_thumb_cache_done = panel._persistent_thumb_cache_total = 10
    panel._persistent_thumb_cache_pending_paths = ["photo.jpg"]
    panel._persistent_thumb_cache_base_dir = ""  # no cache scope, nothing can be queued
    assert not panel._thumbnail_work_active_or_pending()
    panel._persistent_thumb_cache_done = 9
    assert panel._thumbnail_work_active_or_pending()
    panel._persistent_thumb_cache_done = 10
    panel._persistent_thumb_cache_base_dir = "library"
    assert panel._thumbnail_work_active_or_pending()
    # Restore the QObject owner before fixture shutdown.
    monkeypatch.setattr(panel, "_persistent_thumb_cache_worker", None)


def test_metadata_start_skips_unused_focus_file_probes(panel, monkeypatch, tmp_path):
    paths = [os.path.normpath(str(tmp_path / f"photo-{i}.jpg")) for i in range(24)]
    gate = threading.Event()
    started = threading.Event()

    def unexpected_probes(_paths):
        raise AssertionError("disabled focus prefetch must not stat every source on the GUI thread")

    def read_chunk(_loader, chunk):
        started.set()
        assert gate.wait(3)
        return {path: {"rating": 1} for path in chunk}, {}, len(chunk)

    monkeypatch.setattr(panel, "_build_metadata_focus_source_paths", unexpected_probes)
    monkeypatch.setattr(MetadataLoader, "_read_parse_chunk", read_chunk)
    heartbeat = []
    try:
        panel._start_metadata_loader(paths)
        assert started.wait(1)
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        _wait_until(lambda: bool(heartbeat))
        assert not panel._meta_cache
    finally:
        gate.set()
    _wait_until(lambda: panel._metadata_loader is None)
    assert set(panel._meta_cache) == set(paths)
