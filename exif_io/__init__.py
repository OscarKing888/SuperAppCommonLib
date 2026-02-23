# -*- coding: utf-8 -*-
"""
exif_io：EXIF 配置、exiftool 路径、EXIF 读写（exiftool + piexif）。
内含 exiftools_mac / exiftools_win。
"""
from __future__ import annotations

from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.writer import (
    run_exiftool_assignments,
    run_exiftool_json,
    write_exif_with_exiftool,
    write_exif_with_exiftool_by_key,
    write_meta_with_exiftool,
    write_meta_with_piexif,
)
from app_common.exif_io.writer import _get_exiftool_tag_target  # 供 main 读路径使用

__all__ = [
    "get_exiftool_executable_path",
    "run_exiftool_json",
    "run_exiftool_assignments",
    "write_exif_with_exiftool",
    "write_exif_with_exiftool_by_key",
    "write_meta_with_exiftool",
    "write_meta_with_piexif",
    "_get_exiftool_tag_target",
]
