from __future__ import annotations

import os
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

import pytest

from app_common.exif_io import exiftool_runner as runner
from app_common.exif_io.exiftool_path import get_exiftool_executable_path


def test_concurrent_close_waits_for_actual_process_exit(monkeypatch):
    runner.close_exiftool_process()
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()

    class Process:
        stdin = stdout = stderr = None

        def terminate(self):
            entered.set()
            assert release.wait(5)

        def wait(self, timeout):
            return 0

    manager = runner._StayOpenExifTool("exiftool")
    manager._proc = Process()
    monkeypatch.setattr(runner, "_manager", manager)
    first = threading.Thread(target=manager.close)
    second = threading.Thread(target=lambda: (runner.close_exiftool_process(), second_done.set()))
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        assert not second_done.wait(.1), "shutdown returned while worker still owned a live child"
        assert manager._proc is not None
        release.set()
        assert second_done.wait(2)
        assert manager._proc is None
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)


def test_final_shutdown_blocks_new_shared_and_worker_sessions(monkeypatch):
    runner.close_exiftool_process()
    monkeypatch.setattr(runner, "_shutdown", False)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("late process started"))
    runner.shutdown_exiftool_process()
    runner.shutdown_exiftool_process()
    assert "shutting down" in runner.run_exiftool("exiftool", ["-ver"]).stderr
    with runner.exiftool_worker_session(), runner.exiftool_read_request(lambda: False):
        result = runner.run_exiftool("exiftool", ["-ver"], text=False)
        assert result.returncode != 0
        assert b"shutting down" in result.stderr
        assert not runner._worker_local.session


def test_failed_close_preserves_ownership_and_still_closes_other_sessions(monkeypatch):
    runner.close_exiftool_process()

    class Session:
        def __init__(self, fail=False):
            self.fail = fail
            self.closed = False

        def close(self):
            if self.fail:
                raise OSError("injected termination failure")
            self.closed = True

    failed, other = Session(True), Session()
    monkeypatch.setattr(runner, "_manager", failed)
    monkeypatch.setattr(runner, "_read_sessions", {other})
    with pytest.raises(RuntimeError, match="shutdown failed"):
        runner.close_exiftool_process()
    assert other.closed
    assert runner._manager is failed
    failed.fail = False
    runner.close_exiftool_process()
    assert failed.closed
    assert runner._manager is None


def test_job_assignment_failure_reaps_child_and_releases_job(monkeypatch):
    class Process:
        stdin = stdout = stderr = None
        killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            assert self.killed
            return 0

    class Job:
        closed = False

        def assign(self, process):
            raise OSError("injected job assignment failure")

        def close(self):
            self.closed = True

    proc, job = Process(), Job()
    monkeypatch.setattr(runner, "_create_process_job", lambda: job)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: proc)
    manager = runner._StayOpenExifTool("exiftool")
    result = manager.execute(["-ver"])
    assert result.returncode != 0
    assert "assignment failure" in result.stderr
    assert proc.killed and job.closed
    assert manager._proc is None and manager._process_job is None


@pytest.mark.parametrize("exit_mode, use_job", [
    ("normal", True), ("shutdown", True), ("abrupt", True),
    ("normal", False), ("shutdown", False),
])
def test_real_parallel_sessions_do_not_outlive_parent(exit_mode, use_job):
    if exit_mode == "abrupt" and sys.platform != "win32":
        pytest.skip("Windows Job Object fallback")
    psutil = pytest.importorskip("psutil")
    executable = get_exiftool_executable_path()
    if not executable:
        pytest.skip("ExifTool unavailable")
    # Run a fresh interpreter: permanent shutdown and atexit must be tested at
    # the real application-process boundary, not by resetting module globals.
    script = r'''
import json, os, sys, threading
from app_common.exif_io import exiftool_runner as r
if sys.argv[3] == 'no-job':
    r._create_process_job = lambda: None
ready = threading.Barrier(25)
release = threading.Event()
pids = []
errors = []
def work():
    try:
        with r.exiftool_worker_session(), r.exiftool_read_request(lambda: False):
            result = r.run_exiftool(sys.argv[1], ['-ver'])
            assert result.returncode == 0, result.stderr
            pids.append(r._worker_local.session[sys.argv[1]]._proc.pid)
            ready.wait(20)
            release.wait(30)
    except BaseException as exc:
        errors.append(repr(exc))
        ready.abort()
threads = [threading.Thread(target=work, daemon=True) for _ in range(24)]
for thread in threads: thread.start()
try:
    ready.wait(20)
    result = r.run_exiftool(sys.argv[1], ['-ver'])
    assert result.returncode == 0, result.stderr
    pids.append(r._manager._proc.pid)
    print(json.dumps(pids), flush=True)
    # The observer holds process identities before allowing us to exit.
    sys.stdin.readline()
    if sys.argv[2] == 'abrupt': os._exit(0)
    if sys.argv[2] == 'shutdown':
        closer = threading.Thread(target=r.shutdown_exiftool_process)
        closer.start()
        release.set()
        for thread in threads: thread.join(10)
        closer.join(10)
        assert not closer.is_alive()
        assert all(not t.is_alive() for t in threads)
        assert r.run_exiftool(sys.argv[1], ['-ver']).returncode != 0
    assert not errors, errors
finally:
    # Normal exit deliberately leaves the daemon sessions alive for atexit.
    pass
'''
    repo_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=str(repo_root))
    children = []
    with subprocess.Popen(
        [sys.executable, "-c", script, executable, exit_mode, "job" if use_job else "no-job"],
        cwd=repo_root, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **runner.hidden_subprocess_kwargs(),
    ) as parent:
        try:
            # communicate has a deadline even if child setup/protocol fails.
            output = queue.Queue()
            reader = threading.Thread(target=lambda: output.put(parent.stdout.readline()), daemon=True)
            reader.start()
            line = output.get(timeout=30)
            assert line, "child failed before publishing ExifTool PIDs"
            pids = json.loads(line)
            assert len(pids) == 25
            children = [psutil.Process(pid) for pid in pids]
            parent.stdin.write("exit\n")
            parent.stdin.flush()
            _, stderr = parent.communicate(timeout=30)
            assert parent.returncode == 0, stderr
            _, alive = psutil.wait_procs(children, timeout=5)
            assert not alive, f"orphan ExifTool PIDs: {[p.pid for p in alive]}"
        finally:
            if parent.poll() is None:
                # Capture any started children even when setup failed.
                children.extend(psutil.Process(parent.pid).children(recursive=True))
                parent.kill()
                parent.wait(timeout=5)
            for child in children:
                try:
                    if child.is_running():
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(children, timeout=5)
