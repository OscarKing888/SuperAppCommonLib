from concurrent.futures import ThreadPoolExecutor
import threading
import time

from app_common.file_browser import _browser_core as core


def test_heif_decodes_share_limit_and_cancel_waiting_work(monkeypatch, tmp_path):
    paths = [str(tmp_path / f"photo_{index}.hif") for index in range(3)]
    for index in range(3):
        (tmp_path / f"photo_{index}.hif").touch()

    entered_two = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    third_started = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def decode(path, size):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                entered_two.set()
        try:
            assert release.wait(3)
            return bytes((12, 34, 56)), 1, 1
        finally:
            with lock:
                active -= 1

    def cache_lookup(path, *args):
        if path == paths[2]:
            third_started.set()
        return None

    monkeypatch.setattr(core, "_read_thumb_from_disk_cache", cache_lookup)
    monkeypatch.setattr(core, "_schedule_thumb_disk_cache_write", lambda *args: None)
    monkeypatch.setattr(core.thumb_stream, "load_thumbnail_rgb", decode)

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(core._load_thumbnail_image, paths[0], 128)
        second = executor.submit(core._load_thumbnail_image, paths[1], 128)
        assert entered_two.wait(3)
        third = executor.submit(
            core._load_thumbnail_image, paths[2], 128,
            cancelled=cancelled.is_set,
        )
        try:
            assert third_started.wait(3)
            time.sleep(0.1)
            with lock:
                assert maximum == 2
            cancelled.set()
            assert third.result(timeout=1) is None
        finally:
            release.set()
        assert not first.result(timeout=3).isNull()
        assert not second.result(timeout=3).isNull()
    assert maximum == 2
