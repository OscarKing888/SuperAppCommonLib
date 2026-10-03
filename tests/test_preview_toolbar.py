"""图标菜单保持原生选项语义，恢复状态不触发业务回调。"""
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QComboBox
from app_common.preview_toolbar import ViewportOverlayTools, iconize, zoom_menu
from app_common.toggle_button import ToggleToolButton

_APP = QApplication.instance() or QApplication([])


def test_restore_and_radio_menus_are_independent_and_silent():
    a, b = ViewportOverlayTools(with_crop=True), ViewportOverlayTools(with_crop=True)
    events = []
    a.changed.connect(lambda: events.append(a.state()))
    try:
        a.restore(dict(show_focus_box=False, show_bird_box=True, show_crop_effect=False,
                       crop_effect_alpha=87, composition_grid_mode='thirds', composition_grid_line_width=3))
        assert not events
        assert a.grid_button.isChecked() and not b.grid_button.isChecked()
        assert a.alpha.value() == 87 and b.alpha.value() == 160
        menu = a.grid_button.menu()
        menu.aboutToShow.emit()
        actions = [action for action in menu.actions() if action.isCheckable()]
        assert len(actions) == 6
        assert [action.data() for action in actions if action.isChecked()] == ['thirds']
        next(action for action in actions if action.data() == 'crosshair').trigger()
        assert len(events) == 1 and a.grid.currentData() == 'crosshair' and b.grid.currentData() == 'none'
        assert a.crop.menu().actions()[0].defaultWidget().isAncestorOf(a.alpha)
    finally:
        a.close(); b.close()


def test_zoom_menu_preserves_selection_and_tooltip():
    combo = QComboBox()
    combo.addItem('50%', 50)
    combo.addItem('100%', 100)
    button = zoom_menu(combo)
    try:
        assert '50%' in button.toolTip()
        combo.setCurrentIndex(1)
        assert '100%' in button.toolTip() and combo.currentData() == 100
        assert button.menu().actions()[0].defaultWidget() is combo
    finally:
        button.close()


def test_icons_render_at_high_dpi_and_empty_requests_are_safe():
    button = ToggleToolButton()
    for kind in ('default', 'original', 'raw', 'denoised', 'result', 'focus', 'center', 'fit', 'bird', 'crop', 'grid', 'zoom', 'compare', 'link'):
        iconize(button, kind, kind)
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        for state in (QIcon.State.Off, QIcon.State.On):
            image = button.icon().pixmap(QSize(48, 48), QIcon.Mode.Normal, state).toImage()
            assert not image.isNull()
            assert any(image.pixelColor(x, y).alpha() for x in range(48) for y in range(48))
        assert button.icon().pixmap(QSize(0, 0)).isNull()
    button.close()


def test_grid_menu_is_flat_and_width_input_slider_restore_stay_in_sync():
    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest
    a, b = ViewportOverlayTools(), ViewportOverlayTools()
    events = []
    a.changed.connect(lambda: events.append(a.state()))
    menu = a.grid_menu
    try:
        assert all(action.menu() is None for action in menu.actions())
        assert len(menu.mode_actions) == 6
        assert menu.group.isExclusive()
        menu.popup(QPoint(100, 100))
        _APP.processEvents()
        menu.width_slider.setValue(7)
        assert a.width.currentData() == 7 and menu.width_edit.text() == '7'
        assert b.width.currentData() == 1 and len(events) == 1
        menu.width_edit.setFocus()
        menu.width_edit.selectAll()
        QTest.keyClicks(menu.width_edit, '12')
        QTest.keyClick(menu.width_edit, Qt.Key.Key_Return)
        assert a.width.currentData() == 12 and menu.width_slider.value() == 12
        assert len(events) == 2
        menu.width_edit.setText('0')
        menu._commit_width()
        assert a.width.currentData() == 12 and menu.width_edit.text() == '12'
        menu.width_edit.clear()
        menu._commit_width()
        assert a.width.currentData() == 12 and len(events) == 2
        # 静默工作区恢复和菜单反复开合不会替换、删除滑动条和输入框。
        for _ in range(3):
            a.restore(dict(composition_grid_mode='square', composition_grid_line_width=32))
            menu.aboutToShow.emit()
            assert menu.width_slider.value() == 32 and menu.width_edit.text() == '32'
            assert len([action for action in menu.mode_actions if action.isChecked()]) == 1
        assert len(events) == 2
    finally:
        menu.hide()
        a.close(); b.close()
