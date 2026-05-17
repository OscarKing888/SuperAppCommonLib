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
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
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

_XMP_META_NS = "adobe:ns:meta/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_XMP_DC_SUBJECT_TAG = f"{{{_DC_NS}}}subject"
_RDF_DESCRIPTION_TAG = f"{{{_RDF_NS}}}Description"
_RDF_BAG_TAG = f"{{{_RDF_NS}}}Bag"
_RDF_LI_TAG = f"{{{_RDF_NS}}}li"
_RDF_ABOUT_ATTR = f"{{{_RDF_NS}}}about"
_RDF_RESOURCE_ATTR = f"{{{_RDF_NS}}}resource"

_XMP_SUBJECT_KEYS: frozenset[str] = frozenset({
    "xmp-dc:subject",
    "xmp-dc:subjects",
    "xmp:subject",
    "xmp:subjects",
    "subject",
    "subjects",
    "keywords",
    "iptc:keywords",
})


def _is_xmp_subject_key(key: str) -> bool:
    return str(key or "").strip().lower() in _XMP_SUBJECT_KEYS


def _normalise_text_values(values: Iterable[Any], *, split_strings: bool = False) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        parts = text.split(";") if split_strings else [text]
        for part in parts:
            clean = part.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            result.append(clean)
    return result


