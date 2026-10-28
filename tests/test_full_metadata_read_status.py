"""Failed source reads must remain retryable without poisoning full snapshots."""
import os
import threading

import pytest
from PIL import Image

from app_common.exif_io import metadata_store, reader
from app_common.exif_io.photo_meta import PhotoMetaDataXMP


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = metadata_store.MetadataStore(tmp_path / "store.sqlite3", start_scheduler=False)
    monkeypatch.setattr(metadata_store, "get_store", lambda: instance)
    yield instance
    instance.close()


@pytest.fixture
def photo(tmp_path):
    path = (tmp_path / "中文照片.png").resolve()
    Image.new("RGB", (8, 6), "green").save(path)
    return path


@pytest.mark.parametrize("failed_record", [None, {}, {"_metadata_read_ok": False},
                                         {"ExifTool:Error": "temporary failure"}])
def test_failed_full_read_is_not_cached_and_next_read_recovers(store, photo, failed_record):
    def failed(paths):
        return {} if failed_record is None else {photo: {"SourceFile": str(photo), **failed_record}}

    first = reader.read_full_metadata_snapshot([photo], failed)[photo]
    assert first["_metadata_read_ok"] is False
    assert first["_metadata_complete"] is False
    assert store.peek(str(photo), profile="full") is None

    recovered = reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "Model": "恢复后的相机"}},
    )[photo]
    assert recovered["Model"] == "恢复后的相机"
    assert recovered["_metadata_read_ok"] is True
    assert reader.read_full_metadata_snapshot(
        [photo], lambda paths: pytest.fail("successful snapshot was not cached"),
    )[photo]["Model"] == "恢复后的相机"


def test_successful_photo_without_exif_is_a_complete_cache_hit(store, photo, monkeypatch):
    monkeypatch.setattr(reader, "get_exiftool_executable_path", lambda: None)
    first = reader.extract_many_with_xmp_priority([photo])[photo]
    assert first["_metadata_read_ok"] is True
    assert first["_metadata_complete"] is True
    monkeypatch.setattr(reader, "extract_many", lambda *a, **k: pytest.fail("empty EXIF reread"))
    assert reader.extract_many_with_xmp_priority([photo])[photo]["_metadata_read_ok"] is True


def test_legacy_sourcefile_only_poison_is_retried(store, photo):
    store.remember_many(
        {str(photo): {"SourceFile": str(photo), "_metadata_complete": True}},
        profile="full", signatures=reader.full_metadata_signatures([str(photo)]),
    ).result()
    result = reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "Model": "重试成功"}},
    )[photo]
    assert result["Model"] == "重试成功"


def test_failed_revalidation_retains_complete_snapshot_and_pending_chinese_edit(store, photo):
    reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "Model": "原相机"}},
    )
    revision = store.submit_edit(str(photo), {"XMP-dc:Title": "未同步白鹭"}).result().revision
    # A changed fingerprint forces a source retry; the failure must not erase
    # already-known camera fields or the accepted edit in the returned snapshot.
    photo.write_bytes(photo.read_bytes() + b"changed")
    result = reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "_metadata_read_ok": False}},
    )[photo]
    assert result["Model"] == "原相机"
    assert result["Title"] == "未同步白鹭"
    assert result["_metadata_revision"] == revision
    assert result["_metadata_refresh_failed"] is True
    assert store.pending_edits(str(photo))["revision"] == revision
    recovered = reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "Model": "新相机"}},
    )[photo]
    assert recovered["Model"] == "新相机"
    assert recovered["Title"] == "未同步白鹭"


def test_legacy_xmp_only_full_row_needs_successful_source_revalidation(store, photo):
    store.remember_many(
        {str(photo): {"SourceFile": str(photo), "XMP-dc:Title": "旧失败时读到的侧车"}},
        profile="full", signatures=reader.full_metadata_signatures([str(photo)]),
    ).result()
    provisional = reader.read_full_metadata_snapshot([photo], lambda paths: {})[photo]
    assert provisional["_metadata_read_ok"] is False
    assert provisional["_metadata_complete"] is False
    recovered = reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "Model": "相机已恢复"}},
    )[photo]
    assert recovered["Model"] == "相机已恢复"
    assert recovered["_metadata_read_ok"] is True


def test_valid_xmp_does_not_turn_failed_source_into_complete_exif(store, photo):
    assert PhotoMetaDataXMP().write_title(str(photo), "中文侧车白鹭")
    result = reader.read_full_metadata_snapshot(
        [photo], lambda paths: {photo: {"SourceFile": str(photo), "_metadata_read_ok": False}},
    )[photo]
    assert result["Title"] == "中文侧车白鹭"
    assert result["_metadata_complete"] is False
    assert store.peek(str(photo), profile="full") is None


def test_failed_source_without_full_cache_still_reads_accepted_edit(store, photo):
    revision = store.submit_edit(str(photo), {"XMP-dc:Title": "SQLite 已保存白鹭"}).result().revision
    result = reader.read_full_metadata_snapshot([photo], lambda paths: {})[photo]
    assert result["Title"] == "SQLite 已保存白鹭"
    assert result["_metadata_revision"] == revision
    assert result["_metadata_read_ok"] is False
    assert result["_metadata_complete"] is False
    assert store.peek(str(photo), profile="full") is None


def test_cancelled_read_does_not_publish_partial_complete_snapshot(store, photo):
    stopped = threading.Event()

    def source(paths):
        stopped.set()
        return {photo: {"SourceFile": str(photo), "Model": "取消结果"}}

    assert reader.read_full_metadata_snapshot([photo], source, cancelled=stopped.is_set) == {}
    assert store.read_many([os.path.normpath(str(photo))], profile="full") == {}
