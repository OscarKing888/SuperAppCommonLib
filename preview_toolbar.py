"""两款应用共用的视口图标与菜单；只改变控件呈现，不触发图像加载。"""
from __future__ import annotations

try:
    from PyQt6.QtCore import QLineF, QRectF, QSize, Qt, pyqtSignal
    from PyQt6.QtGui import QActionGroup, QIcon, QIconEngine, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMenu, QSlider, QToolButton, QWidget, QWidgetAction
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QLineF, QRectF, QSize, Qt, pyqtSignal
    from PyQt5.QtGui import QIcon, QIconEngine, QPainter, QPen, QPixmap
    from PyQt5.QtWidgets import QActionGroup, QComboBox, QHBoxLayout, QLabel, QMenu, QSlider, QToolButton, QWidget, QWidgetAction

from .toggle_button import ToggleToolButton
from .preview_canvas import PREVIEW_COMPOSITION_GRID_MODES, PREVIEW_COMPOSITION_GRID_LINE_WIDTHS

GRID_ITEMS = (('none', '不显示'), ('thirds', '均分九宫格'), ('golden_thirds', '黄金分割九宫格'),
              ('square', '方格网格'), ('diag_square', '对角线 + 方格'), ('crosshair', '中心十字线'))


class _PreviewIcon(QIconEngine):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def clone(self):
        return _PreviewIcon(self.kind)

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        pen = QPen(Qt.GlobalColor.black)
        pen.setWidthF(1.65)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        def line(x1, y1, x2, y2):
            painter.drawLine(QLineF(x1, y1, x2, y2))
        def box(x, y, w, h):
            painter.drawRect(QRectF(x, y, w, h))
        kind = self.kind
        if kind in ('default', 'original', 'raw', 'denoised', 'result'):
            box(3, 4, 18, 16)
            if kind == 'default':
                painter.drawEllipse(QRectF(8, 7, 9, 10))
                line(7, 2, 13, 2)
            elif kind == 'raw':
                for x in (7, 12, 17):
                    for y in (8, 12, 16):
                        box(x - .5, y - .5, 1, 1)
            elif kind == 'denoised':
                line(12, 7, 12, 17); line(7, 12, 17, 12)
                line(9, 9, 15, 15); line(9, 15, 15, 9)
            else:
                line(4, 17, 9, 11); line(9, 11, 13, 15); line(13, 15, 17, 10); line(17, 10, 20, 15)
                if kind == 'result':
                    line(7, 2, 17, 2); line(7, 22, 17, 22)
        elif kind in ('focus', 'center', 'fit'):
            for x, y, dx, dy in ((3, 3, 1, 1), (21, 3, -1, 1), (3, 21, 1, -1), (21, 21, -1, -1)):
                line(x, y, x + dx * 5, y); line(x, y, x, y + dy * 5)
            if kind == 'focus':
                box(9, 9, 6, 6)
            elif kind == 'center':
                line(7, 12, 17, 12); line(12, 7, 12, 17)
                painter.drawEllipse(QRectF(9, 9, 6, 6))
        elif kind == 'bird':
            # 鸟身、头、喙与尾翼，避免与普通矩形选区混淆。
            painter.drawEllipse(QRectF(7, 9, 10, 9)); painter.drawEllipse(QRectF(14, 5, 5, 5))
            line(19, 7, 22, 8); line(7, 12, 3, 10); line(3, 10, 7, 17)
            line(11, 18, 10, 21); line(15, 18, 15, 21)
        elif kind == 'crop':
            line(6, 2, 6, 18); line(6, 18, 22, 18)
            line(2, 6, 18, 6); line(18, 6, 18, 22)
        elif kind == 'grid':
            box(3, 3, 18, 18)
            for n in (9, 15):
                line(n, 3, n, 21); line(3, n, 21, n)
        elif kind == 'zoom':
            painter.drawEllipse(QRectF(3, 3, 13, 13)); line(15, 15, 21, 21)
            line(6, 9.5, 13, 9.5); line(9.5, 6, 9.5, 13)
        elif kind == 'compare':
            box(3, 4, 18, 16); line(12, 4, 12, 20)
        elif kind == 'link':
            painter.drawRoundedRect(QRectF(2, 8, 12, 8), 4, 4)
            painter.drawRoundedRect(QRectF(10, 8, 12, 8), 4, 4)
        painter.restore()

    def pixmap(self, size, mode, state):
        if size.isEmpty():
            return QPixmap()
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


def iconize(button, kind, label=None):
    """保留按钮文字供辅助功能/旧调用者读取，工具栏仅绘制图标。"""
    if label:
        button.setText(label)
    button.setAccessibleName(label or button.text())
    if not button.toolTip():
        button.setToolTip(label or button.text())
    button.setIcon(QIcon(_PreviewIcon(kind)))
    button.setIconSize(QSize(18, 18))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    return button


def menu_button(kind, label, parent=None):
    button = iconize(ToggleToolButton(label, parent), kind, label)
    button.setCheckable(False)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setMenu(QMenu(button))
    return button


