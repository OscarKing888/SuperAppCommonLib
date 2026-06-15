# -*- coding: utf-8 -*-
"""Shared ExifTool process runner.

The high-frequency metadata paths use ExifTool's stay-open mode so the GUI
does not pay process startup cost for every batch.  Single-shot helpers still
exist for binary-output commands that are easier to keep isolated.
"""
from __future__ import annotations

import atexit
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from typing import Any


_CREATE_NO_WINDOW = 0x08000000
_STDERR_ERROR_MARKERS = ("error:", "no writable tags set")
_STDOUT_ERROR_MARKERS = (
    "weren't updated due to errors",
    "were not updated due to errors",
)


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs that keep Windows GUI apps from flashing a console."""
    if not sys.platform.startswith("win"):
        return {}
    kwargs: dict[str, Any] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW),
    }
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs


def run_exiftool_once(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a one-off ExifTool command with the project's hidden-window policy."""
    merged = hidden_subprocess_kwargs()
    merged.update(kwargs)
    return subprocess.run(list(cmd), **merged)


class _StayOpenExifTool:
    def __init__(self, executable_path: str) -> None:
        self.executable_path = str(executable_path)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_chunks: list[bytes] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    def execute(
        self,
        args: Sequence[str],
        *,
        text: bool = True,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> subprocess.CompletedProcess:
        command_args = [str(arg) for arg in args]
        with self._lock:
            try:
                proc = self._ensure_started_locked()
                self._take_stderr()
                stdin = proc.stdin
                stdout = proc.stdout
                if stdin is None or stdout is None:
                    self.close()
                    return _completed(command_args, 1, b"", b"ExifTool pipes are unavailable", text, encoding, errors)

                payload = b"".join(_encode_arg(arg) + b"\n" for arg in command_args)
                payload += b"-execute\n"
                stdin.write(payload)
                stdin.flush()

                stdout_chunks: list[bytes] = []
                while True:
                    line = stdout.readline()
                    if not line:
                        stderr = self._take_stderr()
                        self.close()
                        return _completed(command_args, 1, b"".join(stdout_chunks), stderr, text, encoding, errors)
                    if line in (b"{ready}\n", b"{ready}\r\n"):
                        break
                    stdout_chunks.append(line)

                time.sleep(0.01)
                stdout_data = b"".join(stdout_chunks)
                stderr_data = self._take_stderr()
                returncode = 1 if _looks_like_error(stdout_data, stderr_data, encoding, errors) else 0
                return _completed(command_args, returncode, stdout_data, stderr_data, text, encoding, errors)
            except Exception as exc:
                self.close()
                return _completed(command_args, 1, b"", str(exc).encode(encoding, errors=errors), text, encoding, errors)

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            try:
                if proc.stdin is not None:
                    proc.stdin.write(b"-stay_open\nFalse\n")
                    proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            finally:
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        if pipe is not None:
                            pipe.close()
                    except Exception:
                        pass

    def _ensure_started_locked(self) -> subprocess.Popen[bytes]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._proc = subprocess.Popen(
            [self.executable_path, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **hidden_subprocess_kwargs(),
        )
        self._stderr_chunks = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True)
        self._stderr_thread.start()
        return self._proc

    def _drain_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = stream.readline()
                if not chunk:
                    break
                with self._stderr_lock:
                    self._stderr_chunks.append(chunk)
        except Exception:
            return

    def _take_stderr(self) -> bytes:
        with self._stderr_lock:
            data = b"".join(self._stderr_chunks)
            self._stderr_chunks.clear()
        return data


def _encode_arg(arg: str) -> bytes:
    return str(arg).encode("utf-8", errors="surrogateescape")


def _completed(
    args: Sequence[str],
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    text: bool,
    encoding: str,
    errors: str,
) -> subprocess.CompletedProcess:
    if text:
        return subprocess.CompletedProcess(
            list(args),
            returncode,
            stdout.decode(encoding, errors=errors),
            stderr.decode(encoding, errors=errors),
        )
    return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


def _looks_like_error(stdout: bytes, stderr: bytes, encoding: str, errors: str) -> bool:
    stderr_text = stderr.decode(encoding, errors=errors).lower()
    stdout_text = stdout.decode(encoding, errors=errors).lower()
    return (
        any(marker in stderr_text for marker in _STDERR_ERROR_MARKERS)
        or any(marker in stdout_text for marker in _STDOUT_ERROR_MARKERS)
    )


_manager_lock = threading.RLock()
_manager: _StayOpenExifTool | None = None


def run_exiftool(
    executable_path: str,
    args: Sequence[str],
    *,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> subprocess.CompletedProcess:
    """Execute one ExifTool command through the shared stay-open process."""
    global _manager
    with _manager_lock:
        if _manager is None or _manager.executable_path != str(executable_path):
            if _manager is not None:
                _manager.close()
            _manager = _StayOpenExifTool(str(executable_path))
        manager = _manager
    return manager.execute(args, text=text, encoding=encoding, errors=errors)


def close_exiftool_process() -> None:
    """Stop the shared ExifTool stay-open process if it is running."""
    global _manager
    with _manager_lock:
        manager = _manager
        _manager = None
    if manager is not None:
        manager.close()


atexit.register(close_exiftool_process)
