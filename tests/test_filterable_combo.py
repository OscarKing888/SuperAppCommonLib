"""检查真实键盘窗口的焦点，而不是直接给过滤框发送按键掩盖焦点错误。"""
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QStyle, QStyleOptionComboBox, QVBoxLayout

from app_common.filterable_combo import FilterableComboBox

_APP = QApplication.instance() or QApplication([])


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def controls():
    host = QDialog()
    host.setModal(True)
    layout = QVBoxLayout(host)
    combo = FilterableComboBox()
    combo.addItem('拍摄时间', ('auto', 'capture_time'))
    combo.addItem('ISO 感光度', ('auto', 'iso'))
    combo.addItem('鸟种', ('auto', 'bird'))
    other = QLineEdit()
    layout.addWidget(combo)
    layout.addWidget(other)
    host.show()
    host.activateWindow()
    wait_until(host.isActiveWindow)
    yield host, combo, other
    combo.hidePopup()
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def open_with_mouse(combo):
    combo.setFocus()
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    arrow = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, combo)
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=arrow.center())
    search = combo._filter_popup_filter
    assert search is not None
    # Cocoa 下旧实现 focusWidget 正确，但原生 focusWindow.focusObject 仍是组合框。
    wait_until(lambda: _APP.focusWindow() is not None
               and _APP.focusWindow().focusObject() is search
               and _APP.focusWidget() is search)
    return search


@pytest.mark.parametrize('editable', [True, False])
def test_mouse_open_routes_native_keyboard_to_filter_and_commits_once(controls, editable):
    _host, combo, _other = controls
    combo.setEditable(editable)
    combo.setInsertPolicy(combo.InsertPolicy.NoInsert)
    activated = []
    combo.activated.connect(activated.append)
    search = open_with_mouse(combo)
    for key in (Qt.Key.Key_I, Qt.Key.Key_S, Qt.Key.Key_O):
        QTest.keyClick(_APP.focusWindow(), key)
    assert search.text() == 'iso'
    assert combo.currentText() == '拍摄时间'
    assert not activated
    assert combo._filter_popup_list.count() == 1
    QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Down)
    assert _APP.focusWidget() is combo._filter_popup_list
    QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Return)
    assert combo.currentData() == ('auto', 'iso')
    assert activated == [1]
    assert combo._filter_popup is None


def test_input_method_commit_escape_and_reopen(controls):
    _host, combo, _other = controls
    combo.setEditable(True)
    search = open_with_mouse(combo)
    ime = QInputMethodEvent()
    ime.setCommitString('鸟')
    QCoreApplication.sendEvent(_APP.focusWindow().focusObject(), ime)
    assert search.text() == '鸟'
    assert combo._filter_popup_list.count() == 1
    assert combo.currentText() == '拍摄时间'
    QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Escape)
    assert combo._filter_popup is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
    search = open_with_mouse(combo)
    assert search.text() == ''
    assert combo._filter_popup_list.count() == 3


def test_outside_click_and_parent_close_dismiss_popup(controls):
    host, combo, other = controls
    open_with_mouse(combo)
    QTest.mouseClick(other, Qt.MouseButton.LeftButton)
    assert combo._filter_popup is None
    host.activateWindow()
    other.setFocus()
    _APP.processEvents()
    assert _APP.focusWidget() is other
    open_with_mouse(combo)
    host.close()
    assert combo._filter_popup is None


def test_hidden_old_popup_cannot_clear_new_popup_or_steal_focus(controls):
    host, combo, other = controls
    combo.showPopup()
    old = combo._filter_popup
    combo.hidePopup()
    combo.showPopup()
    new = combo._filter_popup
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert combo._filter_popup is new and new is not old
    wait_until(lambda: _APP.focusWidget() is combo._filter_popup_filter)
    combo.hidePopup()
    host.activateWindow()
    other.setFocus()
    _APP.processEvents()
    assert _APP.focusWidget() is other


def test_no_match_disabled_item_and_same_selection_activation(controls):
    _host, combo, _other = controls
    combo.model().item(1).setEnabled(False)
    activated = []
    combo.activated.connect(activated.append)
    search = open_with_mouse(combo)
    search.setText('missing')
    QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Return)
    assert not activated and combo._filter_popup is not None
    search.setText('AUTO ISO')
    assert combo._filter_popup_list.count() == 1
    QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Return)
    assert not activated and combo.currentIndex() == 0
    search.clear()
    QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Return)
    assert activated == [0]


def test_destroying_combo_with_open_popup_does_not_call_deleted_owner(controls, monkeypatch):
    import sys
    host, _combo, _other = controls
    transient = FilterableComboBox(host)
    transient.addItem('临时字段')
    host.layout().addWidget(transient)
    transient.show()
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *args: errors.append(args))
    transient.showPopup()
    transient.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
    assert not errors
