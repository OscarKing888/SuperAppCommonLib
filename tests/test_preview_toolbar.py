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
        menu = a.grid_button.menu().actions()[0].menu()
        menu.aboutToShow.emit()
        actions = menu.actions()
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
