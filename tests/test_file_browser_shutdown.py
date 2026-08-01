from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app_common.file_browser import _panel as panel_module
from app_common.file_browser._panel import FileListPanel


_APP = QApplication.instance() or QApplication([])


class _ShutdownPanel(FileListPanel):
    create_filter_bar = False


def test_shutdown_request_stops_and_blocks_thumbnail_rescheduling(monkeypatch) -> None:
    panel = _ShutdownPanel()
    try:
        panel._ensure_thumb_viewport_timer()
        assert panel._thumb_viewport_timer is not None
        panel._thumb_viewport_timer.start(25)

        panel._request_background_shutdown()
        assert panel._background_shutdown_requested
        assert not panel._background_shutdown_started
        assert not panel._thumb_viewport_timer.isActive()

        # 模拟旧 ThumbnailLoader 的 queued finished 在请求关闭后才送达。
        panel._schedule_visible_thumbnail_update()
        assert not panel._thumb_viewport_timer.isActive()

        class _UnexpectedLoader:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("shutdown 后不得创建替代 ThumbnailLoader")

        monkeypatch.setattr(panel_module, "ThumbnailLoader", _UnexpectedLoader)
        panel._start_thumbnail_loader([os.path.normpath("folder/photo.jpg")])
    finally:
        panel.close()


def test_shutdown_request_does_not_skip_finalizer(monkeypatch) -> None:
    waits: list[bool] = []
    monkeypatch.setattr(
        panel_module,
        "_shutdown_thumb_disk_writer",
        lambda wait=True: waits.append(bool(wait)),
    )
    panel = _ShutdownPanel()
    panel._request_background_shutdown()

    panel._shutdown_background_work(thumb_disk_writer_wait=False)

    assert panel._background_shutdown_started
    assert waits == [False]
    panel.close()
