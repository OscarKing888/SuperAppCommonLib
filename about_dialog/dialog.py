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
    )
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPixmap
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTransformation = Qt.TransformationMode.SmoothTransformation
    _RichText = Qt.TextFormat.RichText
except ImportError:
    from PyQt5.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
    )
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QPixmap
    _AlignCenter = Qt.AlignCenter
    _KeepAspectRatio = Qt.KeepAspectRatio
    _SmoothTransformation = Qt.SmoothTransformation
    _RichText = Qt.RichText


def show_about_dialog(
    parent,
    about_info: dict,
    *,
    logo_path: str | None = None,
    banner_path: str | None = None,
) -> None:
    """
    显示关于对话框。

    :param parent: 父窗口（QWidget）
    :param about_info: 关于信息字典，至少包含 app_name、version，其余键值按顺序显示；值为 URL 时显示为可点击链接
    :param logo_path: 可选，Logo 图片路径（如未提供则不显示 Logo）
    :param banner_path: 可选，底部 Banner 图片路径（如未提供则不显示 Banner）
    """
    app_name = about_info.get("app_name", "").strip() or "应用"
    d = QDialog(parent)
    d.setWindowTitle(f"关于 {app_name}")
    main_layout = QVBoxLayout(d)
    main_layout.setSpacing(24)
    main_layout.setContentsMargins(24, 24, 24, 24)
    content = QHBoxLayout()
    content.setSpacing(24)

    # 左侧：LOGO
    if logo_path:
        pix = QPixmap(logo_path)
        if not pix.isNull():
            pix = pix.scaled(128, 128, _KeepAspectRatio, _SmoothTransformation)
            logo_label = QLabel()
            logo_label.setPixmap(pix)
            logo_label.setAlignment(_AlignCenter)
            content.addWidget(logo_label)

    # 右侧：App 信息（version 之后按 cfg 顺序自适应，有几条显示几条；值为 URL 则显示为可点击链接）
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

    btn = QPushButton("确定")
    btn.setDefault(True)
    btn.clicked.connect(d.accept)
    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_row.addWidget(btn)
    main_layout.addLayout(btn_row)

    # 底部：banner 图（缩小为一半显示）；对话框最小尺寸随其调整
    min_w, min_h = 480, 320
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
    d.exec()
