"""Accepted local edits survive deferred/failed XMP publication and cache churn."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from app_common.exif_io.metadata_store import MetadataStore, resource_key
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, PhotoMetaDataReportDB


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app_common.exif_io import metadata_store
    monkeypatch.setenv("SUPERBIRD_METADATA_STORE_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_: None)
    result = MetadataStore(tmp_path / "store.sqlite3", start_scheduler=False)
    monkeypatch.setattr(metadata_store, "_default", result)
    yield result
    result.close()


def photo(tmp_path, name="白鹭.JPG"):
    result = tmp_path / name
    result.write_bytes(b"original photo")
    return str(result)


def test_direct_write_receipt_belongs_to_that_write_not_a_later_edit(store, tmp_path):
    from app_common.exif_io.metadata_store import write_with_published_revision
    path = photo(tmp_path)
    later = []

    class Writer:
        def write(self, path, fields):
            ok = PhotoMetaDataXMP().write(path, fields)
            later.append(store.submit_edit(path, {"title": "随后提交的新鸟名"}).result(3))
            return ok

    revision = write_with_published_revision(Writer(), path, {"title": "识鸟结果"})
    assert revision < later[0].revision == store.current_revision(path)
    assert store.peek(path)["Title"] == "随后提交的新鸟名"


def test_failed_or_unnotified_direct_write_cannot_return_success_revision(store, tmp_path):
    from types import SimpleNamespace
    from app_common.exif_io.metadata_store import write_with_published_revision
    path = photo(tmp_path)
    assert write_with_published_revision(SimpleNamespace(write=lambda *args: False), path, {"title": "失败"}) is None
    with pytest.raises(RuntimeError, match="版本通知"):
        write_with_published_revision(SimpleNamespace(write=lambda *args: True), path, {"title": "未通知"})
    assert store.current_revision(path) == 0


def test_direct_write_notification_completes_after_memory_publication(store, tmp_path, monkeypatch):
    from collections import OrderedDict
    entered, release = threading.Event(), threading.Event()

    class DelayedVersions(OrderedDict):
        def __setitem__(self, key, value):
            entered.set()
            assert release.wait(3)
            super().__setitem__(key, value)

    monkeypatch.setattr(store, "_versions", DelayedVersions())
    path = photo(tmp_path)
    future = store.note_sidecar_write(path)
    try:
        assert entered.wait(3)
        assert not future.done()
    finally:
        release.set()
    revision, _pending = future.result(3)
    assert store.current_revision(path) == revision


def test_commit_is_sql_only_and_reads_pending_before_sidecar(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    monkeypatch.setattr(PhotoMetaDataXMP, "write", lambda *args: pytest.fail("commit cannot write XML"))
    store.remember_many({path: {"XMP-dc:Title": "旧标题", "ISO": 100}}).result(3)
    receipt = store.submit_edit(path, {"title": "中文新标题", "rating": 4}).result(3)
    assert receipt.before["XMP-dc:Title"] == "旧标题"
    assert receipt.record["Title"] == "中文新标题"
    assert store.peek(path)["rating"] == 4
    assert store.read_many([path])[path]["Title"] == "中文新标题"
    assert store.current_revision(path) == receipt.revision == 1
    assert not Path(path).with_suffix(".xmp").exists()


def test_outbox_survives_restart_refresh_and_cache_eviction(store, tmp_path):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "断点恢复"}).result(3)
    store.refresh_scope(str(tmp_path)).result(3)
    store.max_entries = 0
    store.remember_many({photo(tmp_path, "other.JPG"): {"ISO": 200}}).result(3)
    database = store.path
    store.close()
    reopened = MetadataStore(database, start_scheduler=False)
    try:
        assert reopened.pending_status([path])[0]["revision"] == 1
        assert reopened.flush_paths([path])
        assert PhotoMetaDataXMP().read(path)["Title"] == "断点恢复"
        assert not reopened.pending_status()
    finally:
        reopened.close()


def test_same_stem_pending_overlay_and_chinese_sync(store, tmp_path):
    raw = photo(tmp_path, "白鹭.ARW")
    jpeg = photo(tmp_path)
    store.remember_many({p: {"Title": "旧鸟名"} for p in (raw, jpeg)}).result(3)
    store.submit_edit(raw, {"title": "池鹭", "description": "中文描述 Ω", "pick": 1}).result(3)
    assert store.read_many([jpeg])[jpeg]["Title"] == "池鹭"
    assert store.flush_paths([jpeg])
    assert PhotoMetaDataXMP().read(jpeg)["Description"] == "中文描述 Ω"
    assert PhotoMetaDataXMP().read(raw)["pick"] == 1


def test_subject_intent_preserves_disk_only_subjects_and_before_snapshot(store, tmp_path):
    path = photo(tmp_path)
    assert PhotoMetaDataXMP().write_subjects(path, ["unknown;literal", "remove", "external"])
    store.remember_many({path: {"XMP-dc:Subject": ["unknown;literal", "remove"]}}, profile="subjects").result(3)
    receipt = store.submit_edit(path, {}, subject_states={"remove": False, "中文标签": True}).result(3)
    assert receipt.before["XMP-dc:Subject"] == ["unknown;literal", "remove"]
    assert receipt.record["XMP-dc:Subject"] == ["unknown;literal", "中文标签"]
    assert store.flush_paths([path])
    assert PhotoMetaDataXMP().read_subjects(path, strict=True) == ["unknown;literal", "external", "中文标签"]
    assert store.peek(path, profile="subjects")["XMP-dc:Subject"] == ["unknown;literal", "external", "中文标签"]
    assert store.peek_revision(path, profile="subjects") > receipt.revision


def test_failed_sync_keeps_edit_and_retry_information(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "仍须保存"}).result(3)
    original = PhotoMetaDataXMP.write
    monkeypatch.setattr(PhotoMetaDataXMP, "write", lambda *_: False)
    assert not store.flush_paths([path])
    pending = store.pending_status([path])[0]
    assert pending["attempts"] == 1 and pending["error"]
    monkeypatch.setattr(PhotoMetaDataXMP, "write", original)
    assert store.flush_paths([path])
    assert PhotoMetaDataXMP().read(path)["Title"] == "仍须保存"


def test_new_revision_is_not_acknowledged_by_older_sync(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "第一版"}).result(3)
    started, release = threading.Event(), threading.Event()
    original = PhotoMetaDataXMP.write
    def slow(self, source, fields):
        started.set()
        assert release.wait(3)
        return original(self, source, fields)
    monkeypatch.setattr(PhotoMetaDataXMP, "write", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(store._sync_one, path)
        assert started.wait(3)
        store.submit_edit(path, {"title": "第二版"}).result(3)
        release.set()
        assert pending.result(3)
    assert store.pending_status([path])[0]["revision"] == 2
    assert store.peek(path)["Title"] == "第二版"
    assert store.flush_paths([path])
    assert PhotoMetaDataXMP().read(path)["Title"] == "第二版"


def test_stale_import_cannot_overwrite_edit_after_sync(store, tmp_path):
    path = photo(tmp_path)
    versions = store.revisions([path])
    store.submit_edit(path, {"title": "新值"}).result(3)
    assert store.flush_paths([path])
    assert store.remember_many({path: {"Title": "过时值"}}, expected_revisions=versions).result(3) == {}
    assert store.read_many([path])[path]["Title"] == "新值"
    current = store.revisions([path])
    store.remember_many({path: {"Title": "外部后来编辑"}}, expected_revisions=current).result(3)
    assert store.read_many([path])[path]["Title"] == "外部后来编辑"


def test_full_profile_is_not_satisfied_by_partial_edit(store, tmp_path):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "待同步"}).result(3)
    assert store.read_many([path], profile="full") == {}
    assert store.peek(path, profile="full") is None


def test_file_operation_flushes_subtree_and_rejects_edits_across_stores(store, tmp_path):
    path = photo(tmp_path)
    other = MetadataStore(store.path, start_scheduler=False)
    try:
        store.submit_edit(path, {"title": "先同步"}).result(3)
        with store.file_operation([str(tmp_path)]):
            assert not store.pending_status()
            assert PhotoMetaDataXMP().read(path)["Title"] == "先同步"
            with store.file_operation([path]):
                with pytest.raises(RuntimeError, match="未被接受"):
                    other.submit_edit(path, {"rating": 5}).result(3)
            with pytest.raises(RuntimeError):
                with other.file_operation([path]):
                    pytest.fail("must not acquire another process/store guard")
        other.submit_edit(path, {"rating": 5}).result(3)
        assert other.flush_paths([path])
    finally:
        other.close()


def test_failed_flush_prevents_file_operation_and_releases_guard(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "保留"}).result(3)
    monkeypatch.setattr(PhotoMetaDataXMP, "write", lambda *_: False)
    with pytest.raises(OSError, match="文件操作已停止"):
        with store.file_operation([path]):
            pytest.fail("file mutation must not start")
    assert store.submit_edit(path, {"rating": 3}).result(3).revision == 2


def test_change_feed_and_cache_totals(store, tmp_path):
    path = photo(tmp_path)
    store.remember_many({path: {"ISO": 200}}).result(3)
    store.remember_many({path: {"ISO": 400}}).result(3)
    store.submit_edit(path, {"title": "白鹭"}).result(3)
    changes = store.changes_since(0)
    assert changes["changes"][-1]["kind"] == "edit"
    assert changes["changes"][-1]["resource"] == resource_key(path)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count,bytes FROM snapshot_totals").fetchone() == conn.execute("SELECT count(*),sum(bytes) FROM snapshots").fetchone()


def test_pending_partial_survives_refresh_without_faking_complete_profile(store, tmp_path):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "新标题"}).result(3)
    store.refresh_scope(str(tmp_path)).result(3)
    assert store.peek(path)["Title"] == "新标题"
    record = store.read_many([path])[path]
    assert record["_metadata_complete"] is False
    assert store.read_many([path], profile="full") == {}
    assert store.read_many([path], signatures={path: (1,)}) == {}


def test_edit_preserves_validated_complete_snapshot_signature(store, tmp_path):
    path = photo(tmp_path)
    signatures = {path: ((1,), None, "full")}
    store.remember_many({path: {"ISO": 800}}, profile="full", signatures=signatures).result(3)
    store.submit_edit(path, {"title": "本地编辑"}).result(3)
    record = store.read_many([path], profile="full", signatures=signatures)[path]
    assert record["Title"] == "本地编辑" and record["ISO"] == 800
    assert record["_metadata_complete"] is True


def test_external_import_changes_feed_only_when_payload_changes(store, tmp_path):
    path = photo(tmp_path)
    store.remember_many({path: {"ISO": 100}}).result(3)
    first = store.changes_since(0)["seq"]
    store.remember_many({path: {"ISO": 100}}).result(3)
    assert store.changes_since(first)["changes"] == []
    store.remember_many({path: {"ISO": 200}}).result(3)
    assert store.changes_since(first)["changes"][0]["kind"] == "import"
    assert store.peek_revision(path) == 1
    last = store.changes_since(first)["seq"]
    store.remember_many({path: {"ISO": 200}}).result(3)
    assert store.changes_since(last)["changes"] == []
    assert store.peek_revision(path) == 1


def test_read_touch_protects_hot_snapshot_from_eviction(store, tmp_path):
    a, b, c = [photo(tmp_path, name + ".JPG") for name in "abc"]
    store.max_entries = 2
    store.remember_many({a: {"ISO": 100}, b: {"ISO": 200}}).result(3)
    store.read_many([a])
    store.remember_many({c: {"ISO": 300}}).result(3)
    assert set(store.read_many([a, b, c])) == {a, c}


def test_derived_read_alias_observes_parent_edit_but_writes_own_sidecar(store, tmp_path):
    parent = photo(tmp_path)
    derived_dir = tmp_path / "Export"
    derived_dir.mkdir()
    derived = photo(derived_dir)
    parent_sidecar = str(Path(parent).with_suffix(".xmp"))
    signatures = {path: ((1,), (parent_sidecar, (2,)), "full") for path in (parent, derived)}
    store.remember_many({path: {"Title": "旧标题"} for path in (parent, derived)}, signatures=signatures).result(3)
    store.submit_edit(parent, {"title": "父侧车新值"}).result(3)
    assert store.read_many([derived])[derived]["Title"] == "父侧车新值"
    assert store.peek(derived)["Title"] == "父侧车新值"
    assert store.revisions([derived], signatures=signatures)[derived] == 1
    store.submit_edit(derived, {"title": "派生图独立新值"}).result(3)
    assert store.flush_paths([parent, derived])
    assert PhotoMetaDataXMP().read(parent)["Title"] == "父侧车新值"
    assert PhotoMetaDataXMP().read(derived)["Title"] == "派生图独立新值"


def test_system_sidecar_lock_excludes_another_process(store, tmp_path):
    from app_common.exif_io.photo_meta import xmp_sidecar_write_lock
    path = photo(tmp_path)
    code = """import sys
