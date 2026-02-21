# -*- coding: utf-8 -*-
"""
关于对话框模块。配置见 about.cfg；可独立为 Sub Git 库使用。

用法:
    from app_common.about_dialog import show_about_dialog, load_about_info
    info = load_about_info(override_path=None)  # 可选 override_path 覆盖默认
    show_about_dialog(parent, info, logo_path="...", banner_path="...")
"""

from .dialog import show_about_dialog
from .config import load_about_info

__all__ = ["show_about_dialog", "load_about_info"]
