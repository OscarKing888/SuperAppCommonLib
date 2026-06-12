# -*- coding: utf-8 -*-
"""Reusable splitter with a small triangular panel-toggle handle."""
from __future__ import annotations

try:
    from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPen, QPolygon
    from PyQt6.QtWidgets import QApplication, QSplitter, QSplitterHandle
except ImportError:
    from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal  # type: ignore[no-reattr]
    from PyQt5.QtGui import QColor, QPainter, QPen, QPolygon  # type: ignore[no-reattr]
    from PyQt5.QtWidgets import QApplication, QSplitter, QSplitterHandle  # type: ignore[no-reattr]

try:
    from PyQt6.QtCore import QSize
except ImportError:
    from PyQt5.QtCore import QSize  # type: ignore[no-reattr]


_orient = getattr(Qt, "Orientation", None)
_Horizontal = getattr(_orient, "Horizontal", None) if _orient else None
if _Horizontal is None:
    _Horizontal = getattr(Qt, "Horizontal", 1)

try:
    _LeftButton = Qt.MouseButton.LeftButton
except AttributeError:
    _LeftButton = Qt.LeftButton  # type: ignore[attr-defined]

try:
    _PainterAntialiasing = QPainter.RenderHint.Antialiasing
except AttributeError:
    _PainterAntialiasing = QPainter.Antialiasing  # type: ignore[attr-defined]


DEFAULT_HANDLE_WIDTH = 10
DEFAULT_TOGGLE_BUTTON_LENGTH = 28
_MIN_HANDLE_WIDTH = 6
_MIN_BUTTON_LENGTH = 16
_TRIANGLE_HALF_LENGTH = 6
_TRIANGLE_DEPTH = 4


