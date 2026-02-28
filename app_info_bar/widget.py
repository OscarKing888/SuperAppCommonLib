# -*- coding: utf-8 -*-
"""
App 信息条：图标 + 主副标题 + “关于...” 按钮。支持 PyQt5 / PyQt6。
"""
from __future__ import annotations

try:
    from PyQt6.QtWidgets import (
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
    )
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPixmap
    _AlignTop = Qt.AlignmentFlag.AlignTop
    _PointingHandCursor = Qt.CursorShape.PointingHandCursor
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTransformation = Qt.TransformationMode.SmoothTransformation
except ImportError:
    from PyQt5.QtWidgets import (
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
    )
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QPixmap
    _AlignTop = Qt.AlignTop
    _PointingHandCursor = Qt.PointingHandCursor
    _KeepAspectRatio = Qt.KeepAspectRatio
    _SmoothTransformation = Qt.SmoothTransformation


class AppInfoBar(QWidget):
    """
    左侧 App 信息区：图标（可选）+ 主标题 + 副标题 + “关于...” 按钮。
    点击“关于...”时调用 on_about_clicked（若提供）。
    """

    def __init__(
        self,
        parent=None,
        *,
        title: str = "Super Viewer",
        subtitle: str = "查看与编辑EXIF",
        icon_path: str | None = None,
        on_about_clicked: callable | None = None,
    ):
        super().__init__(parent)
        self._on_about_clicked = on_about_clicked
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        if icon_path:
            icon_label = QLabel()
            pix = QPixmap(icon_path)
            icon_label.setPixmap(pix.scaled(64, 64, _KeepAspectRatio, _SmoothTransformation))
            icon_label.setFixedSize(64, 64)
            layout.addWidget(icon_label)
        text_col = QWidget()
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(8, 0, 0, 0)
        text_layout.setSpacing(0)
        text_layout.setAlignment(_AlignTop)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #eee; margin: 0; padding: 0;")
        text_layout.addWidget(title_label)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setStyleSheet("font-size: 12px; color: #999; margin: 0; padding: 0;")
        text_layout.addWidget(subtitle_label)
        about_btn = QPushButton("关于...")
        about_btn.setFlat(True)
        about_btn.setStyleSheet(
            "QPushButton { color: #7eb8ed; text-align: left; padding: 0; margin: 0; min-height: 0; } "
            "QPushButton:hover { color: #9dd; }"
        )
        about_btn.setCursor(_PointingHandCursor)
        about_btn.clicked.connect(self._on_about_click)
        text_layout.addWidget(about_btn)
        layout.addWidget(text_col, 1, _AlignTop)

    def _on_about_click(self):
        if self._on_about_clicked:
            self._on_about_clicked()
