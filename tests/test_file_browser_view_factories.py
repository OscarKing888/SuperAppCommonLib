"""宿主可扩展视图，默认浏览器仍不启用拖放。"""
from PyQt6.QtWidgets import QApplication, QListView, QTreeWidget

from app_common.file_browser._directory_browser import DirectoryBrowserWidget
from app_common.file_browser._models import FileTableView
from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


def test_file_panel_uses_host_view_factories():
    class Table(FileTableView):
        pass
    class Thumbnails(QListView):
        pass
    class Panel(FileListPanel):
        file_table_view_class = Table
        thumbnail_view_class = Thumbnails

    panel = Panel()
    try:
        assert isinstance(panel._tree_widget, Table)
        assert isinstance(panel._list_widget, Thumbnails)
        assert not panel._tree_widget.dragEnabled()
        assert not panel._list_widget.dragEnabled()
        assert panel._tree_widget.model() is panel._file_table_proxy
        assert panel._list_widget.model() is panel._thumb_list_model
    finally:
        panel.close()
        panel.deleteLater()
        _APP.processEvents()


def test_directory_browser_uses_host_tree_factory():
    class Tree(QTreeWidget):
        pass
    class Browser(DirectoryBrowserWidget):
        directory_tree_class = Tree
        def _populate_roots(self):
            pass

    browser = Browser()
    try:
        assert isinstance(browser._tree, Tree)
        assert not browser._tree.acceptDrops()
    finally:
        browser.close()
        browser.deleteLater()
        _APP.processEvents()
