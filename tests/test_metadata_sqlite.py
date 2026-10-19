"""SQLite 是可重建缓存：跨会话命中、侧车同步、并发背压及故障降级。"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from app_common.exif_io import metadata_sqlite as sql
from app_common.exif_io import xmp_sidecar as xmp
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import _batch_read_xmp_sidecar
from app_common.file_browser import _workers
from app_common.file_browser._metadata_memory import MetadataMemoryCache, metadata_signatures


@pytest.fixture
def disk(isolated_metadata_sqlite_cache):
    return isolated_metadata_sqlite_cache


def sources(tmp_path, suffixes=(".HIF",)):
    paths = [str(tmp_path / ("白鹭" + suffix)) for suffix in suffixes]
    for path in paths:
        Path(path).write_bytes(b"source")
    return paths


def loader(paths, **kwargs):
    return _workers.MetadataLoader(paths, object(), memory_cache=MetadataMemoryCache(), **kwargs)


@pytest.fixture
def source_reads(monkeypatch):
    reads = []
    monkeypatch.setattr(_workers.meta_disk_cache, "get_many", lambda *args: {})
    monkeypatch.setattr(_workers, "fast_read_browser_metadata", lambda paths: ({}, paths))

    def read(paths, **kwargs):
        reads.extend(paths)
        return {p: {"SourceFile": p, "ExifIFD:ISO": 800, "IFD0:Make": "SONY"} for p in paths}

    monkeypatch.setattr(_workers, "read_batch_metadata", read)
    return reads


def test_restart_reuses_merged_xmp_and_source_metadata(tmp_path, monkeypatch, disk, source_reads):
    paths = sources(tmp_path)
    PhotoMetaDataXMP().write(paths[0], {"XMP-dc:Title": "飞行中的白鹭"})
    assert disk.flush()
    first = loader(paths)._read_parse_chunk(paths)[0]
    assert disk.flush()
    disk.close()
    reopened = sql.MetadataSQLiteCache(disk.path)
    monkeypatch.setattr(sql, "_default", reopened)
    monkeypatch.setattr(_workers, "_batch_read_xmp_sidecar", lambda *a, **kw: pytest.fail("SQL hit must skip XML"))
    try:
        second = loader(paths)._read_parse_chunk(paths)[0]
        assert first == second
        assert second[paths[0]]["title"] == "飞行中的白鹭"
        assert source_reads == paths
    finally:
        reopened.close()


@pytest.mark.parametrize("title", ["休息白鹭", ""])
def test_write_refreshes_sql_for_raw_jpeg_and_derived_resource(tmp_path, monkeypatch, disk, source_reads, title):
    paths = sources(tmp_path, (".HIF", ".JPG"))
    export = tmp_path / "Export"
    export.mkdir()
    derived = str(export / "白鹭.JPG")
    Path(derived).write_bytes(b"export")
    paths.append(derived)
    store = PhotoMetaDataXMP()
    assert store.write(paths[0], {"XMP-dc:Title": "飞行白鹭", "XMP-xmp:Rating": 5,
                                  "XMP-superpicky:bird_species_cn": "白鹭"})
    assert disk.flush()
    _batch_read_xmp_sidecar(paths)
    loader(paths)._read_metadata_batch(paths)
    assert disk.flush()
    assert store.write(paths[0], {"XMP-dc:Title": title, "XMP-xmp:Rating": 0})
    assert disk.flush()
    with sqlite3.connect(disk.path) as conn:
        assert conn.execute("SELECT count(*) FROM entries WHERE namespace=?", (sql.BROWSER_NAMESPACE,)).fetchone()[0] == 0
        payloads = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM entries WHERE namespace=?", (sql.XMP_NAMESPACE,))]
    assert len(payloads) == 3
    if title:
        assert all(["XMP-dc", "title", title] in p["rows"] for p in payloads)
    else:
        assert all(not any(row[:2] == ["XMP-dc", "title"] for row in p["rows"]) for p in payloads)
    monkeypatch.setattr(xmp, "read_xmp_file", lambda *a, **kw: pytest.fail("write-through XMP cache must be fresh"))
    records = _batch_read_xmp_sidecar(paths)
    assert all(record.get("XMP-dc:Title", "") == title for record in records.values())
    assert all(str(record["XMP-xmp:Rating"]) == "0" for record in records.values())


def test_external_edit_replace_delete_and_create_refresh_both_caches(tmp_path, disk, source_reads):
    paths = sources(tmp_path)
    path = paths[0]
    store = PhotoMetaDataXMP()
    assert store.write(path, {"XMP-dc:Title": "飞行白鹭"})
    assert disk.flush()
    active = loader(paths)
    assert active._read_parse_chunk(paths)[0][path]["title"] == "飞行白鹭"
    assert disk.flush()
    sidecar = tmp_path / "白鹭.xmp"
    stat = sidecar.stat()
    text = sidecar.read_text(encoding="utf-8")
    sidecar.write_text(text.replace("飞行白鹭", "休息白鹭"), encoding="utf-8")
    # 即使大小/mtime 被工具保留，ctime/文件身份仍能发现变化。
    os.utime(sidecar, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert active._read_parse_chunk(paths)[0][path]["title"] == "休息白鹭"
    assert disk.flush()
    replacement = tmp_path / "replacement.tmp"
    replacement.write_text(text.replace("飞行白鹭", "觅食白鹭"), encoding="utf-8")
    os.replace(replacement, sidecar)
    assert loader(paths)._read_parse_chunk(paths)[0][path]["title"] == "觅食白鹭"
    assert disk.flush()
    sidecar.unlink()
    assert active._read_parse_chunk(paths)[0][path]["title"] == ""
    assert disk.flush()
    sidecar.write_text(text, encoding="utf-8")
    assert active._read_parse_chunk(paths)[0][path]["title"] == "飞行白鹭"
    assert len(source_reads) == 5


@pytest.mark.parametrize("change", ["source", "tags", "report"])
def test_restart_revalidates_all_inputs(tmp_path, disk, source_reads, change):
    paths = sources(tmp_path)
    path = paths[0]
    tags = ["-ISO"]
    rows = {path: {"bird_species_cn": "白鹭"}}
    loader(paths, metadata_tags=tags, report_rows_by_path=rows)._read_metadata_batch(paths)
    assert disk.flush()
    if change == "source":
        Path(path).write_bytes(b"changed source")
    elif change == "tags":
        tags.append("-Make")
    else:
        rows[path]["bird_species_cn"] = "小白鹭"
    loader(paths, metadata_tags=tags, report_rows_by_path=rows)._read_metadata_batch(paths)
    assert len(source_reads) == 2


def test_video_probe_is_reused_after_memory_cache_is_discarded(tmp_path, monkeypatch, disk, source_reads):
    paths = sources(tmp_path, (".MP4",))
    probes = []

    def probe(path, **kwargs):
        probes.append(path)
        return {"width": 3840, "height": 2160, "duration": 2.5, "fps": 60}

    monkeypatch.setattr(_workers, "probe_video", probe)
    first = loader(paths)._read_parse_chunk(paths)[0]
    assert disk.flush()
    assert loader(paths)._read_parse_chunk(paths)[0] == first
    assert probes == paths


def test_changed_during_read_or_failure_cannot_persist(tmp_path, monkeypatch, disk, source_reads):
    paths = sources(tmp_path)
    path = paths[0]

    def change(paths, **kwargs):
        Path(path).write_bytes(b"changed during read")
        return {path: {"SourceFile": path, "ISO": 100}}

    monkeypatch.setattr(_workers, "read_batch_metadata", change)
    loader(paths)._read_metadata_batch(paths)
    assert disk.flush()
    assert disk.get_many(sql.BROWSER_NAMESPACE, metadata_signatures(paths, [], {})) == {}
    monkeypatch.setattr(_workers, "read_batch_metadata", lambda *a, **kw: {path: {"Error": "unreadable"}})
    loader(paths)._read_metadata_batch(paths)
    assert disk.flush()
    assert disk.get_many(sql.BROWSER_NAMESPACE, metadata_signatures(paths, [], {})) == {}


def test_capacity_overwrite_parallel_read_and_reopen(tmp_path):
    disk = sql.MetadataSQLiteCache(tmp_path / "中文 缓存.db", max_entries=3, max_bytes=4000)
    keys = [str(tmp_path / str(i)) for i in range(10)]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda p: disk.put_many("test", {p: ((1,), {"中文": p}, None)}, disk.revision()), keys))
        assert disk.flush()
        for _ in range(5):
            disk.put_many("test", {keys[-1]: ((2,), {"中文": "更新"}, None)}, disk.revision())
        assert disk.flush()
        with sqlite3.connect(disk.path) as conn:
            total = conn.execute("SELECT count(*), sum(bytes) FROM entries").fetchone()
            assert conn.execute("SELECT count,bytes FROM totals").fetchone() == total
        assert total[0] <= 3 and total[1] <= 4000
        assert disk.get_many("test", {keys[-1]: (2,)})[keys[-1]] == {"中文": "更新"}
    finally:
        disk.close()
    assert not disk._thread.is_alive()


def test_pending_write_does_not_block_read_and_old_generation_is_rejected(tmp_path, monkeypatch):
    disk = sql.MetadataSQLiteCache(tmp_path / "queue.db")
    started, release = threading.Event(), threading.Event()
    connect = disk._connect_writer

    def wait_connect():
        started.set()
        assert release.wait(5)
        return connect()

    monkeypatch.setattr(disk, "_connect_writer", wait_connect)
    path = str(tmp_path / "photo")
    revision = disk.revision()
    try:
        disk.put_many("test", {path: ((1,), {"title": "old"}, None)}, revision)
        assert started.wait(2)
        disk.invalidate_scope(str(tmp_path))
        assert disk.get_many("test", {path: (1,)}) == {}
        assert not disk.put_many("test", {path: ((1,), {"title": "late"}, None)}, revision)
        release.set()
        assert disk.flush()
        assert disk.get_many("test", {path: (1,)}) == {}
    finally:
        release.set()
        disk.close()


def test_scope_clear_is_exact_and_bad_cache_falls_back(tmp_path):
    disk = sql.MetadataSQLiteCache(tmp_path / "scope.db")
    a, b = [str(tmp_path / directory / "bird") for directory in ("100%_鸟", "100xx鸟")]
    try:
        disk.put_many("test", {p: ((1,), {"title": "白鹭"}, None) for p in (a, b)}, disk.revision())
        assert disk.flush()
        disk.invalidate_scope(str(Path(a).parent))
        assert disk.flush()
        assert disk.get_many("test", {a: (1,), b: (1,)}) == {b: {"title": "白鹭"}}
        with sqlite3.connect(disk.path) as conn:
            conn.execute("UPDATE entries SET payload='broken json'")
        assert disk.get_many("test", {b: (1,)}) == {}
    finally:
        disk.close()
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a database")
    disk = sql.MetadataSQLiteCache(bad)
    try:
        assert disk.get_many("test", {a: (1,)}) == {}
        disk.put_many("test", {a: ((1,), {}, None)}, disk.revision())
        assert disk.flush()
    finally:
        disk.close()


def test_shared_index_scans_once_and_detects_new_mixed_case_sidecar(tmp_path, monkeypatch):
    paths = sources(tmp_path, (".ARW", ".JPG"))
    count = []
    original = xmp._build_xmp_dir_index

    def scan(directory):
        count.append(directory)
        return original(directory)

    monkeypatch.setattr(xmp, "_build_xmp_dir_index", scan)
    index = xmp.XmpDirectoryIndex()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: xmp.find_xmp_sidecars(paths, directory_index=index), range(100)))
    assert all(result == {} for result in results)
    assert len(count) == 1
    sidecar = tmp_path / "白鹭.XmP"
    sidecar.write_text("xmp", encoding="utf-8")
    assert xmp.find_xmp_sidecars(paths, directory_index=index) == dict.fromkeys(paths, str(sidecar))
    sidecar.unlink()
    assert xmp.find_xmp_sidecars(paths, directory_index=index) == {}
    assert len(count) == 3


def test_async_xmp_refresh_does_not_hold_up_unrelated_sql_reads(tmp_path, monkeypatch, disk):
    path = str(tmp_path / "unrelated")
    disk.put_many("test", {path: ((1,), {"title": "白鹭"}, None)}, disk.revision())
    assert disk.flush()
    started, release = threading.Event(), threading.Event()

    def slow_refresh(conn, source):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(disk, "_refresh", slow_refresh)
    try:
        disk.refresh_xmp(str(tmp_path / "edited.jpg"))
        assert started.wait(2)
        # refresh 尚未完成，读取仍直接通过独立只读连接命中。
        assert disk.get_many("test", {path: (1,)}) == {path: {"title": "白鹭"}}
    finally:
        release.set()
        assert disk.flush()


def test_pending_budget_and_cancelled_loader_never_store_bad_results(tmp_path, monkeypatch, disk, source_reads):
    paths = sources(tmp_path)
    active = loader(paths)

    def cancel(paths, **kwargs):
        active.stop()
        return {paths[0]: {"SourceFile": paths[0], "ISO": 100}}

    monkeypatch.setattr(_workers, "read_batch_metadata", cancel)
    assert active._read_parse_chunk(paths)[0] == {}
    assert disk.flush()
    assert disk.get_many(sql.BROWSER_NAMESPACE, metadata_signatures(paths, [], {})) == {}
    bounded = sql.MetadataSQLiteCache(tmp_path / "bounded-queue.db", pending_bytes=100)
    try:
        assert not bounded.put_many("test", {paths[0]: ((1,), {"text": "x" * 1000}, None)}, bounded.revision())
        assert bounded._pending_bytes == 0
        assert bounded._thread is None
    finally:
        bounded.close()
