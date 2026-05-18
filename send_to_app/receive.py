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
import time
import inspect
from collections.abc import Iterable
from typing import Any, Callable

from app_common.log import get_logger

# 协议：客户端发送一行 JSON：{"files": ["path1", "path2", ...]}，UTF-8
_PROTOCOL_ENCODING = "utf-8"
_PROTOCOL_FRAME_SEPARATOR = b"\n"
_PROTOCOL_VERSION = 2
_PROTOCOL_CONNECT_TIMEOUT_MS = 3000
_PROTOCOL_NEGOTIATION_TIMEOUT_MS = 1200
_PROTOCOL_IO_TIMEOUT_MS = 5000
_SOCKET_SEND_CHUNK_SIZE = 64
_CHUNKED_TRANSFER_THRESHOLD = 10
_log = get_logger("send_to_app")

_QT_APIS = ("PyQt6", "PyQt5", "PySide6")
_FILE_OPEN_DISPATCHER_ATTR = "_send_to_app_file_open_dispatcher"
_FILE_OPEN_FILTER_ATTR = "_send_to_app_file_open_filter"
_QT_FILE_OPEN_SUPPORT: dict[str, Any] | None = None


def _iter_qt_api_names() -> tuple[str, ...]:
    """优先复用当前进程里已经加载的 Qt 绑定，避免混用 PyQt/PySide。"""
    preferred: list[str] = []
    for api_name in _QT_APIS:
        if api_name in sys.modules or any(module_name.startswith(f"{api_name}.") for module_name in sys.modules):
            preferred.append(api_name)
    for api_name in _QT_APIS:
        if api_name not in preferred:
            preferred.append(api_name)
    return tuple(preferred)


def _load_qt_modules(*, need_network: bool = False, need_widgets: bool = False) -> tuple[Any, Any | None, Any | None]:
    """按当前绑定优先级加载 QtCore / QtNetwork / QtWidgets。"""
    for api_name in _iter_qt_api_names():
        try:
            if api_name == "PyQt6":
                from PyQt6 import QtCore

                QtNetwork = None
                QtWidgets = None
                if need_network:
                    from PyQt6 import QtNetwork as _QtNetwork

                    QtNetwork = _QtNetwork
                if need_widgets:
                    from PyQt6 import QtWidgets as _QtWidgets

                    QtWidgets = _QtWidgets
            elif api_name == "PyQt5":
                from PyQt5 import QtCore

                QtNetwork = None
                QtWidgets = None
                if need_network:
                    from PyQt5 import QtNetwork as _QtNetwork

                    QtNetwork = _QtNetwork
                if need_widgets:
                    from PyQt5 import QtWidgets as _QtWidgets

                    QtWidgets = _QtWidgets
            else:
                from PySide6 import QtCore

                QtNetwork = None
                QtWidgets = None
                if need_network:
                    from PySide6 import QtNetwork as _QtNetwork

                    QtNetwork = _QtNetwork
                if need_widgets:
                    from PySide6 import QtWidgets as _QtWidgets

                    QtWidgets = _QtWidgets
            return QtCore, QtNetwork, QtWidgets
        except ImportError:
            continue
    raise ImportError("Qt bindings are unavailable")


