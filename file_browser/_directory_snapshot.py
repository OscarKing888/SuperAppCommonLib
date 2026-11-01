"""Bounded directory snapshots for cached-first browsing, independent of validation.

Memory uses the browser's existing metadata budget. Persistent snapshots contain
only a listing, never pending edits, and may always be rebuilt by a scan.
"""
from __future__ import annotations

import hashlib
import os

from app_common.exif_io import metadata_sqlite

_NAMESPACE = "directory-v1"
_SIGNATURE = (1,)
_CHUNK_SIZE = 512


class DirectorySnapshotCache:
    def __init__(self, memory_cache):
        self.memory = memory_cache

    @staticmethod
    def _key(directory, recursive, include_videos, use_report_db):
        return os.path.join(os.path.abspath(directory), ".metadata-directory-snapshot",
                            f"{int(recursive)}-{int(include_videos)}-{int(use_report_db)}")

    def peek(self, directory, recursive, *, include_videos=False, use_report_db=True):
        key = self._key(directory, recursive, include_videos, use_report_db)
        return self.memory.lookup(key, _SIGNATURE)[0]

    def load(self, directory, recursive, *, include_videos=False, use_report_db=True):
        """Worker-only SQLite read; no source file or directory access."""
        generation = self.generation()
        options = dict(include_videos=include_videos, use_report_db=use_report_db)
        cached = self.peek(directory, recursive, **options)
        if cached is not None:
            return cached
        key = self._key(directory, recursive, include_videos, use_report_db)
        disk = metadata_sqlite.get_cache()
        header = disk.get_many(_NAMESPACE, {key: _SIGNATURE}).get(key)
        if not header:
            return None
        try:
            count = int(header["count"])
            parts = int(header["parts"])
            token = header["token"]
            if count < 0 or parts != (count + _CHUNK_SIZE - 1) // _CHUNK_SIZE:
                return None
            keys = [key + f"/{token}/{index}" for index in range(parts)]
            chunks = disk.get_many(_NAMESPACE, {part: _SIGNATURE for part in keys})
            if len(chunks) != parts:
                return None
            files = [path for part in keys for path in chunks[part]["files"]]
            if len(files) != count or any(not isinstance(path, str) for path in files):
                return None
        except (KeyError, TypeError, ValueError):
            return None
        snapshot = {"files": files, "report_root": header.get("report_root"),
                    "report_cache": {}, "report_row_by_path": {}, "metadata": {}}
        if not self.memory.generation_is_current(generation[0]):
            return None
        self.remember(directory, recursive, snapshot, **options, generation=generation)
        return snapshot

    def remember(self, directory, recursive, snapshot, *, include_videos=False,
                 use_report_db=True, persist=False, generation=None):
        key = self._key(directory, recursive, include_videos, use_report_db)
        generation = self.generation() if generation is None else generation
        if not self.memory.generation_is_current(generation[0]):
            return
        self.memory.store_with_mapping(key, _SIGNATURE, snapshot, generation[0],
                                       field="metadata", keys=snapshot.get("files", ()))
        if not persist:
            return
        files = snapshot.get("files", [])
        # Content-addressed parts plus the header are committed in one queue
        # transaction. Dropped/backpressured writes cannot publish half a list.
        token = hashlib.sha256(metadata_sqlite.encode(files).encode("utf-8")).hexdigest()[:20]
        parts = [files[start:start + _CHUNK_SIZE] for start in range(0, len(files), _CHUNK_SIZE)]
        items = {key + f"/{token}/{index}": (_SIGNATURE, {"files": chunk}, None)
                 for index, chunk in enumerate(parts)}
        items[key] = (_SIGNATURE, {"count": len(files), "parts": len(parts), "token": token,
                                  "report_root": snapshot.get("report_root")}, None)
        disk = metadata_sqlite.get_cache()
        disk.put_many(_NAMESPACE, items, generation[1])

    def generation(self):
        """扫描开始时取得令牌；删除后迟到的扫描不可重新持久化旧列表。"""
        return self.memory.generation(), metadata_sqlite.get_cache().revision()

    def remove_paths(self, paths):
        """仅失效包含删除路径的目录快照，保留其它照片的元数据条目。"""
        directories = set()
        for path in paths:
            directory = os.path.dirname(os.path.abspath(path))
            while directory not in directories:
                directories.add(directory)
                parent = os.path.dirname(directory)
                if parent == directory:
                    break
                directory = parent
        disk = metadata_sqlite.get_cache()
        for directory in directories:
            scope = os.path.join(directory, ".metadata-directory-snapshot")
            self.memory.invalidate_scope(scope)
            disk.invalidate_scope(scope)

    def touch(self, directory, recursive, *, include_videos=False, use_report_db=True):
        key = self._key(directory, recursive, include_videos, use_report_db)
        return self.memory.touch(key, _SIGNATURE)[0]

    def update_metadata(self, directory, recursive, updates, *, include_videos=False, use_report_db=True):
        key = self._key(directory, recursive, include_videos, use_report_db)
        return self.memory.patch_mapping(key, _SIGNATURE, updates, field="metadata")
