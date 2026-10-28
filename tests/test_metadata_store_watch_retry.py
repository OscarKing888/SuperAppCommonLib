"""A consumed change notification must survive a temporary source-read failure."""
from concurrent.futures import Future
from types import SimpleNamespace
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from app_common.exif_io.metadata_store import get_store
from app_common.file_browser import _metadata_watch, _workers
from app_common.file_browser._metadata_memory import MetadataMemoryCache

_APP = QApplication.instance() or QApplication([])


class ControlledPool:
    def submit_action(self, action, **kwargs):
        self.action = action
        self.future = Future()
        return self.future

    def finish(self):
        try:
            self.future.set_result(self.action.execute())
        except Exception as error:
            self.future.set_exception(error)


class Panel(QObject):
    def __init__(self, directory, path):
        super().__init__()
        self._current_dir = directory
        self._all_files = [path]
        self._report_row_by_path = {}
        self._meta_proxy = object()
        self._metadata_memory_cache = MetadataMemoryCache()
        self._background_shutdown_requested = False
        self.pool = ControlledPool()
        self.batches = []

    def metadata_work_pool(self):
        return self.pool

    def _apply_store_metadata_snapshot(self, batch):
        self.batches.append(batch)

    def _update_metadata_sync_status(self, status):
        pass


def test_failed_refresh_retries_without_new_event_and_stops_after_success(tmp_path, monkeypatch):
    source = tmp_path / "白鹭.jpg"
    source.write_bytes(b"source")
    path = str(source)
    panel = Panel(str(tmp_path), path)
    watch = _metadata_watch.MetadataStoreWatch(panel)
    watch._timer.stop()
    store = get_store()
    store.refresh_scope(str(tmp_path)).result(5)
    now = [100.0]
    monkeypatch.setattr(_metadata_watch, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls = []

    def read(self, paths, *, failed_paths, **kwargs):
        calls.append(tuple(paths))
        if len(calls) == 1:
            failed_paths.update(paths)
            return {path: {"SourceFile": path}}
        return {path: {"SourceFile": path, "XMP-dc:Title": "恢复后的白鹭"}}

    monkeypatch.setattr(_workers.MetadataLoader, "_read_metadata_batch_uncached", read)
    try:
        watch.poll()
        panel.pool.finish()
        watch.poll()
        seq = watch._seq
        panel.pool.finish()  # The retry is backed off; polling still remains cheap.
        assert len(calls) == 1
        now[0] += 1
        watch.poll()
        panel.pool.finish()
        watch.poll()
        assert len(calls) == 2
        assert any(batch.get(path, {}).get("title") == "恢复后的白鹭" for batch in panel.batches)
        assert watch._seq >= seq
        for _ in range(3):
            panel.pool.finish()
            now[0] += 60
            watch.poll()
        assert len(calls) == 2
    finally:
        watch.request_shutdown()


def test_failed_refresh_is_discarded_after_directory_change_and_shutdown(tmp_path, monkeypatch):
    source = tmp_path / "白鹭.jpg"
    source.write_bytes(b"source")
    path = str(source)
    panel = Panel(str(tmp_path), path)
    watch = _metadata_watch.MetadataStoreWatch(panel)
    watch._timer.stop()
    get_store().refresh_scope(str(tmp_path)).result(5)
    calls = []

    def failed(self, paths, *, failed_paths, **kwargs):
        calls.append(tuple(paths))
        failed_paths.update(paths)
        return {}

    monkeypatch.setattr(_workers.MetadataLoader, "_read_metadata_batch_uncached", failed)
    try:
        watch.poll()
        panel.pool.finish()
        panel._current_dir = str(tmp_path / "other")
        panel._all_files = []
        watch.poll()
        panel.pool.finish()
        watch.request_shutdown()
        watch.poll()
        assert calls == [(path,)]
        assert not panel.batches
    finally:
        watch.request_shutdown()
