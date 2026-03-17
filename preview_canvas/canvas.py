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
import math
from typing import Callable

try:
    from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget
    _QRect_or_QRectF = "QRect | QRectF"
except ImportError:
    from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal  # type: ignore[no-reattr]
    from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap  # type: ignore[no-reattr]
    from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget  # type: ignore[no-reattr]
    _QRect_or_QRectF = "QRect | QRectF"

# Type alias for overlay callables registered at runtime.
OverlayLayer = Callable[["QPainter", "QRectF", "object"], None]
NormalizedBox = tuple[float, float, float, float]
PREVIEW_COMPOSITION_GRID_MODES: tuple[str, ...] = (
    "none",
    "thirds",
    "golden_thirds",
    "square",
    "diag_square",
    "crosshair",
)
PREVIEW_COMPOSITION_GRID_LINE_WIDTHS: tuple[int, ...] = (1, 2, 3, 4)
PREVIEW_SCALE_PRESET_PERCENTS: tuple[int, ...] = (
    10,
    20,
    30,
    40,
    50,
    60,
    70,
    80,
    90,
    100,
    150,
    200,
    250,
    300,
    305,
    400,
    500,
)
_FOCUS_BOX_OUTER_BLACK_WIDTH = 1
_FOCUS_BOX_GREEN_WIDTH = 4
_FOCUS_BOX_INNER_BLACK_WIDTH = 1
_GRID_MODE_ALIASES: dict[str, str] = {
    "off": "none",
}