from app_common.exif_io.photo_meta import xmp_sidecar_write_lock
with xmp_sidecar_write_lock(sys.argv[1]):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", code, path], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        assert child.stdout.readline().strip() == "locked"
        lock = xmp_sidecar_write_lock(str(Path(path).with_suffix(".ARW")))
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        assert not acquired
    finally:
        child.communicate("release\n", timeout=5)
    assert child.returncode == 0
    with xmp_sidecar_write_lock(path):
        pass


def test_pool_barrier_timeout_preserves_pending_and_ownership(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "慢侧车"}).result(3)
    started, release = threading.Event(), threading.Event()
    original = PhotoMetaDataXMP.write
    def slow(self, source, fields):
        started.set()
        assert release.wait(3)
        return original(self, source, fields)
    monkeypatch.setattr(PhotoMetaDataXMP, "write", slow)
    class Pool:
        def __init__(self):
            self.executor = ThreadPoolExecutor(max_workers=1)
        def submit_action(self, action, **kwargs):
            return self.executor.submit(action.execute)
        def cancel(self, future):
            future.cancel()
    pool = Pool()
    store.attach_pool(pool)
    try:
        store._schedule_sync(path, pool)
        assert started.wait(3)
        before = time.monotonic()
        assert not store.flush_paths([path], timeout=.1)
        assert time.monotonic() - before < 1
        assert started.is_set() and store.pending_status()
    finally:
        release.set()
        pool.executor.shutdown(wait=True)
        store.detach_pool(pool)
    assert PhotoMetaDataXMP().read(path)["Title"] == "慢侧车"


