from __future__ import annotations

import time

from PIL import Image
from PyQt6.QtWidgets import QApplication

from app_common.about_dialog.dialog import AboutDialog

_APP = QApplication.instance() or QApplication([])


def _settle(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
    raise AssertionError('About layout did not settle')


def test_many_qr_cards_reflow_and_remain_accessible(tmp_path):
    path = tmp_path / '中文二维码.png'
    Image.new('RGB', (400, 200), 'white').save(path)
    images = [{'path': str(path), 'label': '扫码下载与说明' * 8, 'size': 256, 'url': ''} for _ in range(7)]
    dialog = AboutDialog(None, {'app_name': '测试应用', 'version': '1.2.3'}, images=images)
    try:
        dialog.show()
        dialog.resize(360, 300)
        _settle(lambda: dialog.grid.getItemPosition(6)[0] == 6 and dialog.scroll.verticalScrollBar().maximum() > 0)
        body = dialog.scroll.widget()
        assert dialog.width() == 360
        for card in dialog.cards:
            assert card.geometry().right() <= body.width()
            assert card.image_label.width() == 2 * card.image_label.height()
        bar = dialog.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        _APP.processEvents()
        last = dialog.cards[-1]
        assert last.mapTo(dialog.scroll.viewport(), last.rect().bottomRight()).y() <= dialog.scroll.viewport().height()
        dialog.resize(700, 650)
        _settle(lambda: dialog.grid.getItemPosition(6)[0] == 3)
    finally:
        dialog.close()


def test_text_only_dialog_is_content_sized():
    dialog = AboutDialog(None, {'app_name': '测试', 'version': '1'})
    try:
        dialog.show()
        _APP.processEvents()
        assert dialog.width() < 720
        assert dialog.height() < 320
    finally:
        dialog.close()


def test_two_cards_fit_without_unnecessary_scroll_and_links_use_left_click(tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from app_common.about_dialog import dialog as module

    path = tmp_path / 'qr.png'
    Image.new('RGB', (256, 256), 'white').save(path)
    images = [{'path': str(path), 'size': 256, 'label': '扫码下载', 'url': 'https://example.test'}] * 2
    window = AboutDialog(None, {'app_name': '应用', 'version': '1.0.0'}, images=images)
    opened = []
    monkeypatch.setattr(module.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()))
    try:
        window.show()
        _settle(lambda: not window._initial_fit and not window._relayout_timer.isActive())
        assert window.scroll.verticalScrollBar().maximum() == 0
        QTest.mouseClick(window.cards[0].image_label, Qt.MouseButton.RightButton)
        assert opened == []
        QTest.mouseClick(window.cards[0].image_label, Qt.MouseButton.LeftButton)
        assert opened == ['https://example.test']
    finally:
        window.close()
