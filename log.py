# -*- coding: utf-8 -*-
"""app_common.log – minimal logging for diagnostics (file + stderr).

Usage::
    from app_common.log import get_logger
    log = get_logger("template_manager")
    log.info("opening dialog")
    log.debug("detail: %s", value)
"""
from __future__ import annotations

import sys
from typing import Any, TextIO

# Default: stderr only. Set LOG_FILE to a path to also write to a file.
LOG_FILE: str | None = None
LOG_LEVEL: str = "DEBUG"  # DEBUG | INFO | WARNING | ERROR

_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


def _level_ok(level: str) -> bool:
    return _LEVEL_ORDER.get(level.upper(), 0) >= _LEVEL_ORDER.get(LOG_LEVEL.upper(), 0)


def _format(level: str, name: str, msg: str, *args: Any) -> str:
    parts = [level, name, msg % args if args else msg]
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
