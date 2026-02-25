# -*- coding: utf-8 -*-
"""PreviewCanvas – reusable zoomable/pannable image preview widget for PyQt6/PyQt5.

Built-in features
-----------------
* Checker background (Photoshop-style transparent-area representation).
* Focus-box overlay (green inner / black outer border, normalised 0-1 coords).
* Original-size / fit-to-window mode with smooth mouse-wheel zoom and drag-to-pan.

Open/Closed extension points
-----------------------------
**Subclass hook** (preferred for static overlays)::

    class MyCanvas(PreviewCanvas):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._my_box = None

        def set_my_box(self, box):
            self._my_box = box
            self.update()

        def _on_source_cleared(self):
            self._my_box = None          # reset when pixmap is removed

        def _paint_overlays(self, painter, draw_rect, content_rect):
            # called after built-in focus-box paint; painter is still active
            if self._my_box:
                ...

**Runtime registration** (for loose coupling)::

    canvas = PreviewCanvas()
    canvas.register_overlay_layer(my_overlay_fn)
    # fn signature: (painter: QPainter, draw_rect: QRectF, content_rect: QRect) -> None
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

try:
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget
    _QRect_or_QRectF = "QRect | QRectF"
except ImportError:
    from PyQt5.QtCore import QPointF, QRectF, Qt  # type: ignore[no-reattr]
    from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap  # type: ignore[no-reattr]
    from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget  # type: ignore[no-reattr]
    _QRect_or_QRectF = "QRect | QRectF"

# Type alias for overlay callables registered at runtime.
OverlayLayer = Callable[["QPainter", "QRectF", "object"], None]
NormalizedBox = tuple[float, float, float, float]


@dataclass(slots=True)
class PreviewOverlayState:
    """Built-in overlay state for ``PreviewCanvas``.

    Subclasses can extend this dataclass to carry extra overlay payloads
    without changing the base API (open/closed).
    """

    focus_box: "NormalizedBox | None" = None


@dataclass(slots=True)
class PreviewOverlayOptions:
    """Built-in overlay options for ``PreviewCanvas``.

    Subclasses can extend this dataclass to add more toggles/parameters.
    """

    show_focus_box: bool = True

# ---------------------------------------------------------------------------
# Checker background helper (self-contained, no external deps)
# ---------------------------------------------------------------------------

def draw_checker_background(
    painter: "QPainter",
    rect: "object",  # QRect | QRectF
    cell: int = 8,
) -> None:
    """Draw a Photoshop-style checkerboard over *rect* using *painter*.

    Alternating light/dark cells make transparent areas clearly visible.
    Safe to call with either ``QRect`` or ``QRectF``.
    """
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


# ---------------------------------------------------------------------------
# PreviewCanvas
# ---------------------------------------------------------------------------

class PreviewCanvas(QLabel):
    """Zoomable, pannable image preview widget with checker background.

    Built-in overlays: focus box, original-size mode.
    Extend via ``_paint_overlays`` override or ``register_overlay_layer``.
    """

    def __init__(self, parent: "QWidget | None" = None, *, placeholder_text: str = "暂无预览") -> None:
        super().__init__(placeholder_text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._source_pixmap: "QPixmap | None" = None

        # Built-in: focus box
        self._focus_box: "tuple[float, float, float, float] | None" = None
        self._show_focus_box: bool = True

        # Built-in: zoom / pan
        self._use_original_size: bool = False
        self._zoom: float = 1.0
        self._offset: "QPointF" = QPointF(0.0, 0.0)
        self._dragging: bool = False
        self._last_drag_pos: "QPointF" = QPointF(0.0, 0.0)
        self._min_zoom: float = 0.02
        self._max_zoom: float = 24.0

        # Runtime-registered overlay callables
        self._overlay_layers: list[OverlayLayer] = []

    # ------------------------------------------------------------------
    # Public API – batched overlay state / options (open/closed)
    # ------------------------------------------------------------------

    def apply_overlay_state(self, state: "PreviewOverlayState | None") -> None:
        """Apply built-in (and subclass-extended) overlay payloads in one call."""
        target = state if state is not None else PreviewOverlayState()
        if self._apply_overlay_state_data(target):
            self.update()

    def apply_overlay_options(self, options: "PreviewOverlayOptions | None") -> None:
        """Apply built-in (and subclass-extended) overlay options in one call."""
        target = options if options is not None else PreviewOverlayOptions()
        if self._apply_overlay_options_data(target):
            self.update()

    # ------------------------------------------------------------------
    # Public API – focus box
    # ------------------------------------------------------------------

    def set_focus_box(self, focus_box: "NormalizedBox | None") -> None:
        """Set the focus-box in normalised [0, 1] image coordinates."""
        if self._focus_box == focus_box:
            return
        self._focus_box = focus_box
        self.update()

    def set_show_focus_box(self, enabled: bool) -> None:
        """Show or hide the focus-box overlay."""
        parsed = bool(enabled)
        if self._show_focus_box == parsed:
            return
        self._show_focus_box = parsed
        self.update()

    # ------------------------------------------------------------------
    # Public API – original size / fit mode
    # ------------------------------------------------------------------

    def set_use_original_size(
        self,
        enabled: bool,
        *,
        reset_view: bool = False,
        preserve_view: bool = False,
        preserve_scale: bool = False,
    ) -> None:
        """Switch between fit-to-window and original-pixel-size display."""
        target = bool(enabled)
        if self._source_pixmap is None:
            self._use_original_size = target
            if reset_view:
                self._zoom = 1.0
                self._offset = QPointF(0.0, 0.0)
            self._clamp_offset()
            self._update_cursor()
            self.update()
            return

        view_ratio = self._view_center_ratio() if preserve_view else None
        old_total_scale = self._fit_scale() * self._zoom

        if target == self._use_original_size:
            if reset_view:
                self._zoom = 1.0
                self._offset = QPointF(0.0, 0.0)
            elif view_ratio is not None:
                self._apply_view_center_ratio(view_ratio)
                self._clamp_offset()
                self._update_cursor()
                self.update()
            return

        self._use_original_size = target
        if preserve_scale:
            new_fit = self._fit_scale()
            if new_fit > 0:
                self._zoom = max(self._min_zoom, min(self._max_zoom, old_total_scale / new_fit))
        if reset_view:
            self._zoom = 1.0
            self._offset = QPointF(0.0, 0.0)
        elif view_ratio is not None:
            self._apply_view_center_ratio(view_ratio)
        self._clamp_offset()
        self._update_cursor()
        self.update()

    # ------------------------------------------------------------------
    # Public API – source pixmap
    # ------------------------------------------------------------------

    def set_source_pixmap(
        self,
        pixmap: "QPixmap | None",
        *,
        reset_view: bool = False,
        preserve_view: bool = False,
        preserve_scale: bool = False,
    ) -> None:
        """Replace the displayed pixmap, optionally preserving the current view."""
        old_pixmap = self._source_pixmap
        view_ratio = self._view_center_ratio() if preserve_view else None
        old_total_scale = self._fit_scale() * self._zoom

        self._source_pixmap = pixmap
        if self._source_pixmap is None or self._source_pixmap.isNull():
            self._source_pixmap = None
            self._focus_box = None
            self._zoom = 1.0
            self._offset = QPointF(0.0, 0.0)
            self._dragging = False
            self._on_source_cleared()
            self.setText("暂无预览")
            self._update_cursor()
            self.update()
            return

        if preserve_scale and old_pixmap is not None and not old_pixmap.isNull():
            try:
                ow = float(max(1, old_pixmap.width()))
                oh = float(max(1, old_pixmap.height()))
                nw = float(max(1, self._source_pixmap.width()))
                nh = float(max(1, self._source_pixmap.height()))
                rw, rh = ow / nw, oh / nh
                old_total_scale *= (rw + rh) * 0.5 if abs(rw - rh) <= 0.03 else rw
            except Exception:
                pass

        if preserve_scale:
            new_fit = self._fit_scale()
            if new_fit > 0:
                self._zoom = max(self._min_zoom, min(self._max_zoom, old_total_scale / new_fit))
        if reset_view:
            self._zoom = 1.0
            self._offset = QPointF(0.0, 0.0)
        elif view_ratio is not None:
            self._apply_view_center_ratio(view_ratio)
        self._clamp_offset()
        self._update_cursor()
        self.setText("")
        self.update()

    # ------------------------------------------------------------------
    # Runtime overlay registration
    # ------------------------------------------------------------------

    def register_overlay_layer(self, fn: OverlayLayer) -> None:
        """Register a callable overlay drawn after all built-in overlays.

        ``fn(painter, draw_rect, content_rect)`` is called during ``paintEvent``
        while the painter is still active. Multiple layers are drawn in
        registration order.
        """
        self._overlay_layers.append(fn)

    def unregister_overlay_layer(self, fn: OverlayLayer) -> None:
        """Remove a previously registered overlay callable."""
        try:
            self._overlay_layers.remove(fn)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Extension hooks (override in subclass – do NOT call super() unless
    # you explicitly want both the base behaviour and your own)
    # ------------------------------------------------------------------

    def _apply_overlay_state_data(self, state: "PreviewOverlayState") -> bool:
        """Subclass hook: apply overlay payloads, return whether anything changed.

        Override to extend ``apply_overlay_state`` with new fields while reusing
        the single repaint trigger in the public method (open/closed).
        """
        changed = self._focus_box != state.focus_box
        self._focus_box = state.focus_box
        return changed

    def _apply_overlay_options_data(self, options: "PreviewOverlayOptions") -> bool:
        """Subclass hook: apply overlay options, return whether anything changed."""
        show_focus_box = bool(options.show_focus_box)
        changed = self._show_focus_box != show_focus_box
        self._show_focus_box = show_focus_box
        return changed

    def _paint_overlays(
        self,
        painter: "QPainter",
        draw_rect: "QRectF",
        content_rect: "object",  # QRect
    ) -> None:
        """Subclass hook: paint application-specific overlays.

        Called after built-in overlays (checker, image, focus box) and before
        runtime-registered layers. The ``painter`` is clipped to *content_rect*
        and still active; call ``painter.end()`` is handled by the base class.
        """

    def _on_source_cleared(self) -> None:
        """Subclass hook: called when the source pixmap is set to None/null.

        Override to reset any overlay data that should be cleared together with
        the image (e.g. detection boxes, crop boxes).
        """

    # ------------------------------------------------------------------
    # Internal geometry helpers
    # ------------------------------------------------------------------

    def _fit_scale(self) -> float:
        if self._source_pixmap is None or self._use_original_size:
            return 1.0
        content = self.contentsRect()
        if content.width() <= 0 or content.height() <= 0:
            return 1.0
        return min(
            content.width() / float(max(1, self._source_pixmap.width())),
            content.height() / float(max(1, self._source_pixmap.height())),
        )

    def _view_center_ratio(self) -> "tuple[float, float] | None":
        draw_rect = self._display_rect()
        if draw_rect is None or draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return None
        c = QPointF(self.contentsRect().center())
        return (
            (c.x() - draw_rect.left()) / draw_rect.width(),
            (c.y() - draw_rect.top()) / draw_rect.height(),
        )

    def _apply_view_center_ratio(self, ratio: "tuple[float, float]") -> None:
        if self._source_pixmap is None:
            return
        content = self.contentsRect()
        if content.width() <= 0 or content.height() <= 0:
            return
        total = self._fit_scale() * self._zoom
        if total <= 0:
            return
        dw = self._source_pixmap.width() * total
        dh = self._source_pixmap.height() * total
        cc = QPointF(content.center())
        cx = cc.x() + (0.5 - ratio[0]) * dw
        cy = cc.y() + (0.5 - ratio[1]) * dh
        self._offset = QPointF(cx - cc.x(), cy - cc.y())

    def _display_rect(self) -> "QRectF | None":
        if self._source_pixmap is None:
            return None
        content = self.contentsRect()
        if content.width() <= 0 or content.height() <= 0:
            return None
        scale = self._fit_scale() * self._zoom
        if scale <= 0:
            return None
        dw = self._source_pixmap.width() * scale
        dh = self._source_pixmap.height() * scale
        center = QPointF(content.center()) + self._offset
        return QRectF(center.x() - dw * 0.5, center.y() - dh * 0.5, dw, dh)

    def _can_pan(self) -> bool:
        dr = self._display_rect()
        if dr is None:
            return False
        cr = self.contentsRect()
        return (dr.width() > cr.width() + 0.5) or (dr.height() > cr.height() + 0.5)

    def _clamp_offset(self) -> None:
        if self._source_pixmap is None:
            self._offset = QPointF(0.0, 0.0)
            return
        content = self.contentsRect()
        if content.width() <= 0 or content.height() <= 0:
            self._offset = QPointF(0.0, 0.0)
            return
        scale = self._fit_scale() * self._zoom
        dw = self._source_pixmap.width() * scale
        dh = self._source_pixmap.height() * scale
        lx = max(0.0, (dw - content.width()) * 0.5)
        ly = max(0.0, (dh - content.height()) * 0.5)
        self._offset = QPointF(
            max(-lx, min(lx, self._offset.x())),
            max(-ly, min(ly, self._offset.y())),
        )

    def _update_cursor(self) -> None:
        if self._source_pixmap is None or not self._can_pan():
            self.unsetCursor()
            return
        self.setCursor(
            Qt.CursorShape.ClosedHandCursor if self._dragging else Qt.CursorShape.OpenHandCursor
        )

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._clamp_offset()
        self._update_cursor()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self._source_pixmap is None:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        old_zoom = self._zoom
        new_zoom = max(self._min_zoom, min(self._max_zoom, old_zoom * pow(1.0015, float(delta))))
        if abs(new_zoom - old_zoom) < 1e-9:
            event.accept()
            return
        fit_scale = self._fit_scale()
        if fit_scale <= 0:
            event.ignore()
            return
        content = self.contentsRect()
        cc = QPointF(content.center())
        cur = event.position()
        old_s = fit_scale * old_zoom
        new_s = fit_scale * new_zoom
        if old_s <= 0 or new_s <= 0:
            event.ignore()
            return
        img_c = cc + self._offset
        idx = (cur.x() - img_c.x()) / old_s
        idy = (cur.y() - img_c.y()) / old_s
        self._zoom = new_zoom
        self._offset = QPointF(cur.x() - idx * new_s, cur.y() - idy * new_s) - cc
        self._clamp_offset()
        self._update_cursor()
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._source_pixmap is not None
            and self._can_pan()
        ):
            self._dragging = True
            self._last_drag_pos = event.position()
            self._update_cursor()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging:
            self._offset += event.position() - self._last_drag_pos
            self._last_drag_pos = event.position()
            self._clamp_offset()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._update_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._source_pixmap is None:
            super().paintEvent(event)
            return
        draw_rect = self._display_rect()
        if draw_rect is None:
            super().paintEvent(event)
            return

        content = self.contentsRect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setClipRect(content)

        # ── checker background ────────────────────────────────────────
        draw_checker_background(painter, content)

        # ── image ─────────────────────────────────────────────────────
        painter.drawPixmap(
            draw_rect,
            self._source_pixmap,
            QRectF(0, 0, self._source_pixmap.width(), self._source_pixmap.height()),
        )

        # ── built-in: focus box ───────────────────────────────────────
        if self._show_focus_box and self._focus_box:
            self._paint_focus_box(painter, draw_rect, content)

        # ── subclass overlays ─────────────────────────────────────────
        self._paint_overlays(painter, draw_rect, content)

        # ── runtime-registered layers ─────────────────────────────────
        for layer_fn in self._overlay_layers:
            try:
                layer_fn(painter, draw_rect, content)
            except Exception:
                pass

        painter.end()

    # ------------------------------------------------------------------
    # Built-in overlay painters (private)
    # ------------------------------------------------------------------

    def _paint_focus_box(self, painter: "QPainter", draw_rect: "QRectF", content: "object") -> None:
        fb = self._focus_box
        if fb is None:
            return
        left = int(round(draw_rect.left() + fb[0] * draw_rect.width()))
        top = int(round(draw_rect.top() + fb[1] * draw_rect.height()))
        right = int(round(draw_rect.left() + fb[2] * draw_rect.width()))
        bottom = int(round(draw_rect.top() + fb[3] * draw_rect.height()))

        cl = content.left()
        ct = content.top()
        cr = cl + content.width() - 1
        cb = ct + content.height() - 1
        if cr - cl < 2 or cb - ct < 2:
            return

        left = max(cl, min(cr - 2, left))
        top = max(ct, min(cb - 2, top))
        right = min(cr, max(left + 2, right))
        bottom = min(cb, max(top + 2, bottom))
        bw, bh = right - left, bottom - top

        painter.setBrush(Qt.BrushStyle.NoBrush)

        outer = QPen(QColor("#000000"))
        outer.setWidth(1)
        outer.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(outer)
        painter.drawRect(left, top, bw, bh)

        if bw >= 4 and bh >= 4:
            inner = QPen(QColor("#2EFF55"))
            inner.setWidth(2)
            inner.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(inner)
            painter.drawRect(left + 1, top + 1, max(1, bw - 2), max(1, bh - 2))

        if bw >= 8 and bh >= 8:
            inn2 = QPen(QColor("#000000"))
            inn2.setWidth(1)
            inn2.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(inn2)
            painter.drawRect(left + 3, top + 3, bw - 6, bh - 6)


# ---------------------------------------------------------------------------
# PreviewWithStatusBar – canvas + status bar (open/closed for extension)
# ---------------------------------------------------------------------------

class PreviewWithStatusBar(QWidget):
    """Composite: a PreviewCanvas (or subclass) plus a status bar below.

    The status bar shows resolution info by default. Subclasses can extend
    the status content by overriding ``_get_status_segments()`` (open/closed).

    Usage::
        w = PreviewWithStatusBar(canvas=EditorPreviewCanvas())
        w.set_source_pixmap(pixmap)
        w.set_original_size(4000, 3000)   # optional, for "原始分辨率"
        w.set_source_mode("原图")          # optional, appended to status
        w.set_focus_box(...)               # forwarded to canvas
    """

    def __init__(
        self,
        parent: "QWidget | None" = None,
        *,
        canvas: "PreviewCanvas | None" = None,
    ) -> None:
        super().__init__(parent)
        self._canvas: "PreviewCanvas" = canvas if canvas is not None else PreviewCanvas()
        self._status_label = QLabel("原始分辨率: -")
        self._display_pixmap: "QPixmap | None" = None
        self._original_size: "tuple[int, int] | None" = None
        self._source_mode: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._status_label)

    @property
    def canvas(self) -> "PreviewCanvas":
        """The inner canvas (e.g. for direct access if needed)."""
        return self._canvas

    def set_source_pixmap(
        self,
        pixmap: "QPixmap | None",
        *,
        reset_view: bool = False,
        preserve_view: bool = False,
        preserve_scale: bool = False,
    ) -> None:
        self._display_pixmap = pixmap
        self._canvas.set_source_pixmap(
            pixmap,
            reset_view=reset_view,
            preserve_view=preserve_view,
            preserve_scale=preserve_scale,
        )
        self._refresh_status_bar()

    def set_original_size(self, width: int | None, height: int | None) -> None:
        """Set the 'original' resolution line (e.g. source image before crop)."""
        if width is not None and height is not None:
            self._original_size = (int(width), int(height))
        else:
            self._original_size = None
        self._refresh_status_bar()

    def set_source_mode(self, mode: str) -> None:
        """Set a short mode suffix (e.g. '原图' or '预览图') for the status bar."""
        self._source_mode = str(mode).strip()
        self._refresh_status_bar()

    def apply_overlay_state(self, state: "PreviewOverlayState | None") -> None:
        """Forward batched overlay payloads to the inner canvas."""
        self._canvas.apply_overlay_state(state)

    def apply_overlay_options(self, options: "PreviewOverlayOptions | None") -> None:
        """Forward batched overlay options to the inner canvas."""
        self._canvas.apply_overlay_options(options)

    def _refresh_status_bar(self) -> None:
        segments = self._get_status_segments()
        self._status_label.setText(" | ".join(s for s in segments if s))

    def _get_status_segments(self) -> list[str]:
        """Build status bar segments. Override in subclass to extend (open/closed)."""
        orig_str = "-"
        if self._original_size is not None:
            orig_str = f"{self._original_size[0]}x{self._original_size[1]}"
        elif self._display_pixmap is not None and not self._display_pixmap.isNull():
            orig_str = f"{self._display_pixmap.width()}x{self._display_pixmap.height()}"

        out = [f"原始分辨率: {orig_str}"]
        if self._source_mode:
            out.append(f"({self._source_mode})")
        return out

    def __getattr__(self, name: str):
        """Forward other attributes/methods to the inner canvas."""
        return getattr(self._canvas, name)
