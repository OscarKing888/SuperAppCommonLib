# -*- coding: utf-8 -*-
"""
app_common.file_browser
=======================
目录树浏览器与图像文件列表面板。

用法::

    from app_common.file_browser import DirectoryBrowserWidget, FileListPanel

对外暴露的公开符号：

- ``DirectoryBrowserWidget`` — 目录树（懒加载，macOS 外接卷自动识别）
- ``FileListPanel`` — 图像文件列表（列表/缩略图双模式，含元数据列）
- ``IMAGE_EXTENSIONS`` — 支持的图像扩展名元组
- ``RAW_EXTENSIONS`` — RAW 扩展名集合
"""
from __future__ import annotations

from app_common.file_browser._browser import (
    DirectoryBrowserWidget,
    FileListPanel,
    IMAGE_EXTENSIONS,
    RAW_EXTENSIONS,
)
from app_common.file_browser import _permissions as _write_permissions
from app_common.file_browser._permissions import (
    READONLY_LABEL_SUFFIX,
    clear_readonly_label,
    find_nearest_superpicky_root,
    mark_write_action_disabled,
    readonly_label,
    refresh_superpicky_root_write_permission,
    set_superpicky_root_write_permission_state,
    superpicky_root_writable,
    superpicky_root_write_disabled_tooltip,
    superpicky_root_write_state,
    superpicky_root_write_state_for_path,
)

__all__ = [
    "DirectoryBrowserWidget",
    "FileListPanel",
    "IMAGE_EXTENSIONS",
    "RAW_EXTENSIONS",
    "CURRENT_SUPERPICKY_ROOT_PATH",
    "CURRENT_SUPERPICKY_ROOT_WRITABLE",
    "CURRENT_SUPERPICKY_ROOT_WRITE_ERROR",
    "READONLY_LABEL_SUFFIX",
    "clear_readonly_label",
    "find_nearest_superpicky_root",
    "mark_write_action_disabled",
    "readonly_label",
    "refresh_superpicky_root_write_permission",
    "set_superpicky_root_write_permission_state",
    "superpicky_root_writable",
    "superpicky_root_write_disabled_tooltip",
    "superpicky_root_write_state",
    "superpicky_root_write_state_for_path",
]


def __getattr__(name: str):
    if name in {
        "CURRENT_SUPERPICKY_ROOT_PATH",
        "CURRENT_SUPERPICKY_ROOT_WRITABLE",
        "CURRENT_SUPERPICKY_ROOT_WRITE_ERROR",
    }:
        return getattr(_write_permissions, name)
    raise AttributeError(name)
