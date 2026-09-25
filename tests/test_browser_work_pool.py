from __future__ import annotations

from concurrent.futures import wait
import threading
import time
import pytest

from app_common.file_browser._work_pool import BrowserWorkPool, VISIBLE, PREFETCH, PERSISTENT, METADATA
from app_common.exif_io import exiftool_runner as runner


def test_thumbnail_priority_and_two_reserved_metadata_workers():
    pool = BrowserWorkPool(3, 2)
    blocked = threading.Event()
    started = threading.Barrier(2)
    meta_started = threading.Barrier(3)
    seen = []
    def slow_thumb():
        started.wait(timeout=2)
        blocked.wait(3)
    def metadata():
        meta_started.wait(timeout=2)
        return 'metadata'
    try:
        old = [pool.submit(slow_thumb, priority=PERSISTENT) for _ in range(1)]
        started.wait(timeout=2)
        low = [pool.submit(lambda: seen.append('background'), priority=PERSISTENT) for _ in range(3)]
        prefetch = pool.submit(lambda: seen.append('prefetch'), priority=PREFETCH)
        visible = pool.submit(lambda: seen.append('visible'), priority=VISIBLE)
        meta = [pool.submit(metadata, priority=METADATA) for _ in range(2)]
        # Both metadata workers progress while every thumbnail worker is blocked.
        meta_started.wait(timeout=2)
        assert [f.result(timeout=2) for f in meta] == ['metadata', 'metadata']
        assert pool.snapshot()['thumbnail_active'] == 1
        blocked.set()
        wait(old + low + [visible, prefetch], timeout=3)
        assert seen[0] == 'visible'
        assert seen.index('prefetch') < seen.index('background')
    finally:
        blocked.set()
        assert pool.shutdown(timeout=3)


def test_metadata_does_not_take_viewport_capacity_before_thumbnail_timer():
    pool = BrowserWorkPool(4, 2)
    release = threading.Event()
    entered = threading.Barrier(3)
    def blocked():
        entered.wait(timeout=2)
        release.wait(3)
    try:
        pending = [pool.submit(blocked) for _ in range(2)]
        entered.wait(timeout=2)
        # Metadata was queued first; it must not capture all four pool threads.
        queued_meta = pool.submit(lambda: 'later metadata')
        frame = pool.submit(lambda: 'frame', priority=VISIBLE)
        assert frame.result(timeout=1) == 'frame'
        assert not queued_meta.done()
    finally:
        release.set()
        assert pool.shutdown(timeout=3)


def test_list_mode_borrows_all_workers_and_cancel_purges_generation():
    pool = BrowserWorkPool(4, 2)
    pool.set_thumbnail_mode(False)
    release = threading.Event()
    started = threading.Barrier(5)
    cancelled = threading.Event()
    calls = []
    def blocked():
        started.wait(timeout=3)
        release.wait(3)
    try:
        active = [pool.submit(blocked) for _ in range(4)]
        started.wait(timeout=3)
        stale = [pool.submit(lambda: calls.append('stale'), cancelled=cancelled.is_set) for _ in range(6)]
        cancelled.set()
        pool.cancel_pending()
        assert all(f.cancelled() for f in stale)
        assert len(wait(stale, timeout=.1).done) == len(stale)
        new = pool.submit(lambda: 'new')
        release.set()
        assert new.result(timeout=2) == 'new'
        assert not calls
        assert len(pool._threads) == 4
    finally:
        release.set()
        assert pool.shutdown(timeout=3)


def test_exiftool_sessions_are_parallel_reused_and_closed(monkeypatch):
    instances = []
    barrier = threading.Barrier(2)
    class FakeExifTool:
        def __init__(self, path):
            self.path = path
            self.closed = False
            self.calls = 0
            instances.append(self)
        def execute(self, args, **kwargs):
            self.calls += 1
            assert kwargs['timeout'] == 20
            if args == ['first']:
                barrier.wait(timeout=2)
            return (threading.current_thread().name, id(self))
        def close(self):
            self.closed = True
    monkeypatch.setattr(runner, '_StayOpenExifTool', FakeExifTool)
    pool = BrowserWorkPool(4, 2)
    try:
        def read_twice():
            first = runner.run_exiftool('exiftool', ['first'])
            second = runner.run_exiftool('exiftool', ['second'])
            assert first == second
            return first
        futures = [pool.submit(read_twice) for _ in range(2)]
        values = [future.result(timeout=3) for future in futures]
        assert len({item[1] for item in values}) == 2
        assert len(instances) == 2
        assert all(item.calls == 2 for item in instances)
    finally:
        assert pool.shutdown(timeout=3)
    assert all(item.closed for item in instances)
    assert not runner._read_sessions


