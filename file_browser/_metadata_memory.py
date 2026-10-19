"""浏览器会话的元数据缓存；指纹 I/O 仅由读取 worker 调用。"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import os
import sys
import threading
from time import monotonic

from app_common.exif_io.xmp_sidecar import find_xmp_sidecars
from app_common.superviewer_user_options import METADATA_MEMORY_DEFAULT_MB


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _file_stamp(path: str) -> tuple | None:
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino
    except OSError:
        return None


def metadata_signatures(paths, tags, report_rows) -> dict:
    """保留共享侧车的大小写、派生文件及父目录查找语义，不解析 XML。"""
    sidecars = find_xmp_sidecars(paths)
    signatures = {}
    for path in paths:
        norm = os.path.normpath(path)
        source_stamp = _file_stamp(path)
        if source_stamp is None:
            continue  # 缺失/不可读的源文件不可成为长期命中。
        sidecar = sidecars.get(norm)
        sidecar_stamp = _file_stamp(sidecar) if sidecar else None
        if sidecar and sidecar_stamp is None:
            continue
        row = report_rows.get(norm, {})
        signatures[norm] = (
            source_stamp, (_path_key(sidecar), sidecar_stamp) if sidecar else None,
            tuple(sorted(tags)), tuple(sorted((str(k), repr(v)) for k, v in row.items())),
        )
    return signatures


def _deep_size(value, seen=None) -> int:
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_size(k, seen) + _deep_size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_deep_size(v, seen) for v in value)
    return size


@dataclass
class _Entry:
    signature: tuple
    record: dict
    touched: float
    size: int


class MetadataMemoryCache:
    """每面板独有：最多 2 万条，默认 256 MiB，闲置 15 分钟淘汰，范围外 LRU 优先。"""

    def __init__(self, *, max_entries=20_000, max_bytes=METADATA_MEMORY_DEFAULT_MB * 1024 * 1024,
                 idle_seconds=15 * 60, clock=monotonic):
        self.max_entries = max(0, int(max_entries))
        self.max_bytes = max(0, int(max_bytes))
        self.idle_seconds = max(0, float(idle_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._active = OrderedDict()
        self._inactive = OrderedDict()
        self._scope = ""
        self._bytes = 0
        self._generation = 0

    def _in_scope(self, key):
        return bool(self._scope and key.startswith(self._scope))

    def _drop(self, queue, key):
        self._bytes -= queue.pop(key).size

    def _evict_to_limits(self):
        # 调用方持锁；缩小容量和新结果写入共用同一淘汰策略。
        while (len(self._active) + len(self._inactive) > self.max_entries
               or self._bytes > self.max_bytes):
            victims = self._inactive if self._inactive else self._active
            self._drop(victims, next(iter(victims)))

    def set_max_bytes(self, max_bytes):
        """即时调整容量，不做文件 I/O；保留仍在额度内的条目和读取代次。"""
        with self._lock:
            self.max_bytes = max(0, int(max_bytes))
            self._evict_to_limits()

    def _expire(self, now):
        for queue in (self._inactive, self._active):
            while queue:
                key, entry = next(iter(queue.items()))
                if now - entry.touched < self.idle_seconds:
                    break
                self._drop(queue, key)

    def set_scope(self, directory):
        """纯内存操作；切目录不丢弃数据，也不在 GUI 线程 stat 文件。"""
        scope = _path_key(directory).rstrip(os.sep) + os.sep
        with self._lock:
            if scope == self._scope:
                return
            self._scope = scope
            entries = sorted([*self._active.items(), *self._inactive.items()],
                             key=lambda item: item[1].touched)
            self._active.clear()
            self._inactive.clear()
            for key, entry in entries:
                (self._active if self._in_scope(key) else self._inactive)[key] = entry
            self._expire(self._clock())

    def lookup(self, path, signature):
        """返回副本和写入代次；清理后迟到的旧 worker 不可重新填充缓存。"""
        key = _path_key(path)
        with self._lock:
            now = self._clock()
            self._expire(now)
            queue = self._active if self._in_scope(key) else self._inactive
            entry = queue.get(key)
            if entry is not None:
                if signature is not None and entry.signature == signature:
                    entry.touched = now
                    queue.move_to_end(key)
                    return deepcopy(entry.record), self._generation
                self._drop(queue, key)
            return None, self._generation

    def store(self, path, signature, record, generation):
        if signature is None:
            return
        key = _path_key(path)
        record = deepcopy(record)
        size = _deep_size((key, signature, record)) + 256  # 条目及有序字典节点开销。
        with self._lock:
            if generation != self._generation:
                return
            now = self._clock()
            self._expire(now)
            queue = self._active if self._in_scope(key) else self._inactive
            if key in queue:
                self._drop(queue, key)
            if size > self.max_bytes or not self.max_entries:
                return
            queue[key] = _Entry(signature, record, now, size)
            self._bytes += size
            self._evict_to_limits()

    def invalidate_scope(self, directory):
        prefix = _path_key(directory).rstrip(os.sep) + os.sep
        with self._lock:
            self._generation += 1
            for queue in (self._active, self._inactive):
                for key in list(queue):
                    if key.startswith(prefix):
                        self._drop(queue, key)

    def clear(self):
        with self._lock:
            self._generation += 1
            self._active.clear()
            self._inactive.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            self._expire(self._clock())
            return {"entries": len(self._active) + len(self._inactive), "bytes": self._bytes}
