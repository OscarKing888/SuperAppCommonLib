"""Exception reporting must remain safe in windowed builds without stderr."""

import sqlite3

import pytest

from app_common import log


@pytest.mark.parametrize("windowed", [False, True])
def test_exception_records_original_traceback(tmp_path, monkeypatch, capsys, windowed):
    path = tmp_path / "exception.log"
    monkeypatch.setattr(log, "LOG_FILE", str(path))
    monkeypatch.setattr(log, "_SINKS", {})
    if windowed:
        monkeypatch.setattr(log.sys, "stderr", None)
    logger = log.get_logger("focus")
    try:
        try:
            raise sqlite3.OperationalError("database is locked")
        except sqlite3.OperationalError:
            logger.exception("report fallback failed: %s", "炮台湾公园/100%/report.db")
        output = path.read_text(encoding="utf-8")
        assert "ERROR focus report fallback failed: 炮台湾公园/100%/report.db" in output
        assert "Traceback (most recent call last):" in output
        assert "sqlite3.OperationalError: database is locked" in output
        assert "test_exception_records_original_traceback" in output
        if not windowed:
            assert "sqlite3.OperationalError: database is locked" in capsys.readouterr().err
    finally:
        logger._file._close()
