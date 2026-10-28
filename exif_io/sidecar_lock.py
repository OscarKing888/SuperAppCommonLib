"""Reentrant, process-shared locks for complete same-stem XMP transactions."""
from __future__ import annotations

import hashlib
import errno
import os
from pathlib import Path
import sys
import threading
import time
import weakref


def data_directory() -> Path:
    override = os.environ.get("SUPERBIRD_METADATA_STORE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "SuperBirdTools" / "Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SuperBirdTools"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "SuperBirdTools"


class _SidecarLock:
    def __init__(self, identity):
        self.identity = identity
        self._mutex = threading.RLock()
        self._local = threading.local()

    def acquire(self, blocking=True, timeout=-1):
        acquired = self._mutex.acquire(blocking) if timeout == -1 else self._mutex.acquire(blocking, timeout)
        if not acquired:
            return False
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            return True
        stream = None
        try:
            directory = data_directory() / "sidecar-locks"
            directory.mkdir(parents=True, exist_ok=True)
            name = hashlib.sha256(self.identity.encode("utf-8")).hexdigest() + ".lock"
            stream = (directory / name).open("a+b")
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = None if timeout < 0 else time.monotonic() + timeout
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EBUSY, errno.EDEADLK):
                        raise
                    if not blocking or (deadline is not None and time.monotonic() >= deadline):
                        stream.close()
                        self._mutex.release()
                        return False
                    time.sleep(.02)
            self._local.stream = stream
            self._local.depth = 1
            return True
        except BaseException:
            if stream is not None:
                stream.close()
            self._mutex.release()
            raise

    def release(self):
        depth = getattr(self._local, "depth", 0)
        if not depth:
            raise RuntimeError("Cannot release an unowned sidecar lock")
        self._local.depth = depth - 1
        if depth == 1:
            stream = self._local.stream
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
                del self._local.stream
        self._mutex.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


_registry = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()
_LOCK_STRIPES = 1024


def xmp_sidecar_write_lock(path):
    # Resolve the sidecar stem, not a symlink to the source image. Idle lock
    # objects disappear from this weak registry; active transactions own them.
    stem = os.path.realpath(os.path.splitext(os.path.abspath(os.fspath(path)))[0])
    identity = os.path.normcase(stem).casefold()
    # A deterministic stripe bounds both lock objects and on-disk lock files.
    # Rare collisions only serialize unrelated writes; they cannot mix data.
    identity = str(int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16], 16) % _LOCK_STRIPES)
    with _registry_lock:
        lock = _registry.get(identity)
        if lock is None:
            lock = _registry[identity] = _SidecarLock(identity)
        return lock
