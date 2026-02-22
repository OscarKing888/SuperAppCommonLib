# -*- coding: utf-8 -*-
"""
关于对话框模块。配置见 about.cfg；可独立为 Sub Git 库使用。

用法:
    from app_common.about_dialog import show_about_dialog, load_about_info, load_about_images
    info = load_about_info(app_name="MyApp", version="1.0.0")
    images = load_about_images()  # 读取 about.cfg 中的 images 列表
    show_about_dialog(parent, info, logo_path="...", images=images)
"""

from .dialog import show_about_dialog
from .config import load_about_info, load_about_images

__all__ = ["show_about_dialog", "load_about_info", "load_about_images"]
