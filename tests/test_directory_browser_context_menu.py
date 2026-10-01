"""目录菜单必须使用右键节点，不改变正在浏览的目录。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem

from app_common.file_browser import _directory_browser as directory_browser

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def browser(monkeypatch, tmp_path):
    monkeypatch.setattr(directory_browser.DirectoryBrowserWidget, "_populate_roots", lambda self: None)
    widget = directory_browser.DirectoryBrowserWidget()
    for name in ("当前目录", "右键目录"):
        path = tmp_path / name
        path.mkdir()
        item = QTreeWidgetItem([name])
        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        widget._tree.addTopLevelItem(item)
    widget._tree.setCurrentItem(widget._tree.topLevelItem(0))
    previous_text = _APP.clipboard().text()
    yield widget
    _APP.clipboard().setText(previous_text)
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def test_copy_full_path_uses_context_target_without_directory_selection(browser, monkeypatch):
    target = browser._tree.topLevelItem(1)
    monkeypatch.setattr(browser._tree, "itemAt", lambda pos: target)
    selected = []
    browser.directory_selected.connect(selected.append)
    actions = []
    def execute(menu, pos):
        actions.extend(action.text() for action in menu.actions())
        next(action for action in menu.actions() if action.text() == "复制完整路径").trigger()
    monkeypatch.setattr(directory_browser, "_exec_menu", execute)
    browser._on_dir_context_menu(QPoint(0, 0))
    assert _APP.clipboard().text() == os.path.abspath(target.data(0, Qt.ItemDataRole.UserRole))
    assert selected == []
    assert browser._tree.currentItem() is browser._tree.topLevelItem(0)
    assert "删除所有空目录" in actions
    assert any("显示" in text for text in actions)


@pytest.mark.parametrize("placeholder", [False, True])
def test_blank_or_placeholder_has_no_menu(browser, monkeypatch, placeholder):
    item = QTreeWidgetItem([browser._PLACEHOLDER]) if placeholder else None
    monkeypatch.setattr(browser._tree, "itemAt", lambda pos: item)
    monkeypatch.setattr(directory_browser, "_exec_menu", lambda *args: pytest.fail("no menu expected"))
    browser._on_dir_context_menu(QPoint(0, 0))
