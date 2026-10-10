"""共享选项外壳：原生样式、缩放、动态长页面、切页及按钮生命周期。"""
import pytest
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLineEdit, QStyleFactory,
    QVBoxLayout, QWidget,
)

from app_common.settings_dialog import SettingsDialog

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('style', QStyleFactory.keys())
def test_resize_top_left_scroll_dynamic_content_and_switching(style):
    previous_style = _APP.style().objectName()
    _APP.setStyle(style)
    dialog = SettingsDialog()
    content = QWidget()
    layout = QVBoxLayout(content)
    first = QLineEdit('中文选项')
    layout.addWidget(first)
    scroll = dialog.add_page(content, '常规')
    other = QWidget()
    QVBoxLayout(other).addWidget(QLineEdit('其他设置'))
    dialog.add_page(other, '扩展')
    try:
        dialog.show()
        for size in (QSize(640, 420), QSize(1200, 900), QSize(800, 550)):
            dialog.resize(size)
            _APP.processEvents()
            assert dialog.size() == size
            assert dialog.tabs.tabBar().y() <= 4
            assert content.pos() == QPoint(0, 0)
            assert first.mapTo(scroll.viewport(), QPoint()) == QPoint(16, 16)
            assert content.height() < scroll.viewport().height()
            assert content.width() < scroll.viewport().width()
            assert dialog.buttons.geometry().bottom() <= dialog.height()
            assert dialog.buttons.geometry().top() > dialog.tabs.geometry().bottom()
        # 即使应用为页面保留最小空间，实际字段也必须靠左上而不是居中。
        content.setMinimumSize(500, 300)
        _APP.processEvents()
        assert first.mapTo(scroll.viewport(), QPoint()) == QPoint(16, 16)
        # 页面变长后必须重新计算滚动范围，不能把按钮推到窗口外。
        for i in range(35):
            last = QLineEdit(f'新增设置 {i}')
            layout.addWidget(last)
        _APP.processEvents()
        _APP.processEvents()
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.ensureWidgetVisible(last)
        _APP.processEvents()
        assert scroll.viewport().rect().contains(last.mapTo(scroll.viewport(), last.rect().center()))
        QTest.mouseClick(dialog.tabs.tabBar(), Qt.MouseButton.LeftButton,
                         pos=dialog.tabs.tabBar().tabRect(1).center())
        assert dialog.tabs.currentIndex() == 1
        dialog.tabs.tabBar().setFocus()
        QTest.keyClick(dialog.tabs.tabBar(), Qt.Key.Key_Left)
        assert dialog.tabs.currentIndex() == 0
        assert first.text() == '中文选项'
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()
        _APP.setStyle(previous_style)


def test_buttons_keep_application_validation_and_cancel_semantics():
    class ValidatingDialog(SettingsDialog):
        allowed = False
        saved = 0

        def accept(self):
            if self.allowed:
                self.saved += 1
                super().accept()

    dialog = ValidatingDialog()
    try:
        dialog.show()
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.click()
        assert dialog.isVisible() and dialog.saved == 0
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        assert dialog.result() == QDialog.DialogCode.Rejected and dialog.saved == 0
        dialog.show()
        dialog.allowed = True
        ok.click()
        assert dialog.result() == QDialog.DialogCode.Accepted and dialog.saved == 1
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()