def test_another_store_replays_and_polls_committed_changes(store, tmp_path):
    path = photo(tmp_path)
    store.remember_many({path: {"Title": "初始"}}).result(3)
    class Pool:
        def __init__(self):
            self.executor = ThreadPoolExecutor(max_workers=2)
        def submit_action(self, action, **kwargs):
            return self.executor.submit(action.execute)
        def cancel(self, future):
            future.cancel()
    pool = Pool()
    other = MetadataStore(store.path)
    try:
        other.read_many([path])
        other.attach_pool(pool)
        store.submit_edit(path, {"title": "跨应用编辑"}).result(3)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not other.pending_status() and (other.peek(path) or {}).get("Title") == "跨应用编辑":
                break
            time.sleep(.02)
        assert other.peek(path)["Title"] == "跨应用编辑"
        assert not other.pending_status()
        assert PhotoMetaDataXMP().read(path)["Title"] == "跨应用编辑"
        # A reconciler importing a genuinely later external edit publishes one
        # change and refreshes the other application's warmed memory snapshot.
        other.detach_pool(pool)
        other.remember_many({path: {"Title": "外部编辑"}}, expected_revisions=other.revisions([path])).result(3)
        assert other.peek(path)["Title"] == "外部编辑"
    finally:
        pool.executor.shutdown(wait=True)
        other.close()


