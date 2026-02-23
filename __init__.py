# -*- coding: utf-8 -*-
"""
app_common：关于对话框、App 信息条等通用 UI 子库。可整体作为 Sub Git 库使用。

用法:
    from app_common.about_dialog import show_about_dialog, load_about_info
    from app_common.app_info_bar import AppInfoBar
"""

from app_common.about_dialog import load_about_info, show_about_dialog
from app_common.app_info_bar import AppInfoBar
from app_common.preview_canvas import PreviewCanvas, PreviewWithStatusBar

__all__ = ["show_about_dialog", "load_about_info", "AppInfoBar", "PreviewCanvas", "PreviewWithStatusBar"]
