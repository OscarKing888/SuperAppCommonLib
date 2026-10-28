"""An optional cache on a locked/disconnected disk must not stall metadata reads."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

import pytest

from app_common.exif_io import meta_disk_cache as cache
from app_common.file_browser import _workers


@pytest.fixture(autouse=True)
def clean_cache():
    cache.close_all()
    yield
    cache.close_all()


def test_locked_cache_closes_failed_connection_and_backs_off(tmp_path, monkeypatch):
    db = str(tmp_path / "locked.db")
    writer = sqlite3.connect(db)
    writer.execute("CREATE TABLE unrelated (value TEXT)")
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")
    real_connect = sqlite3.connect
    connections = []
    clock = [100.0]
    monkeypatch.setattr(cache, "monotonic", lambda: clock[0])

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(cache.sqlite3, "connect", connect)
    try:
        for _ in range(24):
            assert cache.get_many(db, {"bird.ARW": (1.0, 10)}) == {}
            assert cache.put_many(db, {"bird.ARW": (1.0, 10, {"ISO": 100})}) == 0
        assert len(connections) == 1  # one failure, not one timeout per worker/read/write
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[0].execute("SELECT 1")
        writer.rollback()
        clock[0] += cache._RETRY_SECONDS + 1
        assert cache.put_many(db, {"bird.ARW": (1.0, 10, {"ISO": 100})}) == 1
        assert cache.get_many(db, {"bird.ARW": (1.0, 10)}) == {"bird.ARW": {"ISO": 100}}
        assert len(connections) == 2
    finally:
        writer.close()


def test_stuck_initialization_does_not_hold_other_workers(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_connect = sqlite3.connect
    bad, good = str(tmp_path / "bad.db"), str(tmp_path / "good.db")

    def connect(path, **kwargs):
        if path == bad:
            entered.set()
            assert release.wait(5)
            raise sqlite3.OperationalError("device unavailable")
        return real_connect(path, **kwargs)

    monkeypatch.setattr(cache.sqlite3, "connect", connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(cache.get_many, bad, {"bird": (1.0, 10)})
        try:
            assert entered.wait(2)
            other = pool.submit(cache.put_many, good, {"bird": (1.0, 10, {"ISO": 100})})
            assert other.result(timeout=1) == 0
            same = pool.submit(cache.get_many, bad, {"bird": (1.0, 10)})
            assert same.result(timeout=1) == {}
        finally:
            release.set()
        assert blocked.result(timeout=2) == {}
    assert cache.put_many(good, {"bird": (1.0, 10, {"ISO": 100})}) == 1


@pytest.mark.parametrize("write", [False, True])
def test_busy_existing_connection_is_optional(tmp_path, write):
    db = str(tmp_path / "cache.db")
    items = {"bird": (1.0, 10, {"ISO": 100})}
    assert cache.put_many(db, items) == 1
    _, lock = cache._get_connection(db)
    lock.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            if write:
                result = pool.submit(cache.put_many, db, items)
                assert result.result(timeout=1) == 0
            else:
                result = pool.submit(cache.get_many, db, {"bird": (1.0, 10)})
                assert result.result(timeout=1) == {}
        finally:
            lock.release()
    assert cache.get_many(db, {"bird": (1.0, 10)}) == {"bird": {"ISO": 100}}


def test_failed_write_rolls_back_partial_cache_batch(tmp_path):
    db = str(tmp_path / "cache.db")
    conn, _ = cache._get_connection(db)
    conn.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON meta WHEN NEW.path = 'bad' "
                 "BEGIN SELECT RAISE(FAIL, 'simulated write failure'); END")
    conn.commit()
    assert cache.put_many(db, {"good": (1.0, 10, {}), "bad": (1.0, 10, {})}) == 0
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM meta").fetchone() == (0,)


def test_metadata_is_returned_when_cache_open_fails(tmp_path, monkeypatch):
    paths = [str(tmp_path / f"bird{i}.ARW") for i in range(24)]
    for path in paths:
        Path(path).write_bytes(b"source")
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(args[0])
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cache.sqlite3, "connect", fail)
    monkeypatch.setattr(_workers, "_batch_read_xmp_sidecar", lambda *a, **kw: {})
    monkeypatch.setattr(_workers, "_meta_disk_cache_db_path_for_file", lambda *a: str(tmp_path / "cache.db"))
    monkeypatch.setattr(_workers, "fast_read_browser_metadata",
                        lambda batch: ({p: {"ExifIFD:ISO": 800} for p in batch}, []))
    loader = _workers.MetadataLoader(paths, object())
    for path in paths:
        assert loader._read_metadata_batch_uncached([path])[path]["ExifIFD:ISO"] == 800
    assert attempts == [str(tmp_path / "cache.db")]


def test_failure_tracking_is_bounded():
    for i in range(cache._MAX_FAILED_DATABASES + 5):
        cache._record_failure(str(i), OSError("offline"))
    assert len(cache._RETRY_AFTER) == cache._MAX_FAILED_DATABASES
