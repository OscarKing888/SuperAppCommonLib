# -*- coding: utf-8 -*-
"""Lightweight RAW embedded focus metadata reader.

TIFF-based RAW uses ExifRead. Canon uses a focused, read-only ExifTool query:
ExifRead cannot read CR3's container or newer Canon AFInfo arrays. Call from
owned metadata/focus workers only, never from held-navigation GUI callbacks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app_common.canon_focus import CANON_FOCUS_TAGS
from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool
from app_common.image_formats import RAW_IMAGE_EXTENSIONS
from app_common.exif_io.tiff_reader import read_tiff_exif_tags
from app_common.log import get_logger

_log = get_logger("raw_focus_metadata")

try:
    import exifread
except ImportError:  # pragma: no cover - exercised when optional dep is absent
    exifread = None


def is_raw_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in RAW_IMAGE_EXTENSIONS


def _tag_value(tag_obj: Any) -> Any:
    values = getattr(tag_obj, "values", None)
    if values not in (None, []):
        return values
    printable = getattr(tag_obj, "printable", None)
    if printable not in (None, ""):
        return printable
    return str(tag_obj)


def _should_keep_focus_tag(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    if not lowered:
        return False
    return (
        lowered in {
            "image make",
            "image model",
            "image orientation",
            "exif exifimagewidth",
            "exif exifimagelength",
        }
        or lowered.startswith("makernote tag 0x2027")
        or lowered.startswith("makernote tag 0x204a")
        or ("focus" in lowered)
        or ("subject" in lowered)
        or ("region" in lowered)
    )


def _read_canon_focus_metadata(path: str | Path) -> dict[str, object]:
    executable = get_exiftool_executable_path()
    if not executable:
        return {}
    try:
        result = run_exiftool(
            executable,
            ["-j", "-G1", "-n", "-a", "-charset", "filename=UTF8",
             "-api", "largefilesupport=1", "-Make", "-Model", "-Orientation",
             "-ExifImageWidth", "-ExifImageHeight", *CANON_FOCUS_TAGS, str(path)],
            text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        if result.returncode != 0:
            _log.warning("Canon focus read failed path=%r: %s", str(path), result.stderr)
            return {}
        payload = json.loads(result.stdout)
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
    except Exception as exc:
        _log.warning("Canon focus read failed path=%r: %s", str(path), exc)
    return {}


def read_raw_embedded_focus_metadata(path: str | Path) -> dict[str, object]:
    """Read embedded camera focus fields; never consult or write sidecars."""
    if not is_raw_image_path(path):
        return {}
    if Path(path).suffix.lower() in {".cr3", ".cr2", ".crw"}:
        return _read_canon_focus_metadata(path)
    if exifread is None:
        return {}
    try:
        with open(path, "rb") as handle:
            tags = read_tiff_exif_tags(handle)
            if tags is None:
                handle.seek(0)
                tags = exifread.process_file(handle, details=True, extract_thumbnail=False)
    except Exception:
        return {}
    if not isinstance(tags, dict) or not tags:
        return {}

    out: dict[str, object] = {"SourceFile": str(path)}
    for key, tag in tags.items():
        if not _should_keep_focus_tag(str(key)):
            continue
        out[str(key)] = _tag_value(tag)

    if "Image Make" in out:
        out.setdefault("Make", out["Image Make"])
    if "Image Model" in out:
        out.setdefault("Model", out["Image Model"])
    if "Image Orientation" in out:
        out.setdefault("Orientation", out["Image Orientation"])
    if "EXIF ExifImageWidth" in out:
        out.setdefault("ExifImageWidth", out["EXIF ExifImageWidth"])
    if "EXIF ExifImageLength" in out:
        out.setdefault("ExifImageHeight", out["EXIF ExifImageLength"])

    # Canon DNG 等 TIFF 容器也可能只有基础字段，不能将其误判成已取得 AF 信息。
    make = str(out.get("Make", "")).lower()
    model = str(out.get("Model", "")).lower()
    if "canon" in make or "canon" in model:
        out.update(_read_canon_focus_metadata(path))
    return out


__all__ = [
    "is_raw_image_path",
    "read_raw_embedded_focus_metadata",
]
