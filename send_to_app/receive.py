# -*- coding: utf-8 -*-
"""
接收「发送到本应用」的文件列表：
1) 冷启动：从命令行参数解析出文件列表；
2) 热启动：通过单例 IPC 接收其它进程发来的文件列表，并由调用方挂接回调处理。
跨平台：Windows（Named Pipe）、macOS（Unix domain socket），均通过 Qt QLocalServer/QLocalSocket。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable

# 协议：客户端发送一行 JSON：{"files": ["path1", "path2", ...]}，UTF-8
_PROTOCOL_ENCODING = "utf-8"


def get_initial_file_list_from_argv(argv: list[str] | None = None) -> list[str]:
    """
    从命令行参数中解析出「文件列表」。
    约定：第一个参数为程序名，其后若为可存在的文件/目录路径则加入列表，
    遇到以 - 开头的参数视为选项，停止解析（选项可由主程序自行处理）。
    用于冷启动时「用本应用打开」传入的文件。

    Returns:
        绝对路径列表（不存在的路径也会保留，由业务决定是否过滤）。
    """
    args = (argv or sys.argv)[1:]
    paths: list[str] = []
    for a in args:
        if a.startswith("-"):
            break
        p = os.path.abspath(os.path.expanduser(a.strip()))
        paths.append(p)
    return paths


def _server_name(app_id: str) -> str:
    """生成当前用户下唯一的 IPC 名称（QLocalServer：Windows Named Pipe / macOS Unix socket）。"""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (app_id or ""))
    if sys.platform == "win32":
        uid = os.environ.get("USERNAME", "default").strip() or "default"
    else:
        try:
            uid = str(os.getuid())
        except (AttributeError, OSError):
            uid = os.environ.get("USER", os.environ.get("USERNAME", "default"))
    name = f"superexif_sendto_{safe}_{uid}"
    if sys.platform == "win32":
        # Windows Named Pipe 名称长度上限 256，且仅允许部分字符
        name = name[:200].replace("\\", "_")
    return name


def _send_via_socket(server_name: str, file_paths: list[str]) -> bool:
    """作为客户端连接已有实例，发送 file_paths 后返回。成功返回 True。"""
    try:
        from PySide6.QtNetwork import QLocalSocket
    except ImportError:
        try:
            from PyQt6.QtNetwork import QLocalSocket
        except ImportError:
            from PyQt5.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(3000):
        return False
    payload = json.dumps({"files": [os.path.normpath(p) for p in file_paths]}, ensure_ascii=False)
    sock.write(payload.encode(_PROTOCOL_ENCODING))
    sock.flush()
    sock.waitForBytesWritten(2000)
    sock.disconnectFromServer()
    unconnected = getattr(QLocalSocket.LocalSocketState, "UnconnectedState", None) or getattr(QLocalSocket, "UnconnectedState", 0)
    if sock.state() != unconnected:
        sock.abort()
    return True


class SingleInstanceReceiver:
    """
    单例接收端：仅在首进程内启动。
    当其它进程通过 send_file_list_to_running_app 发来文件列表时，触发 on_files_received(paths)。
    """

    def __init__(self, app_id: str, on_files_received: Callable[[list[str]], None]):
        self._app_id = app_id
        self._on_files = on_files_received
        self._server = None
        self._name = _server_name(app_id)

    def start(self) -> bool:
        """创建并监听本地 socket。若已被占用则返回 False（表示本进程应为第二实例）。"""
        try:
            from PySide6.QtNetwork import QLocalServer
            from PySide6.QtCore import QByteArray
        except ImportError:
            try:
                from PyQt6.QtNetwork import QLocalServer
                from PyQt6.QtCore import QByteArray
            except ImportError:
                from PyQt5.QtNetwork import QLocalServer
                from PyQt5.QtCore import QByteArray
        self._server = QLocalServer()
        if not self._server.listen(self._name):
            return False
        self._server.newConnection.connect(self._on_connection)
        return True

    def _on_connection(self) -> None:
        if not self._server:
            return
        conn = self._server.nextPendingConnection()
        if not conn:
            return
        done = []

        def read_and_callback() -> None:
            if done:
                return
            try:
                data = conn.readAll().data()
                if data:
                    obj = json.loads(data.decode(_PROTOCOL_ENCODING))
                    paths = obj.get("files")
                    if isinstance(paths, list):
                        done.append(1)
                        self._on_files([os.path.normpath(str(p)) for p in paths])
            except Exception:
                pass
            finally:
                try:
                    conn.disconnectFromServer()
                    if conn.state() != getattr(conn, "UnconnectedState", 0):
                        conn.abort()
                except Exception:
                    pass
                conn.deleteLater()

        try:
            conn.readyRead.connect(read_and_callback)
            if conn.bytesAvailable() > 0:
                read_and_callback()
        except Exception:
            conn.deleteLater()

    def stop(self) -> None:
        if self._server:
            self._server.close()
            self._server = None


def send_file_list_to_running_app(app_id: str, file_paths: list[str]) -> bool:
    """
    将文件列表发给已在运行的同名应用实例（通过单例 IPC）。
    若成功发送则返回 True，调用方应随后退出（由已运行实例处理）；
    若返回 False 表示没有已运行实例，可正常启动新进程。
    """
    if not file_paths:
        return False
    return _send_via_socket(_server_name(app_id), file_paths)