def combo_radio_menu(menu, combo):
    """菜单每次打开读取原控件，恢复工作区时不额外发业务信号。"""
    def populate():
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)
        old = getattr(menu, '_radio_group', None)
        if old is not None:
            old.deleteLater()
        menu._radio_group = group
        for index in range(combo.count()):
            action = menu.addAction(combo.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == combo.currentIndex())
            action.setData(combo.itemData(index))
            action.setEnabled(combo.isEnabled())
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, index=index: combo.setCurrentIndex(index))
    menu.aboutToShow.connect(populate)
    populate()


def zoom_menu(combo):
    button = menu_button('zoom', '缩放比例')
    combo.setAccessibleName('缩放比例')
    combo.setParent(button.menu())
    action = QWidgetAction(button.menu())
    action.setDefaultWidget(combo)
    button.menu().addAction(action)
    def sync(*_):
        button.setToolTip('缩放比例：' + combo.currentText())
    combo.currentTextChanged.connect(sync)
    combo.activated.connect(lambda _index: button.menu().close())
    sync()
    return button


class ViewportOverlayTools(QWidget):
    """每个视口持有独立的显示选项，允许 B 侧接管原有工作区控件。"""
    changed = pyqtSignal()

    def __init__(self, *, focus=None, bird=None, grid=None, width=None,
                 crop=None, alpha=None, with_crop=False, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.focus = focus if focus is not None else ToggleToolButton('显示对焦点')
        self.bird = bird if bird is not None else ToggleToolButton('显示鸟体')
        if focus is None:
            self.focus.setChecked(True)
        self.grid = grid if grid is not None else QComboBox(self)
        if grid is None:
            for value, label in GRID_ITEMS:
                if value in PREVIEW_COMPOSITION_GRID_MODES:
                    self.grid.addItem(label, value)
        self.width = width if width is not None else QComboBox(self)
        if width is None:
            for value in PREVIEW_COMPOSITION_GRID_LINE_WIDTHS:
                self.width.addItem(f'{value} px', value)
        for combo in (self.grid, self.width):
            combo.setParent(self)
            combo.hide()
            combo.currentIndexChanged.connect(self.changed)
        self.crop = self.alpha = None
        if with_crop:
            self.crop = crop if crop is not None else ToggleToolButton('显示裁切效果')
            if crop is None:
                self.crop.setChecked(True)
            iconize(self.crop, 'crop', '显示裁切效果')
            self.alpha = alpha if alpha is not None else QSlider(Qt.Orientation.Horizontal)
            if alpha is None:
                self.alpha.setRange(0, 255)
                self.alpha.setValue(160)
            self.alpha.setAccessibleName("裁切遮罩透明度")
            menu = QMenu(self.crop)
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.addWidget(QLabel('遮罩透明度'))
            layout.addWidget(self.alpha)
            value_label = QLabel(str(self.alpha.value()))
            self.alpha.valueChanged.connect(lambda value: value_label.setText(str(value)))
            layout.addWidget(value_label)
            action = QWidgetAction(menu)
            action.setDefaultWidget(container)
            menu.addAction(action)
            self.crop.setMenu(menu)
            self.crop.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            self.crop.setStyleSheet(self.crop.styleSheet() + 'QToolButton { padding-right: 16px; } QToolButton::menu-button { width: 12px; }')
            self.crop.toggled.connect(self.changed)
            self.alpha.valueChanged.connect(self.changed)
            row.addWidget(self.crop)
        for button, kind in ((self.focus, 'focus'), (self.bird, 'bird')):
            iconize(button, kind)
            row.addWidget(button)
            button.toggled.connect(self.changed)
        self.grid_button = menu_button('grid', '构图线与线宽')
        self.grid_button.setCheckable(True)
        combo_radio_menu(self.grid_button.menu().addMenu('构图线'), self.grid)
        combo_radio_menu(self.grid_button.menu().addMenu('线宽'), self.width)
        row.addWidget(self.grid_button)
        self.changed.connect(self.sync)
        self.sync()

    def sync(self):
        self.grid_button.setChecked(self.grid.currentData() != "none")
        label = self.grid.currentText().removeprefix('构图线：')
        self.grid_button.setToolTip(f'构图线：{label}；线宽：{self.width.currentText()}')

    def state(self):
        result = dict(show_focus_box=self.focus.isChecked(), show_bird_box=self.bird.isChecked(),
                      composition_grid_mode=self.grid.currentData(), composition_grid_line_width=self.width.currentData())
        if self.crop is not None:
            result.update(show_crop_effect=self.crop.isChecked(), crop_effect_alpha=self.alpha.value())
        return result

    def restore(self, state):
        if not isinstance(state, dict):
            return
        widgets = (self.focus, self.bird, self.grid, self.width, self.crop, self.alpha)
        blocked = [(widget, widget.blockSignals(True)) for widget in widgets if widget is not None]
        try:
            for key, widget in (('show_focus_box', self.focus), ('show_bird_box', self.bird), ('show_crop_effect', self.crop)):
                if widget is not None and key in state:
                    widget.setChecked(bool(state[key]))
            for key, widget in (('composition_grid_mode', self.grid), ('composition_grid_line_width', self.width)):
                index = widget.findData(state.get(key))
                if index >= 0:
                    widget.setCurrentIndex(index)
            if self.alpha is not None and 'crop_effect_alpha' in state:
                try:
                    self.alpha.setValue(int(state['crop_effect_alpha']))
                except (ValueError, TypeError):
                    pass
        finally:
            for widget, previous in blocked:
                widget.blockSignals(previous)
        self.sync()
