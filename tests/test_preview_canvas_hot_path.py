from __future__ import annotations

import time

import pytest
from PyQt6.QtCore import QRect, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from app_common.preview_canvas import PreviewCanvas, draw_checker_background

_APP = QApplication.instance() or QApplication([])


def _reference_checker(painter: QPainter, rect, cell: int = 8) -> None:
    """The original per-cell implementation, kept as the pixel reference."""
    x0 = int(rect.x())
    y0 = int(rect.y())
    x1 = x0 + int(rect.width())
    y1 = y0 + int(rect.height())
    light = QColor(203, 203, 203)
    dark = QColor(153, 153, 153)
    ri = 0
    row = y0
    while row < y1:
        row_h = min(cell, y1 - row)
        ci = 0
        col = x0
        while col < x1:
            col_w = min(cell, x1 - col)
            painter.fillRect(col, row, col_w, row_h, light if (ri + ci) % 2 == 0 else dark)
            col += cell
            ci += 1
        row += cell
        ri += 1


def _render(fn, rect, *, cell: int = 8, dpr: float = 1.0, size=(420, 320)) -> QImage:
    image = QImage(int(size[0] * dpr), int(size[1] * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(0)
    painter = QPainter(image)
    painter.setClipRect(QRect(6, 4, size[0] - 12, size[1] - 8))
    fn(painter, rect, cell)
    painter.end()
    return image


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
@pytest.mark.parametrize(
    "rect",
    [QRect(0, 0, 420, 320), QRect(3, 5, 97, 61), QRectF(2.6, 7.4, 55.5, 33.2), QRect(-5, -3, 40, 30)],
)
@pytest.mark.parametrize("cell", [8, 5])
def test_texture_checker_is_pixel_identical_to_per_cell_fill(dpr, rect, cell) -> None:
    expected = _render(_reference_checker, rect, cell=cell, dpr=dpr)
    actual = _render(draw_checker_background, rect, cell=cell, dpr=dpr)
    assert actual == expected


def test_checker_paint_is_not_a_python_per_cell_loop() -> None:
    image = QImage(1400, 1000, QImage.Format.Format_ARGB32_Premultiplied)
    calls = []

    class _CountingPainter(QPainter):
        def fillRect(self, *args):  # noqa: N802 - Qt naming
            calls.append(1)
            return super().fillRect(*args)

    painter = _CountingPainter(image)
    draw_checker_background(painter, QRect(0, 0, 1400, 1000))
    painter.end()
    assert len(calls) <= 1  # was ~22k per-cell calls for this size


def test_canvas_paint_keeps_checker_image_and_overlays() -> None:
    canvas = PreviewCanvas()
    try:
        canvas.resize(300, 200)
        source = QPixmap(40, 60)  # portrait in a landscape canvas leaves letterbox columns
        source.fill(QColor(20, 120, 220))
        canvas.set_source_pixmap(source, reset_view=True)
        canvas.set_composition_grid_mode("thirds")
        started = time.perf_counter()
        frame = canvas.grab().toImage()
        assert (time.perf_counter() - started) < 2.0
        # Letterbox area shows the checkerboard, the image area shows the source colour.
        corner = frame.pixelColor(2, 2)
        assert (corner.red(), corner.green(), corner.blue()) in {(203, 203, 203), (153, 153, 153)}
        center = frame.pixelColor(150, 100)
        assert abs(center.blue() - 220) <= 40
        exported = canvas.render_source_pixmap_with_overlays()
        assert exported is not None and (exported.width(), exported.height()) == (40, 60)
    finally:
        canvas.close()
        canvas.deleteLater()
        _APP.processEvents()