def test_binary_exiftool_read_cancel_kills_child_and_worker_reuses_slot():
    import subprocess
    import sys
    pool = BrowserWorkPool(3, 2)
    cancel = threading.Event()
    started = threading.Event()
    def binary_read():
        started.set()
        return runner.run_exiftool_once([sys.executable, '-c', 'import time; time.sleep(30)'],
                                       stdout=subprocess.PIPE)
    try:
        future = pool.submit(binary_read, priority=VISIBLE, cancelled=cancel.is_set)
        assert started.wait(2)
        cancel.set()
        try:
            future.result(timeout=2)
        except subprocess.CalledProcessError as exc:
            assert 'cancelled' in str(exc.stderr)
        else:
            raise AssertionError('child was not cancelled')
        assert pool.submit(lambda: 42, priority=VISIBLE).result(timeout=2) == 42
    finally:
        cancel.set()
        assert pool.shutdown(timeout=3)


def test_thumbnail_coordinator_refills_without_waiting_for_slow_peer(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from app_common.file_browser._thumbnail import ThumbnailLoader
    global _APP
    _APP = QApplication.instance() or QApplication([])
    pool = BrowserWorkPool(4, 2)
    loader = ThumbnailLoader(128, 1, work_pool=pool)
    release = threading.Event()
    slow_started = threading.Event()
    fast_finished = threading.Event()
    next_finished = threading.Event()
    def decode(path, emit, *, allow_progressive):
        if path == 'slow.jpg':
            slow_started.set()
            release.wait(3)
        elif path == 'fast.jpg':
            fast_finished.set()
        elif path == 'next.jpg':
            next_finished.set()
    monkeypatch.setattr(loader, '_load_single', decode)
    loader.enqueue(['slow.jpg', 'fast.jpg'])
    loader.start()
    try:
        assert slow_started.wait(2) and fast_finished.wait(2)
        loader.enqueue(['next.jpg'])
        assert next_finished.wait(1), 'next thumbnail waited for the slow batch peer'
        assert not release.is_set()
    finally:
        release.set()
        loader.stop()
        assert loader.wait(3000)
        assert pool.shutdown(timeout=3)


def test_metadata_small_batches_emit_before_slow_peer_and_cancel_remaining(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from app_common.file_browser._workers import MetadataLoader
    global _APP
    _APP = QApplication.instance() or QApplication([])
    pool = BrowserWorkPool(4, 2)
    loader = MetadataLoader([f'{i}.jpg' for i in range(40)], object(), work_pool=pool)
    release = threading.Event()
    batches = []
    sizes = []
    def read(chunk):
        sizes.append(len(chunk))
        if chunk[0] == '0.jpg':
            release.wait(3)
        return {path: {} for path in chunk}, {}, len(chunk)
    monkeypatch.setattr(loader, '_read_parse_chunk', read)
    loader.metadata_batch_ready.connect(lambda batch: batches.append(batch))
    loader.start()
    try:
        deadline = time.monotonic() + 2
        while not batches and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(.005)
        assert batches and '0.jpg' not in batches[0]
        assert max(sizes) <= 8
        loader.stop()
        release.set()
        assert loader.wait(3000)
    finally:
        release.set()
        loader.stop()
        assert loader.wait(3000)
        assert pool.shutdown(timeout=3)


def test_coordinators_tolerate_shutdown_racing_with_submit(tmp_path):
    from app_common.file_browser._thumbnail import ThumbnailLoader, PersistentThumbCacheWorker
    from app_common.file_browser._workers import MetadataLoader
    pool = BrowserWorkPool(3, 2)
    pool.shutdown()
    (tmp_path / '.superpicky').mkdir()
    path = str(tmp_path / 'photo.jpg')
    thumbnail = ThumbnailLoader(128, 1, work_pool=pool)
    thumbnail.enqueue([path])
    metadata = MetadataLoader([path], object(), work_pool=pool)
    persistent = PersistentThumbCacheWorker([path], str(tmp_path), work_pool=pool)
    # A late submit must not escape QThread.run and abort the Qt application.
    for coordinator in (thumbnail, metadata, persistent):
        coordinator.run()


def test_real_worker_sessions_read_chinese_sidecar_without_modifying_original(tmp_path):
    from PIL import Image
    from app_common.exif_io import writer
    executable = writer.get_exiftool_executable_path()
    if not executable:
        pytest.skip('ExifTool is unavailable')
    photo = tmp_path / '中文照片.jpg'
    Image.new('RGB', (10, 8), 'blue').save(photo)
    original = photo.read_bytes()
    pool = BrowserWorkPool(3, 2)
    barrier = threading.Barrier(2)
    try:
        writer.run_exiftool_assignments(str(photo), ['-XMP-dc:Description=中文描述：翠鸟'])
        def read():
            barrier.wait(timeout=3)
            return writer.run_exiftool_json(str(photo.with_suffix('.xmp')))
        futures = [pool.submit(read) for _ in range(2)]
        for future in futures:
            records = future.result(timeout=5)
            assert any(value == '中文描述：翠鸟' for record in records for value in record.values())
        assert photo.read_bytes() == original
    finally:
        assert pool.shutdown(timeout=3)
        runner.close_exiftool_process()
