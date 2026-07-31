from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from app_common.exif_io import exiftool_runner
from app_common.exif_io import writer
from app_common.exif_io.exiftool_path import get_exiftool_executable_path


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
            self.stdout = _PipeOut([b'{"SourceFile":"a.jpg"}\n', b"{ready1}\n"])
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

    try:
        cp = exiftool_runner.run_exiftool("exiftool.exe", ["-j", "a.jpg"])

        assert cp.returncode == 0
        assert cp.stdout == '{"SourceFile":"a.jpg"}\n'
        assert popen_calls == [["exiftool.exe", "-stay_open", "True", "-@", "-"]]
        assert b"-j\na.jpg\n-execute1\n" in fake_proc.stdin.data
    finally:
        exiftool_runner.close_exiftool_process()

    assert b"-stay_open\nFalse\n" in fake_proc.stdin.data


def test_stay_open_timeout_kills_process_and_next_command_restarts(monkeypatch) -> None:
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

    class _BlockingPipe:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def readline(self) -> bytes:
            self.closed.wait()
            return b""

        def close(self) -> None:
            self.closed.set()

    class _PipeOut:
        def __init__(self, lines: list[bytes]) -> None:
            self.lines = list(lines)

        def readline(self) -> bytes:
            return self.lines.pop(0) if self.lines else b""

        def close(self) -> None:
            pass

    class _FakeProc:
        def __init__(self, stdout) -> None:
            self.stdin = _PipeIn()
            self.stdout = stdout
            self.stderr = _PipeOut([])
            self.killed = False

        def poll(self):
            return 1 if self.killed else None

        def wait(self, timeout=None):
            if not self.killed and isinstance(self.stdout, _BlockingPipe):
                raise subprocess.TimeoutExpired("exiftool", timeout)
            return 0

        def kill(self) -> None:
            self.killed = True
            self.stdout.close()

        def terminate(self) -> None:
            self.kill()

    hung = _FakeProc(_BlockingPipe())
    restarted = _FakeProc(_PipeOut([b"ok\n", b"{ready2}\n"]))
    processes = iter([hung, restarted])
    monkeypatch.setattr(
        exiftool_runner.subprocess,
        "Popen",
        lambda _cmd, **_kwargs: next(processes),
    )

    try:
        timed_out = exiftool_runner.run_exiftool(
            "exiftool.exe",
            ["-j", "slow.raw"],
            timeout=0.02,
        )
        recovered = exiftool_runner.run_exiftool(
            "exiftool.exe",
            ["-ver"],
            timeout=1.0,
        )
    finally:
        exiftool_runner.close_exiftool_process()

    assert timed_out.returncode == 1
    assert "timed out" in timed_out.stderr
    assert hung.killed is True
    assert recovered.returncode == 0
    assert recovered.stdout == "ok\n"
    assert b"-execute2\n" in restarted.stdin.data


def test_close_cancels_waiting_stay_open_command(monkeypatch) -> None:
    exiftool_runner.close_exiftool_process()

    class _Pipe:
        def __init__(self) -> None:
            self.closed = threading.Event()
            self.data = b""

        def write(self, payload: bytes) -> None:
            self.data += payload

        def flush(self) -> None:
            pass

        def readline(self) -> bytes:
            self.closed.wait()
            return b""

        def close(self) -> None:
            self.closed.set()

    class _FakeProc:
        def __init__(self) -> None:
            self.stdin = _Pipe()
            self.stdout = _Pipe()
            self.stderr = _Pipe()
            self.stopped = False

        def poll(self):
            return 1 if self.stopped else None

        def wait(self, timeout=None):
            if not self.stopped:
                raise subprocess.TimeoutExpired("exiftool", timeout)
            return 0

        def terminate(self) -> None:
            self.stopped = True
            self.stdout.close()
            self.stderr.close()

        def kill(self) -> None:
            self.terminate()

    fake_proc = _FakeProc()
    monkeypatch.setattr(
        exiftool_runner.subprocess,
        "Popen",
        lambda _cmd, **_kwargs: fake_proc,
    )
    result_holder = []
    worker = threading.Thread(
        target=lambda: result_holder.append(
            exiftool_runner.run_exiftool(
                "exiftool.exe",
                ["-j", "blocked.raw"],
                timeout=30.0,
            )
        )
    )
    worker.start()
    deadline = time.monotonic() + 1.0
    while b"-execute1\n" not in fake_proc.stdin.data and time.monotonic() < deadline:
        time.sleep(0.005)

    exiftool_runner.close_exiftool_process()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert result_holder and result_holder[0].returncode == 1
    assert "cancelled" in result_holder[0].stderr


def test_closed_manager_cannot_restart_an_orphan_process(monkeypatch) -> None:
    manager = exiftool_runner._StayOpenExifTool("exiftool.exe")
    manager.close()
    monkeypatch.setattr(
        exiftool_runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("closed manager restarted ExifTool"),
    )

    result = manager.execute(["-ver"])

    assert result.returncode == 1
    assert "closed" in result.stderr


