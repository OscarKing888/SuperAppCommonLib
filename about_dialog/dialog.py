# -*- coding: utf-8 -*-
"""共享 About UI：按内容定初始尺寸，图片自动换行，小屏幕可滚动。"""
from __future__ import annotations

from html import escape

try:
    from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                                QGridLayout, QLabel, QPushButton, QFrame,
                                QScrollArea, QWidget, QSizePolicy)
    from PyQt6.QtCore import Qt, QUrl, QEvent, QTimer
    from PyQt6.QtGui import QPixmap, QDesktopServices, QCursor
except ImportError:
    from PyQt5.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                                QGridLayout, QLabel, QPushButton, QFrame,
                                QScrollArea, QWidget, QSizePolicy)
    from PyQt5.QtCore import Qt, QUrl, QEvent, QTimer
    from PyQt5.QtGui import QPixmap, QDesktopServices, QCursor


class _ImageCard(QFrame):
    """保留原始宽高比的图片卡片，说明文字随卡片宽度换行。"""

    def __init__(self, path: str, label: str, size: int, url: str, parent=None):
        super().__init__(parent)
        self._url = url.strip() if url else ""
        self._pixmap = QPixmap(path)
        self.requested_size = max(32, min(2048, size))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.caption = QLabel(label)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setVisible(bool(label))
        layout.addWidget(self.caption)
        layout.addStretch()
        if self._url:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.setToolTip(f"点击打开：{self._url}")
        self.fit_width(self.requested_size + 16)

    def fit_width(self, width: int) -> None:
        size = max(1, min(self.requested_size, width - 16))
        self.setFixedWidth(size + 16)
        if not self._pixmap.isNull():
            pixmap = self._pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(pixmap)
            self.image_label.setFixedSize(pixmap.size())
        else:
            self.image_label.setText("（图片无法加载）")
            self.image_label.setFixedSize(size, size)
        self.caption.setFixedWidth(size)

    def mousePressEvent(self, event):
        if self._url and event.button() == Qt.MouseButton.LeftButton:
            QDesktopServices.openUrl(QUrl(self._url))
        super().mousePressEvent(event)


class AboutDialog(QDialog):
    def __init__(self, parent, about_info: dict, *, logo_path=None, banner_path=None, images=None):
        super().__init__(parent)
        app_name = str(about_info.get("app_name", "")).strip() or "应用"
        self.setWindowTitle(f"关于 {app_name}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(4, 4, 4, 4)
        self.body_layout.setSpacing(16)
        self.body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._pictures = []
        if logo_path:
            self._add_picture(logo_path, 128)

        title = f"{app_name} {about_info.get('version', '')}".strip()
        lines = [f"<h3>{escape(title)}</h3>"]
        for key, value in about_info.items():
            if key in ("app_name", "version") or not isinstance(value, str) or not value.strip():
                continue
            val = value.strip()
            rendered = f'<a href="{escape(val, quote=True)}">{escape(val)}</a>' if val.lower().startswith(("http://", "https://")) else escape(val)
            lines.append(f"{escape(str(key))}：{rendered}")
        self.info_label = QLabel("<br>".join(lines))
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        self.info_label.setOpenExternalLinks(True)
        # 长链接不应成为窗口最小宽度；字体跟随系统 DPI 和主题。
        self.info_label.setMinimumWidth(0)
        self.info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.body_layout.addWidget(self.info_label)

        self.cards = []
        for item in images or []:
            if isinstance(item, dict) and item.get("path"):
                try:
                    size = int(item.get("size", 120))
                except (TypeError, ValueError, OverflowError):
                    size = 120
                self.cards.append(_ImageCard(item["path"], item.get("label", ""), size, item.get("url", "")))
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        if self.cards:
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            self.body_layout.addWidget(separator)
            self.body_layout.addLayout(self.grid)
        if banner_path:
            self._add_picture(banner_path, 480)
        self.scroll.setWidget(body)

        buttons = QHBoxLayout()
        buttons.addStretch()
        button = QPushButton("确定")
        button.setDefault(True)
        button.clicked.connect(self.accept)
        buttons.addWidget(button)
        outer.addLayout(buttons)
        margins = outer.contentsMargins()
        self._chrome_height = (margins.top() + margins.bottom() + outer.spacing()
                               + button.sizeHint().height() + 2 * self.scroll.frameWidth() + 8)

        screen = parent.screen() if parent is not None else QApplication.primaryScreen()
        available = screen.availableGeometry().size() if screen else self.size()
        max_width, max_height = int(available.width() * .9), int(available.height() * .9)
        preferred = max(480, sum(card.requested_size + 16 for card in self.cards) + max(0, len(self.cards) - 1) * 16 + 80)
        width = min(preferred, max_width)
        self._initial_fit = True
        self._max_initial_height = max_height
        self.resize(width, max_height)
        self._layout_cards(width - 64)
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._fit_content)
        self.scroll.viewport().installEventFilter(self)

    def _add_picture(self, path: str, size: int) -> None:
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            card = _ImageCard(path, "", size, "")
            self._pictures.append(card)
            self.body_layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)

    def _layout_cards(self, width: int) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        cell_width = min(width, max((card.requested_size + 16 for card in self.cards), default=1))
        columns = max(1, (width + 16) // (cell_width + 16))
        for index, card in enumerate(self.cards):
            card.fit_width(cell_width)
            self.grid.addWidget(card, index // columns, index % columns,
                                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        for picture in self._pictures:
            picture.fit_width(width)

    def _fit_content(self) -> None:
        body = self.scroll.widget()
        width = max(1, self.scroll.viewport().width() - 8)
        self._layout_cards(width)
        # QScrollArea 必须收到新的高度，换行后才能滚动到最后一张二维码。
        self.body_layout.invalidate()
        height = max(self.body_layout.sizeHint().height(), self.body_layout.totalHeightForWidth(width))
        body.setMinimumHeight(height)
        if self._initial_fit:
            self._initial_fit = False
            self.resize(self.width(), min(self._max_initial_height, max(160, height + self._chrome_height)))
        body.updateGeometry()

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout_timer.start(0)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._relayout_timer.start(0)
        return super().eventFilter(watched, event)


def show_about_dialog(parent, about_info: dict, *, logo_path=None, banner_path=None, images=None) -> None:
    dialog = AboutDialog(parent, about_info, logo_path=logo_path, banner_path=banner_path, images=images)
    dialog.exec()
