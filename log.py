# -*- coding: utf-8 -*-
"""app_common.log – minimal logging for diagnostics (file + stderr).

Usage::
    from app_common.log import get_logger
    log = get_logger("template_manager")
    log.info("opening dialog")
    log.debug("detail: %s", value)
"""
from __future__ import annotations

import os
import sys
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

_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


def _level_ok(level: str) -> bool:
    return _LEVEL_ORDER.get(level.upper(), 0) >= _LEVEL_ORDER.get(LOG_LEVEL.upper(), 0)


def _format(level: str, name: str, msg: str, *args: Any) -> str:
    parts = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, name, msg % args if args else msg]
    return " ".join(str(p) for p in parts)


class _Logger:
    def __init__(self, name: str) -> None:
        self._name = name
        self._file: TextIO | None = None
        if LOG_FILE:
            try:
                self._file = open(LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115
            except OSError:
                pass

    def _write(self, level: str, msg: str, *args: Any) -> None:
        if not _level_ok(level):
            return
        line = _format(level, self._name, msg, *args) + "\n"
        if self._file:
            try:
                self._file.write(line)
                self._file.flush()
            except OSError:
                pass
        err = sys.stderr
        if err is None or not hasattr(err, "write"):
            return
        try:
            err.write(line)
            err.flush()
        except OSError:
            pass

    def debug(self, msg: str, *args: Any) -> None:
        self._write("DEBUG", msg, *args)

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
