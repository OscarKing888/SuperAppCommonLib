"""Poll durable metadata changes in the browser-owned pool, never on Qt."""
from __future__ import annotations

import logging
import os

try:
    from PyQt6.QtCore import QObject, QTimer
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer

from ._work_action import CallableAction
from ._work_policy import WorkKind


class MetadataStoreWatch(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._seq = 0
        self._future = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    def request_shutdown(self):
        self._closed = True
        self._timer.stop()
        # The browser owns and drains its pool, including an in-flight poll.

    def poll(self):
        if self._closed:
            return
        panel = self._panel
        if self._future is not None:
            if not self._future.done():
                return
            future, self._future = self._future, None
            try:
                directory, seq, batch, status = future.result()
            except Exception:
                logging.getLogger(__name__).exception("Metadata change poll failed")
            else:
                self._seq = seq
                if directory == panel._current_dir:
                    panel._apply_store_metadata_snapshot(batch)
                panel._update_metadata_sync_status(status)
        if not panel._current_dir or panel._background_shutdown_requested:
            return
        # Copy the current scope on Qt; all SQL and source reconciliation occur
        # inside the action. A directory change cannot mutate that scope.
        paths = tuple(panel._all_files)
        directory, seq = panel._current_dir, self._seq
        rows = dict(panel._report_row_by_path)
        proxy = panel._meta_proxy
        memory_cache = panel._metadata_memory_cache
        self._future = panel.metadata_work_pool().submit_action(
            CallableAction(self._read, directory, seq, paths, rows, proxy, memory_cache,
                           cancelled=lambda: self._closed),
            kind=WorkKind.METADATA, priority=20,
        )

    def _read(self, directory, seq, paths, rows, proxy, memory_cache):
        from app_common.exif_io.metadata_store import get_store, path_key, resource_key
        from ._browser_core import _SUPERBIRDSTAMP_BROWSER_METADATA_TAGS
        from ._workers import MetadataLoader

        store = get_store()
        changes = store.changes_since(seq)
        events = changes["changes"]
        resources = {event["resource"] for event in events if event["resource"]}
        refresh = [event["path"] for event in events if event["kind"] == "refresh"]
        affected = set(paths) if changes["reset"] else set()
        for path in paths if resources or refresh else ():
            key = path_key(path)
            if resource_key(path) in resources or any(
                key == scope or key.startswith(scope.rstrip(os.sep) + os.sep) for scope in refresh
            ):
                affected.add(path)
        aliases = getattr(store, "affected_paths", None)
        if resources and callable(aliases):
            affected.update(aliases(paths, resources))
        batch = {}
        if affected and not self._closed:
            raw = store.read_many(affected, profile="browser")
            missing = [path for path in affected if path not in raw or raw[path].get("_metadata_complete") is False]
            if missing and not self._closed:
                # A refresh can intentionally remove its snapshot. Rehydrate
                # once in this owned worker, with the normal revision guard.
                loader = MetadataLoader(missing, meta_proxy=proxy, report_rows_by_path=rows,
                                        selected_dir=directory, memory_cache=memory_cache,
                                        metadata_tags=_SUPERBIRDSTAMP_BROWSER_METADATA_TAGS)
                raw.update(loader._read_metadata_batch(missing))
            batch = {path: MetadataLoader._parse_rec(record) for path, record in raw.items()}
        return directory, changes["seq"], batch, store.pending_status()
