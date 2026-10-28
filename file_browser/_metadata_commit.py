"""Deliver metadata commit Futures on the owning Qt thread.

Submitting an edit never waits for SQLite or touches source files.  A successful
callback means the local transaction is durable; sidecar synchronization has its
own lifetime in MetadataStore.
"""
from __future__ import annotations

from concurrent.futures import Future
import threading

try:
    from PyQt6.QtCore import QObject, QTimer
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer


class MetadataCommitBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = []
        self._closed = False
        self._aggregates = {}
        self._aggregate_lock = threading.RLock()
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._drain)

    @property
    def is_idle(self):
        return not self._pending

    def watch(self, future, on_committed, on_error=None):
        if self._closed:
            raise RuntimeError("元数据编辑器正在关闭。")
        self._pending.append((future, on_committed, on_error))
        self._timer.start()
        return future

    def execute(self, operation, success, failure=None):
        """Run bounded preparation in the browser pool, then deliver on Qt."""
        if self._closed:
            raise RuntimeError("元数据编辑器正在关闭。")
        from ._work_action import CallableAction
        from ._work_policy import WorkKind

        owner = self.parent()
        files = owner if callable(getattr(owner, "metadata_work_pool", None)) else getattr(owner, "_file_list", None)
        if files is None:
            raise RuntimeError("元数据任务没有所属工作池。")
        future = files.metadata_work_pool().submit_action(
            CallableAction(operation), kind=WorkKind.METADATA, priority=-20,
        )
        return self.watch(future, success, failure)

    def submit_edits(self, edits, on_committed, on_error=None):
        from app_common.exif_io.metadata_store import get_store

        if self._closed:
            raise RuntimeError("元数据编辑器正在关闭。")
        store = get_store()
        owner = self.parent()
        files = owner if callable(getattr(owner, "metadata_work_pool", None)) else getattr(owner, "_file_list", None)
        if files is not None:
            store.attach_pool(files.metadata_work_pool())
        edits = list(edits)
        aggregate = Future()

        def deliver(outcome):
            receipts, errors = outcome
            try:
                if receipts:
                    on_committed(receipts)
                if errors:
                    raise RuntimeError("\n".join(str(exc) for exc in errors))
            except Exception as exc:
                if not aggregate.done():
                    aggregate.set_exception(exc)
                if on_error is not None:
                    on_error(exc)
            else:
                aggregate.set_result(receipts)

        def failed(error):
            deliver(([], [error]))

        if len(edits) > 1:
            # Worker backpressure keeps a large selection out of the bounded SQL
            # queue and avoids allocating thousands of GUI timer callbacks.
            def commit_many():
                receipts, errors = [], []
                for path, fields, subject_states in edits:
                    try:
                        receipts.append(store.submit_edit(
                            path, fields, subject_states=subject_states).result())
                    except Exception as exc:
                        errors.append(exc)
                return receipts, errors
            try:
                operation = self.execute(commit_many, deliver, failed)
                self._track_aggregate(operation, aggregate, lambda result: result)
            except Exception as exc:
                failed(exc)
        elif edits:
            path, fields, subject_states = edits[0]
            future = store.submit_edit(path, fields, subject_states=subject_states)
            self.watch(future, lambda receipt: deliver(([receipt], [])), failed)
            self._track_aggregate(future, aggregate, lambda receipt: ([receipt], []))
        else:
            aggregate.set_result([])
        return aggregate

    def _track_aggregate(self, operation, aggregate, outcome):
        # Normally completion includes the GUI update. Closing the owner removes
        # that update, but must still settle callers waiting on accepted work.
        with self._aggregate_lock:
            self._aggregates[operation] = (aggregate, outcome)
        operation.add_done_callback(self._settle_closed_aggregate)

    def _settle_closed_aggregate(self, operation):
        with self._aggregate_lock:
            if not self._closed or not operation.done():
                return
            pending = self._aggregates.pop(operation, None)
            if pending is None:
                return
            aggregate, outcome = pending
            if aggregate.done():
                return
            try:
                receipts, errors = outcome(operation.result())
                if errors:
                    raise RuntimeError("\n".join(str(exc) for exc in errors))
                aggregate.set_result(receipts)
            except Exception as exc:
                aggregate.set_exception(exc)

    def _drain(self):
        for entry in self._pending[:]:
            future, committed, failed = entry
            if not future.done():
                continue
            self._pending.remove(entry)
            if self._closed:
                self._settle_closed_aggregate(future)
                continue
            try:
                result = future.result()
            except Exception as exc:
                if failed is not None:
                    try:
                        failed(exc)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception("Metadata failure UI callback failed")
            else:
                try:
                    committed(result)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Metadata commit UI callback failed")
            with self._aggregate_lock:
                self._aggregates.pop(future, None)
        if not self._pending:
            self._timer.stop()

    def request_shutdown(self):
        # Accepted transactions remain owned by the store and must not be cancelled.
        self._closed = True
        with self._aggregate_lock:
            for operation in list(self._aggregates):
                self._settle_closed_aggregate(operation)
        self._drain()

    def shutdown(self, *, wait=False):
        self.request_shutdown()
        if wait:
            for future, *_ in self._pending:
                try:
                    future.result()
                except Exception:
                    pass
            self._pending.clear()
            self._timer.stop()


def get_metadata_commit_bridge(owner):
    bridge = getattr(owner, "_metadata_commit_bridge", None)
    if bridge is None:
        bridge = MetadataCommitBridge(owner)
        owner._metadata_commit_bridge = bridge
    return bridge
