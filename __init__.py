# -*- coding: utf-8 -*-
"""
app_common：关于对话框、App 信息条等通用 UI 子库。可整体作为 Sub Git 库使用。

用法:
    from app_common.about_dialog import show_about_dialog, load_about_info
    from app_common.app_info_bar import AppInfoBar
"""

from app_common.focus_calc import (
    CameraFocusType,
    extract_focus_box,
    get_focus_point,
    resolve_focus_camera_type,
    resolve_focus_camera_type_from_metadata,
)

__all__ = [
    "CameraFocusType",
    "resolve_focus_camera_type",
    "resolve_focus_camera_type_from_metadata",
    "get_focus_point",
    "extract_focus_box",
]

try:
    from app_common.about_dialog import load_about_info, show_about_dialog
    __all__.extend(["show_about_dialog", "load_about_info"])
except ModuleNotFoundError as exc:
    if not str(getattr(exc, "name", "")).startswith("PyQt"):
        raise

try:
    from app_common.app_info_bar import AppInfoBar
    __all__.append("AppInfoBar")
except ModuleNotFoundError as exc:
    if not str(getattr(exc, "name", "")).startswith("PyQt"):
        raise

try:
    from app_common.preview_canvas import PreviewCanvas, PreviewWithStatusBar
    __all__.extend(["PreviewCanvas", "PreviewWithStatusBar"])
except ModuleNotFoundError as exc:
    if not str(getattr(exc, "name", "")).startswith("PyQt"):
        raise
