"""Cached first paint is independent of source-volume validation."""
import os

from app_common.exif_io import metadata_store
from app_common.file_browser import _workers
from app_common.file_browser._browser_core import _collect_image_files_impl
from app_common.file_browser._directory_snapshot import DirectorySnapshotCache
from app_common.file_browser._metadata_memory import MetadataMemoryCache


def test_persistent_listing_is_chunked_and_loaded_without_source_scan(tmp_path, monkeypatch,
                                                                   isolated_metadata_sqlite_cache):
    files = [str(tmp_path / f"白鹭-{index}.HIF") for index in range(1100)]
    cache = DirectorySnapshotCache(MetadataMemoryCache())
    cache.remember(str(tmp_path), True, {"files": files, "metadata": {}, "report_root": None}, persist=True)
    assert isolated_metadata_sqlite_cache.flush()
    reopened = DirectorySnapshotCache(MetadataMemoryCache())
    monkeypatch.setattr(os, "scandir", lambda *_: (_ for _ in ()).throw(AssertionError("source scan")))
    snapshot = reopened.load(str(tmp_path), True)
    assert snapshot["files"] == files
    assert snapshot["metadata"] == {}


def test_incomplete_persistent_listing_is_a_miss(tmp_path, isolated_metadata_sqlite_cache):
    cache = DirectorySnapshotCache(MetadataMemoryCache())
    files = [str(tmp_path / f"{index}.jpg") for index in range(600)]
    cache.remember(str(tmp_path), True, {"files": files}, persist=True)
    assert isolated_metadata_sqlite_cache.flush()
    import sqlite3
    with sqlite3.connect(isolated_metadata_sqlite_cache.path) as conn:
        conn.execute("DELETE FROM entries WHERE namespace='directory-v1' AND payload LIKE '%files%'")
    assert DirectorySnapshotCache(MetadataMemoryCache()).load(str(tmp_path), True) is None


def test_directory_worker_publishes_snapshot_before_enumeration(tmp_path, monkeypatch):
    seen = []
    cache = DirectorySnapshotCache(MetadataMemoryCache())
    path = str(tmp_path / "飞行.HIF")
    cache.remember(str(tmp_path), True, {"files": [path], "metadata": {}}, use_report_db=False)
    worker = _workers.DirectoryScanWorker(str(tmp_path), True, use_report_db=False, snapshot_cache=cache)
    worker.snapshot_ready.connect(lambda directory, snapshot: seen.append(snapshot["files"]))

    def walk(*args, **kwargs):
        assert seen == [[path]], "first paint must not wait for filesystem enumeration"
        return iter(())

    monkeypatch.setattr(_workers.os, "walk", walk)
    worker.run()
    assert seen == [[path]]


def test_metadata_snapshot_does_not_validate_source_files(tmp_path, monkeypatch):
    path = str(tmp_path / "原图不存在也先显示缓存.HIF")
    metadata_store.get_store().remember_many(
        {path: {"XMP-dc:Title": "白鹭", "ExifIFD:ISO": 800}}, profile="browser",
    ).result()
    loader = _workers.MetadataLoader([path], object(), memory_cache=MetadataMemoryCache())
    batches = []
    loader.metadata_batch_ready.connect(batches.append)
    monkeypatch.setattr(_workers, "metadata_signatures", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("stat before snapshot")))
    monkeypatch.setattr(loader, "_read_metadata_batch_uncached", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("source read")))
    loader._emit_cached_snapshot()
    assert batches[0][path]["title"] == "白鹭"
    assert batches[0][path]["iso"] == "800"


def test_report_supplement_walk_stops_during_enumeration(tmp_path, monkeypatch):
    visited = []

    def walk(*args, **kwargs):
        for index in range(100):
            visited.append(index)
            yield str(tmp_path / str(index)), [], ["bird.jpg"]

    monkeypatch.setattr(os, "walk", walk)
    result = _collect_image_files_impl(str(tmp_path), True, cancelled=lambda: len(visited) >= 2)
    assert result == []
    assert visited == [0, 1]


def test_failed_source_read_does_not_poison_sql_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "failed.HIF"
    source.write_bytes(b"source")
    path = str(source)
    loader = _workers.MetadataLoader([path], object(), memory_cache=MetadataMemoryCache())

    def failed(paths, *, failed_paths, **kwargs):
        failed_paths.update(paths)
        return {p: {"SourceFile": p, "ExifIFD:ISO": 800} for p in paths}

    monkeypatch.setattr(loader, "_read_metadata_batch_uncached", failed)
    loader._read_metadata_batch([path])
    assert metadata_store.get_store().read_many([path]) == {}
