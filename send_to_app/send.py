# -*- coding: utf-8 -*-
"""
发送到外部应用：核心逻辑，不依赖 Qt。
支持一次发送单个或多个文件（全路径），通过命令行参数传给目标应用。
跨平台：Windows（QProcess.startDetached）、macOS（open -a）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

# Windows 下用 Qt 的 startDetached，与项目其它处行为一致，且正确传递带空格的路径
if sys.platform == "win32":
    try:
        from PySide6.QtCore import QProcess
    except ImportError:
        try:
            from PyQt6.QtCore import QProcess
        except ImportError:
            from PyQt5.QtCore import QProcess
    _QProcess = QProcess
else:
    _QProcess = None


def resolve_app_path(app_path: str) -> str:
    """
    将配置中的 app 路径规范化为可执行形式。
    - macOS: 支持 .app 或 Adobe 风格目录，返回可供 open -a 使用的路径或名称。
    - Windows: 返回可执行路径。
    """
    if not app_path:
        return ""
    if sys.platform == "darwin":
        if app_path.endswith(".app"):
            return app_path
        candidate = app_path + ".app"
        if os.path.isdir(candidate):
            return candidate
        if os.path.isdir(app_path):
            folder_name = os.path.basename(app_path)
            inner = os.path.join(app_path, folder_name + ".app")
            if os.path.isdir(inner):
                return inner
            try:
                apps_inside = [x for x in os.listdir(app_path) if x.endswith(".app")]
                if apps_inside:
                    return os.path.join(app_path, apps_inside[0])
            except OSError:
                pass
        return os.path.splitext(os.path.basename(app_path))[0]
    return app_path


def send_files_to_app(
    file_paths: list[str],
    app: dict[str, Any],
    base_directory: str = "",
) -> None:
    """
    用指定外部应用打开一组文件（全路径列表）。

    Args:
        file_paths: 文件路径列表，建议使用绝对路径。
        app: 应用项，至少含 "path"（"name" 仅用于显示）。
        base_directory: 当某项 file_paths 为相对路径时，用于拼接为绝对路径。
    """
    if not app:
        return
    path = app.get("path") or ""
    if not path:
        return
    resolved: list[str] = []
    for fp in file_paths or []:
        if not fp:
            continue
        if not os.path.isabs(fp) and base_directory:
            fp = os.path.normpath(os.path.join(base_directory, fp))
        resolved.append(fp)
    if not resolved:
        return

    if sys.platform == "darwin":
        ap = resolve_app_path(path)
        # open -a App 可接受多个文件
        subprocess.Popen(["open", "-a", ap] + resolved)
    elif sys.platform == "win32" and _QProcess is not None:
        _QProcess.startDetached(path, resolved)
    else:
        subprocess.Popen([path] + resolved)
