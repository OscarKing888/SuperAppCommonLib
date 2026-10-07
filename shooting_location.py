# -*- coding: utf-8 -*-
"""独立的拍摄地点文本；只写 XMP 自定义字段，不读取或改写 GPS。"""
from __future__ import annotations

import os
from pathlib import Path

from .exif_io.photo_meta import PhotoMetaDataXMP
from .image_formats import SUPPORTED_IMAGE_EXTENSIONS

LOCATION_FIELD = "shooting_location"
LOCATION_TAG = f"XMP-superpicky:{LOCATION_FIELD}"


def shooting_location(metadata: dict | None) -> str:
    for key in (LOCATION_TAG, LOCATION_FIELD, f"report.{LOCATION_FIELD}"):
        if key in (metadata or {}):
            # 空值是有效的清除操作，不回退到旧缓存。
            return str(metadata[key] or "").strip()
    return ""


def location_updates(text: str) -> dict[str, str]:
    value = str(text or "").strip()
    return {LOCATION_TAG: value, LOCATION_FIELD: value, f"report.{LOCATION_FIELD}": value}


def write_shooting_location(path: str, text: str) -> dict[str, str]:
    """保存/清空地点并返回缓存更新；失败不报告成功，也不创建孤立侧车。"""
    source = Path(path)
    if not source.is_file() or source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"照片不存在或格式不支持：{path}")
    updates = location_updates(text)
    if not PhotoMetaDataXMP().write_superpicky_fields(os.fspath(source), {LOCATION_FIELD: updates[LOCATION_FIELD]}):
        raise OSError(f"拍摄地点写入失败，原有 XMP 已保留：{path}")
    return updates
