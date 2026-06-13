# -*- coding: utf-8 -*-
"""文件派生元数据的磁盘缓存（SQLite）。

目的：切换目录 / 重访同一目录时，避免对每张图片重新打开文件读取 EXIF/对焦块。

只缓存「文件派生」字段（exifread / exiftool 从图片文件本身读出的拍摄+对焦数据），
**不缓存** XMP sidecar / report.db 字段：
- sidecar/report 在每次加载时都会重新读取并合并（成本很低）；
- 因此用户改评分/标签（写入 sidecar，不改图片文件 mtime/size）时，本缓存天然有效，
  无需额外失效逻辑；图片文件本身被修改时，mtime/size 变化会自动判定缓存失效。

存储位置由调用方决定（通常是照片目录 ``.superpicky/meta_cache/meta_cache.db``，
无 ``.superpicky`` 作用域时回退到本机应用缓存目录）。
"""
from __future__ import annotations

import atexit
import json
import os
import sqlite3
import threading

from app_common.log import get_logger

_log = get_logger("exif_io")

# 缓存数据结构版本：当 fast_reader 输出键、或解析语义变化时自增以整体失效旧缓存。
_SCHEMA_VERSION = 1

_POOL_LOCK = threading.Lock()
_CONNECTIONS: dict[str, "sqlite3.Connection"] = {}
_DB_LOCKS: dict[str, threading.Lock] = {}


def _get_connection(db_path: str) -> tuple["sqlite3.Connection | None", "threading.Lock | None"]:
    norm_db = os.path.normpath(db_path)
    with _POOL_LOCK:
        conn = _CONNECTIONS.get(norm_db)
        lock = _DB_LOCKS.get(norm_db)
        if conn is not None and lock is not None:
            return conn, lock
        try:
            os.makedirs(os.path.dirname(norm_db), exist_ok=True)
            conn = sqlite3.connect(norm_db, check_same_thread=False)
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
            _log.debug("[meta_disk_cache] open failed db=%r err=%s", norm_db, exc)
            return None, None
        lock = threading.Lock()
        _CONNECTIONS[norm_db] = conn
        _DB_LOCKS[norm_db] = lock
        return conn, lock


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
    if conn is None or lock is None:
        return {}
    norms = list(stats.keys())
    out: dict[str, dict] = {}
    try:
        with lock:
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
        _log.debug("[meta_disk_cache] get_many failed db=%r err=%s", db_path, exc)
        return {}
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
    try:
        with lock:
            conn.executemany(
                "INSERT OR REPLACE INTO meta (path, mtime, size, schema, payload) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
    except Exception as exc:
        _log.debug("[meta_disk_cache] put_many failed db=%r err=%s", db_path, exc)
        return 0
    return len(rows)


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


atexit.register(close_all)


__all__ = [
    "get_many",
    "put_many",
    "close_all",
]
