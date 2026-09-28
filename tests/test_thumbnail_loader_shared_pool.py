from __future__ import annotations

from app_common.file_browser._thumbnail import ThumbnailLoader
from app_common.file_browser._work_pool import BrowserWorkPool


def test_shared_thumbnail_loader_tracks_visible_paths_and_finishes() -> None:
    pool = BrowserWorkPool(3, 2)
    loader = ThumbnailLoader(size=128, request_token=1, work_pool=pool)
    loader._max_workers = 2
    decoded: dict[str, bool] = {}
    loader._load_single = lambda path, emit, *, allow_progressive: decoded.__setitem__(
        path, allow_progressive
    )
    visible = "visible.jpg"
    prefetch = "prefetch.jpg"
    loader.enqueue([visible], priority=ThumbnailLoader.PRIORITY_VISIBLE)
    loader.enqueue([prefetch], priority=ThumbnailLoader.PRIORITY_PREFETCH)
    loader.set_desired_paths([visible], [prefetch])

    try:
        loader.run()
        assert decoded == {visible: True, prefetch: False}
    finally:
        loader.stop()
        assert pool.shutdown(timeout=2)