def normalize_file_paths(paths: Iterable[str | os.PathLike[str]] | None) -> list[str]:
    """统一做 expanduser + abspath + normpath + 去重，供 argv/socket/FileOpen 共用。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in paths or ():
        if raw_path is None:
            continue
        try:
            path_text = os.fspath(raw_path)
        except TypeError:
            path_text = str(raw_path)
        path_text = path_text.strip()
        if not path_text:
            continue
        full_path = os.path.abspath(os.path.normpath(os.path.expanduser(path_text)))
        if full_path in seen:
            continue
        seen.add(full_path)
        normalized.append(full_path)
    return normalized


def _file_open_event_type(q_event: Any) -> Any:
    event_type_enum = getattr(q_event, "Type", None)
    if event_type_enum is not None:
        return getattr(event_type_enum, "FileOpen", None)
    return getattr(q_event, "FileOpen", None)


def _get_qt_file_open_support() -> dict[str, Any]:
    """懒加载 FileOpen 事件桥接所需的 Qt 类型，避免无 GUI 场景提前导入。"""
    global _QT_FILE_OPEN_SUPPORT
    if _QT_FILE_OPEN_SUPPORT is not None:
        return _QT_FILE_OPEN_SUPPORT

    QtCore, _, QtWidgets = _load_qt_modules(need_widgets=True)
    if QtWidgets is None:
        raise ImportError("QtWidgets is unavailable")

    QApplication = QtWidgets.QApplication
    QObject = QtCore.QObject
    QEvent = QtCore.QEvent
    QTimer = QtCore.QTimer
    file_open_type = _file_open_event_type(QEvent)

    class _FileOpenEventDispatcher(QObject):
        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            self._pending_file_open_paths: list[str] = []
            self._buffered_batches: list[list[str]] = []
            self._dispatch_callback: Callable[[list[str]], None] | None = None
            self._flush_timer = QTimer(self)
            self._flush_timer.setSingleShot(True)
            self._flush_timer.timeout.connect(self._flush_pending_paths)

        def set_dispatch_callback(self, on_files_received: Callable[[list[str]], None]) -> None:
            self._dispatch_callback = on_files_received
            self.flush()

        def handle_event(self, event: Any) -> bool:
            if file_open_type is None or event.type() != file_open_type:
                return False

            path_text = ""
            try:
                if hasattr(event, "file"):
                    path_text = event.file() or ""
                elif hasattr(event, "url"):
                    url = event.url()
                    if url and url.isLocalFile():
                        path_text = url.toLocalFile() or ""
            except Exception:
                path_text = ""

            normalized_paths = normalize_file_paths([path_text])
            if not normalized_paths:
                return False

            self._pending_file_open_paths.extend(normalized_paths)
            if not self._flush_timer.isActive():
                self._flush_timer.start(0)
            return True

        def flush(self) -> None:
            if self._pending_file_open_paths:
                self._flush_timer.stop()
                self._flush_pending_paths()
                return
            self._flush_buffered_batches()

        def _flush_pending_paths(self) -> None:
            pending_paths = normalize_file_paths(self._pending_file_open_paths)
            self._pending_file_open_paths.clear()
            if not pending_paths:
                return
            if self._dispatch_callback is None:
                self._buffered_batches.append(pending_paths)
                return
            self._dispatch(pending_paths)

        def _flush_buffered_batches(self) -> None:
            if self._dispatch_callback is None or not self._buffered_batches:
                return
            merged_paths = normalize_file_paths(
                path_text
                for batch_paths in self._buffered_batches
                for path_text in batch_paths
            )
            self._buffered_batches.clear()
            if merged_paths:
                self._dispatch(merged_paths)

        def _dispatch(self, paths: list[str]) -> None:
            if self._dispatch_callback is None or not paths:
                return
            try:
                self._dispatch_callback(paths)
            except Exception as exc:
                _log.warning("FileOpen dispatch failed: %s", exc)

    class _FileOpenEventFilter(QObject):
        def __init__(self, dispatcher: _FileOpenEventDispatcher, parent: Any = None) -> None:
            super().__init__(parent)
            self._dispatcher = dispatcher

        def eventFilter(self, watched: Any, event: Any) -> bool:  # type: ignore[override]
            return bool(self._dispatcher.handle_event(event))

    class FileOpenAwareApplication(QApplication):
        def __init__(self, argv: list[str]) -> None:
            super().__init__(argv)
            setattr(self, _FILE_OPEN_DISPATCHER_ATTR, _FileOpenEventDispatcher(self))

        def event(self, event: Any) -> bool:  # type: ignore[override]
            dispatcher = getattr(self, _FILE_OPEN_DISPATCHER_ATTR, None)
            if dispatcher is not None and dispatcher.handle_event(event):
                return True
            return super().event(event)

    _QT_FILE_OPEN_SUPPORT = {
        "QApplication": QApplication,
        "dispatcher_cls": _FileOpenEventDispatcher,
        "filter_cls": _FileOpenEventFilter,
        "app_cls": FileOpenAwareApplication,
    }
    return _QT_FILE_OPEN_SUPPORT


def _ensure_file_open_dispatcher(app: Any) -> Any:
    dispatcher = getattr(app, _FILE_OPEN_DISPATCHER_ATTR, None)
    if dispatcher is not None:
        return dispatcher

    support = _get_qt_file_open_support()
    dispatcher = support["dispatcher_cls"](app)
    event_filter = support["filter_cls"](dispatcher, app)
    app.installEventFilter(event_filter)
    setattr(app, _FILE_OPEN_DISPATCHER_ATTR, dispatcher)
    setattr(app, _FILE_OPEN_FILTER_ATTR, event_filter)
    _log.info("installed FileOpen event filter on existing QApplication")
    return dispatcher


def ensure_file_open_aware_application(argv: list[str] | None = None) -> Any:
    """
    返回支持 macOS QFileOpenEvent 的 QApplication。
    无实例时创建子类实例；已有实例时退回为安装 eventFilter。
    """
    support = _get_qt_file_open_support()
    QApplication = support["QApplication"]
    app = QApplication.instance()
    if app is None:
        app = support["app_cls"](list(argv or sys.argv))
        _log.info("created FileOpen-aware QApplication")
    _ensure_file_open_dispatcher(app)
    return app


def install_file_open_handler(app: Any, on_files_received: Callable[[list[str]], None]) -> None:
    """为 QApplication 绑定统一文件接收回调，并立刻冲刷启动早期缓存的 FileOpen 事件。"""
    dispatcher = _ensure_file_open_dispatcher(app)
    dispatcher.set_dispatch_callback(on_files_received)


def get_initial_file_list_from_argv(argv: list[str] | None = None) -> list[str]:
    """
    从命令行参数中解析出「文件列表」，供冷启动时与目录列表多选同等处理。

    约定：
    - 第一个参数为程序名，其后为非选项参数则视为文件/目录路径，转为绝对路径加入列表。
    - 遇到以 ``-`` 开头的参数即停止解析（如 macOS 的 -psn_0_xxx 等由系统注入的参数不会进入列表）。

    外部程序「用本应用打开」的常见调用方式均被支持，例如：
    - macOS: ``open -a /path/to/SuperViewer.app /path/to/file.jpg`` → argv[1] 为文件路径。
    - Windows: ``QProcess.startDetached(exe_path, [filepath])`` → argv[1] 为文件路径。
    多文件时依次传入即可，解析到第一个 ``-`` 前都会加入列表。

    Returns:
        绝对路径列表（不存在的路径也会保留，由业务决定是否过滤）。
    """
    args = (argv or sys.argv)[1:]
    paths: list[str] = []
    for a in args:
        if a.startswith("-"):
            break
        paths.append(a)
    return normalize_file_paths(paths)


def _canonicalize_app_id(app_id: str) -> str:
    """将显示名/配置名归一为稳定 app_id，避免大小写、空格差异导致 IPC 断开。"""
    raw = str(app_id or "").strip()
    if not raw:
        return "default"

    collapsed = "".join(ch.lower() for ch in raw if ch.isalnum())
    if collapsed:
        return collapsed

    safe = "".join(ch.lower() if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    safe = safe.strip("_")
    return safe or "default"


def _legacy_safe_app_id(app_id: str) -> str:
    """保留旧版本的 app_id 命名方式，便于热发送协议向后兼容。"""
    raw = str(app_id or "").strip()
    if not raw:
        return "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)
    safe = safe.strip("_")
    return safe or "default"


def _server_name_from_safe_id(safe: str) -> str:
    if sys.platform == "win32":
        uid = os.environ.get("USERNAME", "default").strip() or "default"
    else:
        try:
            uid = str(os.getuid())
        except (AttributeError, OSError):
            uid = os.environ.get("USER", os.environ.get("USERNAME", "default"))
    name = f"SuperViewer_sendto_{safe}_{uid}"
    if sys.platform == "win32":
        # Windows Named Pipe 名称长度上限 256，且仅允许部分字符
        name = name[:200].replace("\\", "_")
    return name


def _server_names(app_id: str) -> list[str]:
    """按新旧两种 app_id 规则生成 IPC 名称，优先尝试稳定归一化结果。"""
    names: list[str] = []
    seen: set[str] = set()
    for safe in (_canonicalize_app_id(app_id), _legacy_safe_app_id(app_id)):
        name = _server_name_from_safe_id(safe)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names or [_server_name_from_safe_id("default")]


def _server_name(app_id: str) -> str:
    """返回首选 IPC 名称，保留给旧调用点与诊断脚本使用。"""
    return _server_names(app_id)[0]


def _callback_accepts_completion_callback(callback: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(callback)
    except Exception:
        return False
    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional_count += 1
    return positional_count >= 2


def _callback_accepts_progress_callback(callback: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(callback)
    except Exception:
        return False
    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional_count += 1
    return positional_count >= 3


def _unconnected_socket_state(socket_like: Any) -> Any:
    socket_cls = socket_like if isinstance(socket_like, type) else type(socket_like)
    enum_holder = getattr(socket_cls, "LocalSocketState", None)
    if enum_holder is not None:
        state = getattr(enum_holder, "UnconnectedState", None)
        if state is not None:
            return state
    return getattr(socket_cls, "UnconnectedState", 0)


def _as_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        normalized = int(value)
    except Exception:
        return max(0, int(default))
    return max(0, normalized)


def _write_json_frame(sock: Any, payload: dict[str, Any]) -> bool:
    try:
        frame = json.dumps(payload, ensure_ascii=False).encode(_PROTOCOL_ENCODING) + _PROTOCOL_FRAME_SEPARATOR
        if sock.write(frame) < 0:
            return False
        try:
            sock.flush()
        except Exception:
            pass
        bytes_to_write = getattr(sock, "bytesToWrite", None)
        if callable(bytes_to_write) and bytes_to_write() > 0:
            return bool(sock.waitForBytesWritten(_PROTOCOL_IO_TIMEOUT_MS))
        return True
    except Exception:
        return False


def _build_progress_payload(
    *,
    transfer_id: str,
    phase: str,
    current: int,
    total: int,
    message: str = "",
) -> dict[str, Any]:
    payload = {
        "type": "progress",
        "protocol": _PROTOCOL_VERSION,
        "transfer_id": str(transfer_id or "").strip(),
        "phase": str(phase or "").strip(),
        "current": max(0, int(current)),
        "total": max(0, int(total)),
    }
    text = str(message or "").strip()
    if text:
        payload["message"] = text
    return payload


def _read_protocol_frame(sock: Any, buffer: bytearray, *, timeout_ms: int) -> dict[str, Any] | None:
    deadline = time.monotonic() + (max(1, int(timeout_ms)) / 1000.0)
    while True:
        separator_index = buffer.find(_PROTOCOL_FRAME_SEPARATOR)
        if separator_index >= 0:
            raw_line = bytes(buffer[:separator_index]).strip()
            del buffer[: separator_index + len(_PROTOCOL_FRAME_SEPARATOR)]
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line.decode(_PROTOCOL_ENCODING))
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
            continue

        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if remaining_ms <= 0:
            return None
        if not sock.waitForReadyRead(remaining_ms):
            data = sock.readAll().data() if getattr(sock, "bytesAvailable", lambda: 0)() > 0 else b""
            if data:
                buffer.extend(data)
                continue
            return None
        data = sock.readAll().data()
        if data:
            buffer.extend(data)


def _wait_for_expected_frame(
    sock: Any,
    buffer: bytearray,
    *,
    expected_types: set[str],
    transfer_id: str = "",
    timeout_ms: int = _PROTOCOL_IO_TIMEOUT_MS,
    on_progress_frame: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + (max(1, int(timeout_ms)) / 1000.0)
    while True:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if remaining_ms <= 0:
            return None
        frame = _read_protocol_frame(sock, buffer, timeout_ms=remaining_ms)
        if frame is None:
            return None
        frame_type = str(frame.get("type") or "").strip().lower()
        if not frame_type:
            continue
        frame_transfer_id = str(frame.get("transfer_id") or "").strip()
        if transfer_id and frame_transfer_id and frame_transfer_id != transfer_id:
            continue
        if frame_type == "progress":
            if callable(on_progress_frame):
                try:
                    on_progress_frame(frame)
                except Exception:
                    pass
            continue
        if frame_type in expected_types:
            return frame
        if frame_type == "error":
            return None


def _probe_chunked_socket_protocol(server_name: str) -> bool:
    _, QtNetwork, _ = _load_qt_modules(need_network=True)
    if QtNetwork is None:
        return False
    QLocalSocket = QtNetwork.QLocalSocket
    sock = QLocalSocket()
    try:
        sock.connectToServer(server_name)
        if not sock.waitForConnected(_PROTOCOL_CONNECT_TIMEOUT_MS):
            return False
        if not _write_json_frame(sock, {"type": "probe", "protocol": _PROTOCOL_VERSION}):
            return False
        frame = _read_protocol_frame(sock, bytearray(), timeout_ms=_PROTOCOL_NEGOTIATION_TIMEOUT_MS)
        if not isinstance(frame, dict):
            return False
        return (
            str(frame.get("type") or "").strip().lower() == "probe_ack"
            and _as_non_negative_int(frame.get("protocol"), 0) >= _PROTOCOL_VERSION
        )
    except Exception:
        return False
    finally:
        try:
            sock.disconnectFromServer()
            if sock.state() != _unconnected_socket_state(QLocalSocket):
                sock.abort()
        except Exception:
            pass


def _send_via_socket(server_name: str, file_paths: list[str]) -> bool:
    """作为客户端连接已有实例，发送 file_paths 后返回。成功返回 True。"""
    _, QtNetwork, _ = _load_qt_modules(need_network=True)
    if QtNetwork is None:
        return False
    QLocalSocket = QtNetwork.QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(_PROTOCOL_CONNECT_TIMEOUT_MS):
        return False
    payload = json.dumps({"files": normalize_file_paths(file_paths)}, ensure_ascii=False)
    sock.write(payload.encode(_PROTOCOL_ENCODING))
    sock.flush()
    sock.waitForBytesWritten(2000)
    sock.disconnectFromServer()
    unconnected = _unconnected_socket_state(QLocalSocket)
    if sock.state() != unconnected:
        sock.abort()
    return True


def _send_via_chunked_socket(
    server_name: str,
    file_paths: list[str],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> bool:
    _, QtNetwork, _ = _load_qt_modules(need_network=True)
    if QtNetwork is None:
        return False
    QLocalSocket = QtNetwork.QLocalSocket
    sock = QLocalSocket()
    buffer = bytearray()
    transfer_id = f"{os.getpid()}_{int(time.time() * 1000)}_{len(file_paths)}"
    total = len(file_paths)
    chunk_size = max(1, _SOCKET_SEND_CHUNK_SIZE)

    if callable(progress_callback):
        try:
            progress_callback(0, total, f"热发送 0/{total}")
        except Exception:
            pass

    def forward_progress_frame(frame: dict[str, Any]) -> None:
        if not callable(progress_callback):
            return
        try:
            current_value = _as_non_negative_int(frame.get("current"), 0)
            total_value = max(total, _as_non_negative_int(frame.get("total"), total))
            text = str(frame.get("message") or "").strip()
            phase = str(frame.get("phase") or "").strip().lower()
            if not text:
                if phase == "receiving":
                    text = f"接收方正在接收 {current_value}/{total_value}"
                elif phase in {"import_pending", "importing"}:
                    text = f"接收方正在导入 {current_value}/{total_value}"
                elif phase in {"imported", "completed"}:
                    text = f"接收方已导入 {current_value}/{total_value}"
                else:
                    text = f"接收方处理中 {current_value}/{total_value}"
            progress_callback(min(current_value, max(1, total_value)), total_value, text)
        except Exception:
            pass

    try:
        sock.connectToServer(server_name)
        if not sock.waitForConnected(_PROTOCOL_CONNECT_TIMEOUT_MS):
            return False

        begin_payload = {
            "type": "begin",
            "protocol": _PROTOCOL_VERSION,
            "transfer_id": transfer_id,
            "total_files": total,
        }
        if not _write_json_frame(sock, begin_payload):
            return False
        if _wait_for_expected_frame(sock, buffer, expected_types={"begin_ack"}, transfer_id=transfer_id) is None:
            return False

        for start in range(0, total, chunk_size):
            if callable(cancel_check) and cancel_check():
                _write_json_frame(
                    sock,
                    {
                        "type": "cancel",
                        "protocol": _PROTOCOL_VERSION,
                        "transfer_id": transfer_id,
                    },
                )
                return False

            chunk = file_paths[start : start + chunk_size]
            payload = {
                "type": "chunk",
                "protocol": _PROTOCOL_VERSION,
                "transfer_id": transfer_id,
                "total_files": total,
                "files": chunk,
            }
            if not _write_json_frame(sock, payload):
                return False
            ack = _wait_for_expected_frame(sock, buffer, expected_types={"chunk_ack"}, transfer_id=transfer_id)
            if ack is None:
                return False
            current = min(total, _as_non_negative_int(ack.get("received_files"), start + len(chunk)))
            if callable(progress_callback):
                try:
                    progress_callback(current, total, f"热发送 {current}/{total}")
                except Exception:
                    pass

        if not _write_json_frame(
            sock,
            {
                "type": "end",
                "protocol": _PROTOCOL_VERSION,
                "transfer_id": transfer_id,
                "total_files": total,
            },
        ):
            return False
        if callable(progress_callback):
            try:
                progress_callback(total, total, "已发送路径，等待接收端完成导入...")
            except Exception:
                pass
        if _wait_for_expected_frame(
            sock,
            buffer,
            expected_types={"end_ack"},
            transfer_id=transfer_id,
            on_progress_frame=forward_progress_frame,
        ) is None:
            return False

        if callable(progress_callback):
            try:
                progress_callback(total, total, f"热发送 {total}/{total}")
            except Exception:
                pass
        return True
    except Exception:
        return False
    finally:
        try:
            sock.disconnectFromServer()
            unconnected = _unconnected_socket_state(QLocalSocket)
            if sock.state() != unconnected:
                sock.abort()
        except Exception:
            pass


def _can_connect_to_server(server_name: str, timeout_ms: int = 300) -> bool:
    """探测本地服务是否真的在监听，用于区分活跃实例和残留 socket。"""
    _, QtNetwork, _ = _load_qt_modules(need_network=True)
    if QtNetwork is None:
        return False
    QLocalSocket = QtNetwork.QLocalSocket
    sock = QLocalSocket()
    try:
        sock.connectToServer(server_name)
        ok = bool(sock.waitForConnected(timeout_ms))
        return ok
    finally:
        try:
            sock.abort()
        except Exception:
            pass


class SingleInstanceReceiver:
    """
    单例接收端：仅在首进程内启动。
    当其它进程通过 send_file_list_to_running_app 发来文件列表时，触发 on_files_received(paths)。
    """

    def __init__(
        self,
        app_id: str,
        on_files_received: Callable[[list[str]], None],
        on_transfer_progress: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._app_id = app_id
        self._on_files = on_files_received
        self._on_files_supports_completion_callback = _callback_accepts_completion_callback(on_files_received)
        self._on_files_supports_progress_callback = _callback_accepts_progress_callback(on_files_received)
        self._on_transfer_progress = on_transfer_progress
        self._servers: list[Any] = []
        self._names = _server_names(app_id)

    def _emit_transfer_progress(self, phase: str, current: int, total: int, *, transfer_id: str = "") -> None:
        callback = self._on_transfer_progress
        if callback is None:
            return
        payload = {
            "phase": str(phase or "").strip(),
            "current": max(0, int(current)),
            "total": max(0, int(total)),
        }
        transfer_id_text = str(transfer_id or "").strip()
        if transfer_id_text:
            payload["transfer_id"] = transfer_id_text
        try:
            callback(payload)
        except Exception as exc:
            _log.warning("receiver transfer progress callback failed: %s", exc)

    def start(self) -> bool:
        """创建并监听本地 socket。若已被占用则返回 False（表示本进程应为第二实例）。"""
        _, QtNetwork, _ = _load_qt_modules(need_network=True)
        if QtNetwork is None:
            _log.warning("receiver start failed: QtNetwork is unavailable")
            return False

        for name in self._names:
            if _can_connect_to_server(name):
                _log.info("receiver start skipped; active instance already listening on name=%s", name)
                return False

        started_servers: list[Any] = []
        for name in self._names:
            server = self._listen_one_name(QtNetwork, name)
            if server is not None:
                started_servers.append(server)
        if not started_servers:
            return False
        self._servers = started_servers
        return True

    def _listen_one_name(self, QtNetwork: Any, name: str) -> Any | None:
        """监听单个 IPC 名称；若发现残留 socket 则清理后重试。"""
        QLocalServer = QtNetwork.QLocalServer
        server = QLocalServer()
        if not server.listen(name):
            error_text = server.errorString()
            if not _can_connect_to_server(name):
                removed = False
                try:
                    removed = bool(QLocalServer.removeServer(name))
                except Exception:
                    removed = False
                _log.warning(
                    "receiver listen failed with stale socket; name=%s error=%s removed=%s",
                    name,
                    error_text,
                    removed,
                )
                if removed and server.listen(name):
                    try:
                        _log.info(
                            "receiver listen recovered after stale socket cleanup; name=%s full=%s",
                            name,
                            server.fullServerName(),
                        )
                    except Exception:
                        _log.info("receiver listen recovered after stale socket cleanup; name=%s", name)
                    server.newConnection.connect(lambda s=server: self._on_connection(s))
                    return server
            _log.warning("receiver listen failed; name=%s error=%s", name, error_text)
            return None
        try:
            _log.info("receiver listening; name=%s full=%s", name, server.fullServerName())
        except Exception:
            _log.info("receiver listening; name=%s", name)
        server.newConnection.connect(lambda s=server: self._on_connection(s))
        return server

    def _on_connection(self, server: Any) -> None:
        if server is None:
            return
        conn = server.nextPendingConnection()
        if not conn:
            return
        done = []
        buffer = bytearray()
        session: dict[str, Any] = {
            "transfer_id": "",
            "total_files": 0,
            "paths": [],
            "seen": set(),
            "chunked": False,
        }

        def close_connection() -> None:
            try:
                conn.disconnectFromServer()
                if conn.state() != getattr(conn, "UnconnectedState", 0):
                    conn.abort()
            except Exception:
                pass
            conn.deleteLater()

        def emit_progress(phase: str, current: int, total: int) -> None:
            self._emit_transfer_progress(
                phase,
                current,
                total,
                transfer_id=str(session.get("transfer_id") or ""),
            )

        def forward_progress_to_sender(payload: dict[str, Any] | None) -> None:
            if not isinstance(payload, dict):
                return
            progress_payload = _build_progress_payload(
                transfer_id=str(session.get("transfer_id") or ""),
                phase=str(payload.get("phase") or "").strip(),
                current=_as_non_negative_int(payload.get("current"), 0),
                total=_as_non_negative_int(payload.get("total"), 0),
                message=str(payload.get("message") or "").strip(),
            )
            _write_json_frame(conn, progress_payload)

        def append_session_paths(raw_paths: Any) -> int:
            if not isinstance(raw_paths, list):
                return 0
            appended = 0
            normalized_paths = normalize_file_paths(raw_paths)
            seen: set[str] = session["seen"]
            paths: list[str] = session["paths"]
            for path_text in normalized_paths:
                dedup_key = os.path.normcase(os.path.normpath(path_text))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                paths.append(path_text)
                appended += 1
            return appended

        def finalize_legacy_payload() -> None:
            if done or session["chunked"]:
                return
            raw_payload = bytes(buffer).strip()
            if not raw_payload:
                return
            try:
                obj = json.loads(raw_payload.decode(_PROTOCOL_ENCODING))
            except Exception:
                return
            paths = obj.get("files")
            if not isinstance(paths, list):
                return
            normalized = normalize_file_paths(paths)
            if normalized:
                total = len(normalized)
                done.append(1)
                emit_progress("receiving", 0, total)
                self._on_files(normalized)
                emit_progress("received", total, total)

        def handle_protocol_frame(frame: dict[str, Any]) -> None:
            frame_type = str(frame.get("type") or "").strip().lower()
            if not frame_type:
                return

            if frame_type == "probe":
                _write_json_frame(conn, {"type": "probe_ack", "protocol": _PROTOCOL_VERSION})
                return

            session["chunked"] = True
            if frame_type == "begin":
                session["transfer_id"] = str(frame.get("transfer_id") or "").strip()
                session["total_files"] = _as_non_negative_int(frame.get("total_files"), 0)
                session["paths"] = []
                session["seen"] = set()
                emit_progress("receiving", 0, session["total_files"])
                _write_json_frame(
                    conn,
                    {
                        "type": "begin_ack",
                        "protocol": _PROTOCOL_VERSION,
                        "transfer_id": session["transfer_id"],
                        "total_files": session["total_files"],
                    },
                )
                return

            if frame_type == "chunk":
                if not session["transfer_id"]:
                    session["transfer_id"] = str(frame.get("transfer_id") or "").strip()
                session["total_files"] = max(
                    session["total_files"],
                    _as_non_negative_int(frame.get("total_files"), len(session["paths"])),
                )
                append_session_paths(frame.get("files"))
                current = len(session["paths"])
                total = max(session["total_files"], current)
                emit_progress("receiving", current, total)
                _write_json_frame(
                    conn,
                    {
                        "type": "chunk_ack",
                        "protocol": _PROTOCOL_VERSION,
                        "transfer_id": session["transfer_id"],
                        "received_files": current,
                        "total_files": total,
                    },
                )
                return

            if frame_type == "cancel":
                emit_progress("cancelled", len(session["paths"]), max(session["total_files"], len(session["paths"])))
                done.append(1)
                _write_json_frame(
                    conn,
                    {
                        "type": "cancel_ack",
                        "protocol": _PROTOCOL_VERSION,
                        "transfer_id": session["transfer_id"],
                    },
                )
                return

            if frame_type == "end":
                current = len(session["paths"])
                total = max(session["total_files"], current)
                ack_sent = []

                def finalize_transfer() -> None:
                    if ack_sent:
                        return
                    ack_sent.append(1)
                    _write_json_frame(
                        conn,
                        {
                            "type": "end_ack",
                            "protocol": _PROTOCOL_VERSION,
                            "transfer_id": session["transfer_id"],
                            "received_files": current,
                            "total_files": total,
                        },
                    )
                    emit_progress("received", current, total)
                    if not done:
                        done.append(1)
                    close_connection()

                if current <= 0:
                    finalize_transfer()
                    return

                try:
                    if self._on_files_supports_progress_callback:
                        self._on_files(list(session["paths"]), finalize_transfer, forward_progress_to_sender)
                        return
                    if self._on_files_supports_completion_callback:
                        self._on_files(list(session["paths"]), finalize_transfer)
                        return
                except Exception:
                    finalize_transfer()
                    return

                finalize_transfer()
                self._on_files(list(session["paths"]))

        def read_and_callback() -> None:
            if done:
                return
            try:
                data = conn.readAll().data()
                if data:
                    buffer.extend(data)
                if not session["chunked"] and _PROTOCOL_FRAME_SEPARATOR not in buffer:
                    return
                session["chunked"] = True
                while True:
                    separator_index = buffer.find(_PROTOCOL_FRAME_SEPARATOR)
                    if separator_index < 0:
                        break
                    raw_line = bytes(buffer[:separator_index]).strip()
                    del buffer[: separator_index + len(_PROTOCOL_FRAME_SEPARATOR)]
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line.decode(_PROTOCOL_ENCODING))
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        handle_protocol_frame(obj)
                        if done:
                            break
            except Exception:
                pass
            if done:
                close_connection()

        def finish_connection() -> None:
            if not done:
                finalize_legacy_payload()
            close_connection()

        try:
            conn.readyRead.connect(read_and_callback)
            conn.disconnected.connect(finish_connection)
            if conn.bytesAvailable() > 0:
                read_and_callback()
        except Exception:
            conn.deleteLater()

    def stop(self) -> None:
        for server in self._servers:
            try:
                server.close()
            except Exception:
                pass
        self._servers = []
        _, QtNetwork, _ = _load_qt_modules(need_network=True)
        if QtNetwork is None:
            return
        QLocalServer = QtNetwork.QLocalServer
        for name in self._names:
            try:
                removed = bool(QLocalServer.removeServer(name))
                _log.info("receiver stopped; name=%s removed=%s", name, removed)
            except Exception:
                pass


def send_file_list_to_running_app(
    app_id: str,
    file_paths: list[str],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> bool:
    """
    将文件列表发给已在运行的同名应用实例（通过单例 IPC）。
    若成功发送则返回 True，调用方应随后退出（由已运行实例处理）；
    若返回 False 表示没有已运行实例，可正常启动新进程。
    """
    normalized_paths = normalize_file_paths(file_paths)
    if not normalized_paths:
        return False
    prefer_chunked = len(normalized_paths) > _CHUNKED_TRANSFER_THRESHOLD or callable(progress_callback)
    for server_name in _server_names(app_id):
        if prefer_chunked and _probe_chunked_socket_protocol(server_name):
            if _send_via_chunked_socket(
                server_name,
                normalized_paths,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            ):
                return True
            if callable(cancel_check) and cancel_check():
                return False
            continue
        if callable(progress_callback):
            try:
                progress_callback(0, len(normalized_paths), "目标应用未启用分块热发送，正在兼容发送...")
            except Exception:
                pass
        if _send_via_socket(server_name, normalized_paths):
            if callable(progress_callback):
                try:
                    progress_callback(len(normalized_paths), len(normalized_paths), f"热发送 {len(normalized_paths)}/{len(normalized_paths)}")
                except Exception:
                    pass
            return True
        if callable(cancel_check) and cancel_check():
            return False
    return False
