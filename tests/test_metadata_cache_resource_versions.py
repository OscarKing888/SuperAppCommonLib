"""Cache controls must invalidate resources, not unrelated directory work."""
import threading

from app_common.exif_io.metadata_sqlite import MetadataSQLiteCache


def test_edit_does_not_discard_another_photos_queued_cache(tmp_path, monkeypatch):
    cache = MetadataSQLiteCache(tmp_path / "cache.sqlite")
    started, release = threading.Event(), threading.Event()
    connect = cache._connect_writer

    def paused():
        started.set()
        assert release.wait(5)
        return connect()

    monkeypatch.setattr(cache, "_connect_writer", paused)
    a, b = str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")
    try:
        revision = cache.revision()
        assert cache.put_many("test", {a: ((1,), {"title": "白鹭"}, None)}, revision)
        assert started.wait(2)
        cache.refresh_xmp(b)
        release.set()
        assert cache.flush()
        assert cache.get_many("test", {a: (1,)}) == {a: {"title": "白鹭"}}
    finally:
        release.set()
        cache.close()


def test_recent_read_survives_capacity_eviction(tmp_path):
    cache = MetadataSQLiteCache(tmp_path / "cache.sqlite", max_entries=2)
    a, b, c = [str(tmp_path / name) for name in ("a", "b", "c")]
    try:
        for path in (a, b):
            cache.put_many("test", {path: ((1,), {"path": path}, None)}, cache.revision())
        assert cache.flush()
        assert cache.get_many("test", {a: (1,)})
        cache.put_many("test", {c: ((1,), {"path": c}, None)}, cache.revision())
        assert cache.flush()
        assert set(cache.get_many("test", dict.fromkeys((a, b, c), (1,)))) == {a, c}
    finally:
        cache.close()


def test_scope_refresh_accepts_unrelated_old_ticket(tmp_path):
    cache = MetadataSQLiteCache(tmp_path / "cache.sqlite")
    other = str(tmp_path / "other" / "bird.jpg")
    try:
        revision = cache.revision()
        cache.invalidate_scope(str(tmp_path / "selected"))
        assert cache.put_many("test", {other: ((1,), {"title": "鹭"}, None)}, revision)
        assert cache.flush()
        assert cache.get_many("test", {other: (1,)})[other]["title"] == "鹭"
    finally:
        cache.close()