def test_process_exit_after_commit_keeps_durable_edit(store, tmp_path):
    path = photo(tmp_path)
    code = """import os,sys
from app_common.exif_io.metadata_store import MetadataStore
store=MetadataStore(sys.argv[1],start_scheduler=False)
store.submit_edit(sys.argv[2],{'XMP-dc:Title':'crash-recovery'}).result(5)
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", code, str(store.path), path], capture_output=True,
                            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr
    assert not Path(path).with_suffix(".xmp").exists()
    assert store.read_many([path])[path]["Title"] == "crash-recovery"
    assert store.flush_paths([path])
    assert PhotoMetaDataXMP().read(path)["Title"] == "crash-recovery"


def test_file_operation_allows_owner_but_rejects_concurrent_direct_writer(store, tmp_path):
    path = photo(tmp_path)
    destination = str(tmp_path / "moved.JPG")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.file_operation([path]):
            assert PhotoMetaDataXMP().write_title(path, "事务内编辑")
            assert not pool.submit(PhotoMetaDataXMP().write_title, path, "迟到写入").result(3)
            os.replace(path, destination)
            os.replace(str(Path(path).with_suffix(".xmp")), str(Path(destination).with_suffix(".xmp")))
        assert not pool.submit(PhotoMetaDataXMP().write_title, path, "已移动照片").result(3)
    assert not Path(path).with_suffix(".xmp").exists()
    assert PhotoMetaDataXMP().read(destination)["Title"] == "事务内编辑"


def test_file_operation_waits_for_earlier_direct_source_transaction(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    started, release, entered = threading.Event(), threading.Event(), threading.Event()
    original = PhotoMetaDataXMP._load_or_create_xmp_tree
    def slow(sidecar):
        started.set()
        assert release.wait(3)
        return original(sidecar)
    monkeypatch.setattr(PhotoMetaDataXMP, "_load_or_create_xmp_tree", staticmethod(slow))
    def operation():
        with store.file_operation([path]):
            entered.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(PhotoMetaDataXMP().write_title, path, "先完成写入")
        assert started.wait(3)
        moving = pool.submit(operation)
        assert not entered.wait(.05)
        release.set()
        assert writer.result(3)
        moving.result(3)
    assert entered.is_set()


def test_direct_write_invalidates_shared_aliases_and_preserves_pending(store, tmp_path):
    path = photo(tmp_path)
    derived_dir = tmp_path / "Export"
    derived_dir.mkdir()
    derived = photo(derived_dir)
    signatures = {p: ((1,), (str(Path(path).with_suffix(".xmp")), (2,)), "browser") for p in (path, derived)}
    store.remember_many({p: {"Title": "旧标题"} for p in (path, derived)}, signatures=signatures).result(3)
    store.submit_edit(path, {"description": "尚未同步"}).result(3)
    seq = store.changes_since(0)["seq"]
    assert PhotoMetaDataXMP().write_title(path, "分析结果")
    assert store.changes_since(seq)["changes"][-1]["kind"] == "external"
    assert store.affected_paths([derived], [resource_key(path)]) == [derived]
    assert store.peek(derived)["Description"] == "尚未同步"
    assert store.read_many([derived])[derived]["Description"] == "尚未同步"
    assert store.read_many([path])[path]["Description"] == "尚未同步"
    assert store.pending_status([path])
    assert store.flush_paths([path])
    assert PhotoMetaDataXMP().read(path)["Title"] == "分析结果"
    assert PhotoMetaDataXMP().read(path)["Description"] == "尚未同步"


def test_sync_emits_no_recursive_external_change(store, tmp_path):
    path = photo(tmp_path)
    store.submit_edit(path, {"title": "同步"}).result(3)
    assert store.flush_paths([path])
    assert [item["kind"] for item in store.changes_since(0)["changes"]] == ["edit", "synced"]


@pytest.mark.parametrize("synchronize", [False, True])
def test_close_waits_for_actual_source_completion_before_closing_sql(store, tmp_path, monkeypatch, synchronize):
    path = photo(tmp_path)
    if synchronize:
        store.submit_edit(path, {"title": "关闭前完成"}).result(3)
    started, release = threading.Event(), threading.Event()
    original = PhotoMetaDataXMP._load_or_create_xmp_tree
    def slow(sidecar):
        started.set()
        assert release.wait(3)
        return original(sidecar)
    monkeypatch.setattr(PhotoMetaDataXMP, "_load_or_create_xmp_tree", staticmethod(slow))
    with ThreadPoolExecutor(max_workers=2) as pool:
        operation = pool.submit(store._sync_one, path) if synchronize else pool.submit(PhotoMetaDataXMP().write_title, path, "关闭前完成")
        assert started.wait(3)
        closing = pool.submit(store.close)
        time.sleep(.05)
        assert not closing.done() and store._sql.is_alive()
        release.set()
        assert operation.result(3)
        closing.result(3)
    assert not store._sql.is_alive()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM source_write_leases").fetchone()[0] == 0
        if synchronize:
            assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_simultaneous_store_initialization_retries_wal_schema_contention(tmp_path):
    path = tmp_path / "simultaneous.sqlite3"
    stores = [MetadataStore(path, start_scheduler=False) for _ in range(6)]
    try:
        for item in stores:
            assert item.submit_sql(lambda conn: conn.execute("PRAGMA journal_mode").fetchone()[0], write=False).result(8) == "wal"
    finally:
        for item in stores:
            item.close()


def test_cleared_camera_title_and_comment_survive_sync_and_reopen(store, tmp_path, monkeypatch):
    from PIL import Image
    from app_common.exif_io import metadata_store
    from app_common.exif_io.exiftool_path import get_exiftool_executable_path
    from app_common.exif_io.reader import extract_metadata_with_xmp_priority
    if not get_exiftool_executable_path():
        pytest.skip("ExifTool unavailable")
    path = tmp_path / "camera.jpg"
    exif = Image.Exif()
    exif[269], exif[270], exif[272] = "Camera title", "Camera comment", "Camera model"
    Image.new("RGB", (8, 8), "green").save(path, exif=exif)
    original = path.read_bytes()
    fields = {"XMP-dc:Title": "", "XMP-dc:Description": "", "XMP-tiff:Model": ""}
    store.submit_edit(str(path), fields).result(3)
    assert store.flush_paths([str(path)])
    store.close()
    reopened = MetadataStore(store.path, start_scheduler=False)
    monkeypatch.setattr(metadata_store, "_default", reopened)
    try:
        fresh = extract_metadata_with_xmp_priority(path)
        assert all(fresh[key] == "" for key in fields)
        assert fresh["Title"] == fresh["Description"] == fresh["Model"] == ""
        assert path.read_bytes() == original
    finally:
        reopened.close()


def test_later_nonempty_edit_removes_clear_marker_and_zero_is_not_a_clear(store, tmp_path, monkeypatch):
    import json
    path = photo(tmp_path)
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_: {"title": "报告旧值", "bird_species_cn": "报告鸟名"})
    writer = PhotoMetaDataXMP()
    assert writer.write_title(path, "")
    assert writer.write_rating_pick(path, rating=0, pick=0)
    cleared = writer.read(path)
    assert cleared["Title"] == ""
    assert json.loads(cleared["XMP-superpicky:metadata_cleared_fields"]) == ["XMP-dc:Title"]
    assert writer.write_title(path, "新标题")
    fresh = writer.read(path)
    assert fresh["Title"] == "新标题"
    assert "XMP-superpicky:metadata_cleared_fields" not in fresh
    assert int(fresh["rating"]) == int(fresh["pick"]) == 0


def test_close_keeps_sql_for_owned_file_transaction_and_final_direct_write(store, tmp_path):
    path = photo(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def operation():
        with store.file_operation([path]):
            entered.set()
            assert release.wait(3)
            assert PhotoMetaDataXMP().write_title(path, "事务完整完成")
    with ThreadPoolExecutor(max_workers=2) as pool:
        current = pool.submit(operation)
        assert entered.wait(3)
        closing = pool.submit(store.close)
        time.sleep(.05)
        assert not closing.done() and store._sql.is_alive()
        release.set()
        current.result(3)
        closing.result(3)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM operation_guards").fetchone()[0] == 0
    assert PhotoMetaDataXMP().read(path)["Title"] == "事务完整完成"


def test_late_sql_read_cannot_replace_newer_committed_and_synced_memory(store, tmp_path, monkeypatch):
    path = photo(tmp_path)
    store.remember_many({path: {"Title": "旧值"}}).result(3)
    read_ready, release = threading.Event(), threading.Event()
    original = store._remember_memory
    def publish(profile, source, record):
        if threading.current_thread().name.startswith("late-read"):
            read_ready.set()
            assert release.wait(3)
        return original(profile, source, record)
    monkeypatch.setattr(store, "_remember_memory", publish)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-read") as pool:
        old = pool.submit(store.read_many, [path])
        assert read_ready.wait(3)
        store.submit_edit(path, {"title": "新值"}).result(3)
        assert store.flush_paths([path])
        release.set()
        assert old.result(3)[path]["Title"] == "新值"
    assert store.peek(path)["Title"] == "新值"


def test_changed_alias_import_advances_once_and_keeps_pending_version(store, tmp_path):
    raw, jpeg = photo(tmp_path, "one.ARW"), photo(tmp_path, "one.JPG")
    store.remember_many({path: {"ISO": 100} for path in (raw, jpeg)}).result(3)
    store.submit_edit(raw, {"title": "待同步标题"}).result(3)
    baseline = store.revisions([raw, jpeg])
    result = store.remember_many({path: {"ISO": 200} for path in (raw, jpeg)}, expected_revisions=baseline).result(3)
    assert set(result) == {raw, jpeg}
    assert store.current_revision(raw) == store.peek_revision(jpeg) == 2
    assert store.pending_status()[0]["revision"] == 2
    assert store.peek(jpeg)["Title"] == "待同步标题"


def test_import_after_cache_refresh_advances_known_path_version(store, tmp_path):
    path = photo(tmp_path)
    store.remember_many({path: {"ISO": 100}}).result(3)
    before = store.peek_revision(path)
    store.refresh_scope(str(tmp_path)).result(3)
    store.remember_many({path: {"ISO": 200}}).result(3)
    assert store.peek_revision(path) > before


def test_file_barriers_in_all_pool_slots_sync_inline_without_starvation(store, tmp_path):
    from app_common.file_browser._work_pool import BrowserWorkPool
    paths = [photo(tmp_path, f"worker-{index}.JPG") for index in range(3)]
    for path in paths:
        store.submit_edit(path, {"title": "工作线程内同步"}).result(3)
    pool = BrowserWorkPool(3, 2)
    pool.set_thumbnail_mode(False)
    store.attach_pool(pool)
    ready = threading.Barrier(3)
    def operation(path):
        assert pool.is_worker_thread()
        ready.wait(3)
        with store.file_operation([path], timeout=2):
            return PhotoMetaDataXMP().read(path)["Title"]
    try:
        futures = [pool.submit(operation, path) for path in paths]
        assert [future.result(5) for future in futures] == ["工作线程内同步"] * 3
        assert not store.pending_status()
    finally:
        store.detach_pool(pool)
        assert pool.shutdown(timeout=5)


@pytest.mark.parametrize("field, old, new, aliases", [
    ("XMP-tiff:Model", "旧相机", "新相机", ("Model", "camera_model", "IFD0:Model")),
    ("XMP-aux:Lens", "旧镜头", "新镜头", ("Lens", "LensModel", "lens_model")),
    ("XMP-exif:FNumber", 2.8, 5.6, ("FNumber", "aperture", "ExifIFD:FNumber")),
    ("XMP-exif:ExposureTime", "1/100", "1/200", ("ExposureTime", "shutter")),
    ("XMP-exif:FocalLength", 100, 200, ("FocalLength", "focal_length")),
    ("XMP-exif:DateTimeOriginal", "2020:01:01 00:00:00", "2026:10:28 01:02:03",
     ("DateTimeOriginal", "date_time_original")),
])
def test_pending_camera_edits_replace_bare_full_metadata_aliases(store, tmp_path, field, old, new, aliases):
    path = photo(tmp_path)
    store.remember_many({path: {alias: old for alias in aliases}}, profile="full-pillow").result(3)
    store.submit_edit(path, {field: new}).result(3)
    for record in (store.peek(path, profile="full-pillow"), store.read_many([path], profile="full-pillow")[path]):
        assert all(record[alias] == new for alias in aliases)
