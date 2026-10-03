"""Background derived caches must never replace a simultaneous user's XMP edit."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app_common.exif_io.photo_meta import (
    PhotoMetaDataReportDB, PhotoMetaDataXMP, xmp_sidecar_write_lock,
)


def test_photo_symlink_does_not_change_its_same_stem_sidecar_lock(tmp_path):
    target = tmp_path / "different" / "target.jpg"
    target.parent.mkdir()
    target.write_bytes(b"photo")
    link = tmp_path / "bird.jpg"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert xmp_sidecar_write_lock(str(link)) is xmp_sidecar_write_lock(str(link.with_suffix(".arw")))


@pytest.mark.parametrize("edit", ["title", "description", "subjects", "rating"])
def test_background_cache_and_user_edits_preserve_each_other(tmp_path, monkeypatch, edit):
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    photo = tmp_path / "翠鸟.jpg"
    raw = photo.with_suffix(".arw")
    photo.write_bytes(b"original jpeg")
    raw.write_bytes(b"original raw")
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), "原始标题")
    loaded = threading.Event()
    release = threading.Event()
    original_load = PhotoMetaDataXMP._load_or_create_xmp_tree

    def controlled_load(self, path):
        tree = original_load(path)
        if threading.current_thread().name.startswith("cache-writer"):
            loaded.set()
            assert release.wait(5)
        return tree

    monkeypatch.setattr(PhotoMetaDataXMP, "_load_or_create_xmp_tree", controlled_load)
    methods = {
        "title": lambda: store.write_title(str(photo), "新的中文标题"),
        "description": lambda: store.write_description(str(photo), "新的中文描述"),
        "subjects": lambda: store.add_subjects(str(photo), ["白鹭", "水鸟"]),
        "rating": lambda: store.write_rating_pick(str(photo), rating=5, pick=1),
    }
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cache-writer") as cache_executor, \
                ThreadPoolExecutor(max_workers=1) as user_executor:
            cache = cache_executor.submit(store.write, str(raw), {"XMP-superpicky:bird_body_cache_arw": "检测完成"})
            assert loaded.wait(5)
            # 同名 RAW/JPEG 的读取阶段就必须持有同一事务锁，而非仅锁最终 replace。
            lock = xmp_sidecar_write_lock(str(photo))
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            assert not acquired
            user = user_executor.submit(methods[edit])
            release.set()
            assert cache.result(timeout=5) and user.result(timeout=5)
    finally:
        release.set()
    metadata = store.read(str(photo))
    assert metadata["bird_body_cache_arw"] == "检测完成"
    if edit == "title":
        assert metadata["Title"] == "新的中文标题"
    elif edit == "description":
        assert metadata["Description"] == "新的中文描述"
    elif edit == "subjects":
        assert store.read_subjects(str(photo)) == ["白鹭", "水鸟"]
    else:
        assert metadata["rating"] == 5 and metadata["pick"] == 1
    assert photo.read_bytes() == b"original jpeg"
    assert raw.read_bytes() == b"original raw"
