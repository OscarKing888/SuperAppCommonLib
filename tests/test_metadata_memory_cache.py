"""会话缓存的容量、退休策略及真实文件/XMP 失效回归。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app_common.file_browser._metadata_memory import MetadataMemoryCache, metadata_signatures
from app_common.file_browser import _workers
from app_common.exif_io.photo_meta import PhotoMetaDataXMP


def put(cache, path, record=None, signature=(1,)):
    _, generation = cache.lookup(path, signature)
    cache.store(path, signature, record or {"title": "白鹭", "tags": ["飞行"]}, generation)


def get(cache, path, signature=(1,)):
    return cache.lookup(path, signature)[0]


def test_lru_prefers_outside_active_scope_and_copies_records(tmp_path):
    cache = MetadataMemoryCache(max_entries=2)
    a, b, c = [str(tmp_path / folder / "鸟.HIF") for folder in ("a", "b", "c")]
    source_record = {"title": "白鹭", "tags": ["飞行"]}
    put(cache, a, source_record)
    source_record["tags"].append("changed after store")
    put(cache, b)
    cache.set_scope(str(tmp_path / "a"))
    # 范围外 b 虽然更热，仍优先保留活动目录 a。
    get(cache, b)["tags"].append("changed")
    assert get(cache, b)["tags"] == ["飞行"]
    put(cache, c)
    assert get(cache, b) is None
    assert get(cache, a)["tags"] == ["飞行"]
    cache.set_scope(str(tmp_path))
    get(cache, a)
    put(cache, b)
    assert get(cache, c) is None
    assert cache.stats()["entries"] == 2


def test_idle_expiry_uses_last_access_and_releases_bytes(tmp_path):
    now = [0.0]
    cache = MetadataMemoryCache(idle_seconds=10, clock=lambda: now[0])
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    put(cache, a)
    put(cache, b)
    now[0] = 9
    assert get(cache, a)
    now[0] = 11
    assert get(cache, b) is None
    assert get(cache, a)
    now[0] = 21
    assert cache.stats() == {"entries": 0, "bytes": 0}


def test_byte_limit_oversize_and_zero_capacity(tmp_path):
    cache = MetadataMemoryCache(max_bytes=4000)
    for i in range(30):
        put(cache, str(tmp_path / str(i)))
    assert 0 < cache.stats()["entries"] < 30
    assert cache.stats()["bytes"] <= 4000
    put(cache, str(tmp_path / "huge"), {"text": "鸟" * 10_000})
    assert get(cache, str(tmp_path / "huge")) is None
    empty = MetadataMemoryCache(max_entries=0)
    put(empty, "a")
    assert empty.stats() == {"entries": 0, "bytes": 0}


def test_refresh_and_shutdown_reject_late_stores(tmp_path):
    cache = MetadataMemoryCache()
    path = str(tmp_path / "a.jpg")
    _, generation = cache.lookup(path, (1,))
    cache.invalidate_scope(str(tmp_path))
    cache.store(path, (1,), {"title": "旧数据"}, generation)
    assert get(cache, path) is None
    put(cache, path)
    _, generation = cache.lookup(path, (1,))
    cache.clear()
    cache.store(path, (1,), {"title": "旧数据"}, generation)
    assert cache.stats() == {"entries": 0, "bytes": 0}


def test_resize_retains_active_entries_and_inflight_reads_obey_new_limit(tmp_path):
    cache = MetadataMemoryCache()
    a, b = [str(tmp_path / "active" / name) for name in ("a", "b")]
    c = str(tmp_path / "other" / "c")
    cache.set_scope(str(tmp_path / "active"))
    for path in (a, b, c):
        put(cache, path)
    _, generation = cache.lookup(a, (1,))
    original_bytes = cache.stats()["bytes"]
    # 范围外 c 比 a、b 更新，缩小时也应先淘汰 c。
    cache.set_max_bytes(original_bytes - 1)
    assert get(cache, c) is None
    assert get(cache, a) and get(cache, b)
    assert cache.stats()["bytes"] < original_bytes
    cache.set_max_bytes(original_bytes * 2)
    assert cache.lookup(a, (1,))[1] == generation
    assert get(cache, a) and get(cache, b)
    cache.set_max_bytes(0)
    cache.store(c, (1,), {"title": "迟到的白鹭"}, generation)
    assert cache.stats() == {"entries": 0, "bytes": 0}
    cache.set_max_bytes(original_bytes)
    cache.store(c, (1,), {"title": "迟到的白鹭"}, generation)
    assert get(cache, c)["title"] == "迟到的白鹭"


def test_parallel_readers_keep_capacity_and_copies(tmp_path):
    cache = MetadataMemoryCache(max_entries=20, max_bytes=100_000)

    def use(i):
        path = str(tmp_path / str(i % 40))
        put(cache, path)
        result = get(cache, path)
        if result:
            result["tags"].clear()
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(use, range(500)))
    assert cache.stats()["entries"] <= 20
    assert cache.stats()["bytes"] <= 100_000


@pytest.fixture
def loader_factory(tmp_path, monkeypatch):
    cache = MetadataMemoryCache()
    reads = []
    monkeypatch.setattr(_workers.meta_disk_cache, "get_many", lambda *args: {})
    monkeypatch.setattr(_workers, "fast_read_browser_metadata", lambda paths: ({}, paths))

    def read(paths, **kwargs):
        reads.extend(paths)
        assert not kwargs["use_cache"]
        return {path: {"SourceFile": path, "ExifIFD:ISO": 800,
                       "IFD0:Make": "SONY", "ExifIFD:ExifImageWidth": 6000,
                       "ExifIFD:ExifImageHeight": 4000} for path in paths}

    monkeypatch.setattr(_workers, "read_batch_metadata", read)

    def make(paths, tags=None, rows=None):
        return _workers.MetadataLoader(paths, object(), metadata_tags=tags or ["-ISO"],
                                       report_rows_by_path=rows, memory_cache=cache,
                                       selected_dir=str(tmp_path))
    return make, cache, reads


@pytest.mark.parametrize("change", ["source", "tags", "report"])
def test_new_loaders_reuse_hif_and_revalidate_source_tags_report(tmp_path, loader_factory, change):
    make, cache, reads = loader_factory
    photo = tmp_path / "白鹭.HIF"
    photo.write_bytes(b"heif test")
    path = str(photo)
    tags = ["-ISO"]
    rows = {path: {"bird_species_cn": "白鹭"}}
    first = make([path], tags=tags, rows=rows)._read_parse_chunk([path])[0]
    second = make([path], tags=tags, rows=rows)._read_parse_chunk([path])[0]
    assert second == first and reads == [path]
    if change == "source":
        photo.write_bytes(b"changed source")
    elif change == "tags":
        tags.append("-Make")
    else:
        rows[path]["bird_species_cn"] = "小白鹭"
    make([path], tags=tags, rows=rows)._read_metadata_batch([path])
    assert len(reads) == 2
    make([path], tags=tags, rows=rows)._read_metadata_batch([path])
    assert len(reads) == 2
    photo.unlink()
    make([path], tags=tags, rows=rows)._read_metadata_batch([path])
    assert cache.stats()["entries"] == 0


def test_chinese_sidecar_creation_edit_deletion_and_raw_jpeg_siblings(tmp_path, loader_factory):
    make, _cache, reads = loader_factory
    paths = [str(tmp_path / ("白鹭" + suffix)) for suffix in (".HIF", ".JPG")]
    for path in paths:
        Path(path).write_bytes(b"source")
    store = PhotoMetaDataXMP()
    make(paths)._read_metadata_batch(paths)
    assert len(reads) == 2
    store.write(paths[0], {"XMP-dc:Title": "飞行中的白鹭"})
    assert store.read(paths[0])["XMP-dc:Title"] == "飞行中的白鹭"
    result = make(paths)._read_metadata_batch(paths)
    assert all(result[p]["XMP-dc:Title"] == "飞行中的白鹭" for p in paths)
    assert len(reads) == 4
    make(paths)._read_metadata_batch(paths)
    assert len(reads) == 4
    store.write(paths[1], {"XMP-dc:Title": "休息中的白鹭"})
    result = make(paths)._read_metadata_batch(paths)
    assert all(result[p]["XMP-dc:Title"] == "休息中的白鹭" for p in paths)
    (tmp_path / "白鹭.xmp").unlink()
    result = make(paths)._read_metadata_batch(paths)
    assert all(not result[p].get("XMP-dc:Title") for p in paths)
    assert len(reads) == 8


def test_signatures_follow_parent_and_case_insensitive_xmp(tmp_path):
    export = tmp_path / "Export"
    export.mkdir()
    photo = export / "Bird.JPG"
    photo.write_bytes(b"source")
    xmp = tmp_path / "BIRD.XmP"
    path = str(photo)
    before = metadata_signatures([path], [], {})
    xmp.write_text("中文侧车", encoding="utf-8")
    after = metadata_signatures([path], [], {})
    assert before != after
    xmp.write_text("修改后的侧车", encoding="utf-8")
    assert after != metadata_signatures([path], [], {})


def test_failure_and_file_change_during_read_are_not_cached(tmp_path, loader_factory, monkeypatch):
    make, cache, _reads = loader_factory
    photo = tmp_path / "鸟.HIF"
    photo.write_bytes(b"source")
    path = str(photo)

    def fail(*args, **kwargs):
        raise OSError("temporary failure")

    monkeypatch.setattr(_workers, "read_batch_metadata", fail)
    make([path])._read_metadata_batch([path])
    assert cache.stats()["entries"] == 0

    def change(*args, **kwargs):
        photo.write_bytes(b"new source")
        return {path: {"SourceFile": path, "ISO": 100}}

    monkeypatch.setattr(_workers, "read_batch_metadata", change)
    make([path])._read_metadata_batch([path])
    assert cache.stats()["entries"] == 0


def test_unreadable_sidecar_and_cancelled_read_are_not_cached(tmp_path, loader_factory, monkeypatch):
    make, cache, reads = loader_factory
    photo = tmp_path / "鸟.HIF"
    photo.write_bytes(b"source")
    (tmp_path / "鸟.xmp").write_text("<broken", encoding="utf-8")
    path = str(photo)
    make([path])._read_metadata_batch([path])
    make([path])._read_metadata_batch([path])
    assert len(reads) == 2 and cache.stats()["entries"] == 0
    (tmp_path / "鸟.xmp").unlink()
    loader = make([path])

    def cancel(*args, **kwargs):
        loader.stop()
        return {path: {"SourceFile": path, "ISO": 100}}

    monkeypatch.setattr(_workers, "read_batch_metadata", cancel)
    assert loader._read_parse_chunk([path])[0] == {}
    assert cache.stats()["entries"] == 0


def test_raw_report_fast_path_reuses_embedded_focus(tmp_path, loader_factory, monkeypatch):
    make, _cache, reads = loader_factory
    photo = tmp_path / "鸟.ARW"
    photo.write_bytes(b"source")
    path = str(photo)
    rows = {path: {"iso": 800, "shutter_speed": "0.001", "aperture": 5.6,
                   "focal_length": 400, "camera_model": "ILCE-1M2"}}
    focus_reads = []

    def focus(source):
        focus_reads.append(source)
        return {"Make": "SONY", "Model": "ILCE-1M2", "Sony:CameraOrientation": 6}

    monkeypatch.setattr(_workers, "read_raw_embedded_focus_metadata", focus)
    first = make([path], rows=rows)._read_metadata_batch([path])
    second = make([path], rows=rows)._read_metadata_batch([path])
    assert first == second and focus_reads == [path] and reads == []
