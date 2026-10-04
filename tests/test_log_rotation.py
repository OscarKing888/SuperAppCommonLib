"""Log file rotation: size limit, backup count, legacy oversized logs, several writers."""
from __future__ import annotations

import os

import pytest

from app_common import log as app_log


def _sizes(path):
    return {name: os.path.getsize(path.parent / name) for name in sorted(os.listdir(path.parent))}


def test_rotates_by_size_and_keeps_a_bounded_number_of_backups(tmp_path) -> None:
    path = tmp_path / "app.log"
    sink = app_log._RotatingFile(str(path), max_bytes=1000, backups=2)
    for i in range(200):
        sink.write(f"2026-10-04 12:00:00 INFO 鸟清晰度 line {i:04d}\n")
    sizes = _sizes(path)
    assert set(sizes) == {"app.log", "app.log.1", "app.log.2"}  # app.log.3 never kept
    assert all(size <= 1000 for size in sizes.values())
    assert "line 0199" in path.read_text(encoding="utf-8")  # newest lines in the live file
    assert "鸟清晰度" in (tmp_path / "app.log.1").read_text(encoding="utf-8")


def test_legacy_oversized_log_keeps_only_its_recent_tail(tmp_path) -> None:
    path = tmp_path / "app.log"
    path.write_text("".join(f"old line {i:06d} 中文\n" for i in range(5000)), encoding="utf-8")
    sink = app_log._RotatingFile(str(path), max_bytes=1000, backups=3)
    sink.write("new line\n")
    assert path.read_text(encoding="utf-8") == "new line\n"
    backup = (tmp_path / "app.log.1").read_text(encoding="utf-8")  # whole lines, valid UTF-8
    assert os.path.getsize(tmp_path / "app.log.1") <= 1000
    assert backup.startswith("old line ") and backup.endswith("old line 004999 中文\n")


def test_follows_a_rotation_done_by_another_process(tmp_path) -> None:
    path = tmp_path / "app.log"
    sink = app_log._RotatingFile(str(path), max_bytes=10_000, backups=2)
    sink.write("before\n")
    os.replace(path, tmp_path / "app.log.1")  # the other process rotated
    sink.write("after\n")
    assert path.read_text(encoding="utf-8") == "after\n"
    assert (tmp_path / "app.log.1").read_text(encoding="utf-8") == "before\n"


def test_refused_rename_keeps_logging_and_retries_later(tmp_path, monkeypatch) -> None:
    path = tmp_path / "app.log"
    sink = app_log._RotatingFile(str(path), max_bytes=100, backups=2)
    real_replace = os.replace
    monkeypatch.setattr(app_log.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("in use")))
    for i in range(10):
        sink.write(f"line {i}\n" * 3)
    assert "line 9" in path.read_text(encoding="utf-8")  # nothing lost while rotation is refused
    monkeypatch.setattr(app_log.os, "replace", real_replace)
    sink.write("rotated\n")
    assert path.read_text(encoding="utf-8") == "rotated\n" and (tmp_path / "app.log.1").exists()


def test_all_loggers_share_one_handle(tmp_path, monkeypatch) -> None:
    path = tmp_path / "shared.log"
    monkeypatch.setattr(app_log, "LOG_FILE", str(path))
    monkeypatch.setattr(app_log, "_SINKS", {})
    a, b = app_log.get_logger("a"), app_log.get_logger("b")
    assert a._file is b._file
    a.info("来自 %s", "a")
    b.warning("来自 b")
    text = path.read_text(encoding="utf-8")
    assert "INFO a 来自 a" in text and "WARNING b 来自 b" in text


@pytest.mark.parametrize(("value", "expected"), [("", 10 * 1024 * 1024), ("2048", 64 * 1024),
                                                 ("20000000", 20000000), ("abc", 10 * 1024 * 1024)])
def test_size_limit_from_environment(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("APP_COMMON_LOG_MAX_BYTES", value)
    assert app_log._env_int("APP_COMMON_LOG_MAX_BYTES", 10 * 1024 * 1024, 64 * 1024) == expected
