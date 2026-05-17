# -*- coding: utf-8 -*-
"""Directory browser widget implementation for app_common.file_browser."""
from __future__ import annotations

from app_common.file_browser._browser_core import *

class DirectoryBrowserWidget(QWidget):
    """
    本机目录树浏览器（QTreeWidget + 懒加载）。
    macOS：将 /Volumes 下检测到的外接卷作为独立 root 节点显示。
    Windows：显示各盘符。
    """

    directory_selected = pyqtSignal(str)
    _PLACEHOLDER = "__ph__"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar_widget = QWidget()
        toolbar_widget.setStyleSheet("background: #252525;")
        toolbar = QHBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(6, 4, 6, 2)
        toolbar.setSpacing(4)

        lbl = QLabel("目录")
        lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        toolbar.addWidget(lbl)
        toolbar.addStretch()

        self._btn_refresh_tree = QToolButton()
        self._btn_refresh_tree.setText("刷新")
        self._btn_refresh_tree.setToolTip("刷新文件夹树")
        self._btn_refresh_tree.setAutoRaise(True)
        self._btn_refresh_tree.setStyleSheet(
            "QToolButton { color: #aaa; padding: 2px 6px; border-radius: 3px; }"
            "QToolButton:hover { color: #fff; background: #333; }"
        )
        self._btn_refresh_tree.clicked.connect(self.refresh_directory_tree)
        toolbar.addWidget(self._btn_refresh_tree)
        layout.addWidget(toolbar_widget)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setAnimated(True)
        self._tree.setIndentation(14)
        self._tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; border: none; background: #2a2a2a; }"
            "QTreeWidget::item:selected { background: #3a5a8a; color: #fff; }"
            "QTreeWidget::item:hover { background: #333; }"
        )
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.itemClicked.connect(self._on_clicked)
        self._tree.setContextMenuPolicy(_CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_dir_context_menu)
        self._tree.installEventFilter(self)
        layout.addWidget(self._tree)

        self._populate_roots()

    def _populate_roots(self) -> None:
        """添加根节点：主目录 + macOS 外接卷 / Windows 盘符。"""
        home = os.path.expanduser("~")
        home_item = self._make_item(home, "🏠 " + os.path.basename(home))
        self._tree.addTopLevelItem(home_item)

        if sys.platform == "darwin":
            try:
                root_dev = os.stat("/").st_dev
            except OSError:
                root_dev = -1
            try:
                for entry in sorted(
                    os.scandir("/Volumes"), key=lambda e: e.name.lower()
                ):
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    try:
                        is_external = os.stat(entry.path).st_dev != root_dev
                    except OSError:
                        is_external = True
                    if is_external:
                        vol_item = self._make_item(entry.path, "💾 " + entry.name)
                        self._tree.addTopLevelItem(vol_item)
            except (PermissionError, OSError):
                pass
        elif os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    self._tree.addTopLevelItem(
                        self._make_item(drive, f"💾 {letter}:")
                    )

        self._tree.expandItem(home_item)

    def _make_item(self, path: str, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        item.setData(0, _UserRole, path)
        if os.path.isdir(path):
            item.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
        return item

    @staticmethod
    def _path_key(path: str) -> str:
        """路径归一化键（兼容 Windows 大小写不敏感文件系统）。"""
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _is_same_or_parent_path(self, parent: str, child: str) -> bool:
        """判断 parent 是否为 child 本身或祖先目录。"""
        try:
            parent_abs = os.path.normpath(os.path.abspath(parent))
            child_abs = os.path.normpath(os.path.abspath(child))
            if self._path_key(parent_abs) == self._path_key(child_abs):
                return True
            common = os.path.commonpath([parent_abs, child_abs])
            return self._path_key(common) == self._path_key(parent_abs)
        except Exception:
            return False

    def _find_best_root_item(self, target_path: str) -> QTreeWidgetItem | None:
        """从顶层 root 中找到最匹配 target_path 的节点（最长前缀）。"""
        best_item = None
        best_len = -1
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            root_path = item.data(0, _UserRole)
            if not root_path or not self._is_same_or_parent_path(root_path, target_path):
                continue
            n = len(os.path.normpath(os.path.abspath(root_path)))
            if n > best_len:
                best_item = item
                best_len = n
        return best_item

    def _ensure_children_loaded(self, item: QTreeWidgetItem) -> None:
        """若节点仍是占位符状态，则同步加载其子目录。"""
        if item.childCount() == 1 and item.child(0).text(0) == self._PLACEHOLDER:
            self._on_expanded(item)

    def _find_child_item_by_path(self, parent: QTreeWidgetItem, target_path: str) -> QTreeWidgetItem | None:
        """在 parent 的直接子节点中按真实路径匹配目标目录。"""
        target_key = self._path_key(target_path)
        for i in range(parent.childCount()):
            child = parent.child(i)
            path = child.data(0, _UserRole)
            if path and self._path_key(path) == target_key:
                return child
        return None

    def _find_directory_item(self, path: str, expand_chain: bool = False) -> QTreeWidgetItem | None:
        """按路径定位目录节点；expand_chain=True 时同步展开整条父链。"""
        if not path:
            return None
        try:
            target_path = os.path.normpath(os.path.abspath(path))
        except Exception:
            return None
        if not os.path.isdir(target_path):
            return None

        root_item = self._find_best_root_item(target_path)
        if root_item is None:
            return None

        root_path = root_item.data(0, _UserRole)
        if not root_path:
            return None
        root_path = os.path.normpath(os.path.abspath(root_path))

        chain: list[str] = [target_path]
        cur = target_path
        while self._path_key(cur) != self._path_key(root_path):
            parent = os.path.dirname(cur)
            if not parent or self._path_key(parent) == self._path_key(cur):
                return None
            chain.append(parent)
            cur = parent
        chain.reverse()  # root -> ... -> target

        current = root_item
        if expand_chain:
            self._tree.expandItem(current)
        for sub_path in chain[1:]:
            self._ensure_children_loaded(current)
            if expand_chain:
                self._tree.expandItem(current)
            nxt = self._find_child_item_by_path(current, sub_path)
            if nxt is None:
                return None
            current = nxt

        if expand_chain:
            self._tree.expandItem(current)
        return current

    def _collect_expanded_paths(self) -> list[str]:
        """记录当前已展开目录，供整棵树刷新后恢复展开状态。"""
        expanded_paths: list[str] = []
        seen: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            path = item.data(0, _UserRole)
            if path and item.isExpanded():
                norm_path = self._path_key(path)
                if norm_path not in seen:
                    seen.add(norm_path)
                    expanded_paths.append(path)
                for i in range(item.childCount()):
                    child = item.child(i)
                    if child.text(0) != self._PLACEHOLDER:
                        walk(child)

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))
        return expanded_paths

    def _find_existing_directory_path(self, path: str | None) -> str | None:
        """返回仍存在的目录；若原目录已不存在，则向上回退到最近祖先目录。"""
        if not path:
            return None
        try:
            current = os.path.normpath(os.path.abspath(path))
        except Exception:
            return None
        while True:
            if os.path.isdir(current):
                return current
            parent = os.path.dirname(current)
            if not parent or self._path_key(parent) == self._path_key(current):
                return None
            current = parent

    def refresh_directory_tree(self, emit_signal: bool = False) -> None:
        """重建目录树，并尽量恢复刷新前的展开状态与当前选中目录。"""
        current_item = self._tree.currentItem()
        current_path = current_item.data(0, _UserRole) if current_item is not None else None
        expanded_paths = self._collect_expanded_paths()

        self._tree.setUpdatesEnabled(False)
        try:
            self._tree.clear()
            self._populate_roots()
            for path in expanded_paths:
                self._find_directory_item(path, expand_chain=True)

            restore_path = self._find_existing_directory_path(current_path)
            if restore_path:
                self.select_directory(restore_path, emit_signal=emit_signal)
        finally:
            self._tree.setUpdatesEnabled(True)

    def select_directory(self, path: str, emit_signal: bool = True) -> bool:
        """
        按路径展开目录树并选中目标目录。
        返回是否成功定位到目标目录节点。
        """
        current = self._find_directory_item(path, expand_chain=True)
        if current is None:
            return False
        target_path = current.data(0, _UserRole)
        if not target_path:
            return False

        self._tree.setCurrentItem(current)
        self._tree.clearSelection()
        current.setSelected(True)
        try:
            self._tree.scrollToItem(current)
        except Exception:
            pass
        if emit_signal:
            self.directory_selected.emit(target_path)
        return True

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        """懒加载：展开时填充子目录。"""
        if item.childCount() > 0 and item.child(0).text(0) != self._PLACEHOLDER:
            return
        item.takeChildren()
        path = item.data(0, _UserRole)
        if not path:
            return
        try:
            for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                child = QTreeWidgetItem([entry.name])
                child.setData(0, _UserRole, entry.path)
                child.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
                item.addChild(child)
        except (PermissionError, OSError):
            pass

    def _on_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        path = item.data(0, _UserRole)
        if path and os.path.isdir(path):
            self.directory_selected.emit(path)

    def _refresh_dir_item_children(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, _UserRole)
        if not path or not os.path.isdir(path):
            return
        was_expanded = item.isExpanded()
        item.takeChildren()
        item.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
        if was_expanded:
            self._on_expanded(item)
            self._tree.expandItem(item)

    def _trash_empty_subdirectories(self, path: str, item: QTreeWidgetItem) -> None:
        moved_paths, failed_paths = move_empty_dirs_to_trash(path, include_root=False)
        self._refresh_dir_item_children(item)

        trash_name = "废纸篓" if sys.platform == "darwin" else "回收站"
        if failed_paths:
            QMessageBox.warning(
                self,
                "删除空目录",
                f"已移入{trash_name} {len(moved_paths)} 个空目录，另有 {len(failed_paths)} 个目录处理失败。",
            )
            return
        if moved_paths:
            QMessageBox.information(
                self,
                "删除空目录",
                f"已移入{trash_name} {len(moved_paths)} 个空目录。",
            )
            return
        QMessageBox.information(
            self,
            "删除空目录",
            "没有找到可删除的空目录。",
        )

    def eventFilter(self, obj, event) -> bool:
        if obj is not self._tree or event is None or event.type() != _EventKeyPress:
            return super().eventFilter(obj, event)
        key = event.key()
        item = self._tree.currentItem()
        if item is None:
            return super().eventFilter(obj, event)
        path = item.data(0, _UserRole)
        if not path or not os.path.isdir(path):
            return super().eventFilter(obj, event)
        target = None
        if key == _KeyUp:
            target = self._tree.itemAbove(item)
        elif key == _KeyDown:
            target = self._tree.itemBelow(item)
        elif key == _KeyLeft:
            target = item.parent()
        elif key == _KeyRight:
            if item.childCount() > 0:
                self._ensure_children_loaded(item)
                self._tree.expandItem(item)
                if item.childCount() > 0:
                    child = item.child(0)
                    if child.text(0) != self._PLACEHOLDER:
                        target = child
        if target is None:
            return super().eventFilter(obj, event)
        target_path = target.data(0, _UserRole)
        if not target_path or not os.path.isdir(target_path):
            return super().eventFilter(obj, event)
        self._tree.setCurrentItem(target)
        self._tree.clearSelection()
        target.setSelected(True)
        try:
            self._tree.scrollToItem(target)
        except Exception:
            pass
        self.directory_selected.emit(target_path)
        return True

    def _on_dir_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, _UserRole)
        if not path:
            return
        menu = QMenu(self)
        label = "在Finder中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        act = menu.addAction(label)
        act.triggered.connect(lambda: reveal_in_file_manager(path))
        menu.addSeparator()
        act_remove_empty = menu.addAction("删除所有空目录")
        act_remove_empty.triggered.connect(
            lambda checked=False, p=path, it=item: self._trash_empty_subdirectories(p, it)
        )
        _exec_menu(menu, self._tree.viewport().mapToGlobal(pos))
