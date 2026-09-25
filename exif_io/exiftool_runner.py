# -*- coding: utf-8 -*-
"""Shared ExifTool process runner.

The high-frequency metadata paths use ExifTool's stay-open mode so the GUI
does not pay process startup cost for every batch.  Single-shot helpers still
exist for binary-output commands that are easier to keep isolated.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import os
import queue
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from typing import Any


_CREATE_NO_WINDOW = 0x08000000
_STDERR_ERROR_MARKERS = ("error:", "no writable tags set")
_STDOUT_ERROR_MARKERS = (
    "weren't updated due to errors",
    "were not updated due to errors",
)
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0


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
    request = getattr(_worker_local, 'request', None)
    if request is None:
        return subprocess.run(list(cmd), **merged)
    cancelled, default_timeout = request
    timeout = merged.pop('timeout', None)
    if timeout is None:
        timeout = default_timeout
    check = merged.pop('check', False)
    payload = merged.pop('input', None)
    if merged.pop('capture_output', False):
        merged['stdout'] = subprocess.PIPE
        merged['stderr'] = subprocess.PIPE
    if payload is not None:
        merged['stdin'] = subprocess.PIPE
    else:
        merged.setdefault('stdin', subprocess.DEVNULL)
    if cancelled.is_set():
        raise subprocess.CalledProcessError(1, list(cmd), stderr='ExifTool command cancelled')
    deadline = _monotonic() + float(timeout)
    with subprocess.Popen(list(cmd), **merged) as process:
        try:
            while True:
                if cancelled.is_set():
                    raise subprocess.CalledProcessError(1, list(cmd), stderr='ExifTool command cancelled')
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(list(cmd), timeout)
                try:
                    out, err = process.communicate(input=payload, timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    payload = None
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    result = subprocess.CompletedProcess(list(cmd), process.returncode, out, err)
    if check:
        result.check_returncode()
    return result


class _StayOpenExifTool:
    def __init__(self, executable_path: str) -> None:
        self.executable_path = str(executable_path)
        self._execute_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._cancel_event = threading.Event()
        self._command_sequence = 0
        self._stdout_queue: queue.Queue[bytes | None] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
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
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> subprocess.CompletedProcess:
        command_args = [str(arg) for arg in args]
        timeout_seconds = (
            _DEFAULT_COMMAND_TIMEOUT_SECONDS
            if timeout is None
            else max(0.01, float(timeout))
        )
        with self._execute_lock:
            try:
                if cancel_event is not None and cancel_event.is_set():
                    return _completed(
                        command_args,
                        1,
                        b"",
                        b"ExifTool command cancelled",
                        text,
                        encoding,
                        errors,
                    )
                with self._state_lock:
                    if self._closed:
                        return _completed(
                            command_args,
                            1,
                            b"",
                            b"ExifTool runner is closed",
                            text,
                            encoding,
                            errors,
                        )
                    self._cancel_event.clear()
                    proc = self._ensure_started_locked()
                    stdout_queue = self._stdout_queue
                    self._command_sequence += 1
                    command_id = self._command_sequence
                self._take_stderr()
                stdin = proc.stdin
                if stdin is None:
                    self.close()
                    return _completed(command_args, 1, b"", b"ExifTool pipes are unavailable", text, encoding, errors)

                payload = b"".join(_encode_arg(arg) + b"\n" for arg in command_args)
                payload += f"-execute{command_id}\n".encode("ascii")
                stdin.write(payload)
                stdin.flush()

                stdout_chunks: list[bytes] = []
                # Accumulate short monotonic waits so close() can cancel a
                # blocked command promptly instead of waiting for the timeout.
                remaining = timeout_seconds
                while True:
                    if self._cancel_event.is_set() or (
                        cancel_event is not None and cancel_event.is_set()
                    ):
                        if cancel_event is not None and cancel_event.is_set():
                            self._abort_process(proc)
                        return _completed(
                            command_args,
                            1,
                            b"".join(stdout_chunks),
                            b"ExifTool command cancelled",
                            text,
                            encoding,
                            errors,
                        )
                    wait_slice = min(0.1, remaining)
                    if wait_slice <= 0:
                        self._abort_process(proc)
                        return _completed(
                            command_args,
                            1,
                            b"".join(stdout_chunks),
                            f"ExifTool command timed out after {timeout_seconds:.2f}s".encode("utf-8"),
                            text,
                            encoding,
                            errors,
                        )
                    wait_started = _monotonic()
                    try:
                        line = stdout_queue.get(timeout=wait_slice)
                    except queue.Empty:
                        remaining -= max(0.0, _monotonic() - wait_started)
                        continue
                    remaining -= max(0.0, _monotonic() - wait_started)
                    if line is None:
                        stderr = self._take_stderr()
                        self._abort_process(proc)
                        if self._cancel_event.is_set() and not stderr:
                            stderr = b"ExifTool command cancelled"
                        return _completed(command_args, 1, b"".join(stdout_chunks), stderr, text, encoding, errors)
                    if line.strip() == f"{{ready{command_id}}}".encode("ascii"):
                        break
                    stdout_chunks.append(line)

                stdout_data = b"".join(stdout_chunks)
                stderr_data = self._take_stderr()
                returncode = 1 if _looks_like_error(stdout_data, stderr_data, encoding, errors) else 0
                return _completed(command_args, returncode, stdout_data, stderr_data, text, encoding, errors)
            except Exception as exc:
                self._abort_process(locals().get("proc"))
                return _completed(command_args, 1, b"", str(exc).encode(encoding, errors=errors), text, encoding, errors)

    def close(self) -> None:
        self._cancel_event.set()
        with self._state_lock:
            self._closed = True
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
        self._finish_process(proc)

    def _finish_process(self, proc: subprocess.Popen[bytes]) -> None:
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

    def _abort_process(self, proc: subprocess.Popen[bytes] | None) -> None:
        if proc is None:
            return
        with self._state_lock:
            if self._proc is proc:
                self._proc = None
        try:
            proc.kill()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        self._finish_process(proc)

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
        self._stdout_queue = queue.Queue()
        self._stderr_chunks = []
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout,
            args=(self._proc, self._stdout_queue),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True)
        self._stderr_thread.start()
        return self._proc

    @staticmethod
    def _drain_stdout(
        proc: subprocess.Popen[bytes],
        output_queue: queue.Queue[bytes | None],
    ) -> None:
        stream = proc.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                output_queue.put(line)
        except Exception:
            pass
        finally:
            output_queue.put(None)

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


@contextmanager
def utf8_safe_exiftool_assignments(assignments: Sequence[str]):
    """Yield assignments with non-ASCII values redirected from UTF-8 files.

    ExifTool command-line value decoding differs between packaged Windows and
    macOS builds.  ``-Tag<=file`` makes the value encoding deterministic while
    retaining existing inline arguments for ASCII-only values.
    """
    safe_assignments: list[str] = []
    temp_paths: list[str] = []
    try:
        for raw_assignment in assignments:
            assignment = str(raw_assignment)
            if "<=" in assignment or not assignment.startswith("-") or "=" not in assignment:
                safe_assignments.append(assignment)
                continue
            assignment_prefix, value = assignment.split("=", 1)
            if value.isascii() and "\r" not in value and "\n" not in value:
                safe_assignments.append(assignment)
                continue
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix="sbt-exiftool-",
                suffix=".txt",
                delete=False,
            ) as handle:
                handle.write(value)
                temp_path = handle.name
            temp_paths.append(temp_path)
            safe_assignments.append(f"{assignment_prefix}<={temp_path}")
        yield safe_assignments
    finally:
        for temp_path in temp_paths:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _monotonic() -> float:
    import time

    return time.monotonic()


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


# Browser read sessions are local to bounded worker threads. The global session
# remains the compatibility path for UI/CLI reads and metadata writes.
_worker_local = threading.local()
_read_sessions: set[_StayOpenExifTool] = set()


@contextmanager
def exiftool_worker_session():
    previous = getattr(_worker_local, 'session', None)
    if previous is not None:
        yield
        return
    sessions = {}
    _worker_local.session = sessions
    try:
        yield
    finally:
        _worker_local.session = None
        for manager in sessions.values():
            manager.close()
            with _manager_lock:
                _read_sessions.discard(manager)


class _ReadCancellation:
    def __init__(self, callback):
        self.callback = callback

    def is_set(self):
        return bool(self.callback())


@contextmanager
def exiftool_read_request(cancelled, *, timeout=20):
    previous = getattr(_worker_local, 'request', None)
    _worker_local.request = (_ReadCancellation(cancelled), timeout)
    try:
        yield
    finally:
        _worker_local.request = previous


def run_exiftool(
    executable_path: str,
    args: Sequence[str],
    *,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    timeout: float | None = None,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess:
    """Execute one ExifTool command through the shared stay-open process.

    ``cancel_event`` cancels only this command.  The in-flight stay-open
    process is discarded to keep protocol framing synchronized, and the next
    command transparently starts a fresh process.
    """
    sessions = getattr(_worker_local, 'session', None)
    request = getattr(_worker_local, 'request', None)
    if sessions is not None and request is not None:
        manager = sessions.get(str(executable_path))
        if manager is None:
            manager = _StayOpenExifTool(str(executable_path))
            sessions[str(executable_path)] = manager
            with _manager_lock:
                _read_sessions.add(manager)
        return manager.execute(args, text=text, encoding=encoding, errors=errors,
                               timeout=timeout if timeout is not None else request[1],
                               cancel_event=cancel_event if cancel_event is not None else request[0])
    global _manager
    with _manager_lock:
        if _manager is None or _manager.executable_path != str(executable_path):
            if _manager is not None:
                _manager.close()
            _manager = _StayOpenExifTool(str(executable_path))
        manager = _manager
    return manager.execute(
        args,
        text=text,
        encoding=encoding,
        errors=errors,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def close_exiftool_process() -> None:
    """Stop the shared ExifTool stay-open process if it is running."""
    global _manager
    with _manager_lock:
        manager = _manager
        _manager = None
        read_sessions = tuple(_read_sessions)
    if manager is not None:
        manager.close()
    for session in read_sessions:
        session.close()


atexit.register(close_exiftool_process)
