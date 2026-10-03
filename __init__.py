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
    get_focus_point_for_display,
    resolve_focus_camera_type,
    resolve_focus_camera_type_from_metadata,
    resolve_focus_display_orientation,
)

__all__ = [
    "CameraFocusType",
    "resolve_focus_camera_type",
    "resolve_focus_camera_type_from_metadata",
    "resolve_focus_display_orientation",
    "get_focus_point",
    "get_focus_point_for_display",
    "extract_focus_box",
]

# 顶层导出继续兼容旧调用；只读元数据、图像格式和 CLI 不应因导入包而加载 Qt。
_LAZY_UI_EXPORTS = {
    "show_about_dialog": "app_common.about_dialog",
    "load_about_info": "app_common.about_dialog",
    "load_about_images": "app_common.about_dialog",
    "AppInfoBar": "app_common.app_info_bar",
    "PreviewCanvas": "app_common.preview_canvas",
    "PreviewWithStatusBar": "app_common.preview_canvas",
    "TriangleToggleSplitter": "app_common.triangle_toggle_splitter",
    "TriangleToggleSplitterHandle": "app_common.triangle_toggle_splitter",
}
__all__.extend(_LAZY_UI_EXPORTS)


def __getattr__(name):
    module_name = _LAZY_UI_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_UI_EXPORTS))
