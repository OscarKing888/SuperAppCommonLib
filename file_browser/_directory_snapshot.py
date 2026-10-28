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
        self.remember(directory, recursive, snapshot, **options)
        return snapshot

    def remember(self, directory, recursive, snapshot, *, include_videos=False,
                 use_report_db=True, persist=False):
        key = self._key(directory, recursive, include_videos, use_report_db)
        _, generation = self.memory.touch(key, _SIGNATURE)
        self.memory.store_with_mapping(key, _SIGNATURE, snapshot, generation,
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
        disk.put_many(_NAMESPACE, items, disk.revision())

    def touch(self, directory, recursive, *, include_videos=False, use_report_db=True):
        key = self._key(directory, recursive, include_videos, use_report_db)
        return self.memory.touch(key, _SIGNATURE)[0]

    def update_metadata(self, directory, recursive, updates, *, include_videos=False, use_report_db=True):
        key = self._key(directory, recursive, include_videos, use_report_db)
        return self.memory.patch_mapping(key, _SIGNATURE, updates, field="metadata")
