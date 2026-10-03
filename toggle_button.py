# -*- coding: utf-8 -*-
"""两款应用共用的高对比预览开关，保留 Qt 原生 checked / toggled 语义。"""
from __future__ import annotations

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPalette, QPixmap
    from PyQt6.QtWidgets import QApplication, QToolButton
except ImportError:  # pragma: no cover - PyQt5 compatibility
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPainter, QPalette, QPixmap
    from PyQt5.QtWidgets import QApplication, QToolButton


# 紧凑过滤徽章复用选中态，保留自己的圆角、尺寸及未选中颜色。
TOGGLE_CHECKED_STYLE = """
QToolButton:checked {
    background-color: #1769c2;
    color: #ffffff;
    border-color: #70b7ff;
}
QToolButton:checked:hover { background-color: #2079d8; }
QToolButton:checked:pressed { background-color: #105399; }
QToolButton:checked:focus { border: 1px dashed #ffffff; }
QToolButton:checked:disabled {
    background-color: #425c78;
    color: #c0cbd6;
    border-color: #637c96;
}
"""

TOGGLE_BUTTON_STYLE = """
QToolButton {
    background-color: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 4px 6px;
}
QToolButton:hover:!checked { background-color: palette(midlight); border-color: #579de6; }
QToolButton:pressed:!checked { background-color: palette(mid); }
QToolButton:focus { border: 1px dashed #579de6; }
QToolButton:disabled {
    background-color: palette(button);
    color: palette(mid);
    border-color: palette(mid);
}
""" + TOGGLE_CHECKED_STYLE


class _ToggleIconEngine(QIconEngine):
    """按绘制状态给单色工具图标着色；高 DPI 和主题切换无需重建按钮。"""

    def __init__(self, source: QIcon):
        super().__init__()
        self.source = QIcon(source)

    def clone(self):
        return _ToggleIconEngine(self.source)

    def paint(self, painter, rect, mode, state):
        ratio = painter.device().devicePixelRatioF()
        size = rect.size() * ratio
        pixmap = self.source.pixmap(size)
        if pixmap.isNull():
            return
        pixmap.setDevicePixelRatio(ratio)
        modes = getattr(QIcon, "Mode", QIcon)
        states = getattr(QIcon, "State", QIcon)
        roles = getattr(QPalette, "ColorRole", QPalette)
        groups = getattr(QPalette, "ColorGroup", QPalette)
        if state == states.On:
            color = QColor("#c0cbd6" if mode == modes.Disabled else "#ffffff")
        else:
            group = groups.Disabled if mode == modes.Disabled else groups.Active
            color = QApplication.palette().color(group, roles.ButtonText)
        tint = QPainter(pixmap)
        composition = getattr(QPainter, "CompositionMode", QPainter)
        tint.setCompositionMode(composition.CompositionMode_SourceIn)
        tint.fillRect(pixmap.rect(), color)
        tint.end()
        painter.drawPixmap(rect, pixmap)

    def pixmap(self, size, mode, state):
        if size.isEmpty():
            return QPixmap()
        result = QPixmap(size)
        result.fill(getattr(Qt, "GlobalColor", Qt).transparent)
        painter = QPainter(result)
        self.paint(painter, result.rect(), mode, state)
        painter.end()
        return result


class ToggleToolButton(QToolButton):
    """统一文字/单色图标开关；互斥模式继续交给 QButtonGroup 管理。

    不拼接勾号、不修改文案，也不连接业务信号；setChecked/blockSignals 与
    工作区恢复照常工作。普通瞬时操作仍使用原来的 QPushButton/QToolButton。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setAutoRaise(False)
        self.setFocusPolicy(getattr(Qt, "FocusPolicy", Qt).StrongFocus)
        self.setStyleSheet(TOGGLE_BUTTON_STYLE)
        QApplication.instance().paletteChanged.connect(self._on_application_palette_changed)

    def _on_application_palette_changed(self, palette) -> None:
        # Qt 局部 QSS 会缓存 palette()；重解析背景与动态图标使用同一主题。
        self.setPalette(palette)
        self.setStyleSheet(self.styleSheet())
        self.update()

    def setIcon(self, icon: QIcon) -> None:
        super().setIcon(QIcon(_ToggleIconEngine(icon)) if not icon.isNull() else icon)


__all__ = ["ToggleToolButton", "TOGGLE_CHECKED_STYLE"]
