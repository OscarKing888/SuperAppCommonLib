# -*- coding: utf-8 -*-
"""One bounded priority pool per browser, shared across directory generations."""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
import heapq
import itertools
import threading
import time

from app_common.log import get_logger
from app_common.exif_io.exiftool_runner import exiftool_worker_session, exiftool_read_request

_log = get_logger('file_browser.pool')
VISIBLE = 0
PREFETCH = 10
PERSISTENT = 20
METADATA = 30


class BrowserPoolClosed(RuntimeError):
    """A coordinator raced with its owner stopping the browser."""


@dataclass(order=True)
class _Job:
    priority: int
    sequence: int
    future: Future = field(compare=False)
    function: object = field(compare=False)
    args: tuple = field(compare=False)
    kwargs: dict = field(compare=False)
    cancelled: object = field(compare=False)
    queued_at: float = field(default_factory=time.monotonic, compare=False)


class BrowserWorkPool:
    """Reserved metadata capacity plus priority thumbnail lanes.

    In thumbnail mode metadata is capped at its reservation, so an early metadata
    submission cannot occupy all lanes before the viewport timer runs. Without thumbnail demand
    metadata can borrow every lane. Coordinators submit bounded windows of jobs;
    they do not own or shut down this pool.
    """
    def __init__(self, max_workers: int, metadata_workers: int = 2):
        self.metadata_workers = max(2, int(metadata_workers))
        self.max_workers = max(self.metadata_workers + 1, int(max_workers))
        self.thumbnail_workers = self.max_workers - self.metadata_workers
        self._condition = threading.Condition()
        self._queues = {'metadata': [], 'thumbnail': []}
        self._active = {'metadata': 0, 'thumbnail': 0}
        self._sequence = itertools.count()
        self._closed = False
        self._thumbnail_mode = True
        self._threads = []
        self._owner = threading.get_ident()
        self._completed = 0
        self._max_queue_ms = 0.0
        self._max_run_ms = 0.0
        for index in range(self.max_workers):
            worker = threading.Thread(target=self._run, args=(index < self.metadata_workers,),
                                      name=f'browser-worker-{index + 1}', daemon=True)
            self._threads.append(worker)
            worker.start()
        _log.info('[browser.pool] started total=%s metadata_reserved=%s thumbnails=%s',
                  self.max_workers, self.metadata_workers, self.thumbnail_workers)

    def set_thumbnail_mode(self, enabled: bool):
        with self._condition:
            self._thumbnail_mode = bool(enabled)
            self._condition.notify_all()

    def submit(self, function, *args, priority=METADATA, cancelled=lambda: False, **kwargs):
        future = Future()
        with self._condition:
            if self._closed:
                raise BrowserPoolClosed('Browser worker pool is closed')
            kind = 'metadata' if priority == METADATA else 'thumbnail'
            heapq.heappush(self._queues[kind], _Job(priority, next(self._sequence), future,
                                                   function, args, kwargs, cancelled))
            self._condition.notify_all()
        return future

    @staticmethod
    def _cancel(future):
        if not future.done() and future.cancel():
            # Future.wait() also needs the cancellation notification normally
            # supplied by an executor worker picking the cancelled work item.
            future.set_running_or_notify_cancel()

    def reprioritize(self, future, priority):
        with self._condition:
            for job in self._queues['thumbnail']:
                if job.future is future:
                    job.priority = priority
                    heapq.heapify(self._queues['thumbnail'])
                    self._condition.notify_all()
                    return

    def cancel(self, future):
        with self._condition:
            self._cancel(future)
            self._condition.notify_all()

    def cancel_pending(self):
        """Purge cancelled generations without waiting behind a slow decode."""
        with self._condition:
            for kind, queue in self._queues.items():
                kept = []
                for job in queue:
                    if job.future.cancelled() or job.cancelled():
                        self._cancel(job.future)
                    else:
                        kept.append(job)
                heapq.heapify(kept)
                self._queues[kind] = kept
            self._condition.notify_all()

    def _take(self, reserved):
        while True:
            if self._closed:
                return None, None
            meta_allowed = not self._thumbnail_mode or self._active['metadata'] < self.metadata_workers
            kinds = ('metadata',) if reserved else (('thumbnail',) if self._thumbnail_mode else ('thumbnail', 'metadata'))
            for kind in kinds:
                if kind == 'metadata' and not meta_allowed:
                    continue
                queue = self._queues[kind]
                while queue:
                    job = heapq.heappop(queue)
                    if job.cancelled() or job.future.cancelled():
                        self._cancel(job.future)
                        continue
                    if not job.future.set_running_or_notify_cancel():
                        continue
                    self._active[kind] += 1
                    return kind, job
            self._condition.wait(0.05)

    def _run(self, reserved):
        # An ExifTool session belongs to one actual pool thread, not one batch.
        # Threads can therefore read concurrently and reuse their own process.
        with exiftool_worker_session():
            while True:
                with self._condition:
                    kind, job = self._take(reserved)
                if job is None:
                    return
                started = time.monotonic()
                wait_ms = (started - job.queued_at) * 1000
                try:
                    context = exiftool_read_request(lambda: self._closed or job.cancelled(), timeout=20)
                    with context:
                        value = job.function(*job.args, **job.kwargs)
                    job.future.set_result(value)
                except BaseException as exc:
                    job.future.set_exception(exc)
                finally:
                    run_ms = (time.monotonic() - started) * 1000
                    with self._condition:
                        self._active[kind] -= 1
                        self._completed += 1
                        self._max_queue_ms = max(self._max_queue_ms, wait_ms)
                        self._max_run_ms = max(self._max_run_ms, run_ms)
                        self._condition.notify_all()
                    if run_ms >= 2000:
                        _log.info('[browser.pool] slow_task kind=%s queue_ms=%.1f run_ms=%.1f', kind, wait_ms, run_ms)

    def snapshot(self):
        with self._condition:
            return dict(total=self.max_workers, metadata_reserved=self.metadata_workers,
                        metadata_active=self._active['metadata'], thumbnail_active=self._active['thumbnail'],
                        metadata_queued=len(self._queues['metadata']), thumbnail_queued=len(self._queues['thumbnail']),
                        completed=self._completed, max_queue_ms=self._max_queue_ms, max_run_ms=self._max_run_ms)

    def request_shutdown(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError('Only the browser owner may close its worker pool')
        with self._condition:
            self._closed = True
            for queue in self._queues.values():
                for job in queue:
                    self._cancel(job.future)
                queue.clear()
            self._condition.notify_all()

    def is_finished(self):
        return all(not thread.is_alive() for thread in self._threads)

    def shutdown(self, timeout=None):
        self.request_shutdown()
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        for thread in self._threads:
            thread.join(None if deadline is None else max(0, deadline - time.monotonic()))
        return self.is_finished()
