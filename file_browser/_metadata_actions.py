"""Bounded metadata copy/write actions for the browser's shared worker pool."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import dataclass, field
from pathlib import Path
import copy
import os
import queue
import threading

from app_common.bird_species_copy import species_snapshot
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_policy import WorkKind
from app_common.file_browser._work_pool import BrowserPoolClosed
try:
    from PyQt6.QtCore import QThread
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QThread


def sidecar_fingerprint(path):
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


@dataclass
class MetadataResult:
    source: str
    message: str = ""
    updates: dict = field(default_factory=dict)
    saved_fingerprint: tuple | None = None


class MetadataWriteAction(WorkerAction):
    """One sidecar transaction; independent photos can execute concurrently."""
    def __init__(self, path, fields, updates, *, writer=None, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.path = os.path.normpath(os.path.abspath(path))
        self.fields, self.updates = copy.deepcopy(fields), copy.deepcopy(updates)
        self.writer = writer or PhotoMetaDataXMP().write

    def execute(self):
        with xmp_sidecar_write_lock(self.path):
            if self.is_cancelled():
                return None
            if not Path(self.path).is_file():
                raise FileNotFoundError(f"找不到照片原文件：{self.path}")
            if not self.writer(self.path, self.fields):
                raise OSError(f"XMP 写入失败，原有元数据已保留：{self.path}")
            stamp = sidecar_fingerprint(PhotoMetaDataXMP().sidecar_path_for(self.path))
        return MetadataResult(self.path, updates=self.updates, saved_fingerprint=stamp)


class SpeciesCopyAction(WorkerAction):
    def __init__(self, path, payload, *, sources=None, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.path, self.payload = path, copy.deepcopy(payload)
        # Keep report/cache layers separate until the sidecar's species is known.
        self.sources = copy.deepcopy(sources if sources is not None else (payload,))

    def execute(self):
        if self.is_cancelled():
            return None
        sidecar = PhotoMetaDataXMP().read(self.path)
        payload = {**self.payload, **species_snapshot(*self.sources, sidecar)}
        if not payload["bird_species_cn"] and not self.is_cancelled():
            # The pool owns a reusable, thread-local ExifTool read session.
            from app_common.exif_io import read_batch_metadata
            records = read_batch_metadata([self.path])
            record = records.get(os.path.normpath(self.path)) or records.get(self.path) or {}
            payload.update(species_snapshot(*self.sources, record, sidecar))
        return payload


class LocationCopyAction(WorkerAction):
    def __init__(self, path, *, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.path = path

    def execute(self):
        from app_common.shooting_location import shooting_location
        return shooting_location(PhotoMetaDataXMP().read(self.path))


class MetadataBatchWorker(QThread):
    """Coordinator only: a bounded window, refilled on individual completion.

    The browser owns the pool. This thread never closes it, and always waits for
    in-flight writes before emitting real QThread.finished. Cancellation retains
    completed writes; shutdown may discard UI delivery to avoid a full-queue wait.
    """
    def __init__(self, paths, action_factory, pool):
        super().__init__()
        self.paths = tuple(paths)
        self.action_factory, self.pool = action_factory, pool
        self.results = queue.Queue(maxsize=128)
        self.cancelled, self.discard_results = threading.Event(), threading.Event()
        self.error = ""
        self._active = 0
        self._active_lock = threading.Lock()

    @property
    def active_count(self):
        with self._active_lock:
            return self._active

    def _action(self, path):
        action = self.action_factory(path, self.cancelled.is_set)
        owner = self

        class CountedAction(WorkerAction):
            def execute(self):
                with owner._active_lock:
                    owner._active += 1
                try:
                    return action.execute()
                finally:
                    with owner._active_lock:
                        owner._active -= 1

        return CountedAction(cancelled=self.cancelled.is_set)

    def _publish(self, result):
        while not self.discard_results.is_set():
            try:
                self.results.put(result, timeout=.05)
                return
            except queue.Full:
                pass

    def run(self):
        pending, lease = {}, None
        paths = iter(self.paths)
        exhausted = False
        try:
            lease = self.pool.begin_producer(WorkKind.METADATA)
            while pending or not exhausted:
                while not exhausted and not self.cancelled.is_set() and len(pending) < self.pool.max_workers:
                    path = next(paths, None)
                    if path is None:
                        exhausted = True
                        break
                    # Explicit edits go before background metadata reads in this lane.
                    future = self.pool.submit_action(self._action(path), kind=WorkKind.METADATA, priority=-10)
                    pending[future] = path
                if self.cancelled.is_set():
                    exhausted = True
                    for future in pending:
                        self.pool.cancel(future)
                if not pending:
                    break
                done, _ = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                for future in done:
                    path = pending.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = MetadataResult(path, str(exc))
                    if result is not None:
                        self._publish(result)
        except BrowserPoolClosed:
            self.cancelled.set()
        except Exception as exc:
            self.error = str(exc)
        finally:
            # Submission/owner shutdown races must not abandon an already running write.
            for future in pending:
                self.pool.cancel(future)
            for future, path in pending.items():
                if future.cancelled():
                    continue
                try:
                    result = future.result()
                except Exception as exc:
                    result = MetadataResult(path, str(exc))
                if result is not None:
                    self._publish(result)
            self.pool.end_producer(WorkKind.METADATA, lease)
