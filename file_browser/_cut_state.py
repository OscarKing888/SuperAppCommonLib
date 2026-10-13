# -*- coding: utf-8 -*-
"""剪贴板派生的待剪切显示状态；解析和绘制查询均不访问文件系统。"""
from __future__ import annotations

import json
import os

try:
    from PyQt6.QtCore import QObject, pyqtSignal
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, pyqtSignal


_FILE_CLIPBOARD_ACTION_MIME = "application/x-superbirdtools-file-action"
_FILE_CLIPBOARD_ENTRIES_MIME = "application/x-superbirdtools-file-entries"
CUT_LABEL = "待剪切"


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


def cut_paths_from_mime(mime) -> frozenset[str]:
    """只识别应用的内部剪切协议，普通 URL/文本复制不产生待剪切状态。"""
    if mime is None:
        return frozenset()
    try:
        action = bytes(mime.data(_FILE_CLIPBOARD_ACTION_MIME)).decode("utf-8").strip().lower()
        if action != "cut":
            return frozenset()
        entries = json.loads(bytes(mime.data(_FILE_CLIPBOARD_ENTRIES_MIME)).decode("utf-8"))
        if not isinstance(entries, list):
            return frozenset()
        paths = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source")
            if not isinstance(source, str) or not source:
                continue
            paths.add(_path_key(source))
            # 报告中的旧显示路径与实际操作源可能不同；不把同名 XMP 当作照片。
            display = entry.get("display_source")
            if isinstance(display, str) and display:
                paths.add(_path_key(display))
        return frozenset(paths)
    except (ValueError, TypeError, UnicodeError):
        return frozenset()


class FileCutState(QObject):
    changed = pyqtSignal()

    def __init__(self, clipboard, parent=None, *, resolve_cached_path=None):
        super().__init__(parent)
        self._clipboard = clipboard
        self._resolve_cached_path = resolve_cached_path
        self._paths = cut_paths_from_mime(clipboard.mimeData())
        clipboard.dataChanged.connect(self._refresh)

    def _refresh(self):
        paths = cut_paths_from_mime(self._clipboard.mimeData())
        if paths != self._paths:
            self._paths = paths
            self.changed.emit()

    def contains(self, path: str) -> bool:
        if not self._paths or not path:
            return False
        if _path_key(path) in self._paths:
            return True
        actual = self._resolve_cached_path(path) if self._resolve_cached_path else None
        return bool(actual and _path_key(actual) in self._paths)


def cut_tooltip(tooltip: str, pending: bool) -> str:
    if not pending:
        return tooltip
    line = "待剪切：粘贴后移动；复制其他内容或清空剪贴板可取消。"
    if "</body>" in tooltip:
        return tooltip.replace("</body>", f"<div>{line}</div></body>", 1)
    return f"{tooltip}\n{line}" if tooltip else line
