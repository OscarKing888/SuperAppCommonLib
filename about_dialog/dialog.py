# -*- coding: utf-8 -*-
"""
关于对话框实现。支持 PyQt5 / PyQt6，通过传入 about_info 与可选图片路径构建界面。
"""
from __future__ import annotations

from html import escape

try:
    from PyQt6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QFrame,
    )
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QPixmap, QDesktopServices, QCursor
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
    _AlignHCenter = Qt.AlignmentFlag.AlignHCenter
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTransformation = Qt.TransformationMode.SmoothTransformation
    _RichText = Qt.TextFormat.RichText
    _PointingHandCursor = Qt.CursorShape.PointingHandCursor
except ImportError:
    from PyQt5.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QFrame,
    )
    from PyQt5.QtCore import Qt, QUrl
    from PyQt5.QtGui import QPixmap, QDesktopServices, QCursor
    _AlignCenter = Qt.AlignCenter
    _AlignHCenter = Qt.AlignHCenter
    _KeepAspectRatio = Qt.KeepAspectRatio
    _SmoothTransformation = Qt.SmoothTransformation
    _RichText = Qt.RichText
    _PointingHandCursor = Qt.PointingHandCursor


class _ImageCard(QFrame):
    """单张图片卡片：图片 + 可选说明文字，支持点击打开 URL。"""

    def __init__(self, path: str, label: str, size: int, url: str, parent=None):
        super().__init__(parent)
        self._url = url.strip() if url else ""

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        # 图片
        pix = QPixmap(path)
        img_label = QLabel()
        if not pix.isNull():
            scaled = pix.scaled(size, size, _KeepAspectRatio, _SmoothTransformation)
            img_label.setPixmap(scaled)
            img_label.setFixedSize(scaled.width(), scaled.height())
        else:
            img_label.setText("（图片无法加载）")
            img_label.setFixedSize(size, size)
        img_label.setAlignment(_AlignCenter)
        layout.addWidget(img_label, alignment=_AlignHCenter)

        # 说明文字
        if label:
            caption = QLabel(label)
            caption.setAlignment(_AlignHCenter)
            caption.setStyleSheet("font-size: 11px; color: #555;")
            caption.setWordWrap(True)
            layout.addWidget(caption)

        # 可点击样式
        if self._url:
            self.setCursor(QCursor(_PointingHandCursor))
            self.setToolTip(f"点击打开：{self._url}")
            self.setStyleSheet(
                "QFrame { border: 1px solid transparent; border-radius: 6px; }"
                "QFrame:hover { border: 1px solid #aaa; background: rgba(0,0,0,0.04); }"
            )

    def mousePressEvent(self, event):
        if self._url:
            QDesktopServices.openUrl(QUrl(self._url))
        super().mousePressEvent(event)


def show_about_dialog(
    parent,
    about_info: dict,
    *,
    logo_path: str | None = None,
    banner_path: str | None = None,
    images: list[dict] | None = None,
) -> None:
    """
    显示关于对话框。

    :param parent: 父窗口（QWidget）
    :param about_info: 关于信息字典，至少包含 app_name、version，其余键值按顺序显示；值为 URL 时显示为可点击链接
    :param logo_path: 可选，Logo 图片路径
    :param banner_path: 可选，底部 Banner 图片路径
    :param images: 可选，图片卡片列表，每项含 path / label / size / url（通常由 load_about_images() 提供）
    """
    app_name = about_info.get("app_name", "").strip() or "应用"
    d = QDialog(parent)
    d.setWindowTitle(f"关于 {app_name}")    
    main_layout = QVBoxLayout(d)
    main_layout.setSpacing(20)
    main_layout.setContentsMargins(24, 24, 24, 24)

    # ── 顶部：Logo + 文字信息 ─────────────────────────────────────────────────
    content = QHBoxLayout()
    content.setSpacing(24)

    if logo_path:
        pix = QPixmap(logo_path)
        if not pix.isNull():
            pix = pix.scaled(128, 128, _KeepAspectRatio, _SmoothTransformation)
            logo_label = QLabel()
            logo_label.setPixmap(pix)
            logo_label.setAlignment(_AlignCenter)
            content.addWidget(logo_label)

    title = f"{about_info.get('app_name', '')} {about_info.get('version', '')}".strip()
    lines = [f"<b style='font-size:14px'>{escape(title)}</b>"]
    for key, value in about_info.items():
        if key in ("app_name", "version") or not isinstance(value, str):
            continue
        val = value.strip()
        if not val:
            continue
        if val.lower().startswith(("http://", "https://")):
            lines.append(f"{escape(key)}：<a href=\"{escape(val)}\">{escape(val)}</a>")
        else:
            lines.append(f"{escape(key)}：{escape(val)}")
    text = "<br>".join("&nbsp;" if not line else line for line in lines)
    info_label = QLabel(text)
    info_label.setWordWrap(True)
    info_label.setStyleSheet("font-size: 12px; line-height: 1.4;")
    info_label.setTextFormat(_RichText)
    info_label.setOpenExternalLinks(True)
    content.addWidget(info_label, stretch=1)
    main_layout.addLayout(content)

    # ── 图片行（二维码、网站截图等）────────────────────────────────────────────
    valid_images = [img for img in (images or []) if img.get("path")]
    if valid_images:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ddd;")
        main_layout.addWidget(sep)

        img_row = QHBoxLayout()
        img_row.setSpacing(16)
        img_row.addStretch()
        for img in valid_images:
            card = _ImageCard(
                path=img.get("path", ""),
                label=img.get("label", ""),
                size=int(img.get("size", 120)),
                url=img.get("url", ""),
            )
            img_row.addWidget(card)
        img_row.addStretch()
        main_layout.addLayout(img_row)

    # ── 确定按钮 ──────────────────────────────────────────────────────────────
    btn = QPushButton("确定")
    btn.setDefault(True)
    btn.clicked.connect(d.accept)
    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_row.addWidget(btn)
    main_layout.addLayout(btn_row)

    # ── 底部 Banner（可选）────────────────────────────────────────────────────
    min_w, min_h = 720, 320
    if banner_path:
        banner_pix = QPixmap(banner_path)
        if not banner_pix.isNull():
            bw, bh = banner_pix.width(), banner_pix.height()
            banner_pix = banner_pix.scaled(bw // 2, bh // 2, _KeepAspectRatio, _SmoothTransformation)
            sw, sh = banner_pix.width(), banner_pix.height()
            banner_label = QLabel()
            banner_label.setPixmap(banner_pix)
            banner_label.setAlignment(_AlignCenter)
            banner_label.setMinimumSize(sw, sh)
            main_layout.addWidget(banner_label, alignment=_AlignCenter)
            min_w = max(min_w, sw + 48)
            min_h = min_h + 24 + sh

    d.setMinimumSize(min_w, min_h)
    d.resize(min_w, min_h)
    d.exec()
