# -*- coding: utf-8 -*-
"""
OOD metadata abstraction layer for SuperViewer.

Hierarchy
---------
PhotoMetaData (ABC)
├── PhotoMetaDataEXIFEmbeded  – embedded EXIF via exiftool / Pillow fallback
├── PhotoMetaDataXMP           – XMP sidecar files (.xmp)
└── PhotoMetaDataReportDB      – SuperPicky report.db (SQLite)

PhotoMetaDataProxy             – composite; merges all three with priority
                                 ReportDB > XMP > EXIF (for reads)
                                 routes writes to appropriate backend(s)

All `read()` methods return an **exiftool-G1-style flat dict** so callers do
not need to care about the underlying source.  Existing functions in
``reader.py``, ``writer.py``, ``xmp_sidecar.py`` and ``report_db.py`` are
**not modified** — this module is purely additive.
"""
from __future__ import annotations

import abc
import os
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class PhotoMetaData(abc.ABC):
    """Abstract base class for a single photo-metadata source."""

    @abc.abstractmethod
    def read(self, path: str) -> dict[str, Any]:
        """Read metadata for one file.

        Returns an exiftool-G1-style flat dict (may be empty ``{}``).
        Always includes ``"SourceFile"`` when non-empty.
        """
        ...

    def read_batch(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        """Read metadata for multiple files.

        Default implementation calls :meth:`read` per file; subclasses may
        override with a faster batched implementation.

        Returns ``{normpath(path): flat_dict}``.
        """
        return {os.path.normpath(p): self.read(p) for p in paths}

    @abc.abstractmethod
    def write(self, path: str, fields: dict[str, Any]) -> bool:
        """Write metadata fields to this source.

        Returns ``True`` on success, ``False`` if not supported or failed.
        """
        ...

    def supports_write(self) -> bool:
        """Whether this source can write metadata (override to return True)."""
        return False


# ---------------------------------------------------------------------------
# EXIF Embedded (exiftool + Pillow fallback)
# ---------------------------------------------------------------------------

class PhotoMetaDataEXIFEmbeded(PhotoMetaData):
    """Reads metadata embedded in the image file (exiftool or Pillow).

    Writes back via exiftool assignments (``-Tag=value`` syntax).
    """

    def __init__(self, mode: str = "auto") -> None:
        """
        Parameters
        ----------
        mode:
            ``"auto"`` – use exiftool if available, else Pillow;
            ``"on"``   – require exiftool;
            ``"off"``  – Pillow only.
        """
        self._mode = mode

    def read(self, path: str) -> dict[str, Any]:
        try:
            from .reader import extract_metadata_with_xmp_priority
            return extract_metadata_with_xmp_priority(path, mode=self._mode) or {}
        except Exception:
            return {}

    def read_batch(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        """Batch-optimised read via ``read_batch_metadata`` (single exiftool call)."""
        try:
            from .writer import read_batch_metadata
            raw = read_batch_metadata(paths)
            # Normalise keys so callers always use normpath
            return {os.path.normpath(k): v for k, v in raw.items()}
        except Exception:
            return super().read_batch(paths)

    def write(self, path: str, fields: dict[str, Any]) -> bool:
        """Write arbitrary exiftool-style tag assignments to the embedded EXIF."""
        if not fields:
            return True
        assignments = [f"-{k}={v}" for k, v in fields.items()]
        try:
            from .writer import run_exiftool_assignments
            run_exiftool_assignments(path, assignments)
            return True
        except Exception:
            return False

    def supports_write(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# XMP Sidecar
# ---------------------------------------------------------------------------

class PhotoMetaDataXMP(PhotoMetaData):
    """Reads metadata from XMP sidecar files; writes via exiftool ``-o``."""

    def read(self, path: str) -> dict[str, Any]:
        try:
            from .xmp_sidecar import read_xmp_sidecar
            rows = read_xmp_sidecar(path)
            if not rows:
                return {}
            rec: dict[str, Any] = {"SourceFile": path}
            for group, name, value in rows:
                rec[f"{group}:{name}"] = value
            return rec
        except Exception:
            return {}

    def write(self, path: str, fields: dict[str, Any]) -> bool:
        """Write fields into the XMP sidecar (creates/updates ``<stem>.xmp``)."""
        if not fields:
            return True
        try:
            from .exiftool_path import get_exiftool_executable_path
            from .writer import run_exiftool_assignments
            et = get_exiftool_executable_path()
            if not et:
                return False
            stem = os.path.splitext(os.path.normpath(path))[0]
            xmp_path = f"{stem}.xmp"
            # exiftool: write to sidecar only
            assignments = [f"-{k}={v}" for k, v in fields.items()]
            # We write to the sidecar by passing the image path and using -o
            import subprocess, tempfile
            all_args = assignments + [f"-o={xmp_path}", os.path.normpath(path)]
            fd, argfile = tempfile.mkstemp(suffix=".args", prefix="et_xmp_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for a in all_args:
                        f.write(a + "\n")
                cp = subprocess.run(
                    [et, "-@", argfile],
                    capture_output=True, check=False,
                )
                return cp.returncode == 0
            finally:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
        except Exception:
            return False

    def supports_write(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# ReportDB
# ---------------------------------------------------------------------------

class PhotoMetaDataReportDB(PhotoMetaData):
    """Reads / writes metadata stored in a SuperPicky ``report.db``.

    Two modes:
    * **Cache mode** – supply ``cache`` (a ``stem → row_dict`` mapping already
      loaded by ``DirectoryScanWorker``).  All reads are O(1) in-memory lookups.
    * **DB mode** – supply ``report_root`` (directory containing ``.superpicky``).
      Each :meth:`read` opens the DB for a single ``get_photo`` query.
      Prefer cache mode for bulk reads inside the file browser.

    Call :meth:`update_cache` / :meth:`update_report_root` when the active
    directory changes.
    """

    def __init__(
        self,
        report_root: str | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._report_root = report_root
        self._cache = cache  # stem → row_dict (may be None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_for(self, path: str) -> dict[str, Any] | None:
        stem = Path(path).stem

        # Fast path: in-memory cache
        if self._cache is not None:
            return self._cache.get(stem)

        # Slow path: open DB directly
        root = self._report_root
        if not root:
            try:
                from app_common.report_db import find_report_root
                root = find_report_root(os.path.dirname(path), max_levels=4)
            except Exception:
                return None
        if not root:
            return None
        try:
            from app_common.report_db import ReportDB
            db = ReportDB.open_if_exists(root)
            if db is None:
                return None
            row = db.get_photo(stem)
            db.close()
            return row
        except Exception:
            return None

    # ------------------------------------------------------------------
    # PhotoMetaData interface
    # ------------------------------------------------------------------

    def read(self, path: str) -> dict[str, Any]:
        row = self._row_for(path)
        if not row:
            return {}
        try:
            from app_common.report_db import report_row_to_exiftool_style
            flat = report_row_to_exiftool_style(row, path)
            # Also carry raw DB fields that UI layers read directly (e.g. bird species)
            for key in ("bird_species_cn", "bird_species_en"):
                val = str(row.get(key) or "").strip()
                if val:
                    flat[key] = val
            return flat
        except Exception:
            return {}

    def read_batch(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        """Batch read – O(n) in-memory when cache is populated."""
        return {os.path.normpath(p): self.read(p) for p in paths}

    def write(self, path: str, fields: dict[str, Any]) -> bool:
        """Insert-or-update the DB row for this file's stem with ``fields``."""
        if not fields:
            return True
        stem = Path(path).stem
        root = self._report_root
        if not root:
            try:
                from app_common.report_db import find_report_root
                root = find_report_root(os.path.dirname(path), max_levels=4)
            except Exception:
                return False
        if not root:
            return False
        try:
            from app_common.report_db import ReportDB
            db = ReportDB.open_if_exists(root)
            if db is None:
                return False
            db.insert_photo({"filename": stem, **fields})
            db.close()
            # Keep in-memory cache in sync
            if self._cache is not None:
                row = dict(self._cache.get(stem) or {})
                row.update(fields)
                self._cache[stem] = row
            return True
        except Exception:
            return False

    def supports_write(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # State update helpers (call when active directory changes)
    # ------------------------------------------------------------------

    def update_cache(self, cache: dict[str, dict[str, Any]] | None) -> None:
        """Replace the in-memory stem-cache (e.g. after DirectoryScanWorker finishes)."""
        self._cache = cache

    def update_report_root(self, report_root: str | None) -> None:
        """Update the report root directory (e.g. after navigating to a new folder)."""
        self._report_root = report_root


# ---------------------------------------------------------------------------
# Proxy (composite)
# ---------------------------------------------------------------------------

# Fields that belong exclusively to report.db (not embedded in the image file).
# When the proxy routes a write, these go to ReportDB; everything else to EXIF/XMP.
_REPORT_DB_ONLY_FIELDS: frozenset[str] = frozenset({
    "rating", "pick",
    "has_bird", "confidence",
    "head_sharp", "left_eye", "right_eye", "beak",
    "nima_score", "is_flying", "flight_conf",
    "focus_status", "focus_x", "focus_y",
    "adj_sharpness", "adj_topiq",
    "bird_species_cn", "bird_species_en", "birdid_confidence",
    "exposure_status",
})


class PhotoMetaDataProxy(PhotoMetaData):
    """Composite metadata source that merges ReportDB, XMP sidecar and embedded EXIF.

    Read priority (highest → lowest)
    ---------------------------------
    1. **ReportDB** – curated ratings, species, focus, AI scores
    2. **XMP sidecar** – Lightroom-compatible tags (Title, Rating, Label …)
    3. **EXIF embedded** – camera-original (ISO, shutter, GPS …)

    Write routing
    -------------
    * ``_REPORT_DB_ONLY_FIELDS`` → :class:`PhotoMetaDataReportDB`
    * All other fields → :class:`PhotoMetaDataEXIFEmbeded`
      (XMP-prefixed keys also written to :class:`PhotoMetaDataXMP` sidecar)

    Parameters
    ----------
    exif, xmp, report_db:
        Provide pre-constructed instances to share state (e.g. the same
        ``PhotoMetaDataReportDB`` that ``DirectoryScanWorker`` updates).
        If omitted, default instances with no pre-loaded cache are used.
    """

    def __init__(
        self,
        exif: PhotoMetaDataEXIFEmbeded | None = None,
        xmp: PhotoMetaDataXMP | None = None,
        report_db: PhotoMetaDataReportDB | None = None,
    ) -> None:
        self._exif = exif or PhotoMetaDataEXIFEmbeded()
        self._xmp = xmp or PhotoMetaDataXMP()
        self._report_db = report_db or PhotoMetaDataReportDB()

    # ------------------------------------------------------------------
    # Properties for direct access to sub-sources
    # ------------------------------------------------------------------

    @property
    def exif(self) -> PhotoMetaDataEXIFEmbeded:
        return self._exif

    @property
    def xmp(self) -> PhotoMetaDataXMP:
        return self._xmp

    @property
    def report_db(self) -> PhotoMetaDataReportDB:
        return self._report_db

    # ------------------------------------------------------------------
    # PhotoMetaData interface
    # ------------------------------------------------------------------

    def read(self, path: str) -> dict[str, Any]:
        """Merge all three sources; higher-priority keys overwrite lower ones."""
        merged: dict[str, Any] = {"SourceFile": path}
        # Apply in ascending priority order so later sources win
        for source in (self._exif, self._xmp, self._report_db):
            try:
                data = source.read(path)
                if data:
                    merged.update(data)
            except Exception:
                pass
        return merged

    def read_batch(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        """Merge batch reads from all three sources (EXIF uses single exiftool call)."""
        norm_paths = [os.path.normpath(p) for p in paths]
        result: dict[str, dict[str, Any]] = {n: {"SourceFile": n} for n in norm_paths}

        # 1. EXIF (batched)
        try:
            for norm, data in self._exif.read_batch(paths).items():
                if norm in result and data:
                    result[norm].update(data)
        except Exception:
            pass

        # 2. XMP per-file
        for p, norm in zip(paths, norm_paths):
            try:
                data = self._xmp.read(p)
                if data and norm in result:
                    result[norm].update(data)
            except Exception:
                pass

        # 3. ReportDB (O(1) per file if cache loaded)
        for p, norm in zip(paths, norm_paths):
            try:
                data = self._report_db.read(p)
                if data and norm in result:
                    result[norm].update(data)
            except Exception:
                pass

        return result

    def write(self, path: str, fields: dict[str, Any]) -> bool:
        """Route fields to appropriate backends and return overall success."""
        if not fields:
            return True

        db_fields = {k: v for k, v in fields.items() if k in _REPORT_DB_ONLY_FIELDS}
        file_fields = {k: v for k, v in fields.items() if k not in _REPORT_DB_ONLY_FIELDS}
        xmp_fields = {k: v for k, v in file_fields.items() if k.upper().startswith("XMP")}

        success = True
        if db_fields:
            success = self._report_db.write(path, db_fields) and success
        if file_fields:
            success = self._exif.write(path, file_fields) and success
        if xmp_fields:
            # Also mirror XMP fields into the sidecar (best-effort, non-fatal)
            self._xmp.write(path, xmp_fields)
        return success

    def supports_write(self) -> bool:
        return True
