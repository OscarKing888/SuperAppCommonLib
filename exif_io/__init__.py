# -*- coding: utf-8 -*-
"""
exif_io：EXIF 配置、exiftool 路径、EXIF 读写（exiftool + piexif）。
内含 exiftools_mac / exiftools_win。
"""
from __future__ import annotations

from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.reader import (
    extract_many,
    extract_many_with_xmp_priority,
    extract_pillow_metadata,
    extract_metadata_with_xmp_priority,
)
from app_common.exif_io.writer import (
    run_exiftool_assignments,
    run_exiftool_json,
    write_exif_with_exiftool,
    write_exif_with_exiftool_by_key,
    write_meta_with_exiftool,
    write_meta_with_piexif,
    read_batch_metadata,
    inject_metadata_cache,
    DEFAULT_METADATA_TAGS,
)
from app_common.exif_io.writer import _get_exiftool_tag_target  # 供 main 读路径使用
from app_common.exif_io.xmp_sidecar import find_xmp_sidecar, read_xmp_sidecar
from app_common.exif_io.json_sidecar import (
    DEFAULT_SUPERPICKY_SIDECAR_DIRNAME,
    JSON_SIDECAR_SUFFIX,
    central_json_sidecar_path_for,
    find_json_sidecar,
    find_nearest_superpicky_root,
    json_sidecar_candidate_paths_for,
    json_sidecar_path_for,
    json_sidecar_to_flat_dict,
    load_superpicky_sidecar_config,
    read_json_sidecar,
    sibling_json_sidecar_path_for,
    superpicky_sidecar_dir_for_root,
)
from app_common.exif_io.photo_meta import (
    PhotoMetaData,
    PhotoMetaDataEXIFEmbeded,
    PhotoMetaDataJSON,
    PhotoMetaDataXMP,
    PhotoMetaDataReportDB,
    PhotoMetaDataProxy,
    extract_exposure_settings,
    format_aperture_value,
    format_iso_value,
    format_shutter_value,
)

__all__ = [
    "get_exiftool_executable_path",
    "run_exiftool_json",
    "run_exiftool_assignments",
    "write_exif_with_exiftool",
    "write_exif_with_exiftool_by_key",
    "write_meta_with_exiftool",
    "write_meta_with_piexif",
    "_get_exiftool_tag_target",
    "find_xmp_sidecar",
    "read_xmp_sidecar",
    "JSON_SIDECAR_SUFFIX",
    "DEFAULT_SUPERPICKY_SIDECAR_DIRNAME",
    "central_json_sidecar_path_for",
    "find_json_sidecar",
    "find_nearest_superpicky_root",
    "json_sidecar_candidate_paths_for",
    "json_sidecar_path_for",
    "json_sidecar_to_flat_dict",
    "load_superpicky_sidecar_config",
    "read_json_sidecar",
    "sibling_json_sidecar_path_for",
    "superpicky_sidecar_dir_for_root",
    "read_batch_metadata",
    "inject_metadata_cache",
    "DEFAULT_METADATA_TAGS",
    "extract_many",
    "extract_many_with_xmp_priority",
    "extract_pillow_metadata",
    "extract_metadata_with_xmp_priority",
    # OOD metadata abstraction
    "PhotoMetaData",
    "PhotoMetaDataEXIFEmbeded",
    "PhotoMetaDataJSON",
    "PhotoMetaDataXMP",
    "PhotoMetaDataReportDB",
    "PhotoMetaDataProxy",
    "extract_exposure_settings",
    "format_aperture_value",
    "format_iso_value",
    "format_shutter_value",
]
