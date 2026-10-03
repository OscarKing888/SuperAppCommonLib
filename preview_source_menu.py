"""为预览来源循环按钮附加互斥菜单；状态与加载策略仍由各视口持有。"""
try:
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtGui import QActionGroup
    from PyQt6.QtWidgets import QMenu, QToolButton
except ImportError:  # pragma: no cover - PyQt5 compatibility
    from PyQt5.QtCore import pyqtSignal
    from PyQt5.QtWidgets import QActionGroup, QMenu, QToolButton


class PreviewSourceMenu(QMenu):
    mode_selected = pyqtSignal(str)

    def __init__(self, button):
        super().__init__(button)
        self.group = QActionGroup(self)
        self.group.setExclusive(True)
        self.mode_actions = {}
        for mode, label in (('default', '默认预览'), ('raw', '显示 RAW'), ('denoised', '显示降噪')):
            action = self.addAction(label)
            action.setCheckable(True)
            action.setData(mode)
            self.group.addAction(action)
            self.mode_actions[mode] = action
        self.group.triggered.connect(lambda action: self.mode_selected.emit(action.data()))
        button.setMenu(self)
        button.setPopupMode(getattr(QToolButton, 'ToolButtonPopupMode', QToolButton).MenuButtonPopup)
        # 分离主按钮与箭头的点击区域；沿用共享 Toggle 的主题及选中态。
        button.setStyleSheet(button.styleSheet() + '''
QToolButton { padding-right: 20px; }
QToolButton::menu-button { width: 16px; border-left: 1px solid palette(mid); }
''')
        self.sync('default', raw_available=False)

    def sync(self, mode, *, raw_available):
        effective = 'default' if mode == 'raw' and not raw_available else mode
        self.mode_actions['raw'].setEnabled(raw_available)
        self.mode_actions.get(effective, self.mode_actions['default']).setChecked(True)
