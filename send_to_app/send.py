# -*- coding: utf-8 -*-
"""
发送到外部应用：核心逻辑。
支持一次发送单个或多个文件（全路径）：
- 若配置了 app_id，则优先按本地 socket 协议热发送给已运行实例；
- 否则或热发送失败时，回退为命令行启动目标应用。
跨平台：Windows（QProcess.startDetached）、macOS（open -a）。
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
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

_SEND_PROGRESS_DIALOG_THRESHOLD = 10


def _resolve_socket_app_id(app: dict[str, Any]) -> str:
    """返回外部应用声明的热接收 app_id；为空表示仅使用启动回退。"""
    for key in ("app_id", "send_to_app_id"):
        value = str(app.get(key) or "").strip()
        if value:
            return value
    return ""


def _try_send_via_socket(
    app: dict[str, Any],
    file_paths: list[str],
    *,
    progress_callback: Any = None,
    cancel_check: Any = None,
) -> bool:
    """若外部应用声明了 app_id，则尝试按项目内 send_to_app 协议热发送。"""
    app_id = _resolve_socket_app_id(app)
    if not app_id:
        return False
    try:
        from .receive import send_file_list_to_running_app
    except Exception:
        return False
    try:
        return send_file_list_to_running_app(
            app_id,
            file_paths,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except Exception:
        return False


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


def _normalize_send_paths(file_paths: list[str], base_directory: str = "") -> list[str]:
    resolved: list[str] = []
    for fp in file_paths or []:
        if not fp:
            continue
        if not os.path.isabs(fp) and base_directory:
            fp = os.path.normpath(os.path.join(base_directory, fp))
        resolved.append(fp)
    return resolved


def _launch_app_with_files(path: str, resolved: list[str]) -> None:
    if sys.platform == "darwin":
        ap = resolve_app_path(path)
        # open -a App 可接受多个文件
        subprocess.Popen(["open", "-a", ap] + resolved)
        return
    if sys.platform == "win32" and _QProcess is not None:
        _QProcess.startDetached(path, resolved)
        return
    subprocess.Popen([path] + resolved)


def _send_files_to_app_sync(
    resolved: list[str],
    app: dict[str, Any],
    *,
    progress_callback: Any = None,
    cancel_check: Any = None,
) -> tuple[bool, str]:
    path = str(app.get("path") or "").strip()
    app_name = str(app.get("name") or path or "外部应用").strip()
    total = len(resolved)
    if not path or not resolved:
        return (False, "未找到可用的发送目标。")

    if _try_send_via_socket(
        app,
        resolved,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    ):
        return (True, f"已热发送 {total} 个文件到 {app_name}。")

    if callable(cancel_check) and cancel_check():
        return (False, "发送已取消。")

    if callable(progress_callback):
        try:
            progress_callback(0, total, f"正在启动 {app_name} ...")
        except Exception:
            pass
    try:
        _launch_app_with_files(path, resolved)
    except Exception as exc:
        return (False, f"发送到 {app_name} 失败：{exc}")
    if callable(progress_callback):
        try:
            progress_callback(total, total, f"已启动 {app_name}，并传入 {total} 个文件。")
        except Exception:
            pass
    return (True, f"已启动 {app_name}，并传入 {total} 个文件。")


def _load_progress_dialog_support() -> tuple[Any, Any, Any, Any, Any] | None:
    try:
        from .receive import _load_qt_modules
    except Exception:
        return None
    try:
        QtCore, _, QtWidgets = _load_qt_modules(need_widgets=True)
    except Exception:
        return None
    if QtWidgets is None:
        return None
    QApplication = getattr(QtWidgets, "QApplication", None)
    QProgressDialog = getattr(QtWidgets, "QProgressDialog", None)
    QMessageBox = getattr(QtWidgets, "QMessageBox", None)
    QTimer = getattr(QtCore, "QTimer", None)
    Qt = getattr(QtCore, "Qt", None)
    if None in (QApplication, QProgressDialog, QMessageBox, QTimer, Qt):
        return None
    return (QApplication, QProgressDialog, QMessageBox, QTimer, Qt)


def _send_files_to_app_async_with_progress(
    resolved: list[str],
    app: dict[str, Any],
) -> bool:
    qt_support = _load_progress_dialog_support()
    if qt_support is None:
        return False
    QApplication, QProgressDialog, QMessageBox, QTimer, Qt = qt_support
    q_app = QApplication.instance()
    if q_app is None:
        return False

    total = len(resolved)
    app_name = str(app.get("name") or app.get("path") or "外部应用").strip()
    parent = q_app.activeWindow()
    dialog = QProgressDialog(f"准备发送到 {app_name} ...", "取消", 0, max(1, total), parent)
    dialog.setWindowTitle("发送到外部应用")
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    try:
        dialog.setWindowModality(Qt.WindowModality.NonModal)
    except Exception:
        dialog.setWindowModality(Qt.NonModal)
    dialog.setValue(0)
    dialog.show()

    progress_queue = queue.Queue()
    cancel_event = threading.Event()

    def worker() -> None:
        try:
            success, message = _send_files_to_app_sync(
                resolved,
                app,
                progress_callback=lambda current, total_value, text: progress_queue.put(
                    ("progress", current, total_value, text)
                ),
                cancel_check=cancel_event.is_set,
            )
        except Exception as exc:
            progress_queue.put(("done", False, str(exc), None))
            return
        progress_queue.put(("done", success, message, None))

    thread = threading.Thread(
        target=worker,
        name="send_to_app_worker",
        daemon=True,
    )

    timer = QTimer(dialog)

    def close_success_dialog() -> None:
        if dialog.isVisible():
            dialog.close()

    def poll_progress() -> None:
        handled_done = False
        while True:
            try:
                kind, value1, value2, value3 = progress_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                current = max(0, int(value1))
                total_value = max(0, int(value2))
                text = str(value3 or "").strip() or f"热发送 {current}/{total_value}"
                dialog.setMaximum(max(1, total_value))
                dialog.setValue(min(current, max(1, total_value)))
                dialog.setLabelText(text)
                continue
            handled_done = True
            success = bool(value1)
            message = str(value2 or "").strip() or "发送失败。"
            timer.stop()
            if success:
                dialog.setValue(dialog.maximum())
                dialog.setLabelText(message)
                QTimer.singleShot(250, close_success_dialog)
            else:
                dialog.close()
                if cancel_event.is_set() and "取消" in message:
                    return
                QMessageBox.warning(parent or dialog, "发送失败", message)
            break
        if not handled_done and cancel_event.is_set() and dialog.isVisible():
            dialog.setLabelText("正在取消发送...")

    dialog.canceled.connect(cancel_event.set)
    timer.timeout.connect(poll_progress)
    timer.start(60)
    dialog._send_to_app_worker_thread = thread
    dialog._send_to_app_progress_queue = progress_queue
    dialog._send_to_app_progress_timer = timer
    thread.start()
    return True


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
    resolved = _normalize_send_paths(file_paths, base_directory=base_directory)
    if not resolved:
        return

    if len(resolved) > _SEND_PROGRESS_DIALOG_THRESHOLD and _send_files_to_app_async_with_progress(resolved, app):
        return

    _send_files_to_app_sync(resolved, app)
