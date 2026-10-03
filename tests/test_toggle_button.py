"""实际 Qt 绘制与交互回归：选中态不能被主题或静默恢复覆盖。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPalette, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QButtonGroup

from app_common.toggle_button import ToggleToolButton

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("background,text", [("#303030", "#dddddd"), ("#eeeeee", "#222222")])
def test_rendered_states_icons_keyboard_and_silent_restore(background, text):
    original = _APP.palette()
    palette = QPalette(original)
    palette.setColor(QPalette.ColorRole.Button, QColor(background))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(text))
    _APP.setPalette(palette)
    button = ToggleToolButton("显示 RAW")
    button.resize(140, 32)
    button.show()
    _APP.processEvents()
    changes = []
    button.toggled.connect(changes.append)
    try:
        # 固定采样文字和圆角以外的区域，不依赖系统字体或平台抗锯齿。
        off = button.grab().toImage().pixelColor(8, 8)
        button.blockSignals(True)
        button.setChecked(True)
        button.blockSignals(False)
        on = button.grab().toImage().pixelColor(8, 8)
        assert changes == []
        assert on.blue() > on.red() + 80 and on != off
        rendered = button.grab().toImage()
        assert any(rendered.pixelColor(x, y).name() == "#ffffff"
                   for x in range(20, 120) for y in range(8, 24))
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert changes == [False] and not button.isChecked()

        glyph = QPixmap(18, 18)
        glyph.fill(QColor("black"))
        button.setIcon(QIcon(glyph))
        for mode, state, expected in (
            (QIcon.Mode.Normal, QIcon.State.Off, text),
            (QIcon.Mode.Normal, QIcon.State.On, "#ffffff"),
            (QIcon.Mode.Disabled, QIcon.State.On, "#c0cbd6"),
        ):
            icon = button.icon().pixmap(QSize(18, 18), mode, state).toImage()
            assert icon.pixelColor(9, 9).name() == expected
        button.setChecked(True)
        button.setEnabled(False)
        disabled_on = button.grab().toImage().pixelColor(8, 8)
        button.setChecked(False)
        disabled_off = button.grab().toImage().pixelColor(8, 8)
        assert disabled_on != disabled_off and disabled_on != on
        assert button.text() == "显示 RAW"
    finally:
        button.close()
        _APP.setPalette(original)


def test_exclusive_tools_keep_native_selection_semantics():
    group = QButtonGroup()
    first, second = ToggleToolButton("选择"), ToggleToolButton("裁切")
    group.addButton(first)
    group.addButton(second)
    first.setChecked(True)
    second.click()
    assert second.isChecked() and not first.isChecked()
    second.click()
    assert second.isChecked()


def test_live_theme_change_keeps_icon_and_background_in_same_palette():
    from app_common.preview_toolbar import iconize
    original = _APP.palette()
    button = iconize(ToggleToolButton(), 'grid', '构图线')
    button.setChecked(False)
    button.resize(48, 36)
    button.show()
    try:
        for background, text in (('#eeeeee', '#222222'), ('#303030', '#eeeeee'), ('#eeeeee', '#222222')):
            palette = QPalette(original)
            palette.setColor(QPalette.ColorRole.Button, QColor(background))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(text))
            _APP.setPalette(palette)
            _APP.processEvents()
            button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
            assert button.grab().toImage().pixelColor(8, 8).name() == background
            icon = button.icon().pixmap(QSize(36, 36), QIcon.Mode.Normal, QIcon.State.Off).toImage()
            assert any(icon.pixelColor(x, y).name() == text and icon.pixelColor(x, y).alpha() == 255
                       for x in range(36) for y in range(36))
    finally:
        button.close()
        _APP.setPalette(original)