def test_caller_cancel_event_cancels_only_command_and_allows_restart(monkeypatch) -> None:
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

    class _BlockingPipe:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def readline(self) -> bytes:
            self.closed.wait()
            return b""

        def close(self) -> None:
            self.closed.set()

    class _PipeOut:
        def __init__(self, lines: list[bytes]) -> None:
            self.lines = list(lines)

        def readline(self) -> bytes:
            return self.lines.pop(0) if self.lines else b""

        def close(self) -> None:
            pass

    class _FakeProc:
        def __init__(self, stdout) -> None:
            self.stdin = _PipeIn()
            self.stdout = stdout
            self.stderr = _PipeOut([])
            self.killed = False

        def poll(self):
            return 1 if self.killed else None

        def wait(self, timeout=None):
            if not self.killed and isinstance(self.stdout, _BlockingPipe):
                raise subprocess.TimeoutExpired("exiftool", timeout)
            return 0

        def kill(self) -> None:
            self.killed = True
            self.stdout.close()

        def terminate(self) -> None:
            self.kill()

    blocked = _FakeProc(_BlockingPipe())
    restarted = _FakeProc(_PipeOut([b"13.55\n", b"{ready2}\n"]))
    processes = iter([blocked, restarted])
    monkeypatch.setattr(
        exiftool_runner.subprocess,
        "Popen",
        lambda _cmd, **_kwargs: next(processes),
    )
    cancel_event = threading.Event()
    result_holder = []
    worker_thread = threading.Thread(
        target=lambda: result_holder.append(
            exiftool_runner.run_exiftool(
                "exiftool.exe",
                ["-j", "blocked.raw"],
                timeout=30.0,
                cancel_event=cancel_event,
            )
        )
    )
    worker_thread.start()
    deadline = time.monotonic() + 1.0
    while b"-execute1\n" not in blocked.stdin.data and time.monotonic() < deadline:
        time.sleep(0.005)
    cancel_event.set()
    worker_thread.join(timeout=1.0)

    try:
        restarted_result = exiftool_runner.run_exiftool(
            "exiftool.exe",
            ["-ver"],
            timeout=1.0,
        )
    finally:
        exiftool_runner.close_exiftool_process()

    assert not worker_thread.is_alive()
    assert result_holder and result_holder[0].returncode == 1
    assert "cancelled" in result_holder[0].stderr
    assert blocked.killed is True
    assert restarted_result.returncode == 0
    assert restarted_result.stdout == "13.55\n"


def test_utf8_assignment_temp_file_content_and_finally_cleanup() -> None:
    temp_path = ""
    with pytest.raises(RuntimeError, match="probe"):
        with exiftool_runner.utf8_safe_exiftool_assignments(
            ["-XMP-dc:Title=ASCII", "-XMP-dc:Description=中文说明\n第二行"]
        ) as assignments:
            assert assignments[0] == "-XMP-dc:Title=ASCII"
            prefix, temp_path = assignments[1].split("<=", 1)
            assert prefix == "-XMP-dc:Description"
            assert Path(temp_path).read_text(encoding="utf-8") == "中文说明\n第二行"
            raise RuntimeError("probe")

    assert temp_path
    assert not Path(temp_path).exists()


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


def test_writer_never_edits_parent_or_derived_stem_sidecar(monkeypatch, tmp_path) -> None:
    source_sidecar = tmp_path / "IMG_0001.xmp"
    derived_dir = tmp_path / "exports"
    derived_dir.mkdir()
    derived_image = derived_dir / "IMG_0001-DxO_DeepPRIME.jpg"
    source_sidecar.write_text("<x:xmpmeta />", encoding="utf-8")
    derived_image.write_bytes(b"not really a jpeg")
    captured: dict[str, list[str]] = {}

    def _fake_run_exiftool(_executable: str, args: list[str], **_kwargs):
        captured["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, "1 image files created\n", "")

    monkeypatch.setattr(writer, "get_exiftool_executable_path", lambda: "exiftool.exe")
    monkeypatch.setattr(writer, "run_exiftool", _fake_run_exiftool)

    writer.run_exiftool_assignments(str(derived_image), ["-XMP-dc:Title=derived"])

    args = captured["args"]
    assert str(source_sidecar) not in args
    assert "-o" in args
    assert args[args.index("-o") + 1] == str(derived_dir / "IMG_0001-DxO_DeepPRIME.xmp")


def test_real_exiftool_chinese_assignment_round_trip(tmp_path) -> None:
    executable = get_exiftool_executable_path()
    if not executable:
        pytest.skip("bundled/system ExifTool is unavailable")
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow is unavailable")

    image_path = tmp_path / "中文样图.jpg"
    sidecar_path = tmp_path / "中文样图.xmp"
    Image.new("RGB", (2, 2), "white").save(image_path, "JPEG")

    try:
        writer.run_exiftool_assignments(
            str(image_path),
            ["-XMP-dc:Description=中文描述：翠鸟"],
        )
        records = writer.run_exiftool_json(str(sidecar_path))
    finally:
        exiftool_runner.close_exiftool_process()

    assert sidecar_path.is_file()
    assert records
    assert any(
        value == "中文描述：翠鸟"
        for record in records
        for key, value in record.items()
        if key.lower().endswith(":description")
    )
