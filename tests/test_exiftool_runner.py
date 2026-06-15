from __future__ import annotations

import subprocess

from app_common.exif_io import exiftool_runner
from app_common.exif_io import writer


def test_hidden_subprocess_kwargs_uses_create_no_window_on_windows(monkeypatch) -> None:
    class _StartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(exiftool_runner.sys, "platform", "win32")
    monkeypatch.setattr(exiftool_runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(exiftool_runner.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(exiftool_runner.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(exiftool_runner.subprocess, "STARTUPINFO", _StartupInfo, raising=False)

    kwargs = exiftool_runner.hidden_subprocess_kwargs()

    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["startupinfo"].dwFlags & 1
    assert kwargs["startupinfo"].wShowWindow == 0


def test_run_exiftool_uses_stay_open_protocol(monkeypatch) -> None:
    exiftool_runner.close_exiftool_process()

    class _PipeIn:
        def __init__(self) -> None:
            self.data = b""

        def write(self, payload: bytes) -> None:
            self.data += payload

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class _PipeOut:
        def __init__(self, lines: list[bytes]) -> None:
            self.lines = list(lines)

        def readline(self) -> bytes:
            if not self.lines:
                return b""
            return self.lines.pop(0)

        def close(self) -> None:
            pass

    class _FakeProc:
        def __init__(self) -> None:
            self.stdin = _PipeIn()
            self.stdout = _PipeOut([b'{"SourceFile":"a.jpg"}\n', b"{ready}\n"])
            self.stderr = _PipeOut([])

        def poll(self) -> None:
            return None

        def wait(self, timeout: int | float | None = None) -> int:
            return 0

    fake_proc = _FakeProc()
    popen_calls: list[list[str]] = []

    def _fake_popen(cmd, **_kwargs):
        popen_calls.append(list(cmd))
        return fake_proc

    monkeypatch.setattr(exiftool_runner.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(exiftool_runner.time, "sleep", lambda _seconds: None)

    try:
        cp = exiftool_runner.run_exiftool("exiftool.exe", ["-j", "a.jpg"])

        assert cp.returncode == 0
        assert cp.stdout == '{"SourceFile":"a.jpg"}\n'
        assert popen_calls == [["exiftool.exe", "-stay_open", "True", "-@", "-"]]
        assert b"-j\na.jpg\n-execute\n" in fake_proc.stdin.data
    finally:
        exiftool_runner.close_exiftool_process()

    assert b"-stay_open\nFalse\n" in fake_proc.stdin.data


def test_run_exiftool_assignments_uses_separate_sidecar_output_option(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"not really a jpeg")
    captured: dict[str, list[str]] = {}

    def _fake_run_exiftool(_executable: str, args: list[str], **_kwargs):
        captured["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, "1 image files created\n", "")

    monkeypatch.setattr(writer, "get_exiftool_executable_path", lambda: "exiftool.exe")
    monkeypatch.setattr(writer, "run_exiftool", _fake_run_exiftool)

    writer.run_exiftool_assignments(str(image_path), ["-XMP-dc:Title<=title.txt"])

    args = captured["args"]
    assert "-o" in args
    output_index = args.index("-o") + 1
    assert args[output_index] == str(tmp_path / "sample.xmp")
    assert not any(arg.startswith("-o=") for arg in args)


def test_run_exiftool_assignments_edits_existing_sidecar_directly(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "sample.jpg"
    sidecar_path = tmp_path / "sample.xmp"
    image_path.write_bytes(b"not really a jpeg")
    sidecar_path.write_text("<x:xmpmeta />", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    def _fake_run_exiftool(_executable: str, args: list[str], **_kwargs):
        captured["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, "1 image files updated\n", "")

    monkeypatch.setattr(writer, "get_exiftool_executable_path", lambda: "exiftool.exe")
    monkeypatch.setattr(writer, "run_exiftool", _fake_run_exiftool)

    writer.run_exiftool_assignments(str(image_path), ["-XMP-dc:Title<=title.txt"])

    args = captured["args"]
    assert "-o" not in args
    assert str(sidecar_path) in args
    assert str(image_path) not in args
