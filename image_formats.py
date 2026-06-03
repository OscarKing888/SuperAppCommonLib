# -*- coding: utf-8 -*-
"""Shared image format extension configuration for all apps."""
from __future__ import annotations

IMAGE_EXTENSION_GROUPS: dict[str, tuple[str, ...]] = {
    "jpeg": (".jpg", ".jpeg"),
    "standard": (".png", ".webp", ".tiff", ".tif"),
    "heif": (".heic", ".heif", ".hif"),
    "raw": (
        ".cr2", ".cr3", ".crw",
        ".nef", ".nrw",
        ".arw", ".srf", ".sr2",
        ".rw2", ".raw",
        ".orf", ".ori",
        ".raf",
        ".dng",
        ".pef", ".ptx",
        ".x3f",
        ".rwl",
        ".3fr", ".dcr", ".kdc", ".mef", ".mrw", ".rwz",
    ),
    "photoshop": (".psd",),
}


def _normalized_unique_extensions(groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ext.lower() for ext in groups))


JPEG_IMAGE_EXTENSIONS = frozenset(_normalized_unique_extensions(IMAGE_EXTENSION_GROUPS["jpeg"]))
STANDARD_IMAGE_EXTENSIONS = frozenset(
    _normalized_unique_extensions(IMAGE_EXTENSION_GROUPS["jpeg"] + IMAGE_EXTENSION_GROUPS["standard"])
)
HEIF_IMAGE_EXTENSIONS = frozenset(_normalized_unique_extensions(IMAGE_EXTENSION_GROUPS["heif"]))
RAW_IMAGE_EXTENSIONS = frozenset(_normalized_unique_extensions(IMAGE_EXTENSION_GROUPS["raw"]))
PHOTOSHOP_IMAGE_EXTENSIONS = frozenset(_normalized_unique_extensions(IMAGE_EXTENSION_GROUPS["photoshop"]))
PIL_IMAGE_EXTENSIONS = frozenset(STANDARD_IMAGE_EXTENSIONS | PHOTOSHOP_IMAGE_EXTENSIONS)

IMAGE_EXTENSIONS = _normalized_unique_extensions(
    tuple(ext for group in IMAGE_EXTENSION_GROUPS.values() for ext in group)
)
SUPPORTED_IMAGE_EXTENSIONS = frozenset(IMAGE_EXTENSIONS)

# Compatibility aliases used by existing modules.
STANDARD_EXTENSIONS = STANDARD_IMAGE_EXTENSIONS
HEIF_EXTENSIONS = HEIF_IMAGE_EXTENSIONS
RAW_EXTENSIONS = RAW_IMAGE_EXTENSIONS
PHOTOSHOP_EXTENSIONS = PHOTOSHOP_IMAGE_EXTENSIONS
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS
