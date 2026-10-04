# -*- coding: utf-8 -*-
"""app_common.log – minimal logging for diagnostics (file + stderr).

Usage::
    from app_common.log import get_logger
    log = get_logger("template_manager")
    log.info("opening dialog")
    log.debug("detail: %s", value)

The log file rotates by size: past ``LOG_MAX_BYTES`` it becomes ``<file>.1`` and
at most ``LOG_BACKUP_COUNT`` older files are kept (``APP_COMMON_LOG_MAX_BYTES`` /
``APP_COMMON_LOG_BACKUPS``), so disk use stays below ``(backups + 1) × max``.
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


def _default_app_name() -> str:
    """尽量给日志目录一个稳定的应用名，便于打包后排查。"""
    raw = ""
    try:
        raw = Path(sys.executable if getattr(sys, "frozen", False) else (sys.argv[0] if sys.argv else "")).stem
    except Exception:
        raw = ""
    raw = (raw or "BirdStamp").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return safe or "BirdStamp"


def _default_log_file() -> str | None:
    """窗口版打包应用默认落盘日志，开发态仍保持 stderr 即可。"""
    override = os.environ.get("APP_COMMON_LOG_FILE", "").strip()
    if override:
        return override
    if not getattr(sys, "frozen", False):
        return None

    app_name = _default_app_name()
    if sys.platform == "win32":
        base = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or str(Path.home() / "AppData" / "Local")
        )
        log_dir = Path(base) / app_name / "logs"
    elif sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs" / app_name
    else:
        log_dir = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / app_name

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return str(log_dir / "app.log")


# Default: stderr only in dev; frozen app also writes to a user-writable log file.
LOG_FILE: str | None = _default_log_file()
LOG_LEVEL: str = os.environ.get("APP_COMMON_LOG_LEVEL", "DEBUG").upper()  # DEBUG | INFO | WARNING | ERROR


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, "").strip() or default))
    except ValueError:
        return default


LOG_MAX_BYTES: int = _env_int("APP_COMMON_LOG_MAX_BYTES", 10 * 1024 * 1024, 64 * 1024)
LOG_BACKUP_COUNT: int = _env_int("APP_COMMON_LOG_BACKUPS", 5, 1)

_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


def _level_ok(level: str) -> bool:
    return _LEVEL_ORDER.get(level.upper(), 0) >= _LEVEL_ORDER.get(LOG_LEVEL.upper(), 0)


def _format(level: str, name: str, msg: str, *args: Any) -> str:
    parts = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, name, msg % args if args else msg]
    return " ".join(str(p) for p in parts)


def _trim_to_tail(path: str, max_bytes: int) -> None:
    """Keep only the last ``max_bytes`` (from a line start) of an oversized file."""
    try:
        size = os.path.getsize(path)
        if size <= max_bytes:
            return
        with open(path, "rb") as src:
            src.seek(size - max_bytes)
            tail = src.read()
        newline = tail.find(b"\n")
        tail = tail[newline + 1:] if newline >= 0 else tail
        tmp = path + ".trim"
        with open(tmp, "wb") as dst:
            dst.write(tail)
        os.replace(tmp, path)
    except OSError:
        pass


class _RotatingFile:
    """One appending handle per log file, shared by every logger in the process.

    Several processes may append to the same file (the app and its CLI
    diagnostics): the size is read from the path, and when another process has
    rotated the file this one reopens the new file. If a rename is refused
    (Windows, file open elsewhere) rotation is retried on a later write.
    """

    def __init__(self, path: str, max_bytes: int, backups: int) -> None:
        self.path, self.max_bytes, self.backups = path, max_bytes, backups
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        self._ino = None

    def _close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        self._file, self._ino = None, None

    def _rotate(self) -> None:
        self._close()  # Windows cannot rename a file this process still holds
        try:
            for i in range(self.backups - 1, 0, -1):
                older = f"{self.path}.{i}"
                if os.path.exists(older):
                    os.replace(older, f"{self.path}.{i + 1}")
            os.replace(self.path, f"{self.path}.1")
        except OSError:
            return
        # A legacy log that grew unbounded (GBs) keeps only its recent tail.
        _trim_to_tail(f"{self.path}.1", self.max_bytes)

    def write(self, line: str) -> None:
        data_len = len(line.encode("utf-8", "replace"))
        with self._lock:
            try:
                try:
                    st = os.stat(self.path)
                except FileNotFoundError:
                    st = None
                if st is None or (self._file is not None and st.st_ino != self._ino):
                    self._close()  # rotated or removed by another process
                if st is not None and st.st_size and st.st_size + data_len > self.max_bytes:
                    self._rotate()
                if self._file is None:
                    self._file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115
                    self._ino = os.fstat(self._file.fileno()).st_ino
                self._file.write(line)
                self._file.flush()
            except OSError:
                self._close()


_SINKS: dict[str, _RotatingFile] = {}
_SINKS_LOCK = threading.Lock()


def _sink(path: str) -> _RotatingFile:
    with _SINKS_LOCK:
        sink = _SINKS.get(path)
        if sink is None:
            sink = _SINKS[path] = _RotatingFile(path, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
        return sink


class _Logger:
    def __init__(self, name: str) -> None:
        self._name = name
        self._file = _sink(LOG_FILE) if LOG_FILE else None

    def _write(self, level: str, msg: str, *args: Any) -> None:
        if not _level_ok(level):
            return
        line = _format(level, self._name, msg, *args) + "\n"
        if self._file is not None:
            self._file.write(line)
        err = sys.stderr
        if err is None or not hasattr(err, "write"):
            return
        try:
            err.write(line)
            err.flush()
        except OSError:
            pass

    def debug(self, msg: str, *args: Any) -> None:
        pass
        # self._write("DEBUG", msg, *args)

    def info(self, msg: str, *args: Any) -> None:
        self._write("INFO", msg, *args)

    def warning(self, msg: str, *args: Any) -> None:
        self._write("WARNING", msg, *args)

    def error(self, msg: str, *args: Any) -> None:
        self._write("ERROR", msg, *args)


def get_logger(name: str) -> _Logger:
    return _Logger(name)


def get_log_file_path() -> str | None:
    return LOG_FILE
