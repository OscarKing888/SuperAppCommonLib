"""Same-photo aliases share one durable edit sequence and one outbox."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import subprocess
import sys
import threading
import time

import pytest

from app_common.exif_io import metadata_store as module
from app_common.exif_io.metadata_store import MetadataStore, resource_key
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock


def aliases(tmp_path):
    folder = tmp_path / "long-directory-for-photo-alias"
    folder.mkdir()
    photo = folder / "original-photo.jpg"
    photo.write_bytes(b"photo")
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        assert ctypes.windll.kernel32.GetShortPathNameW(str(photo), buffer, len(buffer))
        short = buffer.value
        if os.path.normcase(short) == os.path.normcase(str(photo)):
            pytest.skip("This volume has no Windows short-name aliases")
    else:
        link = tmp_path / "alias"
        link.symlink_to(folder, target_is_directory=True)
        short = str(link / photo.name)
    return str(photo.resolve()), short


def test_alias_edits_keep_field_order_across_stores(tmp_path, monkeypatch):
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    monkeypatch.setattr(module, "_default", store)
    other = None
    try:
        first = store.submit_edit(alias, {"title": "旧中文标题"}).result(5)
        second = store.submit_edit(path, {"title": "新中文标题"}).result(5)
        store.submit_edit(alias, {"rating": 4}).result(5)
        assert first.revision < second.revision
        assert len(store.pending_status()) == 1
        other = MetadataStore(store.path, start_scheduler=False)
        assert other.pending_edits(alias)["fields"]["XMP-dc:Title"] == "新中文标题"
        assert other.flush_paths([path])
        assert store.flush_paths([alias])
        assert PhotoMetaDataXMP().read(path)["Title"] == "新中文标题"
        assert not store.pending_status()
    finally:
        if other: other.close()
        store.close()


def test_another_process_alias_edit_does_not_get_reverted_by_later_other_field(tmp_path, monkeypatch):
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    monkeypatch.setattr(module, "_default", store)
    try:
        store.submit_edit(alias, {"title": "旧标题"}).result(5)
        code = """import sys