def _normalise_subject_value(value: Any, *, split_strings: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return _normalise_text_values([value], split_strings=split_strings)
    return _normalise_text_values(value, split_strings=split_strings)


class PhotoMetaDataXMP(PhotoMetaData):
    """Reads metadata from XMP sidecar files.

    Generic field writes still use exiftool when available.  Keyword-style
    custom tags are exposed as ``dc:subject`` and can be read/written directly
    in the sidecar XML, independent of report.db.
    """

    SUBJECT_KEYS = _XMP_SUBJECT_KEYS

    def sidecar_path_for(self, path: str) -> Path:
        """Return the existing or default sidecar path for ``path``."""
        try:
            from .xmp_sidecar import find_xmp_sidecar
            found = find_xmp_sidecar(path)
            if found:
                return Path(found)
        except Exception:
            pass
        return Path(os.path.splitext(os.path.normpath(path))[0] + ".xmp")

    def read(self, path: str) -> dict[str, Any]:
        try:
            from .xmp_sidecar import read_xmp_sidecar
            rows = read_xmp_sidecar(path)
            if not rows:
                return {}
            rec: dict[str, Any] = {"SourceFile": path}
            for group, name, value in rows:
                rec[f"{group}:{name}"] = value
                if group == "XMP-dc" and str(name).lower() == "subject":
                    rec["XMP-dc:Subject"] = value
            return rec
        except Exception:
            return {}

    def write(self, path: str, fields: dict[str, Any]) -> bool:
        """Write fields into the XMP sidecar (creates/updates ``<stem>.xmp``)."""
        if not fields:
            return True

        subject_seen = False
        subject_values: list[str] = []
        remaining_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if _is_xmp_subject_key(key):
                subject_seen = True
                subject_values.extend(_normalise_subject_value(value, split_strings=True))
            else:
                remaining_fields[key] = value

        success = True
        if subject_seen:
            success = self.write_subjects(path, subject_values) and success
        if not remaining_fields:
            return success

        try:
            from .exiftool_path import get_exiftool_executable_path
            et = get_exiftool_executable_path()
            if not et:
                return False
            xmp_path = str(self.sidecar_path_for(path))
            # exiftool: write to sidecar only
            assignments = [f"-{k}={v}" for k, v in remaining_fields.items()]
            # We write to the sidecar by passing the image path and using -o
            import subprocess
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

    def read_subjects(self, path: str) -> list[str]:
        """Read XMP ``dc:subject`` values as an ordered, de-duplicated list."""
        sidecar_path = self.sidecar_path_for(path)
        if not sidecar_path.is_file():
            return []
        try:
            root = ET.parse(sidecar_path).getroot()
        except Exception:
            return []

        values: list[str] = []
        for desc in root.iter(_RDF_DESCRIPTION_TAG):
            attr_value = desc.attrib.get(_XMP_DC_SUBJECT_TAG)
            if attr_value:
                values.extend(_normalise_subject_value(attr_value, split_strings=True))
            for child in desc:
                if child.tag != _XMP_DC_SUBJECT_TAG:
                    continue
                values.extend(self._subject_values_from_element(child))
        return _normalise_text_values(values)

    def write_subjects(self, path: str, subjects: Iterable[Any]) -> bool:
        """Replace XMP ``dc:subject`` values in the sidecar.

        Existing non-subject XMP properties are preserved.  Empty ``subjects``
        removes the ``dc:subject`` node while leaving the sidecar itself intact.
        """
        clean_subjects = _normalise_text_values(subjects)
        sidecar_path = self.sidecar_path_for(path)
        try:
            tree = self._load_or_create_xmp_tree(sidecar_path)
            if tree is None:
                return False
            root = tree.getroot()
            desc = self._ensure_description(root)
            self._replace_subject_node(desc, clean_subjects)
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            return self._write_tree_atomic(tree, sidecar_path)
        except Exception:
            return False

    @staticmethod
    def _new_xmp_tree() -> ET.ElementTree:
        ET.register_namespace("x", _XMP_META_NS)
        ET.register_namespace("rdf", _RDF_NS)
        ET.register_namespace("dc", _DC_NS)
        root = ET.Element(f"{{{_XMP_META_NS}}}xmpmeta")
        rdf = ET.SubElement(root, f"{{{_RDF_NS}}}RDF")
        ET.SubElement(rdf, _RDF_DESCRIPTION_TAG, {_RDF_ABOUT_ATTR: ""})
        return ET.ElementTree(root)

    @classmethod
    def _load_or_create_xmp_tree(cls, sidecar_path: Path) -> ET.ElementTree | None:
        ET.register_namespace("x", _XMP_META_NS)
        ET.register_namespace("rdf", _RDF_NS)
        ET.register_namespace("dc", _DC_NS)
        if not sidecar_path.exists():
            return cls._new_xmp_tree()
        try:
            return ET.parse(sidecar_path)
        except ET.ParseError:
            return None

    @staticmethod
    def _ensure_description(root: ET.Element) -> ET.Element:
        desc = root.find(f".//{_RDF_DESCRIPTION_TAG}")
        if desc is not None:
            return desc

        rdf = root if root.tag == f"{{{_RDF_NS}}}RDF" else root.find(f".//{{{_RDF_NS}}}RDF")
        if rdf is None:
            rdf = ET.SubElement(root, f"{{{_RDF_NS}}}RDF")
        return ET.SubElement(rdf, _RDF_DESCRIPTION_TAG, {_RDF_ABOUT_ATTR: ""})

    @staticmethod
    def _replace_subject_node(desc: ET.Element, subjects: list[str]) -> None:
        for child in list(desc):
            if child.tag == _XMP_DC_SUBJECT_TAG:
                desc.remove(child)
        if not subjects:
            return

        subject = ET.SubElement(desc, _XMP_DC_SUBJECT_TAG)
        bag = ET.SubElement(subject, _RDF_BAG_TAG)
        for value in subjects:
            item = ET.SubElement(bag, _RDF_LI_TAG)
            item.text = value

    @staticmethod
    def _subject_values_from_element(element: ET.Element) -> list[str]:
        values: list[str] = []
        for container_tag in ("Bag", "Seq", "Alt"):
            container = element.find(f"{{{_RDF_NS}}}{container_tag}")
            if container is None:
                continue
            for item in container.findall(_RDF_LI_TAG):
                value = item.text or item.attrib.get(_RDF_RESOURCE_ATTR) or item.attrib.get("resource")
                if value:
                    values.append(value)
            return _normalise_text_values(values)
        if element.text and element.text.strip():
            return _normalise_subject_value(element.text, split_strings=True)
        return []

    @staticmethod
    def _write_tree_atomic(tree: ET.ElementTree, sidecar_path: Path) -> bool:
        if hasattr(ET, "indent"):
            ET.indent(tree, space="  ")
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{sidecar_path.name}.",
            suffix=".tmp",
            dir=str(sidecar_path.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
            os.replace(tmp_path, sidecar_path)
            return True
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

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

    RAW_READ_FIELDS: frozenset[str] = frozenset({
        "rating",
        "pick",
        "bird_species_cn",
        "bird_species_en",
        "burst_id",
        "burst_position",
    })

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

    def _path_key(self, path: str) -> str:
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.normpath(os.path.abspath(path)))
        except Exception:
            return os.path.normcase(os.path.normpath(path))

    def _row_candidate_paths(self, row: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        base_dir = self._report_root or ""
        for key in ("current_path", "_current_path_report_raw", "original_path"):
            text = str((row or {}).get(key) or "").strip()
            if not text:
                continue
            text = text.replace("\\", os.sep).replace("/", os.sep)
            if os.path.isabs(text):
                candidates.append(os.path.normpath(text))
            elif base_dir:
                candidates.append(os.path.normpath(os.path.join(base_dir, text)))
            else:
                candidates.append(os.path.normpath(text))

        current_text = str((row or {}).get("current_path") or "").strip().replace("\\", os.sep).replace("/", os.sep)
        original_text = str((row or {}).get("original_path") or "").strip().replace("\\", os.sep).replace("/", os.sep)
        original_ext = Path(original_text).suffix if original_text else ""
        if current_text and original_ext:
            try:
                current_full = current_text if os.path.isabs(current_text) else os.path.join(base_dir, current_text)
                candidates.append(os.path.normpath(str(Path(current_full).with_suffix(original_ext))))
            except Exception:
                pass
        return candidates

    def _row_matches_path(self, row: dict[str, Any], path: str) -> bool:
        stem = Path(path).stem if path else ""
        if stem and str((row or {}).get("filename") or "").strip() == stem:
            return True
        for key in ("current_path", "_current_path_report_raw", "original_path"):
            text = str((row or {}).get(key) or "").strip()
            if text and Path(text.replace("\\", os.sep).replace("/", os.sep)).stem == stem:
                return True
        target_key = self._path_key(path)
        return bool(target_key and any(self._path_key(candidate) == target_key for candidate in self._row_candidate_paths(row)))

    def _row_for(self, path: str) -> dict[str, Any] | None:
        stem = Path(path).stem

        # Fast path: in-memory cache
        if self._cache is not None:
            cached = self._cache.get(stem)
            if cached is not None:
                return cached
            for row in self._cache.values():
                if isinstance(row, dict) and self._row_matches_path(row, path):
                    return row
            return None

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
            try:
                row = db.get_photo(stem)
                if row is not None:
                    return row
                for candidate in db.get_all_photos():
                    if isinstance(candidate, dict) and self._row_matches_path(candidate, path):
                        return candidate
                return None
            finally:
                db.close()
        except Exception:
            return None

    def _filename_for_path(self, path: str) -> str:
        row = self._row_for(path)
        if isinstance(row, dict):
            filename = str(row.get("filename") or "").strip()
            if filename:
                return filename
        return Path(path).stem

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
            # Also carry raw DB fields that UI layers read directly.
            for key in self.RAW_READ_FIELDS:
                val = row.get(key)
                if val is not None and (not isinstance(val, str) or val.strip()):
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
        filename = self._filename_for_path(path)
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
            try:
                if not db.update_photo(filename, fields):
                    db.insert_photo({"filename": filename, **fields})
            finally:
                db.close()
            # Keep in-memory cache in sync
            if self._cache is not None:
                row = dict(self._cache.get(filename) or {})
                row["filename"] = filename
                row.update(fields)
                self._cache[filename] = row
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
    "burst_id", "burst_position",
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
