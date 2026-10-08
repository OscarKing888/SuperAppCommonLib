# -*- coding: utf-8 -*-
"""两款应用共用的设置页导航：左侧 Tab、横排文字及图标。"""

try:
    from PyQt6.QtCore import QRect, QSize, Qt
    from PyQt6.QtGui import QIcon, QPainter, QPalette, QPen
    from PyQt6.QtWidgets import QTabBar, QTabWidget, QStyle, QStyleOptionTab
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QRect, QSize, Qt
    from PyQt5.QtGui import QIcon, QPainter, QPalette, QPen
    from PyQt5.QtWidgets import QTabBar, QTabWidget, QStyle, QStyleOptionTab


class SidebarTabBar(QTabBar):
    """原生 Tab 交互配合统一横排绘制，避免 macOS 竖向样式旋转内容。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setExpanding(False)
        self.setMouseTracking(True)

    def tabSizeHint(self, index):
        icon_width = self.iconSize().width() + 8 if not self.tabIcon(index).isNull() else 0
        return QSize(max(132, self.fontMetrics().horizontalAdvance(self.tabText(index)) + icon_width + 28),
                     max(40, self.fontMetrics().height() + 20, self.iconSize().height() + 16))

    def minimumTabSizeHint(self, index):
        return self.tabSizeHint(index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        states = getattr(QStyle, "StateFlag", QStyle)
        roles = getattr(QPalette, "ColorRole", QPalette)
        groups = getattr(QPalette, "ColorGroup", QPalette)
        modes = getattr(QIcon, "Mode", QIcon)
        icon_states = getattr(QIcon, "State", QIcon)
        alignment = getattr(Qt, "AlignmentFlag", Qt)
        try:
            for index in range(self.count()):
                if not self.isTabVisible(index):
                    continue
                option = QStyleOptionTab()
                self.initStyleOption(option, index)
                selected = index == self.currentIndex()
                enabled = bool(option.state & states.State_Enabled)
                group = groups.Active if enabled else groups.Disabled
                palette = option.palette
                rect = self.tabRect(index)
                background = roles.Highlight if selected else (
                    roles.Button if option.state & states.State_MouseOver else roles.Window)
                foreground = roles.HighlightedText if selected else roles.WindowText
                # 背景、图标、文字都使用 tabRect；不调用会旋转/重排内容的原生 Tab 绘制。
                painter.setPen(getattr(Qt, "PenStyle", Qt).NoPen)
                painter.setBrush(palette.color(group, background))
                painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)
                content = rect.adjusted(14, 4, -14, -4)
                if not option.icon.isNull():
                    size = self.iconSize()
                    icon_rect = QRect(content.left(), rect.center().y() - size.height() // 2,
                                      size.width(), size.height())
                    mode = modes.Disabled if not enabled else modes.Selected if selected else modes.Normal
                    option.icon.paint(painter, icon_rect, alignment.AlignCenter, mode,
                                      icon_states.On if selected else icon_states.Off)
                    content.setLeft(icon_rect.right() + 9)
                painter.setPen(palette.color(group, foreground))
                text = self.fontMetrics().elidedText(self.tabText(index),
                    getattr(Qt, "TextElideMode", Qt).ElideRight, max(0, content.width()))
                painter.drawText(content, alignment.AlignLeft | alignment.AlignVCenter, text)
                if selected and self.hasFocus():
                    painter.setBrush(getattr(Qt, "BrushStyle", Qt).NoBrush)
                    painter.setPen(QPen(palette.color(group, foreground), 1,
                                        getattr(Qt, "PenStyle", Qt).DotLine))
                    painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), 4, 4)
        finally:
            painter.end()


class SidebarTabWidget(QTabWidget):
    """使用原生 addTab / setCurrentWidget 扩展页面，保留键盘和主题行为。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(SidebarTabBar(self))
        self.setTabPosition(getattr(QTabWidget, "TabPosition", QTabWidget).West)
        self.setIconSize(QSize(20, 20))
