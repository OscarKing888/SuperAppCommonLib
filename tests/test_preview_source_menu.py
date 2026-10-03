"""来源菜单互斥、状态同步和主按钮点击语义。"""
from pathlib import Path
import subprocess
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionToolButton

from app_common.preview_source_menu import PreviewSourceMenu
from app_common.toggle_button import ToggleToolButton

_APP = QApplication.instance() or QApplication([])


def _exercise_split_menu():
    button = ToggleToolButton('默认预览')
    menu = PreviewSourceMenu(button)
    menu.sync('default', raw_available=True)
    clicks, selected, opened = [], [], []
    button.clicked.connect(clicks.append)
    menu.mode_selected.connect(selected.append)
    menu.aboutToShow.connect(lambda: opened.append(True))
    button.resize(button.sizeHint())
    button.show()
    _APP.processEvents()
    try:
        option = QStyleOptionToolButton()
        button.initStyleOption(option)
        arrow = button.style().subControlRect(QStyle.ComplexControl.CC_ToolButton, option,
                                             QStyle.SubControl.SC_ToolButtonMenu, button)
        assert arrow.width() >= 12
        QTimer.singleShot(30, menu.close)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=arrow.center())
        assert opened == [True] and clicks == [] and selected == []
        menu.mode_actions['denoised'].trigger()
        assert selected == ['denoised'] and clicks == []
        assert [a.data() for a in menu.actions() if a.isChecked()] == ['denoised']
        button.click()
        assert len(clicks) == 1  # 主按钮仍可由应用连接循环切换。
    finally:
        button.close()


def test_split_menu_opens_without_cycling_and_radio_selection_is_exclusive():
    # 弹出菜单会进入原生嵌套事件循环；独立进程避免其它 GUI 用例遗留定时器回调。
    result = subprocess.run([sys.executable, '-c',
        'import runpy,sys; runpy.run_path(sys.argv[1], run_name="__main__")', str(Path(__file__))],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sync_is_silent_and_non_raw_effective_state_is_default():
    button = ToggleToolButton()
    menu = PreviewSourceMenu(button)
    selected = []
    menu.mode_selected.connect(selected.append)
    menu.sync('raw', raw_available=True)
    assert menu.group.checkedAction().data() == 'raw'
    menu.sync('raw', raw_available=False)
    assert menu.group.checkedAction().data() == 'default'
    assert not menu.mode_actions['raw'].isEnabled()
    menu.sync('denoised', raw_available=False)
    assert menu.group.checkedAction().data() == 'denoised'
    assert selected == []


if __name__ == '__main__':
    _exercise_split_menu()
