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

__all__ = [
    "DirectoryBrowserWidget",
    "FileListPanel",
    "IMAGE_EXTENSIONS",
    "RAW_EXTENSIONS",
]
