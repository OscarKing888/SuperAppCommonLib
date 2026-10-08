"""原生 macOS 等样式下，侧边 Tab 的背景、图标和文字必须落在同一横向按钮中。"""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPalette, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyleFactory, QWidget

from app_common.sidebar_tabs import SidebarTabWidget

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('style', QStyleFactory.keys())
@pytest.mark.parametrize('dark', [False, True])
@pytest.mark.parametrize('scale', [1, 2])
def test_native_style_renders_horizontal_selected_tab_and_switches_pages(style, dark, scale):
    old_style, old_palette = _APP.style().objectName(), _APP.palette()
    _APP.setStyle(style)
    palette = QPalette(old_palette)
    for role, color in ((QPalette.ColorRole.Window, '#303030' if dark else '#eeeeee'),
                        (QPalette.ColorRole.WindowText, '#eeeeee' if dark else '#222222'),
                        (QPalette.ColorRole.Highlight, '#0078df'),
                        (QPalette.ColorRole.HighlightedText, '#ffffff')):
        palette.setColor(role, QColor(color))
    _APP.setPalette(palette)
    tabs = SidebarTabWidget()
    pixmap = QPixmap(20, 20)
    pixmap.fill(QColor('#e8b550'))
    first, second = QWidget(), QWidget()
    icon = QIcon(pixmap)
    icon.addPixmap(pixmap, QIcon.Mode.Selected, QIcon.State.On)
    tabs.addTab(first, icon, '安全区')
    tabs.resize(440, 260)
    tabs.show()
    _APP.processEvents()
    try:
        bar = tabs.tabBar()
        rect = bar.tabRect(0)
        rendered = QImage(bar.width()*scale, bar.height()*scale, QImage.Format.Format_ARGB32)
        rendered.setDevicePixelRatio(scale)
        rendered.fill(Qt.GlobalColor.transparent)
        bar.render(rendered)
        # 蓝色选中背景横跨按钮；原生 macOS 旧实现把背景转成竖条或不绘制文字。
        for x in (rect.left()+10, rect.right()-10):
            assert rendered.pixelColor(x*scale, (rect.center().y()-10)*scale).name() == '#0078df'
        assert any(rendered.pixelColor(x, y).name() == '#ffffff'
                   for x in range(44*scale, (rect.right()-10)*scale)
                   for y in range(10*scale, (rect.bottom()-8)*scale))
        assert any(rendered.pixelColor(x, y).name() == '#e8b550'
                   for x in range(14*scale, 34*scale)
                   for y in range(10*scale, 30*scale))
        tabs.addTab(second, '其他设置')
        _APP.processEvents()
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
        assert tabs.currentWidget() is second
        bar.setFocus()
        QTest.keyClick(bar, Qt.Key.Key_Left)
        assert tabs.currentWidget() is first
        tabs.setTabEnabled(1, False)
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
        assert tabs.currentWidget() is first
    finally:
        tabs.close()
        tabs.deleteLater()
        _APP.processEvents()
        _APP.setStyle(old_style)
        _APP.setPalette(old_palette)
