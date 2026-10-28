"""本机可丢弃的元数据缓存：批量读取、单写线程、XMP 指纹校验。

SQLite 不是元数据源。失败/锁冲突按未命中处理；不接触 report.db 或照片目录。
JSON 仅作为数据库内的安全序列化格式，避免不可信缓存触发 pickle 代码执行。
"""
from __future__ import annotations

import atexit
from collections import deque
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import sys
import threading
import time

from app_common.log import get_logger
from .shared_read import open_shared_read

_log = get_logger("metadata.sqlite")
XMP_NAMESPACE = "xmp-v1"
BROWSER_NAMESPACE = "browser-v1"


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


def file_stamp(path):
    try:
        st = os.stat(path)
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
    except OSError:
        return None


def sidecar_stamp(path):
    """Worker-only strong fingerprint; Windows ctime is not a change counter."""
    try:
        with open_shared_read(path) as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            after = os.fstat(stream.fileno())
        stamp = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        final = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        return (*stamp, digest) if stamp == final else None
    except OSError:
        return None


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def default_path():
    override = os.environ.get("SUPERBIRD_METADATA_CACHE_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "SuperBirdTools" / "Cache"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "SuperBirdTools"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "SuperBirdTools"
    return base / "metadata-v1.sqlite3"


class MetadataSQLiteCache:
    def __init__(self, path, *, max_bytes=512 * 1024 * 1024, max_entries=100_000,
                 pending_bytes=16 * 1024 * 1024):
        self.path = Path(path).absolute()
        self.max_bytes = max(0, int(max_bytes))
        self.max_entries = max(0, int(max_entries))
        self._pending_limit = pending_bytes
        self._pending_bytes = 0
        self._queue = queue.Queue(maxsize=1024)
        self._lock = threading.Lock()
        self._thread = None
        self._closed = False
        self._disabled = False
        self._revision = 0
        self._invalidations = deque(maxlen=4096)
        self._pending_controls = {}

    def revision(self):
        with self._lock:
            return self._revision

    @staticmethod
    def _affected(kind, target, path, dependency):
        if kind == "invalidate":
            return path.startswith(target) or bool(dependency and dependency.startswith(target))
        sidecar = os.path.splitext(target)[0] + ".xmp"
        return path == target or dependency == sidecar or os.path.splitext(path)[0] == os.path.splitext(target)[0]

    def _current(self, revision, path, dependency="", *, pending=False):
        """Called under _lock. An edit must not discard another photo's work."""
        if self._invalidations and revision < self._invalidations[0][0] - 1:
            return False
        events = [(r, k, t) for r, k, t in self._invalidations if r > revision]
        if pending:
            events.extend((self._revision, k, t) for k, t in self._pending_controls)
        return not any(self._affected(k, t, path, dependency) for _, k, t in events)

    def get_many(self, namespace, signatures):
        """只读连接不等待写队列；调用者在 metadata worker 中调用。"""
        with self._lock:
            if self._closed or self._disabled:
                return {}
            revision = self._revision
        expected = {path_key(p): (p, encode(s)) for p, s in signatures.items() if s is not None}
        if not expected:
            return {}
        result = {}
        dependencies = {}
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.03)) as conn:
                keys = list(expected)
                for start in range(0, len(keys), 400):
                    chunk = keys[start:start + 400]
                    rows = conn.execute(
                        f"SELECT path, signature, payload, dependency FROM entries WHERE namespace=? AND path IN ({','.join('?' for _ in chunk)})",
                        [namespace, *chunk],
                    )
                    for key, signature, payload, dependency in rows:
                        original, wanted = expected[key]
                        if signature != wanted:
                            continue
                        try:
                            value = json.loads(payload)
                            if isinstance(value, dict):
                                result[original] = value
                                dependencies[original] = dependency
                        except (ValueError, TypeError):
                            pass
        except (OSError, sqlite3.Error) as exc:
            _log.debug("[metadata.sqlite] read fallback path=%r error=%s", str(self.path), exc)
            return {}
        with self._lock:
            result = {p: value for p, value in result.items()
                      if self._current(revision, path_key(p), dependencies[p], pending=True)}
        if result:
            self._enqueue("touch", [(time.time_ns(), namespace, path_key(p)) for p in result], len(result) * 128)
        return result

    def _enqueue(self, kind, data, size=0, revision=None):
        with self._lock:
            if self._closed or self._disabled:
                return False
            if kind == "put":
                data = [row for row in data if self._current(revision, row[1], row[4])]
                if not data:
                    return False
            if size + self._pending_bytes > self._pending_limit:
                return False  # 缓存背压只丢缓存写入，不阻塞照片读取。
            control = kind in ("refresh", "invalidate")
            if control:
                self._revision += 1
                self._invalidations.append((self._revision, kind, data))
                key = (kind, data)
                self._pending_controls[key] = self._pending_controls.get(key, 0) + 1
            try:
                self._queue.put_nowait((kind, data, size, self._revision if revision is None else revision))
            except queue.Full:
                if control:
                    self._pending_controls[key] -= 1
                    if not self._pending_controls[key]:
                        del self._pending_controls[key]
                    self._disabled = True  # 极端编辑洪峰降级为源文件读取，绝不返回旧值。
                return False
            self._pending_bytes += size
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="metadata-sqlite-writer", daemon=True)
                self._thread.start()
            return True

    def put_many(self, namespace, items, revision):
        """items: path -> (signature, payload, resolved sidecar)。入队后由后台提交。"""
        rows = []
        size = 0
        for path, (signature, payload, dependency) in items.items():
            try:
                encoded = encode(payload)
                stamp = encode(signature)
                entry_size = len(encoded.encode("utf-8")) + len(stamp.encode("utf-8")) + len(path.encode("utf-8")) + 256
                if signature is None or entry_size > min(self.max_bytes, 2 * 1024 * 1024):
                    continue
                rows.append((namespace, path_key(path), stamp, encoded,
                             path_key(dependency) if dependency else "", time.time_ns(), entry_size))
                size += entry_size
            except (TypeError, ValueError, OverflowError):
                continue  # 不可安全序列化的值仍可使用内存缓存。
        return bool(rows) and self._enqueue("put", rows, size, revision)

    def refresh_xmp(self, image_path):
        """XMP 写入成功后的通知；此处只有内存/队列操作，不 stat 或访问 SQL。"""
        return self._enqueue("refresh", path_key(image_path))

    def invalidate_scope(self, directory):
        return self._enqueue("invalidate", path_key(directory).rstrip(os.sep) + os.sep)

    def _connect_writer(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=0.1)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA journal_size_limit=8388608")
            conn.execute("CREATE TABLE IF NOT EXISTS entries (namespace TEXT, path TEXT, signature TEXT NOT NULL, payload TEXT NOT NULL, dependency TEXT NOT NULL, touched INTEGER NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(namespace,path))")
            conn.execute("CREATE INDEX IF NOT EXISTS entries_dependency ON entries(dependency)")
            conn.execute("CREATE INDEX IF NOT EXISTS entries_age ON entries(touched)")
            conn.execute("CREATE TABLE IF NOT EXISTS totals (id INTEGER PRIMARY KEY, count INTEGER, bytes INTEGER)")
            conn.execute("INSERT OR IGNORE INTO totals SELECT 1,count(*),coalesce(sum(bytes),0) FROM entries")
            conn.execute("CREATE TRIGGER IF NOT EXISTS entries_insert AFTER INSERT ON entries BEGIN UPDATE totals SET count=count+1, bytes=bytes+new.bytes WHERE id=1; END")
            conn.execute("CREATE TRIGGER IF NOT EXISTS entries_delete AFTER DELETE ON entries BEGIN UPDATE totals SET count=count-1, bytes=bytes-old.bytes WHERE id=1; END")
            conn.execute("CREATE TRIGGER IF NOT EXISTS entries_update AFTER UPDATE ON entries BEGIN UPDATE totals SET bytes=bytes+new.bytes-old.bytes WHERE id=1; END")
            conn.commit()
            return conn
        except Exception:
            conn.close()
            raise

    @staticmethod
    def _insert(conn, rows):
        conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?) ON CONFLICT(namespace,path) DO UPDATE SET signature=excluded.signature,payload=excluded.payload,dependency=excluded.dependency,touched=excluded.touched,bytes=excluded.bytes", rows)

    def _refresh(self, conn, source):
        from .xmp_sidecar import find_xmp_sidecar, read_xmp_file
        sidecar = find_xmp_sidecar(source)
        dependency = path_key(sidecar) if sidecar else os.path.splitext(source)[0] + ".xmp"
        # RAW/JPEG 和已经登记的派生文件共享侧车；逐资源解析，保留 rdf:about 语义。
        known = [row[0] for row in conn.execute(
            "SELECT path FROM entries WHERE namespace=? AND dependency=?", (XMP_NAMESPACE, dependency))]
        conn.execute("DELETE FROM entries WHERE dependency=? OR path=?", (dependency, source))
        before = sidecar_stamp(sidecar) if sidecar else None
        if before is None:
            return
        rows = []
        for image in set([source, *known]):
            values = read_xmp_file(sidecar, image_path=image)
            if not values:
                continue
            payload = encode({"rows": values})
            signature = encode((dependency, before))
            size = len(payload.encode("utf-8")) + len(signature.encode("utf-8")) + len(image.encode("utf-8")) + 256
            if size <= min(self.max_bytes, 2 * 1024 * 1024):
                rows.append((XMP_NAMESPACE, image, signature, payload, dependency, time.time_ns(), size))
        if sidecar_stamp(sidecar) == before:
            self._insert(conn, rows)

    def _prune(self, conn):
        count, size = conn.execute("SELECT count,bytes FROM totals WHERE id=1").fetchone()
        if count <= self.max_entries and size <= self.max_bytes:
            return
        victims = []
        for namespace, path, entry_size in conn.execute("SELECT namespace,path,bytes FROM entries ORDER BY touched"):
            if count <= self.max_entries and size <= self.max_bytes:
                break
            victims.append((namespace, path))
            count -= 1
            size -= entry_size
        conn.executemany("DELETE FROM entries WHERE namespace=? AND path=?", victims)

    def _run(self):
        conn = None
        try:
            while True:
                kind, data, size, revision = self._queue.get()
                try:
                    if kind == "stop":
                        return
                    if kind == "barrier":
                        data.set()
                        continue
                    if conn is None:
                        conn = self._connect_writer()
                    if kind == "put":
                        with self._lock:
                            current = [row for row in data if self._current(revision, row[1], row[4])]
                        self._insert(conn, current)
                    elif kind == "touch":
                        conn.executemany("UPDATE entries SET touched=? WHERE namespace=? AND path=?", data)
                    elif kind == "refresh":
                        self._refresh(conn, data)
                    elif kind == "invalidate":
                        # substr 避免中文路径中的 %/_ 被 SQL LIKE 当成通配符。
                        conn.execute("DELETE FROM entries WHERE substr(path,1,?)=?", (len(data), data))
                    self._prune(conn)
                    conn.commit()
                except Exception as exc:
                    if conn is not None:
                        try:
                            conn.rollback()
                        except sqlite3.Error:
                            pass
                    if kind in ("invalidate", "refresh"):
                        with self._lock:
                            self._disabled = True
                    _log.debug("[metadata.sqlite] write fallback path=%r error=%s", str(self.path), exc)
                finally:
                    with self._lock:
                        self._pending_bytes -= size
                        if kind in ("invalidate", "refresh"):
                            key = (kind, data)
                            self._pending_controls[key] -= 1
                            if not self._pending_controls[key]:
                                del self._pending_controls[key]
                    self._queue.task_done()
        finally:
            if conn is not None:
                conn.close()

    def flush(self, timeout=5):
        event = threading.Event()
        with self._lock:
            if self._thread is None:
                return True
        try:
            self._queue.put(("barrier", event, 0, 0), timeout=timeout)
        except queue.Full:
            return False
        return event.wait(timeout)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is not None:
            try:
                self._queue.put(("stop", None, 0, 0), timeout=5)
            except queue.Full:
                _log.warning("[metadata.sqlite] shutdown queue full path=%r", str(self.path))
                return
            thread.join(timeout=5)
            if thread.is_alive():
                _log.warning("[metadata.sqlite] shutdown still draining path=%r", str(self.path))


_default = None
_default_lock = threading.Lock()


def get_cache():
    global _default
    with _default_lock:
        if _default is None:
            _default = MetadataSQLiteCache(default_path())
        return _default


def close_cache():
    global _default
    with _default_lock:
        cache, _default = _default, None
    if cache is not None:
        cache.close()


atexit.register(close_cache)
