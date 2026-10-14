"""Bounded in-memory image cache and optional disposable proxy cache on disk."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import logging
import os
from pathlib import Path
import tempfile
import threading
import zipfile

import numpy as np

from .model import canonical_json


class ImageCache:
    def __init__(self, directory=None, *, memory_bytes=512*1024**2, disk_bytes=1024**3):
        self.directory = Path(directory) if directory else None
        self.memory_bytes = max(0,int(memory_bytes))
        self.disk_bytes = max(0,int(disk_bytes))
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    @staticmethod
    def key(parts):
        return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()

    def get(self, key):
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
                return value
        if self.directory is None or not self.disk_bytes:
            return None
        path = self.directory/(key+".npz")
        try:
            # Only small proxies are persisted. Refuse unexpectedly expanded or
            # pickled contents before NumPy allocates an untrusted cache entry.
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > 20*1024**2:
                    return None
            with np.load(path,allow_pickle=False) as stored:
                rgba = stored["rgba"]
                size = stored["size"]
                if (rgba.dtype != np.float32 or rgba.ndim != 3 or rgba.shape[2] != 4
                        or max(rgba.shape[:2]) > 1024 or min(rgba.shape[:2]) <= 0
                        or size.shape != (2,) or not np.issubdtype(size.dtype,np.integer)
                        or (size <= 0).any() or (size > 64_000_000).any()
                        or int(size[0])*int(size[1]) > 64_000_000
                        or not np.isfinite(rgba).all() or (rgba < 0).any() or (rgba > 1).any()):
                    return None
                value = (rgba,tuple(int(v) for v in size))
            os.utime(path,None)
            self.put(key,*value)
            return value
        except (OSError,ValueError,KeyError,zipfile.BadZipFile,EOFError):
            return None

    def put(self, key, rgba, size, *, persist=False):
        rgba.setflags(write=False)
        with self._lock:
            previous = self._items.pop(key,None)
            if previous is not None:
                self._bytes -= previous[0].nbytes
            if rgba.nbytes <= self.memory_bytes:
                self._items[key] = (rgba,size)
                self._bytes += rgba.nbytes
            while self._bytes > self.memory_bytes:
                _,(old,_) = self._items.popitem(last=False)
                self._bytes -= old.nbytes
        if not persist or self.directory is None or not self.disk_bytes or max(rgba.shape[:2]) > 1024:
            return
        name = None
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            fd,name = tempfile.mkstemp(prefix=".proxy-",suffix=".tmp",dir=self.directory)
            with os.fdopen(fd,"wb") as stream:
                np.savez_compressed(stream,rgba=rgba,size=np.asarray(size,dtype=np.int64))
            os.replace(name,self.directory/(key+".npz"))
            self.trim_disk()
        except OSError as exc:
            # A disposable disk cache must not prevent editing or export.
            logging.getLogger(__name__).warning("Photo edit proxy cache unavailable: %s",exc)
        finally:
            if name is not None:
                try:
                    Path(name).unlink(missing_ok=True)
                except OSError:
                    pass

    def trim_disk(self):
        if self.directory is None:
            return
        entries = []
        for path in self.directory.glob("*.npz"):
            try:
                stat = path.stat()
                entries.append((stat.st_mtime_ns,path,stat.st_size))
            except OSError:
                continue
        total = sum(size for _,_,size in entries)
        for _,path,size in sorted(entries):
            if total <= self.disk_bytes:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                continue

    def clear(self, *, disk=False):
        with self._lock:
            self._items.clear()
            self._bytes = 0
        if disk and self.directory is not None:
            for path in self.directory.glob("*.npz"):
                if len(path.stem)==64 and all(c in "0123456789abcdef" for c in path.stem):
                    path.unlink(missing_ok=True)