from app_common.exif_io.metadata_store import MetadataStore
s=MetadataStore(sys.argv[1], start_scheduler=False)
s.submit_edit(sys.argv[2], {'title':'new-process-title'}).result(5)
s.close()
"""
        subprocess.run([sys.executable, "-c", code, str(store.path), path], check=True,
                       cwd=Path(__file__).resolve().parents[2], timeout=15)
        store.submit_edit(alias, {"rating": 5}).result(5)
        assert len(store.pending_status()) == 1
        assert store.flush_paths([path])
        assert PhotoMetaDataXMP().read(path)["Title"] == "new-process-title"
    finally:
        store.close()


def test_identity_resolution_is_not_on_submitter_or_sql_thread(tmp_path, monkeypatch):
    from app_common.exif_io import metadata_identity
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    called = []
    original = metadata_identity.os.path.realpath
    def resolve(value):
        called.append(threading.get_ident())
        return original(value)
    monkeypatch.setattr(metadata_identity.os.path, "realpath", resolve)
    try:
        store.submit_edit(alias, {"title": "异步规范身份"}).result(5)
        assert called and threading.get_ident() not in called and store._sql_ident not in called
        assert resource_key(alias) == resource_key(path)
    finally:
        store.close()


def test_retargeted_directory_alias_does_not_redirect_an_accepted_old_edit(tmp_path, monkeypatch):
    a, b, link = tmp_path / "a", tmp_path / "b", tmp_path / "current"
    a.mkdir(); b.mkdir()
    for directory in (a, b):
        (directory / "p.jpg").write_bytes(b"photo")
    try:
        link.symlink_to(a, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    monkeypatch.setattr(module, "_default", store)
    try:
        alias = str(link / "p.jpg")
        store.submit_edit(alias, {"title": "原目标已接受"}).result(5)
        link.unlink()
        link.symlink_to(b, target_is_directory=True)
        store.submit_edit(alias, {"description": "新目标已接受"}).result(5)
        assert len(store.pending_status()) == 2
        assert store.flush_paths([str(a), str(b)])
        assert PhotoMetaDataXMP().read(str(a / "p.jpg"))["Title"] == "原目标已接受"
        assert "Title" not in PhotoMetaDataXMP().read(str(b / "p.jpg"))
        assert PhotoMetaDataXMP().read(str(b / "p.jpg"))["Description"] == "新目标已接受"
    finally:
        store.close()


def test_short_filename_direct_writer_and_readers_use_canonical_sidecar(tmp_path):
    from app_common.exif_io.xmp_sidecar import read_xmp_sidecar, find_xmp_sidecars
    path, alias = aliases(tmp_path)
    assert xmp_sidecar_write_lock(alias) is xmp_sidecar_write_lock(path)
    writer = PhotoMetaDataXMP()
    assert writer.write(alias, {"title": "规范侧车", "XMP-dc:Subject": ["中文标签"]})
    assert Path(path).with_suffix(".xmp").exists()
    assert writer.read(path)["Title"] == "规范侧车"
    assert writer.read(alias)["Title"] == "规范侧车"
    assert writer.read_subjects(alias, strict=True) == ["中文标签"]
    assert read_xmp_sidecar(alias)
    assert Path(find_xmp_sidecars([alias])[os.path.normpath(alias)]) == Path(path).with_suffix(".xmp")


def seed_legacy(store, records):
    def seed(conn):
        for path, fields in records:
            resource = os.path.splitext(os.path.normcase(os.path.abspath(path)))[0].casefold()
            conn.execute("INSERT INTO resources(resource,path,revision) VALUES(?,?,1)", (resource, path))
            seq = store._change(conn, path, resource, "edit")
            conn.execute("INSERT INTO outbox(resource,path,revision,fields,subjects,last_seq) VALUES(?,?,1,?,'{}',?)",
                         (resource, path, module._json(fields), seq))
    store.submit_sql(seed).result(5)


def test_legacy_single_field_duplicate_outbox_merges_by_global_sequence(tmp_path, monkeypatch):
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    store._identity_ready.result(5)
    seed_legacy(store, [(alias, {"XMP-dc:Title": "旧"}), (path, {"XMP-dc:Title": "新"})])
    store.close()
    reopened = MetadataStore(store.path, start_scheduler=False)
    monkeypatch.setattr(module, "_default", reopened)
    try:
        reopened._identity_ready.result(5)
        assert len(reopened.pending_status()) == 1
        assert reopened.flush_paths([path])
        assert reopened.flush_paths([alias])
        assert PhotoMetaDataXMP().read(path)["Title"] == "新"
    finally:
        reopened.close()


def test_recorded_field_sequences_do_not_use_aggregate_last_edit_time(tmp_path, monkeypatch):
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    store._identity_ready.result(5)
    seed_legacy(store, [(alias, {"XMP-dc:Title": "旧", "XMP-xmp:Rating": 4}),
                        (path, {"XMP-dc:Title": "新"})])
    old_resource = os.path.splitext(os.path.normcase(os.path.abspath(alias)))[0].casefold()
    store.submit_sql(lambda c: c.executemany("INSERT INTO edit_intents VALUES(?,?,?,?,?)", [
        (old_resource, "field", "XMP-dc:Title", module._json("旧"), 1),
        (old_resource, "field", "XMP-xmp:Rating", "4", 3),
    ])).result(5)
    store.submit_sql(lambda c: c.execute("UPDATE outbox SET last_seq=3 WHERE resource=?", (old_resource,))).result(5)
    store.close()
    reopened = MetadataStore(store.path, start_scheduler=False)
    monkeypatch.setattr(module, "_default", reopened)
    try:
        reopened._identity_ready.result(5)
        assert reopened.flush_paths([path])
        metadata = PhotoMetaDataXMP().read(path)
        assert metadata["Title"] == "新" and metadata["rating"] == 4
    finally:
        reopened.close()


def test_ambiguous_legacy_field_history_is_retained_until_explicit_resolution(tmp_path, monkeypatch):
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    store._identity_ready.result(5)
    seed_legacy(store, [(path, {"XMP-dc:Title": "较新标题"}),
                        (alias, {"XMP-dc:Title": "旧标题", "XMP-xmp:Rating": 4})])
    store.close()
    reopened = MetadataStore(store.path, start_scheduler=False)
    monkeypatch.setattr(module, "_default", reopened)
    try:
        reopened._identity_ready.result(5)
        assert not reopened.flush_paths([path])
        assert not Path(path).with_suffix(".xmp").exists()
        assert reopened._call(lambda c: c.execute("SELECT original_tasks FROM identity_conflicts").fetchone())
        assert "冲突" in reopened.pending_status()[0]["error"] or "先后顺序" in reopened.pending_status()[0]["error"]
        reopened.submit_edit(path, {"title": "用户明确保留的标题"}).result(5)
        assert reopened.flush_paths([path])
        assert PhotoMetaDataXMP().read(path)["Title"] == "用户明确保留的标题"
    finally:
        reopened.close()


def test_changed_full_source_invalidates_browser_but_enrichment_does_not_cycle(tmp_path):
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    old = ("source", (str(tmp_path / "p.xmp"), "old"))
    new = ("source", (str(tmp_path / "p.xmp"), "new"))
    try:
        store.remember_many({path: {"rating": 1}}, signatures={path: (*old, "browser")}).result(5)
        store.remember_many({path: {"rating": 1}}, profile="full", signatures={path: (*old, "full")}).result(5)
        assert store.read_many([path])[path]["rating"] == 1
        store.remember_many({path: {"rating": 5}}, profile="full", signatures={path: (*new, "full")}).result(5)
        assert not store.read_many([path])
        assert store.peek(path) is None
        store.remember_many({path: {"rating": 5}}, signatures={path: (*new, "browser")}).result(5)
        assert store.read_many([path], profile="full")[path]["rating"] == 5
        before = store.changes_since(0)["seq"]
        store.remember_many({path: {"rating": 5}}, profile="full", signatures={path: (*new, "full")}).result(5)
        assert store.changes_since(before)["changes"] == []
    finally:
        store.close()


def test_first_full_profile_detects_external_change_from_existing_browser_signature(tmp_path):
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    try:
        # Browser and full source stamps have different historical field order.
        browser = ((100, 200, 300, 400, 500), (str(tmp_path / "p.xmp"), "old"), ())
        full = ((500, 400, 300, 100, 200), (str(tmp_path / "p.xmp"), "new"), "full")
        store.remember_many({path: {"rating": 1}}, signatures={path: browser}).result(5)
        store.remember_many({path: {"rating": 5}}, profile="full", signatures={path: full}).result(5)
        assert store.read_many([path]) == {}
    finally:
        store.close()


def test_unchanged_browser_fields_notify_full_profile_invalidation(tmp_path):
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    old = ("source", (str(tmp_path / "p.xmp"), "old"))
    new = ("source", (str(tmp_path / "p.xmp"), "new"))
    try:
        store.remember_many({path: {"Title": "不变"}}, signatures={path: old}).result(5)
        store.remember_many({path: {"Title": "不变", "Copyright": "旧版权"}}, profile="full", signatures={path: old}).result(5)
        revision, seq = store.current_revision(path), store.changes_since(0)["seq"]
        store.remember_many({path: {"Title": "不变"}}, signatures={path: new}).result(5)
        assert store.peek(path, profile="full") is None
        assert store.current_revision(path) > revision
        assert store.changes_since(seq)["changes"]
    finally:
        store.close()


def test_unregistered_directory_alias_refreshes_canonical_rows(tmp_path):
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    try:
        store.remember_many({path: {"Title": "旧缓存"}}).result(5)
        store.refresh_scope(str(Path(alias).parent)).result(5)
        assert store.read_many([path]) == {}
    finally:
        store.close()


def test_cache_reads_do_not_resolve_known_or_unknown_paths_during_restart(tmp_path, monkeypatch):
    from app_common.exif_io import metadata_identity
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    store.remember_many({alias: {"Title": "缓存首屏"}}).result(5)
    store.submit_edit(alias, {"rating": 4}).result(5)
    store.close()
    # Simulate a new process: only persisted mappings are available. Legacy
    # replay is blocked on the photo volume while cached reads remain usable.
    with metadata_identity._lock:
        metadata_identity._paths.clear()
    entered, release = threading.Event(), threading.Event()
    original = module.MetadataStore._prepare_existing
    def blocked_prepare(self):
        self._call(lambda conn: None)
        entered.set()
        assert release.wait(5)
        return original(self)
    monkeypatch.setattr(module.MetadataStore, "_prepare_existing", blocked_prepare)
    monkeypatch.setattr(metadata_identity.os.path, "realpath", lambda *_a, **_k: pytest.fail("Cache lookup accessed the photo disk"))
    reopened = MetadataStore(store.path, start_scheduler=False)
    try:
        assert entered.wait(2)
        unknown = str(tmp_path / "never-scanned" / "new.jpg")
        assert reopened.read_many([alias, unknown])[os.path.normpath(alias)]["rating"] == 4
        assert reopened.read_many([path])[os.path.normpath(path)]["Title"] == "缓存首屏"
        assert reopened.revisions([path, unknown])[unknown] == 0
        assert not reopened._identity_ready.done()
    finally:
        release.set()
        reopened.close()


def test_owned_worker_lock_wait_respects_flush_deadline(tmp_path, monkeypatch):
    from app_common.file_browser._work_pool import BrowserWorkPool
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    monkeypatch.setattr(module, "_default", store)
    store.submit_edit(path, {"title": "保留待同步"}).result(5)
    pool = BrowserWorkPool(3, 2)
    pool.set_thumbnail_mode(False)
    store.attach_pool(pool)
    try:
        with xmp_sidecar_write_lock(path):
            future = pool.submit(store.flush_paths, [path], timeout=.05)
            assert future.result(1) is False
        assert store.pending_status()
        assert not Path(path).with_suffix(".xmp").exists()
    finally:
        store.detach_pool(pool)
        pool.shutdown(timeout=3)
        store.close()


def test_atomic_write_already_started_finishes_after_deadline(tmp_path, monkeypatch):
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    monkeypatch.setattr(module, "_default", store)
    store.submit_edit(path, {"title": "完整原子发布"}).result(5)
    entered, release = threading.Event(), threading.Event()
    original = PhotoMetaDataXMP.write
    def writing(self, source, fields):
        entered.set()
        assert release.wait(3)
        return original(self, source, fields)
    monkeypatch.setattr(PhotoMetaDataXMP, "write", writing)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(store.flush_paths, [path], timeout=.05)
            assert entered.wait(2)
            time.sleep(.08)
            release.set()
            assert future.result(3)
        assert PhotoMetaDataXMP().read(path)["Title"] == "完整原子发布"
        assert not store.pending_status()
    finally:
        release.set()
        store.close()


def test_scheduled_sync_lock_wait_exits_after_flush_deadline(tmp_path, monkeypatch):
    from app_common.file_browser._work_pool import BrowserWorkPool
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    monkeypatch.setattr(module, "_default", store)
    store.submit_edit(path, {"title": "可恢复任务"}).result(5)
    pool = BrowserWorkPool(3, 2)
    pool.set_thumbnail_mode(False)
    store.attach_pool(pool)
    try:
        with xmp_sidecar_write_lock(path), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(store.flush_paths, [path], timeout=.05)
            assert future.result(1) is False
            with store._lock:
                running = [item[0] for item in store._running.values()]
            assert running
            for action in running:
                assert action.result(1) is False
            assert store.pending_status()
            assert store._active_sync == 0
            store.close()
    finally:
        store.detach_pool(pool)
        pool.shutdown(timeout=3)
        store.close()


def test_thread_and_process_lock_contention_share_one_timeout_budget(monkeypatch):
    import errno
    from types import SimpleNamespace
    from app_common.exif_io import sidecar_lock
    now = [0.0]
    released = []
    class ContendedMutex:
        def acquire(self, *_args):
            now[0] += .04
            return True
        def release(self):
            released.append(True)
    def contention(*_args):
        raise OSError(errno.EACCES, "another process owns the lock")
    def advance(seconds):
        now[0] += seconds
    monkeypatch.setattr(sidecar_lock, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=advance))
    if os.name == "nt":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", contention)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", contention)
    lock = sidecar_lock._SidecarLock("deadline-budget")
    lock._mutex = ContendedMutex()
    assert not lock.acquire(timeout=.05)
    assert now[0] == pytest.approx(.05)
    assert released == [True]


def test_evicted_alias_is_loaded_from_sql_without_photo_io(tmp_path, monkeypatch):
    from app_common.exif_io import metadata_identity
    path, alias = aliases(tmp_path)
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    try:
        store.remember_many({alias: {"Title": "持久别名缓存"}}).result(5)
        receipt = store.submit_edit(alias, {"rating": 4}).result(5)
        monkeypatch.setattr(metadata_identity, "_LIMIT", 4)
        for index in range(8):
            metadata_identity.register_alias(str(tmp_path / f"alias{index}"), str(tmp_path / f"target{index}"))
        assert metadata_identity.path_key(alias) == metadata_identity.lexical_key(alias)
        with monkeypatch.context() as blocked:
            blocked.setattr(metadata_identity.os.path, "realpath", lambda *_a, **_k: pytest.fail("SQL alias lookup accessed the photo disk"))
            assert store.read_many([alias])[os.path.normpath(alias)]["Title"] == "持久别名缓存"
            assert store.revisions([alias])[os.path.normpath(alias)] == receipt.revision
    finally:
        store.close()


def test_queued_edits_and_edits_after_close_all_finish(tmp_path):
    from concurrent.futures import wait
    path = str(tmp_path / "p.jpg")
    Path(path).touch()
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    entered, release = threading.Event(), threading.Event()
    def block_identity():
        entered.set()
        assert release.wait(5)
    store._identity.submit(block_identity)
    assert entered.wait(2)
    edits = [store.submit_edit(path, {"title": f"待处理{index}"}) for index in range(30)]
    with ThreadPoolExecutor(max_workers=1) as executor:
        closing = executor.submit(store.close)
        release.set()
        closing.result(5)
    edits.extend(store.submit_edit(path, {"title": "关闭后拒绝"}) for _ in range(10))
    completed, pending = wait(edits, timeout=2)
    assert not pending
    assert all(future.exception() is not None for future in edits[-10:])


def test_sql_alias_batch_larger_than_memory_limit_keeps_every_hit(tmp_path, monkeypatch):
    from app_common.exif_io import metadata_identity
    store = MetadataStore(tmp_path / "store.sqlite", start_scheduler=False)
    paths = [str(tmp_path / f"photo-{index}.jpg") for index in range(8)]
    aliases_by_path = {path: str(tmp_path / f"old-alias-{index}.jpg") for index, path in enumerate(paths)}
    try:
        store.remember_many({path: {"Title": "原快照"} for path in paths}).result(5)
        store.remember_many({path: {"Title": "新快照"} for path in paths}).result(5)
        store.submit_sql(lambda conn: conn.executemany("INSERT INTO identity_aliases VALUES(?,?)", [
            (metadata_identity.lexical_key(alias), metadata_identity.lexical_key(path))
            for path, alias in aliases_by_path.items()
        ])).result(5)
        monkeypatch.setattr(metadata_identity, "_LIMIT", 4)
        with metadata_identity._lock:
            metadata_identity._paths.clear()
        with monkeypatch.context() as blocked:
            blocked.setattr(metadata_identity.os.path, "realpath", lambda *_a, **_k: pytest.fail("Alias batch accessed the photo disk"))
            rows = store.read_many(list(aliases_by_path.values()))
            revisions = store.revisions(list(aliases_by_path.values()))
        assert len(rows) == 8 and all(row["Title"] == "新快照" for row in rows.values())
        assert set(revisions.values()) == {1}
    finally:
        store.close()
