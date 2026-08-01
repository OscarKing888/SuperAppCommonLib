from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from app_common.preview_canvas import canvas as canvas_module
from app_common.preview_canvas.canvas import PreviewCanvas, PreviewWithStatusBar

_APP = QApplication.instance() or QApplication([])


def test_preview_canvas_can_disable_source_pixmap_performance_logging(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(canvas_module, "perf_log", lambda *args, **kwargs: calls.append((args, kwargs)))
    pixmap = QPixmap(32, 24)

    canvas = PreviewCanvas()
    canvas.set_source_pixmap(pixmap, log_performance=False)
    canvas.set_source_pixmap(None, log_performance=False)

    assert calls == []


def test_status_wrapper_forwards_source_pixmap_performance_flag(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(canvas_module, "perf_log", lambda *args, **kwargs: calls.append((args, kwargs)))
    wrapper = PreviewWithStatusBar()

    wrapper.set_source_pixmap(QPixmap(32, 24), log_performance=False)

    assert calls == []
