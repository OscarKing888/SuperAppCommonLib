"""折叠仅影响显示；独立宿主也具备一致主题、键盘和几何行为。"""
import time

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFormLayout, QLabel, QLineEdit,
    QSizePolicy, QStyleFactory, QVBoxLayout, QWidget,
)

from app_common.collapsible_section import CollapsibleSection

_APP = QApplication.instance() or QApplication([])


def settle_until(predicate):
    deadline = time.monotonic() + 2
    while True:
        _APP.processEvents()
        if predicate():
            return
        assert time.monotonic() < deadline, '折叠组布局未稳定'


def test_keyboard_toggle_keeps_input_enabled_state_and_signal_count():
    section = CollapsibleSection('中文参数')
    form = QFormLayout(section.body)
    edit = QLineEdit('保留中文输入')
    enabled = QCheckBox('启用处理')
    enabled.setChecked(True)
    disabled = QLineEdit('不可编辑')
    disabled.setEnabled(False)
    form.addRow('名称', edit)
    form.addRow(enabled)
    form.addRow(disabled)
    signals = []
    changed = []
    section.toggled.connect(signals.append)
    edit.textChanged.connect(changed.append)
    enabled.toggled.connect(changed.append)
    section.show()
    try:
        section.header_button.setFocus()
        QTest.keyClick(section.header_button, Qt.Key.Key_Space)
        assert not section.is_expanded() and not edit.isVisible()
        section.set_expanded(False)
        section.header_button.click()
        settle_until(edit.isVisible)
        assert signals == [False, True]
        assert edit.text() == '保留中文输入'
        assert enabled.isChecked() and enabled.isEnabled()
        assert not disabled.isEnabled()
        assert not changed
    finally:
        section.close()
        section.deleteLater()


def test_nested_stretched_sections_release_height_and_restore_policy():
    root = QWidget()
    layout = QVBoxLayout(root)
    outer = CollapsibleSection('外层')
    inner_layout = QVBoxLayout(outer.body)
    inner = CollapsibleSection('内层')
    form = QVBoxLayout(inner.body)
    content = QLabel('高内容')
    content.setMinimumHeight(360)
    form.addWidget(content)
    inner_layout.addWidget(inner)
    outer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout.addWidget(outer, 1)
    layout.addStretch(1)
    root.resize(400, 700)
    root.show()
    try:
        settle_until(lambda: outer.height() > 360)
        expanded_height = outer.height()
        for _ in range(3):
            inner.set_expanded(False)
            outer.set_expanded(False)
            settle_until(lambda: outer.height() < 60)
            outer.set_expanded(True)
            assert not inner.is_expanded()
            inner.set_expanded(True)
            settle_until(lambda: outer.height() == expanded_height)
            assert outer.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
            assert outer.rect().contains(content.mapTo(outer, content.rect().bottomRight()))
    finally:
        root.close()
        root.deleteLater()


@pytest.mark.parametrize('style', QStyleFactory.keys())
@pytest.mark.parametrize('dark', [False, True])
@pytest.mark.parametrize('font_px', [13, 24])
def test_independent_group_title_body_share_background_and_fit_font(style, dark, font_px, tmp_path):
    old_style = _APP.style().objectName()
    _APP.setStyle(style)
    section = CollapsibleSection('统一分组')
    palette = QPalette(section.palette())
    bg = QColor('#242424' if dark else '#ffffff')
    fg = QColor('#eeeeee' if dark else '#202020')
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Window):
        palette.setColor(role, bg)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText, QPalette.ColorRole.Text):
        palette.setColor(role, fg)
    section.setPalette(palette)
    font = section.font()
    font.setPixelSize(font_px)
    section.setFont(font)
    layout = QVBoxLayout(section.body)
    label = QLabel('中文正文')
    layout.addWidget(label)
    section.resize(320, 140)
    section.show()
    try:
        settle_until(lambda: label.isVisible() and section.header_button.height() >= section.header_button.fontMetrics().height())
        QTest.mouseMove(label, label.rect().center())
        _APP.processEvents()
        # 离屏插件不保证发送鼠标离开事件；截图检查未悬停状态。
        section.header_button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        image = section.grab().toImage()
        x = section.width() - 30
        header_y = section.header_button.geometry().center().y()
        body_y = label.mapTo(section, label.rect().center()).y()
        colors = (image.pixelColor(x, header_y).name(), image.pixelColor(x, body_y).name())
        assert colors == (bg.name(), bg.name())
        assert section.rect().contains(label.mapTo(section, label.rect().bottomRight()))
        assert image.save(str(tmp_path/'section.png'))
    finally:
        section.close()
        section.deleteLater()
        _APP.setStyle(old_style)


def test_external_apps_can_collapse_and_cancel_without_saving(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QListWidget
    from app_common.send_to_app import settings_ui

    calls = []
    monkeypatch.setattr(settings_ui._config, 'load_config', lambda **kw: {
        'apps': [{'name': '中文应用', 'path': '/example/app'}],
    })
    monkeypatch.setattr(settings_ui._config, 'save_config', lambda *a, **kw: calls.append(kw))

    def interact(dialog):
        dialog.show()
        _APP.processEvents()
        group = dialog.findChild(CollapsibleSection)
        listing = group.findChild(QListWidget)
        listing.setCurrentRow(0)
        group.set_expanded(False)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons.isVisible()
        group.set_expanded(True)
        assert listing.currentRow() == 0
        assert '中文应用' in listing.item(0).text()
        buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, 'exec', interact)
    settings_ui.show_external_apps_settings_dialog(None, config_dir=str(tmp_path))
    assert not calls
    assert not list(tmp_path.iterdir())
