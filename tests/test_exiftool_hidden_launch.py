import subprocess
import sys
from unittest.mock import patch

from app_common.exif_io import exiftool_path, exiftool_runner, writer


def test_version_probe_hides_windows_console(monkeypatch, tmp_path):
    executable = tmp_path / "exiftool.exe"
    executable.touch()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "13.00\n", "")

    monkeypatch.setattr(exiftool_runner.subprocess, "run", fake_run)
    exiftool_path._is_usable_exiftool.cache_clear()
    try:
        with patch.object(exiftool_runner.sys, "platform", "win32"):
            assert exiftool_path._is_usable_exiftool(str(executable))
    finally:
        exiftool_path._is_usable_exiftool.cache_clear()

    command, kwargs = calls[0]
    assert command == [str(executable), "-ver"]
    assert kwargs["creationflags"] & exiftool_runner._CREATE_NO_WINDOW
    assert kwargs["capture_output"] is True
    if hasattr(subprocess, "STARTUPINFO"):
        assert kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert kwargs["startupinfo"].wShowWindow == getattr(subprocess, "SW_HIDE", 0)


def test_single_file_metadata_read_uses_hidden_runner(monkeypatch, tmp_path):
    photo = tmp_path / "测试.jpg"
    photo.touch()
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '[{"EXIF:Make":"Camera"}]', "")

    monkeypatch.setattr(writer, "get_exiftool_executable_path", lambda: "exiftool.exe")
    monkeypatch.setattr(writer, "run_exiftool_once", fake_run)

    assert writer.run_exiftool_json(str(photo)) == [{"EXIF:Make": "Camera"}]
    assert len(commands) == 1
    command, kwargs = commands[0]
    assert command[0] == "exiftool.exe"
    if sys.platform.startswith("win"):
        assert "-@" in command
    else:
        assert str(photo) in command
    assert kwargs["capture_output"] is True
