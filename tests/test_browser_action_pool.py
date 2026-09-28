from __future__ import annotations

import threading
import time

from app_common.file_browser._work_action import CallableAction
from app_common.file_browser._work_policy import WorkKind
from app_common.file_browser._work_pool import BrowserWorkPool, VISIBLE, PERSISTENT, METADATA


def test_pool_prioritizes_visible_and_borrows_idle_metadata_capacity():
    pool = BrowserWorkPool(3, 2)
    gate = threading.Event()
    started = threading.Event()
    order: list[str] = []

    def blocker():
        started.set()
        gate.wait(2)

    producer = pool.begin_producer(WorkKind.THUMBNAIL)
    try:
        blocking = pool.submit_action(CallableAction(blocker), kind=WorkKind.THUMBNAIL,
                                      priority=PERSISTENT)
        assert started.wait(1)
        slow = pool.submit_action(CallableAction(lambda: order.append("persistent")),
                                  kind=WorkKind.THUMBNAIL, priority=PERSISTENT)
        visible = pool.submit_action(CallableAction(lambda: order.append("visible")),
                                     kind=WorkKind.THUMBNAIL, priority=VISIBLE)
        gate.set()
        blocking.result(timeout=2)
        visible.result(timeout=2)
        slow.result(timeout=2)
        assert order == ["visible", "persistent"]
    finally:
        gate.set()
        pool.end_producer(WorkKind.THUMBNAIL, producer)

    futures = [pool.submit_action(CallableAction(lambda: time.sleep(.03)),
                                  kind=WorkKind.METADATA, priority=METADATA) for _ in range(3)]
    try:
        for future in futures:
            future.result(timeout=2)
        assert pool.snapshot()["completed"] >= 6
    finally:
        assert pool.shutdown(timeout=2)


def test_pool_reserves_metadata_slots_then_lends_them_and_cancels_queued_shutdown():
    pool = BrowserWorkPool(3, 2)
    gate = threading.Event()
    started = threading.Event()
    first_started = threading.Event()
    active = 0
    lock = threading.Lock()

    def blocking():
        nonlocal active
        with lock:
            active += 1
            if active == 1:
                first_started.set()
            if active == 3:
                started.set()
        assert gate.wait(2)

    try:
        metadata = pool.begin_producer(WorkKind.METADATA)
        thumbnail = pool.begin_producer(WorkKind.THUMBNAIL)
        futures = [pool.submit_action(CallableAction(blocking), kind=WorkKind.THUMBNAIL,
                                      priority=VISIBLE) for _ in range(3)]
        assert first_started.wait(1)
        assert pool.snapshot()["thumbnail_active"] == 1
        pool.end_producer(WorkKind.METADATA, metadata)
        assert started.wait(1)
        queued = pool.submit_action(CallableAction(lambda: None), kind=WorkKind.THUMBNAIL,
                                    priority=PERSISTENT)
        pool.request_shutdown()
        assert queued.cancelled()
        gate.set()
        for future in futures:
            future.result(timeout=2)
        pool.end_producer(WorkKind.THUMBNAIL, thumbnail)
        assert pool.shutdown(timeout=2)
    finally:
        gate.set()
        pool.shutdown(timeout=2)
