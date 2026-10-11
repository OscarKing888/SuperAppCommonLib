# -*- coding: utf-8 -*-
"""
发送到外部应用 - 图标：按 extern_app.json 中的应用路径取系统图标。

macOS 取 .app bundle 图标，Windows 取 .exe 内嵌图标（均由 QFileIconProvider 交给系统解析）；
路径不存在或系统未返回图标时，绘制带应用名首字的圆角方块，保证工具栏按钮可区分。
"""
from __future__ import annotations

import os
import sys
from typing import Any

try:
    from PyQt6.QtCore import QFileInfo, QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import QFileIconProvider
except ImportError:  # pragma: no cover - Qt5 fallback
    from PyQt5.QtCore import QFileInfo, QRectF, Qt
    from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt5.QtWidgets import QFileIconProvider

from .config import external_app_display_name
from .send import resolve_app_path

__all__ = ["external_app_display_name", "external_app_icon", "resolve_app_icon_path"]

_FALLBACK_COLORS = (
    (0, 120, 212),
    (16, 124, 16),
    (202, 80, 16),
    (135, 100, 184),
    (0, 153, 188),
    (194, 57, 179),
)


def _mac_application_dirs() -> list[str]:
    return [
        "/Applications",
        "/System/Applications",
        os.path.join(os.path.expanduser("~"), "Applications"),
    ]


def resolve_app_icon_path(app: dict[str, Any]) -> str:
    """返回可交给系统取图标的现存路径；找不到时返回空串。"""
    raw = str(app.get("path") or "").strip()
    if not raw:
        return ""
    if sys.platform == "darwin":
        resolved = resolve_app_path(raw)
        if resolved and os.path.exists(resolved):
            return resolved
        # resolve_app_path 对找不到的 bundle 返回 open -a 用的应用名，再按常见安装目录查找。
        name = os.path.splitext(os.path.basename(resolved or raw))[0]
        for root in _mac_application_dirs():
            candidate = os.path.join(root, name + ".app")
            if os.path.isdir(candidate):
                return candidate
        return ""
    return raw if os.path.isfile(raw) else ""


def _fallback_icon(name: str, logical: int = 20) -> QIcon:
    """绘制带首字的圆角方块图标，颜色按名称稳定选择。"""
    dpr = 2
    pixmap = QPixmap(logical * dpr, logical * dpr)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    try:
        hints = getattr(QPainter, "RenderHint", QPainter)
        painter.setRenderHint(hints.Antialiasing, True)
        painter.setRenderHint(hints.TextAntialiasing, True)
        painter.scale(dpr, dpr)
        color = QColor(*_FALLBACK_COLORS[sum(ord(ch) for ch in name) % len(_FALLBACK_COLORS)])
        painter.setBrush(color)
        pen_style = getattr(getattr(Qt, "PenStyle", Qt), "NoPen")
        painter.setPen(pen_style)
        rect = QRectF(1.0, 1.0, logical - 2.0, logical - 2.0)
        painter.drawRoundedRect(rect, 4.0, 4.0)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(logical * 0.6))
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        align = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignCenter")
        painter.drawText(rect, align, (name[:1] or "?").upper())
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return QIcon(pixmap)


def external_app_icon(app: dict[str, Any], provider: QFileIconProvider | None = None) -> QIcon:
    """返回外部应用的系统图标；无法解析时返回首字占位图标。"""
    icon_path = resolve_app_icon_path(app)
    if icon_path:
        try:
            icon = (provider or QFileIconProvider()).icon(QFileInfo(icon_path))
        except Exception:
            icon = QIcon()
        if not icon.isNull() and icon.availableSizes():
            return icon
    return _fallback_icon(external_app_display_name(app))