class TriangleToggleSplitterHandle(QSplitterHandle):
    """Splitter handle with a small triangular click target and draggable body."""

    def __init__(self, orientation, splitter: "TriangleToggleSplitter") -> None:
        super().__init__(orientation, splitter)
        self._pressed = False
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._hover_pos: QPoint | None = None
        self.setMouseTracking(True)
        self._sync_tooltip()

    def sizeHint(self) -> QSize:
        splitter = self.splitter()
        if isinstance(splitter, TriangleToggleSplitter):
            handle_width = splitter.toggle_handle_width()
            button_length = splitter.toggle_button_length()
        else:
            handle_width = DEFAULT_HANDLE_WIDTH
            button_length = DEFAULT_TOGGLE_BUTTON_LENGTH
        if self.orientation() == _Horizontal:
            return QSize(handle_width, button_length)
        return QSize(button_length, handle_width)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:
        self.update()
        self._sync_tooltip()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_pos = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == _LeftButton:
            pos = self._event_pos(event)
            self._hover_pos = pos
            if self._toggle_button_rect().contains(pos):
                self._pressed = True
                self._dragging = False
                self._press_pos = pos
                super().mousePressEvent(event)
                self.update()
                return
            self._reset_press_state()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = self._event_pos(event)
        self._hover_pos = pos
        self._sync_tooltip()
        if self._pressed:
            if not self._dragging and self._press_pos is not None:
                try:
                    moved = (pos - self._press_pos).manhattanLength()
                except Exception:
                    moved = abs(pos.x() - self._press_pos.x()) + abs(pos.y() - self._press_pos.y())
                if moved >= self._drag_threshold():
                    self._dragging = True
            if self._dragging:
                super().mouseMoveEvent(event)
                self.update()
                return
            event.accept()
            self.update()
            return
        super().mouseMoveEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._pressed and event.button() == _LeftButton:
            pos = self._event_pos(event)
            was_dragging = self._dragging
            should_toggle = not was_dragging and self._toggle_button_rect().contains(pos)
            self._reset_press_state()
            self._hover_pos = pos
            super().mouseReleaseEvent(event)
            if should_toggle:
                splitter = self.splitter()
                if isinstance(splitter, TriangleToggleSplitter):
                    splitter.toggle_panel_for_handle(self)
            event.accept()
            self.update()
            return
        self._reset_press_state()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        self._sync_tooltip()
        painter = QPainter(self)
        try:
            painter.setRenderHint(_PainterAntialiasing, True)
            hover = self._pressed or self._toggle_button_hovered()
            button_rect = self._toggle_button_rect()
            if not button_rect.isEmpty():
                painter.fillRect(button_rect, QColor(255, 255, 255, 30 if hover else 10))
            color = QColor(220, 225, 230, 230 if hover else 168)
            painter.setPen(QPen(color, 1))
            painter.setBrush(color)
            painter.drawPolygon(self._triangle_polygon())
        finally:
            painter.end()

    def _reset_press_state(self) -> None:
        self._pressed = False
        self._dragging = False
        self._press_pos = None

    def _event_pos(self, event) -> QPoint:
        try:
            return event.position().toPoint()
        except Exception:
            return event.pos()

    def _drag_threshold(self) -> int:
        try:
            return max(1, int(QApplication.startDragDistance()))
        except Exception:
            return 4

    def _toggle_button_rect(self) -> QRect:
        rect = self.rect()
        splitter = self.splitter()
        if isinstance(splitter, TriangleToggleSplitter):
            button_length = splitter.toggle_button_length()
            handle_width = splitter.toggle_handle_width()
        else:
            button_length = DEFAULT_TOGGLE_BUTTON_LENGTH
            handle_width = DEFAULT_HANDLE_WIDTH
        if self.orientation() == _Horizontal:
            width = min(rect.width(), handle_width)
            height = min(rect.height(), button_length)
        else:
            width = min(rect.width(), button_length)
            height = min(rect.height(), handle_width)
        x = rect.x() + max(0, (rect.width() - width) // 2)
        y = rect.y() + max(0, (rect.height() - height) // 2)
        return QRect(x, y, width, height)

    def _toggle_button_hovered(self) -> bool:
        return self._hover_pos is not None and self._toggle_button_rect().contains(self._hover_pos)

    def _triangle_polygon(self) -> QPolygon:
        rect = self._toggle_button_rect()
        cx = rect.center().x()
        cy = rect.center().y()
        splitter = self.splitter()
        collapsed = (
            isinstance(splitter, TriangleToggleSplitter)
            and splitter.is_target_panel_collapsed_for_handle(self)
        )
        side = splitter.target_panel_side_for_handle(self) if isinstance(splitter, TriangleToggleSplitter) else "left"
        long = _TRIANGLE_HALF_LENGTH
        depth = _TRIANGLE_DEPTH
        if self.orientation() == _Horizontal:
            if side == "right":
                points = self._left_triangle(cx, cy, long, depth) if collapsed else self._right_triangle(cx, cy, long, depth)
            else:
                points = self._right_triangle(cx, cy, long, depth) if collapsed else self._left_triangle(cx, cy, long, depth)
        else:
            if side == "bottom":
                points = self._up_triangle(cx, cy, long, depth) if collapsed else self._down_triangle(cx, cy, long, depth)
            else:
                points = self._down_triangle(cx, cy, long, depth) if collapsed else self._up_triangle(cx, cy, long, depth)
        return QPolygon(points)

    @staticmethod
    def _left_triangle(cx: int, cy: int, long: int, depth: int) -> list[QPoint]:
        return [QPoint(cx + depth, cy - long), QPoint(cx + depth, cy + long), QPoint(cx - depth, cy)]

    @staticmethod
    def _right_triangle(cx: int, cy: int, long: int, depth: int) -> list[QPoint]:
        return [QPoint(cx - depth, cy - long), QPoint(cx - depth, cy + long), QPoint(cx + depth, cy)]

    @staticmethod
    def _up_triangle(cx: int, cy: int, long: int, depth: int) -> list[QPoint]:
        return [QPoint(cx - long, cy + depth), QPoint(cx + long, cy + depth), QPoint(cx, cy - depth)]

    @staticmethod
    def _down_triangle(cx: int, cy: int, long: int, depth: int) -> list[QPoint]:
        return [QPoint(cx - long, cy - depth), QPoint(cx + long, cy - depth), QPoint(cx, cy + depth)]

    def _sync_tooltip(self) -> None:
        splitter = self.splitter()
        if not isinstance(splitter, TriangleToggleSplitter):
            return
        if self._hover_pos is not None and not self._toggle_button_rect().contains(self._hover_pos):
            self.setToolTip("拖动调整面板大小")
            return
        index = splitter.target_panel_index_for_handle(self)
        if index < 0:
            self.setToolTip("")
            return
        action = "展开" if splitter.is_panel_collapsed(index) else "折叠"
        side = splitter.target_panel_side_for_handle(self)
        side_text = "右侧" if side in ("right", "bottom") else "左侧"
        self.setToolTip(f"{action}{side_text}面板")


class TriangleToggleSplitter(QSplitter):
    """Splitter whose handles can collapse/expand adjacent panels."""

    stateChanged = pyqtSignal()

    def __init__(
        self,
        orientation,
        parent=None,
        *,
        handle_width: int = DEFAULT_HANDLE_WIDTH,
        toggle_button_length: int = DEFAULT_TOGGLE_BUTTON_LENGTH,
    ) -> None:
        super().__init__(orientation, parent)
        self._toggle_restore_sizes: dict[int, list[int]] = {}
        self._toggle_target_by_handle_index: dict[int, int] = {}
        self._handle_width = max(_MIN_HANDLE_WIDTH, int(handle_width))
        self._toggle_button_length = max(_MIN_BUTTON_LENGTH, int(toggle_button_length))
        self.setHandleWidth(self._handle_width)

    def createHandle(self) -> QSplitterHandle:
        return TriangleToggleSplitterHandle(self.orientation(), self)

    def toggle_handle_width(self) -> int:
        return self._handle_width

    def toggle_button_length(self) -> int:
        return self._toggle_button_length

    def left_panel_index_for_handle(self, handle: QSplitterHandle) -> int:
        handle_index = self._handle_index(handle)
        if handle_index <= 0:
            return -1
        return handle_index - 1

    def set_handle_toggle_target(self, handle_index: int, panel_index: int) -> None:
        handle_index = int(handle_index)
        panel_index = int(panel_index)
        if handle_index <= 0 or not (0 <= panel_index < self.count()):
            return
        if panel_index not in (handle_index - 1, handle_index):
            return
        self._toggle_target_by_handle_index[handle_index] = panel_index
        self._refresh_handles()

    def target_panel_index_for_handle(self, handle: QSplitterHandle) -> int:
        handle_index = self._handle_index(handle)
        if handle_index <= 0:
            return -1
        target = self._toggle_target_by_handle_index.get(handle_index)
        if target is not None and 0 <= target < self.count():
            return target
        return handle_index - 1

    def target_panel_side_for_handle(self, handle: QSplitterHandle) -> str:
        handle_index = self._handle_index(handle)
        target_index = self.target_panel_index_for_handle(handle)
        if self.orientation() == _Horizontal:
            return "right" if target_index == handle_index else "left"
        return "bottom" if target_index == handle_index else "top"

    def is_left_panel_collapsed_for_handle(self, handle: QSplitterHandle) -> bool:
        index = self.left_panel_index_for_handle(handle)
        return index >= 0 and self.is_panel_collapsed(index)

    def is_target_panel_collapsed_for_handle(self, handle: QSplitterHandle) -> bool:
        index = self.target_panel_index_for_handle(handle)
        return index >= 0 and self.is_panel_collapsed(index)

    def is_panel_collapsed(self, index: int) -> bool:
        sizes = self.sizes()
        return 0 <= index < len(sizes) and sizes[index] <= 1

    def toggle_panel_for_handle(self, handle: QSplitterHandle) -> None:
        index = self.target_panel_index_for_handle(handle)
        if index < 0:
            return
        changed = False
        if self.is_panel_collapsed(index):
            changed = self._expand_panel(index)
        else:
            changed = self._collapse_panel(index)
        if changed:
            self._refresh_handles()
            self.stateChanged.emit()

    def toggle_left_panel_for_handle(self, handle: QSplitterHandle) -> None:
        self.toggle_panel_for_handle(handle)

    def _handle_index(self, handle: QSplitterHandle) -> int:
        for index in range(1, self.count()):
            if self.handle(index) is handle:
                return index
        return -1

    def export_panel_state(self) -> dict:
        """Return a JSON-serializable snapshot of splitter sizes and restore data."""
        count = self.count()
        restore_sizes: dict[str, list[int]] = {}
        for panel_index, sizes in sorted(self._toggle_restore_sizes.items()):
            normalized = self._coerce_size_list(sizes, count)
            if normalized and 0 <= panel_index < count and normalized[panel_index] > 1:
                restore_sizes[str(panel_index)] = normalized
        return {
            "version": 1,
            "panel_count": count,
            "orientation": "horizontal" if self.orientation() == _Horizontal else "vertical",
            "sizes": self._coerce_size_list(self.sizes(), count),
            "restore_sizes": restore_sizes,
        }

    def restore_panel_state(self, state: dict | None) -> bool:
        """Restore a state produced by export_panel_state()."""
        if not isinstance(state, dict):
            return False
        count = self.count()
        sizes = self._coerce_size_list(state.get("sizes"), count)
        if not sizes or sum(sizes) <= 0:
            return False
        restore_sizes: dict[int, list[int]] = {}
        raw_restore_sizes = state.get("restore_sizes")
        if isinstance(raw_restore_sizes, dict):
            for raw_index, raw_sizes in raw_restore_sizes.items():
                try:
                    panel_index = int(raw_index)
                except (TypeError, ValueError):
                    continue
                normalized = self._coerce_size_list(raw_sizes, count)
                if normalized and 0 <= panel_index < count and normalized[panel_index] > 1:
                    restore_sizes[panel_index] = normalized
        self._toggle_restore_sizes = restore_sizes
        self.setSizes(sizes)
        self._refresh_handles()
        return True

    @staticmethod
    def _coerce_size_list(value, count: int) -> list[int]:
        if not isinstance(value, (list, tuple)) or len(value) != count:
            return []
        sizes: list[int] = []
        for item in value:
            try:
                number = int(item)
            except (TypeError, ValueError):
                return []
            sizes.append(max(0, number))
        return sizes

    def _collapse_panel(self, index: int) -> bool:
        sizes = self.sizes()
        if not (0 <= index < len(sizes)) or sizes[index] <= 1:
            return False
        self._toggle_restore_sizes[index] = list(sizes)
        collapsed_width = sizes[index]
        sizes[index] = 0
        target = index + 1 if index + 1 < len(sizes) else index - 1
        if 0 <= target < len(sizes):
            sizes[target] += collapsed_width
        self.setSizes(sizes)
        return True

    def _expand_panel(self, index: int) -> bool:
        restore_sizes = self._toggle_restore_sizes.get(index)
        if restore_sizes and len(restore_sizes) == self.count() and restore_sizes[index] > 1:
            self.setSizes(restore_sizes)
            return True

        sizes = self.sizes()
        if not (0 <= index < len(sizes)):
            return False
        target = index + 1 if index + 1 < len(sizes) else index - 1
        width = self._fallback_expand_size(index)
        if 0 <= target < len(sizes):
            width = min(width, max(1, sizes[target] - 80))
            sizes[target] = max(1, sizes[target] - width)
        sizes[index] = max(1, width)
        self.setSizes(sizes)
        return True

    def _fallback_expand_size(self, index: int) -> int:
        widget = self.widget(index)
        minimum = widget.minimumSizeHint()
        preferred = widget.sizeHint()
        if self.orientation() == _Horizontal:
            return max(180, minimum.width(), preferred.width())
        return max(180, minimum.height(), preferred.height())

    def _refresh_handles(self) -> None:
        for index in range(1, self.count()):
            handle = self.handle(index)
            if isinstance(handle, TriangleToggleSplitterHandle):
                handle._sync_tooltip()
            handle.update()


__all__ = [
    "DEFAULT_HANDLE_WIDTH",
    "DEFAULT_TOGGLE_BUTTON_LENGTH",
    "TriangleToggleSplitter",
    "TriangleToggleSplitterHandle",
]