def normalize_preview_composition_grid_mode(value: object) -> str:
    """将构图线模式收敛到受支持的枚举，并兼容旧配置别名。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
        normalized = _GRID_MODE_ALIASES.get(normalized, normalized)
        if normalized in PREVIEW_COMPOSITION_GRID_MODES:
            return normalized
    return PREVIEW_COMPOSITION_GRID_MODES[0]


def normalize_preview_composition_grid_line_width(value: object) -> int:
    """将构图线线宽收敛到受支持的正整数。"""
    try:
        width = int(value)
    except Exception:
        return PREVIEW_COMPOSITION_GRID_LINE_WIDTHS[0]
    min_width = PREVIEW_COMPOSITION_GRID_LINE_WIDTHS[0]
    max_width = PREVIEW_COMPOSITION_GRID_LINE_WIDTHS[-1]
    if width < min_width:
        return min_width
    if width > max_width:
        return max_width
    return width if width in PREVIEW_COMPOSITION_GRID_LINE_WIDTHS else min_width


def format_preview_scale_percent(scale_percent: object) -> str:
    """格式化当前预览缩放百分比。"""
    try:
        parsed = float(scale_percent)
    except Exception:
        return "-"
    if not math.isfinite(parsed) or parsed <= 0.0:
        return "-"
    rounded = round(parsed)
    if abs(parsed - float(rounded)) < 0.05:
        return f"{int(rounded)}%"
    return f"{parsed:.1f}%"


def sync_preview_scale_preset_combo(
    combo: object,
    scale_percent: object,
    *,
    empty_text: str = "-",
) -> None:
    """将当前缩放百分比同步到预设下拉框。"""
    if combo is None:
        return
    try:
        parsed = float(scale_percent)
    except Exception:
        parsed = None
    if parsed is not None and (not math.isfinite(parsed) or parsed <= 0.0):
        parsed = None

    old_blocked = False
    try:
        old_blocked = bool(combo.blockSignals(True))
    except Exception:
        old_blocked = False
    try:
        current_index = -1
        if parsed is not None:
            rounded = int(round(parsed))
            if abs(parsed - float(rounded)) < 0.05:
                try:
                    current_index = int(combo.findData(rounded))
                except Exception:
                    current_index = -1
        try:
            combo.setCurrentIndex(current_index)
        except Exception:
            pass
        text = format_preview_scale_percent(parsed) if parsed is not None else str(empty_text)
        try:
            combo.setEditText(text)
        except Exception:
            pass
    finally:
        try:
            combo.blockSignals(old_blocked)
        except Exception:
            pass


def configure_preview_scale_preset_combo(
    combo: object,
    *,
    tooltip: str | None = None,
    fixed_width: int | None = None,
    empty_text: str = "-",
) -> None:
    """配置一个统一的预览缩放预设下拉框。"""
    if combo is None:
        return
    try:
        combo.setEditable(True)
    except Exception:
        pass
    try:
        insert_policy = getattr(combo.__class__, "InsertPolicy", None)
        if insert_policy is not None and hasattr(insert_policy, "NoInsert"):
            combo.setInsertPolicy(insert_policy.NoInsert)
    except Exception:
        pass
    try:
        combo.clear()
    except Exception:
        pass
    for percent in PREVIEW_SCALE_PRESET_PERCENTS:
        try:
            combo.addItem(f"{percent}%", int(percent))
        except Exception:
            continue
    if fixed_width is not None:
        try:
            combo.setFixedWidth(int(fixed_width))
        except Exception:
            pass
    if tooltip:
        try:
            combo.setToolTip(str(tooltip))
        except Exception:
            pass
    line_edit = None
    try:
        line_edit = combo.lineEdit()
    except Exception:
        line_edit = None
    if line_edit is not None:
        try:
            line_edit.setReadOnly(True)
        except Exception:
            pass
        try:
            line_edit.setPlaceholderText("缩放")
        except Exception:
            pass
    sync_preview_scale_preset_combo(combo, None, empty_text=empty_text)


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
    composition_grid_mode: str = PREVIEW_COMPOSITION_GRID_MODES[0]
    composition_grid_line_width: int = PREVIEW_COMPOSITION_GRID_LINE_WIDTHS[0]

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

    display_scale_percent_changed = pyqtSignal(object)

    def __init__(self, parent: "QWidget | None" = None, *, placeholder_text: str = "暂无预览") -> None:
        super().__init__(placeholder_text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._source_pixmap: "QPixmap | None" = None

        # Built-in: focus box
        self._focus_box: "tuple[float, float, float, float] | None" = None
        self._show_focus_box: bool = True
        self._composition_grid_mode: str = PREVIEW_COMPOSITION_GRID_MODES[0]
        self._composition_grid_line_width: int = PREVIEW_COMPOSITION_GRID_LINE_WIDTHS[0]

        # Built-in: zoom / pan
        self._use_original_size: bool = False
        self._zoom: float = 1.0
        self._offset: "QPointF" = QPointF(0.0, 0.0)
        self._dragging: bool = False
        self._last_drag_pos: "QPointF" = QPointF(0.0, 0.0)
        self._min_zoom: float = 0.02
        self._max_zoom: float = 24.0
        self._last_emitted_display_scale_percent: float | None = None

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

    def set_composition_grid_mode(self, mode: str | None) -> None:
        """Set the composition-guide overlay mode."""
        parsed = normalize_preview_composition_grid_mode(mode)
        if self._composition_grid_mode == parsed:
            return
        self._composition_grid_mode = parsed
        self.update()

    def set_composition_grid_line_width(self, width: int | str | None) -> None:
        """Set the composition-guide overlay line width."""
        parsed = normalize_preview_composition_grid_line_width(width)
        if self._composition_grid_line_width == parsed:
            return
        self._composition_grid_line_width = parsed
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
            self._emit_display_scale_percent_changed()
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
                self._emit_display_scale_percent_changed()
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
        self._emit_display_scale_percent_changed()

    def current_display_scale_percent(self) -> float | None:
        """返回当前屏幕显示尺寸相对原图像素的百分比。"""
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return None
        total_scale = self._fit_scale() * self._zoom
        if not math.isfinite(total_scale) or total_scale <= 0.0:
            return None
        return total_scale * 100.0

    def set_display_scale_percent(
        self,
        scale_percent: float | int,
        *,
        preserve_view: bool = True,
    ) -> bool:
        """按绝对百分比设置预览显示比例。"""
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return False
        try:
            parsed = float(scale_percent)
        except Exception:
            return False
        if not math.isfinite(parsed) or parsed <= 0.0:
            return False
        fit_scale = self._fit_scale()
        if fit_scale <= 0.0:
            return False
        target_total_scale = parsed / 100.0
        target_zoom = max(self._min_zoom, min(self._max_zoom, target_total_scale / fit_scale))
        view_ratio = self._view_center_ratio() if preserve_view else None
        if abs(target_zoom - self._zoom) < 1e-9 and view_ratio is None:
            self._emit_display_scale_percent_changed(force=True)
            return True
        self._zoom = target_zoom
        if view_ratio is not None:
            self._apply_view_center_ratio(view_ratio)
        self._clamp_offset()
        self._update_cursor()
        self.update()
        self._emit_display_scale_percent_changed(force=True)
        return True

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
            self._emit_display_scale_percent_changed(force=True)
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
        self._emit_display_scale_percent_changed(force=True)

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

    def render_source_pixmap_with_overlays(self) -> "QPixmap | None":
        """Render the current source pixmap with overlays at source resolution."""
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return None
        rendered = self._source_pixmap.copy()
        painter = QPainter(rendered)
        try:
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            except Exception:
                pass
            content_rect = rendered.rect()
            painter.setClipRect(content_rect)
            self._paint_overlay_layers(
                painter,
                QRectF(0.0, 0.0, float(rendered.width()), float(rendered.height())),
                content_rect,
            )
        finally:
            painter.end()
        return rendered

    def save_source_pixmap_with_overlays(
        self,
        path: str,
        fmt: str | None = None,
        quality: int = -1,
    ) -> bool:
        """Save the current source pixmap together with active overlays."""
        rendered = self.render_source_pixmap_with_overlays()
        if rendered is None or rendered.isNull():
            return False
        if fmt is None:
            return rendered.save(path, quality=quality)
        return rendered.save(path, fmt, quality)

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
        composition_grid_mode = normalize_preview_composition_grid_mode(
            getattr(options, "composition_grid_mode", PREVIEW_COMPOSITION_GRID_MODES[0])
        )
        composition_grid_line_width = normalize_preview_composition_grid_line_width(
            getattr(options, "composition_grid_line_width", PREVIEW_COMPOSITION_GRID_LINE_WIDTHS[0])
        )
        changed = self._show_focus_box != show_focus_box
        self._show_focus_box = show_focus_box
        if self._composition_grid_mode != composition_grid_mode:
            self._composition_grid_mode = composition_grid_mode
            changed = True
        if self._composition_grid_line_width != composition_grid_line_width:
            self._composition_grid_line_width = composition_grid_line_width
            changed = True
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

    def _composition_grid_target_rect(
        self,
        draw_rect: "QRectF",
        content_rect: "object",  # QRect
    ) -> "QRectF":
        """Hook for subclasses to constrain composition guides to a sub-rect."""
        return draw_rect

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

    def _emit_display_scale_percent_changed(self, *, force: bool = False) -> None:
        current = self.current_display_scale_percent()
        last = self._last_emitted_display_scale_percent
        if not force:
            if current is None and last is None:
                return
            if current is not None and last is not None and abs(current - last) < 1e-6:
                return
        self._last_emitted_display_scale_percent = current
        self.display_scale_percent_changed.emit(current)

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
        self._emit_display_scale_percent_changed()

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
        self._emit_display_scale_percent_changed(force=True)
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
        self._paint_overlay_layers(painter, draw_rect, content)

        painter.end()

    # ------------------------------------------------------------------
    # Built-in overlay painters (private)
    # ------------------------------------------------------------------

    def _paint_overlay_layers(
        self,
        painter: "QPainter",
        draw_rect: "QRectF",
        content_rect: "object",  # QRect
    ) -> None:
        if self._composition_grid_mode != PREVIEW_COMPOSITION_GRID_MODES[0]:
            self._paint_composition_grid(painter, draw_rect, content_rect)

        if self._show_focus_box and self._focus_box:
            self._paint_focus_box(painter, draw_rect, content_rect)

        self._paint_overlays(painter, draw_rect, content_rect)

        for layer_fn in self._overlay_layers:
            try:
                layer_fn(painter, draw_rect, content_rect)
            except Exception:
                pass

    def _paint_composition_grid(
        self,
        painter: "QPainter",
        draw_rect: "QRectF",
        content_rect: "object",  # QRect
    ) -> None:
        target_rect = self._composition_grid_target_rect(draw_rect, content_rect)
        if target_rect.width() < 2.0 or target_rect.height() < 2.0:
            return

        left = float(target_rect.left())
        top = float(target_rect.top())
        right = float(target_rect.right())
        bottom = float(target_rect.bottom())
        width = float(target_rect.width())
        height = float(target_rect.height())
        line_width = self._composition_grid_line_width
        mode = self._composition_grid_mode
        golden_ratio_minor = 0.3819660112501051
        golden_ratio_major = 1.0 - golden_ratio_minor

        segments: list[tuple[QPointF, QPointF]] = []

        def add_vertical(frac: float) -> None:
            x = left + (width * frac)
            segments.append((QPointF(x, top), QPointF(x, bottom)))

        def add_horizontal(frac: float) -> None:
            y = top + (height * frac)
            segments.append((QPointF(left, y), QPointF(right, y)))

        def add_square_grid() -> None:
            cell = min(width, height) / 3.0
            if cell <= 0.0:
                return
            start_x = left + max(0.0, (width - cell * 3.0) * 0.5)
            x = start_x + cell
            while x < right - 0.5:
                segments.append((QPointF(x, top), QPointF(x, bottom)))
                x += cell
            start_y = top + max(0.0, (height - cell * 3.0) * 0.5)
            y = start_y + cell
            while y < bottom - 0.5:
                segments.append((QPointF(left, y), QPointF(right, y)))
                y += cell

        if mode == "thirds":
            add_vertical(1.0 / 3.0)
            add_vertical(2.0 / 3.0)
            add_horizontal(1.0 / 3.0)
            add_horizontal(2.0 / 3.0)
        elif mode == "golden_thirds":
            add_vertical(golden_ratio_minor)
            add_vertical(golden_ratio_major)
            add_horizontal(golden_ratio_minor)
            add_horizontal(golden_ratio_major)
        elif mode == "square":
            add_square_grid()
        elif mode == "diag_square":
            add_square_grid()
            segments.append((QPointF(left, top), QPointF(right, bottom)))
            segments.append((QPointF(left, bottom), QPointF(right, top)))
        elif mode == "crosshair":
            add_vertical(0.5)
            add_horizontal(0.5)

        if not segments:
            return

        painter.save()
        try:
            for start, end in segments:
                self._draw_composition_grid_line(painter, start, end, line_width)
        finally:
            painter.restore()

    def _draw_composition_grid_line(
        self,
        painter: "QPainter",
        start: "QPointF",
        end: "QPointF",
        line_width: int,
    ) -> None:
        shadow_pen = QPen(QColor(0, 0, 0, 112))
        shadow_pen.setWidth(max(2, int(line_width) + 2))
        try:
            shadow_pen.setCosmetic(True)
        except Exception:
            pass
        painter.setPen(shadow_pen)
        painter.drawLine(start, end)

        line_pen = QPen(QColor(255, 255, 255, 176))
        line_pen.setWidth(max(1, int(line_width)))
        try:
            line_pen.setCosmetic(True)
        except Exception:
            pass
        painter.setPen(line_pen)
        painter.drawLine(start, end)

    def _paint_focus_box(self, painter: "QPainter", draw_rect: "QRectF", content: "object") -> None:
        def _fill_box_ring(
            left_px: int,
            top_px: int,
            right_px: int,
            bottom_px: int,
            thickness: int,
            color: "QColor",
        ) -> tuple[int, int, int, int]:
            if thickness <= 0:
                return (left_px, top_px, right_px, bottom_px)
            width_px = right_px - left_px
            height_px = bottom_px - top_px
            ring = min(int(thickness), max(0, width_px // 2), max(0, height_px // 2))
            if ring <= 0:
                return (left_px, top_px, right_px, bottom_px)

            painter.fillRect(left_px, top_px, width_px, ring, color)
            painter.fillRect(left_px, bottom_px - ring, width_px, ring, color)

            inner_height = height_px - (ring * 2)
            if inner_height > 0:
                painter.fillRect(left_px, top_px + ring, ring, inner_height, color)
                painter.fillRect(right_px - ring, top_px + ring, ring, inner_height, color)
            return (left_px + ring, top_px + ring, right_px - ring, bottom_px - ring)

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
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ring_left, ring_top, ring_right, ring_bottom = _fill_box_ring(
            left,
            top,
            right + 1,
            bottom + 1,
            _FOCUS_BOX_OUTER_BLACK_WIDTH,
            QColor("#000000"),
        )
        ring_left, ring_top, ring_right, ring_bottom = _fill_box_ring(
            ring_left,
            ring_top,
            ring_right,
            ring_bottom,
            _FOCUS_BOX_GREEN_WIDTH,
            QColor("#2EFF55"),
        )
        _fill_box_ring(
            ring_left,
            ring_top,
            ring_right,
            ring_bottom,
            _FOCUS_BOX_INNER_BLACK_WIDTH,
            QColor("#000000"),
        )


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
        w.set_cropped_size(3000, 1688)    # optional, for "裁切后分辨率"
        w.set_source_mode("原图")          # optional, appended to status
        w.set_focus_box(...)               # forwarded to canvas
    """

    display_scale_percent_changed = pyqtSignal(object)

    def __init__(
        self,
        parent: "QWidget | None" = None,
        *,
        canvas: "PreviewCanvas | None" = None,
    ) -> None:
        super().__init__(parent)
        self._canvas: "PreviewCanvas" = canvas if canvas is not None else PreviewCanvas()
        self._status_label = QLabel("原始分辨率: - | 裁切后分辨率: - | 缩放比例: -")
        self._display_pixmap: "QPixmap | None" = None
        self._original_size: "tuple[int, int] | None" = None
        self._cropped_size: "tuple[int, int] | None" = None
        self._source_mode: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._status_label)
        if hasattr(self._canvas, "display_scale_percent_changed"):
            self._canvas.display_scale_percent_changed.connect(self._on_canvas_display_scale_percent_changed)

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

    def set_cropped_size(self, width: int | None, height: int | None) -> None:
        """Set the 'cropped' resolution line (e.g. crop output before resize)."""
        if width is not None and height is not None:
            self._cropped_size = (int(width), int(height))
        else:
            self._cropped_size = None
        self._refresh_status_bar()

    def set_source_mode(self, mode: str) -> None:
        """Set a short mode suffix (e.g. '原图' or '预览图') for the status bar."""
        self._source_mode = str(mode).strip()
        self._refresh_status_bar()

    def current_display_scale_percent(self) -> float | None:
        """返回当前预览显示比例百分比。"""
        return self._canvas.current_display_scale_percent()

    def set_display_scale_percent(
        self,
        scale_percent: float | int,
        *,
        preserve_view: bool = True,
    ) -> bool:
        """按绝对百分比设置内层画布缩放。"""
        return self._canvas.set_display_scale_percent(scale_percent, preserve_view=preserve_view)

    def apply_overlay_state(self, state: "PreviewOverlayState | None") -> None:
        """Forward batched overlay payloads to the inner canvas."""
        self._canvas.apply_overlay_state(state)

    def apply_overlay_options(self, options: "PreviewOverlayOptions | None") -> None:
        """Forward batched overlay options to the inner canvas."""
        self._canvas.apply_overlay_options(options)

    def _refresh_status_bar(self) -> None:
        segments = self._get_status_segments()
        self._status_label.setText(" | ".join(s for s in segments if s))

    def _on_canvas_display_scale_percent_changed(self, scale_percent: object) -> None:
        self._refresh_status_bar()
        self.display_scale_percent_changed.emit(scale_percent)

    def _get_status_segments(self) -> list[str]:
        """Build status bar segments. Override in subclass to extend (open/closed)."""
        orig_str = "-"
        if self._original_size is not None:
            orig_str = f"{self._original_size[0]}x{self._original_size[1]}"
        elif self._display_pixmap is not None and not self._display_pixmap.isNull():
            orig_str = f"{self._display_pixmap.width()}x{self._display_pixmap.height()}"

        cropped_str = "-"
        if self._cropped_size is not None:
            cropped_str = f"{self._cropped_size[0]}x{self._cropped_size[1]}"

        out = [
            f"原始分辨率: {orig_str}",
            f"裁切后分辨率: {cropped_str}",
            f"缩放比例: {format_preview_scale_percent(self.current_display_scale_percent())}",
        ]
        if self._source_mode:
            out.append(f"({self._source_mode})")
        return out

    def __getattr__(self, name: str):
        """Forward other attributes/methods to the inner canvas."""
        return getattr(self._canvas, name)
