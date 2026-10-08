# -*- coding: utf-8 -*-
"""两款应用共用的设置页导航：左侧 Tab、横排文字及图标。"""

try:
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QTabBar, QTabWidget, QStyle, QStyleOptionTab, QStylePainter
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QSize
    from PyQt5.QtWidgets import QTabBar, QTabWidget, QStyle, QStyleOptionTab, QStylePainter


class SidebarTabBar(QTabBar):
    """West-side tabs with horizontal, keyboard-accessible labels."""
    def tabSizeHint(self, index):
        icon_width = self.iconSize().width() + 8 if not self.tabIcon(index).isNull() else 0
        return QSize(max(132, self.fontMetrics().horizontalAdvance(self.tabText(index)) + icon_width + 28),
                     max(40, self.fontMetrics().height() + 20))

    def paintEvent(self, event):
        painter = QStylePainter(self)
        controls = getattr(QStyle, "ControlElement", QStyle)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(controls.CE_TabBarTabShape, option)
            option.shape = getattr(QTabBar, "Shape", QTabBar).RoundedNorth
            painter.drawControl(controls.CE_TabBarTabLabel, option)


class SidebarTabWidget(QTabWidget):
    """使用原生 addTab / setCurrentWidget 扩展页面，保留键盘和主题行为。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(SidebarTabBar(self))
        self.setTabPosition(getattr(QTabWidget, "TabPosition", QTabWidget).West)
        self.setIconSize(QSize(20, 20))
