"""快速缓存存在检查与显式刷新：真实缓存、失败保留及线程交错。"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import time

from PIL import Image
import pytest
from PyQt6.QtGui import QColor, QImage

from app_common.file_browser import _browser_core as core, _thumbnail as thumbs


def picture(path, color="blue", size=(640, 480)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return str(path)


def task_for(tmp_path, *, refresh=False, scoped=True):
    if scoped:
        (tmp_path / ".superpicky").mkdir(exist_ok=True)
    source = picture(tmp_path / "中文 照片.jpg")
    return thumbs.PersistentThumbCacheTask(source, str(tmp_path), {}, str(tmp_path), (128,),
                                            fast_cache_check=True, refresh_id=int(refresh))


def cache_for(task, size=128, color="red"):
    target = core._persistent_thumb_cache_path_for_file(
        task.source_path, task.current_dir, size, selected_dir=task.current_dir)
    picture(target, color, (size, size * 3 // 4))
    return Path(target)


def forbidden(*args, **kwargs):
    raise AssertionError("快速缓存命中不应读取内容、来源时间戳或解码")


def test_fast_check_accepts_existing_filename_without_opening_image(tmp_path, monkeypatch):
    task = task_for(tmp_path)
    cached = cache_for(task)
    cached.write_bytes(b"not a jpeg")
    os.utime(cached, (1, 1))
    monkeypatch.setattr(core, "_thumb_cache_image_dimensions", forbidden)
    monkeypatch.setattr(thumbs, "_resolve_thumb_source_path", forbidden)
    monkeypatch.setattr(thumbs, "_thumb_source_stamp", forbidden)
    monkeypatch.setattr(thumbs, "_load_thumbnail_image", forbidden)
    assert thumbs.PersistentThumbCacheWorker._process_task(task, threading.Event()) == (task.source_path, 0, 1, 0)
    cached.unlink()
    assert not core._existing_persistent_thumb_cache_path_for_exact_size(
        task.source_path, task.current_dir, 128, selected_dir=task.current_dir, existence_only=True)


def test_refresh_uses_original_and_replaces_existing_tiers_only(tmp_path, monkeypatch):
    task = task_for(tmp_path, refresh=True)
    cached = [cache_for(task), cache_for(task, 512)]
    preview = Path(core._preview_cache_target_for_file(task.source_path, task.current_dir))
    picture(preview, "green")
    preview_before = preview.read_bytes()
    source_before = Path(task.source_path).read_bytes()
    other = picture(tmp_path / ".superpicky/thumb_cache/128/other.jpg", "yellow")
    other_before = Path(other).read_bytes()
    monkeypatch.setattr(thumbs, "_load_thumbnail_image", forbidden)
    assert thumbs.PersistentThumbCacheWorker._process_task(task, threading.Event()) == (task.source_path, 1, 0, 0)
    for path in cached:
        with Image.open(path) as image:
            red, green, blue = image.getpixel((0, 0))
            assert blue > 200 and red < 20 and green < 20
    assert not (tmp_path / ".superpicky/thumb_cache/2048").exists()
    assert Path(task.source_path).read_bytes() == source_before
    assert preview.read_bytes() == preview_before
    assert Path(other).read_bytes() == other_before


def test_refresh_failure_and_cancellation_keep_old_cache(tmp_path, monkeypatch):
    task = task_for(tmp_path, refresh=True)
    cache = cache_for(task)
    before = cache.read_bytes()
    monkeypatch.setattr(thumbs.thumb_stream, "load_thumbnail_rgb", lambda *_: None)
    assert thumbs.PersistentThumbCacheWorker._process_task(task, threading.Event())[3] == 1
    assert cache.read_bytes() == before
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(thumbs.thumb_stream, "load_thumbnail_rgb", forbidden)
    assert thumbs.PersistentThumbCacheWorker._process_task(task, cancelled)[3] == 1
    assert cache.read_bytes() == before


def test_refresh_without_scope_uses_local_cache(tmp_path, monkeypatch):
    # A developer's home may contain .superpicky above pytest's temporary root.
    monkeypatch.setattr(core, "_find_cache_superpicky_dir_for_file", lambda *_a, **_k: "")
    monkeypatch.setattr(core, "_thumb_disk_cache_dir", lambda: str(tmp_path / "local"))
    task = task_for(tmp_path, refresh=True, scoped=False)
    assert thumbs.PersistentThumbCacheWorker._process_task(task, threading.Event())[1:] == (1, 0, 0)
    cached = core._thumb_disk_cache_path(task.source_path, os.path.getmtime(task.source_path), 128, task.current_dir)
    assert Path(cached).is_file()
    assert not (tmp_path / ".superpicky").exists()


def test_persistent_image_is_loaded_directly_without_duplicate_disk_cache(tmp_path, monkeypatch):
    task = task_for(tmp_path)
    cache_for(task)
    memory = thumbs.ThumbnailMemoryCache()
    loader = thumbs.ThumbnailLoader(128, 1, current_dir=task.current_dir, thumb_cache=memory, fast_cache_check=True)
    loader.set_desired_paths([task.source_path], [])
    monkeypatch.setattr(thumbs, "_read_thumb_from_disk_cache", forbidden)
    monkeypatch.setattr(thumbs, "_schedule_thumb_disk_cache_write", forbidden)
    frames = []
    loader._load_single(task.source_path, lambda *args: frames.append(args), allow_progressive=True)
    assert len(frames) == 1
    assert frames[0][2].pixelColor(0, 0).red() > 200
    assert memory.get(task.source_path, 128) is not None


def test_memory_invalidation_rejects_late_results_and_preserves_other_photos():
    memory = thumbs.ThumbnailMemoryCache()
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    memory.put("中文.jpg", 64, image)
    memory.put("other.png", 128, image)
    old_generation = memory.generation("中文.jpg")
    memory.invalidate(["中文.jpg"])
    memory.put("中文.jpg", 64, image, generation=old_generation)
    assert memory.get("中文.jpg", 64) is None
    assert memory.get("other.png", 128) is not None
    assert memory.stats()["bytes"] == image.sizeInBytes()


def test_same_source_automatic_work_cannot_overwrite_manual_refresh(tmp_path, monkeypatch):
    task = task_for(tmp_path)
    worker = thumbs.PersistentThumbCacheWorker([task.source_path], task.current_dir, sizes=[128], fast_cache_check=True)
    old_entered, release_old = threading.Event(), threading.Event()
    calls = []
    process = worker._process_task

    def controlled(job, stop):
        calls.append(job.refresh_id)
        if not job.refresh_id:
            old_entered.set()
            assert release_old.wait(3)
        return process(job, stop)

    monkeypatch.setattr(worker, "_process_task", controlled)
    with ThreadPoolExecutor(2) as pool:
        old = pool.submit(worker._process_serialized_task, task, threading.Event())
        assert old_entered.wait(3)
        forced = pool.submit(worker._process_serialized_task, replace(task, refresh_id=1), threading.Event())
        assert calls == [0]
        release_old.set()
        assert old.result(timeout=3)[3] == 0
        assert forced.result(timeout=3)[3] == 0
    assert calls == [0, 1]
    core._shutdown_thumb_disk_writer()


@pytest.mark.parametrize("shutdown", [False, True])
def test_refresh_survives_scope_change_but_cancels_on_shutdown(tmp_path, monkeypatch, shutdown):
    from app_common.file_browser._work_pool import BrowserWorkPool
    task = task_for(tmp_path)
    cached = cache_for(task)
    before = cached.read_bytes()
    pool = BrowserWorkPool(3, 2)
    worker = thumbs.PersistentThumbCacheWorker([], task.current_dir, work_pool=pool, fast_cache_check=True)
    entered, release = threading.Event(), threading.Event()
    original = worker._process_task

    def blocked(job, stop):
        entered.set()
        assert release.wait(3)
        return original(job, stop)

    monkeypatch.setattr(worker, "_process_task", blocked)
    worker.enqueue_paths([task.source_path], task.current_dir, sizes=[128], force_refresh=True)
    worker.start()
    try:
        assert entered.wait(3)
        worker.enqueue_paths([], str(tmp_path / "another folder"), replace_focus=True)
        if shutdown:
            worker.stop()
        release.set()
        if shutdown:
            assert worker.wait(3000)
            assert cached.read_bytes() == before
        else:
            deadline = time.monotonic() + 3
            while not worker._completed_results:
                assert time.monotonic() < deadline
                threading.Event().wait(.005)
            assert next(iter(worker._completed_results.values())) == (1, 0, 0)
            with Image.open(cached) as result:
                assert result.getpixel((0, 0))[2] > 200
    finally:
        release.set()
        worker.stop()
        assert worker.wait(3000)
        assert pool.shutdown(timeout=3)
