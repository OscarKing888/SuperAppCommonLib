# -*- coding: utf-8 -*-
"""跨应用折叠分组：只管理显示，不改变正文控件的值、启用状态或任务生命周期。"""
from __future__ import annotations

try:
    from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
    from PyQt6.QtGui import QPainter, QPalette, QPolygonF
    from PyQt6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QStyle, QStyleOptionFocusRect, QToolButton, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
    from PyQt5.QtGui import QPainter, QPalette, QPolygonF
    from PyQt5.QtWidgets import QFrame, QScrollArea, QSizePolicy, QStyle, QStyleOptionFocusRect, QToolButton, QVBoxLayout, QWidget

_Policy = getattr(QSizePolicy, "Policy", QSizePolicy)
_Role = getattr(QPalette, "ColorRole", QPalette)
_Group = getattr(QPalette, "ColorGroup", QPalette)
_Arrow = getattr(Qt, "ArrowType", Qt)
_Align = getattr(Qt, "AlignmentFlag", Qt)
_Pen = getattr(Qt, "PenStyle", Qt)
_Focus = getattr(Qt, "FocusPolicy", Qt)
_ToolStyle = getattr(Qt, "ToolButtonStyle", Qt)
_Elide = getattr(Qt, "TextElideMode", Qt)
_RenderHint = getattr(QPainter, "RenderHint", QPainter)
_Primitive = getattr(QStyle, "PrimitiveElement", QStyle)

# 样式只管理边距和字体；容器在 paintEvent 中读取实时 palette，支持运行时换肤。
COLLAPSIBLE_SECTION_STYLE = """
QToolButton#CollapsibleHeaderButton {
    font-weight: 600;
    border: none;
    background: transparent;
    padding: 0;
}
QFrame#CollapsibleContentFrame {
    border: none;
    background: transparent;
    padding: 0;
}
"""


def refresh_layout_chain(widget: QWidget | None) -> None:
    """刷新到最近滚动区的嵌套布局，不调整宿主窗口尺寸。"""
    if widget is None:
        return

    scroll_area: QScrollArea | None = None
    chain: list[QWidget] = []
    current: QWidget | None = widget
    while current is not None:
        chain.append(current)
        if isinstance(current, QScrollArea):
            scroll_area = current
            break
        current = current.parentWidget()

    for node in chain:
        if isinstance(node, CollapsibleSection):
            node.refresh_section_layout()
        layout = node.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        node.updateGeometry()

    if scroll_area is None:
        return
    inner = scroll_area.widget()
    if inner is None or inner in chain:
        return
    layout = inner.layout()
    if layout is not None:
        layout.invalidate()
        layout.activate()
    inner.updateGeometry()


class _DisclosureButton(QToolButton):
    """按逻辑像素绘制小实心三角，避免系统主题把箭头放大为粗折线。"""

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 36, max(28, metrics.height() + 10))

    def minimumSizeHint(self) -> QSize:
        return QSize(36, self.sizeHint().height())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(_RenderHint.Antialiasing)
        palette = self.palette()
        group = _Group.Active if self.isEnabled() else _Group.Disabled
        color = palette.color(group, _Role.WindowText)
        if self.isDown() or self.underMouse():
            hover = palette.color(_Role.Highlight)
            hover.setAlpha(24 if self.isDown() else 12)
            painter.setPen(_Pen.NoPen)
            painter.setBrush(hover)
            painter.drawRoundedRect(self.rect(), 5, 5)
        cy = self.height() / 2
        # 8×5 / 5×8 的实心三角，在高 DPI 下由 Qt 自动缩放。
        points = ([(10, cy - 2), (18, cy - 2), (14, cy + 3)] if self.isChecked()
                  else [(12, cy - 4), (17, cy), (12, cy + 4)])
        painter.setPen(_Pen.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        painter.setPen(color)
        text_rect = QRect(26, 0, max(0, self.width() - 34), self.height())
        painter.drawText(text_rect, _Align.AlignLeft | _Align.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(), _Elide.ElideRight, text_rect.width()))
        if self.hasFocus():
            option = QStyleOptionFocusRect()
            option.initFrom(self)
            option.rect = self.rect().adjusted(2, 2, -2, -2)
            self.style().drawPrimitive(_Primitive.PE_FrameFocusRect, option, painter, self)


