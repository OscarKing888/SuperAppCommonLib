# -*- coding: utf-8 -*-
"""Lightweight RAW embedded focus metadata reader.

This module intentionally avoids exiftool.  It uses exifread to read the
camera MakerNote / focus tags needed by ``focus_calc`` so RAW focus overlays
can be resolved from the file's embedded metadata first.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app_common.image_formats import RAW_IMAGE_EXTENSIONS

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


def read_raw_embedded_focus_metadata(path: str | Path) -> dict[str, object]:
    """Read RAW focus metadata directly from the file without exiftool."""
    if exifread is None or not is_raw_image_path(path):
        return {}
    try:
        with open(path, "rb") as handle:
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

    return out


__all__ = [
    "is_raw_image_path",
    "read_raw_embedded_focus_metadata",
]
