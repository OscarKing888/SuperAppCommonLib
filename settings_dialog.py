# -*- coding: utf-8 -*-
"""可扩展的用户选项外壳：应用提供页面及保存逻辑，公共层负责布局。"""

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (
        QDialog, QDialogButtonBox, QFrame, QLabel, QLayout, QScrollArea,
        QSizePolicy, QVBoxLayout,
    )
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import (
        QDialog, QDialogButtonBox, QFrame, QLabel, QLayout, QScrollArea,
        QSizePolicy, QVBoxLayout,
    )

from .sidebar_tabs import SidebarTabWidget


class SettingsPage(QScrollArea):
    """内容按自身所需尺寸靠左上排列；小窗口通过滚动访问完整页面。"""

    def __init__(self, content, parent=None):
        super().__init__(parent)
        alignment = getattr(Qt, "AlignmentFlag", Qt)
        self.setAlignment(alignment.AlignLeft | alignment.AlignTop)
        self.setFrameShape(getattr(QFrame, "Shape", QFrame).NoFrame)
        self.setWidgetResizable(True)
        policy = getattr(QSizePolicy, "Policy", QSizePolicy)
        # Maximum 允许内容收窄，但不把多余空间分摊给表单行或空白区域。
        content.setSizePolicy(policy.Maximum, policy.Maximum)
        if content.layout() is not None:
            content.layout().setAlignment(alignment.AlignLeft | alignment.AlignTop)
            content.layout().setContentsMargins(16, 16, 16, 16)
            content.layout().setSpacing(12)
            content.layout().setSizeConstraint(
                getattr(QLayout, "SizeConstraint", QLayout).SetMinimumSize)
        self.setWidget(content)


class SettingsDialog(QDialog):
    """共享设置对话框；子类用 add_page 注册 QWidget，覆写 accept 校验/保存。"""

    def __init__(self, parent=None, *, title="用户选项"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(640, 420)
        self.resize(920, 660)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.description = QLabel(self)
        self.description.setWordWrap(True)
        self.description.hide()
        layout.addWidget(self.description)

        self.tabs = SidebarTabWidget(self)
        layout.addWidget(self.tabs, 1)
        standard = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
        self.buttons = QDialogButtonBox(standard.Ok | standard.Cancel, parent=self)
        self.buttons.button(standard.Ok).setText("确定")
        self.buttons.button(standard.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def set_description(self, text):
        self.description.setText(text)
        self.description.setVisible(bool(text))

    def add_page(self, content, title, icon=None):
        """返回滚动页，便于调用方切页或定位控件；不接管应用的配置持久化。"""
        page = SettingsPage(content, self.tabs)
        self.tabs.addTab(page, icon if icon is not None else QIcon(), title)
        return page
