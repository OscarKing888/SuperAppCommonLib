from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMenu

from app_common.file_browser import _panel
from app_common.send_to_app import external_app_display_name

_APP = QApplication.instance() or QApplication([])


def test_display_name_drops_app_and_exe_suffix() -> None:
    assert external_app_display_name({"name": "Adobe Photoshop 2026.app", "path": "/x"}) == "Adobe Photoshop 2026"
    assert external_app_display_name({"name": "Viewer.EXE", "path": "C:/x/Viewer.exe"}) == "Viewer"
    assert external_app_display_name({"name": "DxO PureRAW 6", "path": "/x"}) == "DxO PureRAW 6"
    assert external_app_display_name({"name": "鸟类工具 v2.0", "path": "/x"}) == "鸟类工具 v2.0"
    assert external_app_display_name({"name": ".app", "path": "/x"}) == ".app"


def test_display_name_falls_back_to_path_file_name() -> None:
    assert external_app_display_name({"name": "", "path": "/Applications/Imaging Edge/Viewer.app/"}) == "Viewer"
    assert external_app_display_name({"name": " ", "path": "C:\\Program Files\\照片\\编辑器.exe"}) == "编辑器"
    assert external_app_display_name({"name": "", "path": ""}) == "未命名"
    assert external_app_display_name({}, "外部应用") == "外部应用"


def test_context_menu_send_actions_use_display_name(monkeypatch) -> None:
    apps = [
        {"name": "SuperBirdStamp.app", "path": "/x/SuperBirdStamp.app", "app_id": "birdstamp"},
        {"name": "", "path": "/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app"},
    ]
    monkeypatch.setattr(_panel, "get_external_apps", lambda: apps)
    panel = _panel.FileListPanel(create_filter_bar=False)
    menu = QMenu()
    try:
        panel._add_send_to_external_app_actions(menu, ["/photos/a.jpg"])
        assert [a.text() for a in menu.actions()] == ["发送到:SuperBirdStamp", "发送到:Adobe Photoshop 2026"]
    finally:
        menu.deleteLater()
        panel.close()
        _APP.processEvents()
