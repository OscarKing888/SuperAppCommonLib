# -*- coding: utf-8 -*-
"""文件派生元数据的磁盘缓存（SQLite）。

目的：切换目录 / 重访同一目录时，避免对每张图片重新打开文件读取 EXIF/对焦块。

只缓存「文件派生」字段（exifread / exiftool 从图片文件本身读出的拍摄+对焦数据），
**不缓存** XMP sidecar / report.db 字段：
- 本模块仅是原图派生字段层；完整合并结果及解析后的 XMP 由 metadata_sqlite 缓存；
- 完整缓存未命中时，调用方重新读取/合并 sidecar/report；
- 因此用户改评分/标签（写入 sidecar，不改图片文件 mtime/size）时，本缓存天然有效，
  无需额外失效逻辑；图片文件本身被修改时，mtime/size 变化会自动判定缓存失效。

存储位置由调用方决定（通常是照片目录 ``.superpicky/meta_cache/meta_cache.db``，
无 ``.superpicky`` 作用域时回退到本机应用缓存目录）。
"""
from __future__ import annotations

import atexit
from collections import OrderedDict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
from time import monotonic

from app_common.log import get_logger

_log = get_logger("exif_io")

# 缓存数据结构版本：当 fast_reader 输出键、或解析语义变化时自增以整体失效旧缓存。
_SCHEMA_VERSION = 1

_POOL_LOCK = threading.Lock()
_CONNECTIONS: dict[str, "sqlite3.Connection"] = {}
_DB_LOCKS: dict[str, threading.Lock] = {}

# This is an optional cache: a failed disk must not serialize every browser worker.
_SQLITE_TIMEOUT = 0.05
_RETRY_SECONDS = 60.0
_MAX_FAILED_DATABASES = 128
_FAILURE_LOCK = threading.Lock()
_RETRY_AFTER: OrderedDict[str, float] = OrderedDict()


def _cooling_down(db_path: str) -> bool:
    with _FAILURE_LOCK:
        deadline = _RETRY_AFTER.get(db_path)
        if deadline is None:
            return False
        if monotonic() < deadline:
            return True
        del _RETRY_AFTER[db_path]
        return False


def _record_failure(db_path: str, exc: Exception) -> None:
    norm_db = os.path.normpath(db_path)
    with _FAILURE_LOCK:
        first = norm_db not in _RETRY_AFTER
        _RETRY_AFTER[norm_db] = monotonic() + _RETRY_SECONDS
        _RETRY_AFTER.move_to_end(norm_db)
        while len(_RETRY_AFTER) > _MAX_FAILED_DATABASES:
            _RETRY_AFTER.popitem(last=False)
    if first:
        _log.warning("[meta_disk_cache] unavailable; bypass for %.0fs db=%r err=%s",
                     _RETRY_SECONDS, norm_db, exc)


def _get_connection(db_path: str) -> tuple["sqlite3.Connection | None", "threading.Lock | None"]:
    norm_db = os.path.normpath(db_path)
    if _cooling_down(norm_db) or not _POOL_LOCK.acquire(blocking=False):
        return None, None
    try:
        if _cooling_down(norm_db):
            return None, None
        conn = _CONNECTIONS.get(norm_db)
        lock = _DB_LOCKS.get(norm_db)
        if conn is not None and lock is not None:
            return conn, lock
        try:
            # 已被改名/移走的照片目录不能被迟到的缓存写入重新创建。
            parent = Path(os.path.abspath(norm_db)).parent
            for ancestor in (parent, *parent.parents):
                if ancestor.name == ".superpicky" and not ancestor.parent.is_dir():
                    return None, None
            os.makedirs(os.path.dirname(norm_db), exist_ok=True)
            conn = sqlite3.connect(norm_db, check_same_thread=False, timeout=_SQLITE_TIMEOUT)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta ("
                "path TEXT PRIMARY KEY, "
                "mtime REAL NOT NULL, "
                "size INTEGER NOT NULL, "
                "schema INTEGER NOT NULL, "
                "payload TEXT NOT NULL)"
            )
            conn.commit()
        except Exception as exc:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            _record_failure(norm_db, exc)
            return None, None
        lock = threading.Lock()
        _CONNECTIONS[norm_db] = conn
        _DB_LOCKS[norm_db] = lock
        return conn, lock
    finally:
        _POOL_LOCK.release()


def get_many(
    db_path: str,
    stats: dict[str, tuple[float, int]],
) -> dict[str, dict]:
    """返回 ``stats`` 中 mtime/size 与缓存一致的条目 ``{norm: payload_dict}``。

    ``stats``: ``{normpath: (mtime, size)}``。
    """
    if not db_path or not stats:
        return {}
    conn, lock = _get_connection(db_path)
    if conn is None or lock is None or not lock.acquire(blocking=False):
        return {}
    norms = list(stats.keys())
    out: dict[str, dict] = {}
    try:
        for start in range(0, len(norms), 500):
            chunk = norms[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT path, mtime, size, schema, payload FROM meta WHERE path IN ({placeholders})",
                chunk,
            ).fetchall()
            for path, mtime, size, schema, payload in rows:
                expected = stats.get(path)
                if expected is None or int(schema) != _SCHEMA_VERSION:
                    continue
                exp_mtime, exp_size = expected
                if int(size) != int(exp_size) or abs(float(mtime) - float(exp_mtime)) > 1e-6:
                    continue
                try:
                    rec = json.loads(payload)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    out[path] = rec
    except Exception as exc:
        _record_failure(db_path, exc)
        return {}
    finally:
        lock.release()
    return out


def put_many(
    db_path: str,
    items: dict[str, tuple[float, int, dict]],
) -> int:
    """写入/更新缓存条目。``items``: ``{normpath: (mtime, size, payload_dict)}``。"""
    if not db_path or not items:
        return 0
    conn, lock = _get_connection(db_path)
    if conn is None or lock is None:
        return 0
    rows = []
    for norm, value in items.items():
        try:
            mtime, size, payload = value
            rows.append((norm, float(mtime), int(size), _SCHEMA_VERSION, json.dumps(payload, ensure_ascii=False)))
        except Exception:
            continue
    if not rows:
        return 0
    if not lock.acquire(blocking=False):
        return 0
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO meta (path, mtime, size, schema, payload) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        _record_failure(db_path, exc)
        return 0
    finally:
        lock.release()
    return len(rows)


@contextmanager
def directory_file_operation(directory):
    """短暂释放目录内缓存句柄，并在文件系统操作期间阻止重新打开。"""
    root = Path(os.path.abspath(directory))
    with _POOL_LOCK:
        for path in list(_CONNECTIONS):
            if Path(os.path.abspath(path)).is_relative_to(root):
                with _DB_LOCKS.pop(path):
                    _CONNECTIONS.pop(path).close()
        yield


def close_all() -> None:
    """关闭所有连接（应用退出或测试清理时调用）。"""
    with _POOL_LOCK:
        for conn in _CONNECTIONS.values():
            try:
                conn.close()
            except Exception:
                pass
        _CONNECTIONS.clear()
        _DB_LOCKS.clear()
        with _FAILURE_LOCK:
            _RETRY_AFTER.clear()


atexit.register(close_all)


__all__ = [
    "get_many",
    "put_many",
    "close_all",
    "directory_file_operation",
]
