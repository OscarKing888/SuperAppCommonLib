"""包顶层保留 UI 导出，但数据模块可用于没有 Qt 的命令行。"""
import subprocess
import sys


def test_data_imports_do_not_load_qt():
    script = """
import builtins, sys
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.startswith(('PyQt', 'PySide')):
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
from app_common.image_formats import RAW_IMAGE_EXTENSIONS
from app_common.exif_io import PhotoMetaDataXMP
assert '.arw' in RAW_IMAGE_EXTENSIONS
assert not any(name.startswith(('PyQt', 'PySide')) for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_legacy_ui_exports_resolve_to_original_objects():
    import app_common
    from app_common.about_dialog import load_about_images, load_about_info, show_about_dialog
    from app_common.app_info_bar import AppInfoBar
    from app_common.preview_canvas import PreviewCanvas, PreviewWithStatusBar
    from app_common.triangle_toggle_splitter import TriangleToggleSplitter, TriangleToggleSplitterHandle

    expected = {name: value for name, value in locals().copy().items() if name != "app_common"}
    for name, value in expected.items():
        assert getattr(app_common, name) is value
        assert name in app_common.__all__ and name in dir(app_common)
