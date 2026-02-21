# -*- coding: utf-8 -*-
"""
App 信息条模块：图标 + 主副标题 + “关于...” 按钮。可独立为 Sub Git 库使用。

用法:
    from app_common.app_info_bar import AppInfoBar
    bar = AppInfoBar(parent, title="MyApp", subtitle="...", icon_path="...", on_about_clicked=callback)
"""

from .widget import AppInfoBar

__all__ = ["AppInfoBar"]