class CollapsibleSection(QFrame):
    """可折叠的分组容器。"""

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        expanded: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        self.setProperty("collapsibleSection", True)
        self.setStyleSheet(COLLAPSIBLE_SECTION_STYLE)
        self._expanded_size_policy = QSizePolicy(self.sizePolicy())
        self._content_widget: QWidget | None = None
        self._expanded = bool(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.header_button = _DisclosureButton(self)
        self.header_button.setObjectName("CollapsibleHeaderButton")
        self.header_button.setToolButtonStyle(_ToolStyle.ToolButtonTextBesideIcon)
        self.header_button.setArrowType(_Arrow.DownArrow if self._expanded else _Arrow.RightArrow)
        self.header_button.setText(str(title or "").strip())
        self.header_button.setAccessibleName(str(title or "").strip())
        self.header_button.setFocusPolicy(_Focus.StrongFocus)
        self.header_button.setCheckable(True)
        self.header_button.setChecked(self._expanded)
        self.header_button.setSizePolicy(_Policy.Expanding, _Policy.Fixed)
        self.header_button.clicked.connect(self.set_expanded)
        root.addWidget(self.header_button)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("CollapsibleContentFrame")
        self.content_layout = QVBoxLayout(self.content_frame)
        self.content_layout.setContentsMargins(8, 0, 8, 8)
        self.content_layout.setSpacing(0)
        self.content_frame.setVisible(self._expanded)
        root.addWidget(self.content_frame)
        self.set_content_widget(QWidget())
        self._apply_expanded_size_policy(self._expanded)

    def paintEvent(self, event) -> None:
        # QSS 的 palette() 会缓存颜色；实时绘制避免换肤后标题和正文颜色不一致。
        painter = QPainter(self)
        painter.setRenderHint(_RenderHint.Antialiasing)
        painter.setPen(self.palette().color(_Role.Mid))
        painter.setBrush(self.palette().color(_Role.Base))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

    @property
    def body(self) -> QWidget:
        """表单直接放进正文；标题和正文共用容器背景。"""
        assert self._content_widget is not None
        return self._content_widget

    def title(self) -> str:
        return self.header_button.text()

    def setTitle(self, title: str) -> None:
        self.header_button.setText(title)
        self.header_button.setAccessibleName(title)
        self.header_button.updateGeometry()

    def _apply_expanded_size_policy(self, expanded: bool) -> None:
        if expanded:
            self.setSizePolicy(self._expanded_size_policy)
        else:
            self._expanded_size_policy = QSizePolicy(self.sizePolicy())
            policy = QSizePolicy(self._expanded_size_policy)
            policy.setVerticalPolicy(_Policy.Maximum)
            self.setSizePolicy(policy)

    def set_content_widget(self, widget: QWidget) -> None:
        if self._content_widget is widget:
            return
        if self._content_widget is not None:
            self.content_layout.removeWidget(self._content_widget)
            self._content_widget.setParent(None)
        self._content_widget = widget
        self.content_layout.addWidget(widget)

    def refresh_section_layout(self) -> None:
        content = self._content_widget
        if content is not None:
            content.updateGeometry()
            content_layout = content.layout()
            if content_layout is not None:
                content_layout.invalidate()
                content_layout.activate()
        self.content_frame.updateGeometry()
        self.content_layout.invalidate()
        self.content_layout.activate()
        self.updateGeometry()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        state = bool(expanded)
        if self._expanded == state:
            self.header_button.setChecked(state)
            self.header_button.setArrowType(_Arrow.DownArrow if state else _Arrow.RightArrow)
            self.content_frame.setVisible(state)
            self.refresh_section_layout()
            return
        self._expanded = state
        blocked = self.header_button.blockSignals(True)
        self.header_button.setChecked(state)
        self.header_button.blockSignals(blocked)
        self.header_button.setArrowType(_Arrow.DownArrow if state else _Arrow.RightArrow)
        self.content_frame.setVisible(state)
        self._apply_expanded_size_policy(state)
        refresh_layout_chain(self)
        self.toggled.emit(state)
