# -*- coding: utf-8 -*-
"""进程内快速元数据读取器（不依赖 exiftool 子进程）。

文件浏览器列表加载时，绝大多数文件是相机直出的 RAW/JPEG/TIFF。
对这些文件，``exifread`` 只读取文件头部的 TIFF/IFD 结构即可一次性拿到：

- 标准拍摄参数：快门、光圈、ISO、焦距、镜头、机型、拍摄时间、尺寸、方向；
- Sony MakerNote 对焦块（``0x2027`` / ``0x204A`` / ``0x2037``），用于列表缩略图角标焦点框。

实测（参见提交说明）：单张 ARW 约 24ms，HEIF 经 EXIF blob 约 3ms，
对比 exiftool 子进程批量约 77ms/张、HEIF 约 40ms/张，且对焦框结果与 exiftool 完全一致。

设计约束：
1. 本模块只负责「文件内」拍摄/对焦字段。用户可写元数据（标题/评分/Pick/标签/
   锐度/美学/对焦状态）仍由 XMP sidecar 解析负责，二者在
   ``MetadataLoader._read_metadata_batch`` 中合并；
2. 输出 exiftool ``-G1`` 风格的平坦字典，复用 ``focus_calc`` / ``_browser_core`` 现有候选键；
3. 字段不齐或格式不在快速路径授权集内的文件，由 ``fast_read_browser_metadata`` 标记为
   ``incomplete``，交回 exiftool 兜底（保持零回归）。
"""
from __future__ import annotations

import os
from pathlib import Path

from app_common.image_formats import (
    JPEG_IMAGE_EXTENSIONS,
    RAW_IMAGE_EXTENSIONS,
)
from app_common.log import get_logger

try:
    import exifread
except ImportError:  # pragma: no cover - optional dependency missing
    exifread = None

_log = get_logger("exif_io")

# 快速路径授权的扩展名：exifread 可在进程内完整覆盖其拍摄/对焦字段。
# TIFF 与 JPEG/RAW 共享同一套 IFD 解析。
# HEIF/HIF 暂不纳入：Sony 把显示方向写在 MakerNote CameraOrientation，exifread 无法从
# HEIF 的 EXIF blob 取到该字段，竖拍 HIF 的对焦框会错位，故继续走 exiftool 兜底。
# 其余格式（PNG/WEBP/PSD）也留给 exiftool。
_TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})
FAST_READER_EXTENSIONS = frozenset(
    RAW_IMAGE_EXTENSIONS | JPEG_IMAGE_EXTENSIONS | _TIFF_EXTENSIONS
)

# 判定「拍摄字段是否到手」用的键；任意命中即认为该文件可跳过 exiftool。
_CAPTURE_PRESENCE_KEYS = (
    "ExposureTime",
    "FNumber",
    "ISO",
    "FocalLength",
    "DateTimeOriginal",
    "Model",
)


def _tag_printable(tag) -> str:
    printable = getattr(tag, "printable", None)
    if printable not in (None, ""):
        return str(printable)
    values = getattr(tag, "values", None)
    if values not in (None, []):
        return str(values)
    return str(tag)


def _tag_first_int(tag) -> int | None:
    values = getattr(tag, "values", None)
    if isinstance(values, (list, tuple)) and values:
        try:
            return int(values[0])
        except (TypeError, ValueError):
            return None
    try:
        return int(str(getattr(tag, "printable", "")).strip())
    except (TypeError, ValueError):
        return None


def _tag_int_list(tag) -> list[int]:
    values = getattr(tag, "values", None)
    if not isinstance(values, (list, tuple)):
        return []
    out: list[int] = []
    for item in values:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            return []
    return out


def _join_numbers(numbers: list[int]) -> str:
    return " ".join(str(n) for n in numbers)


def _decode_byte_pairs_le(numbers: list[int]) -> list[int] | None:
    """Sony MakerNote 0x2037(FocusFrameSize) 以 UNDEFINED 字节序列存储，按小端 uint16 重组。"""
    if not numbers or len(numbers) % 2 != 0:
        return None
    if any(n < 0 or n > 255 for n in numbers):
        return None
    return [numbers[i] + numbers[i + 1] * 256 for i in range(0, len(numbers), 2)]


# exifread 标签名 -> 输出键。值取 printable 字符串，交由 _browser_core 的格式化函数解析。
_PRINTABLE_KEY_MAP: dict[str, tuple[str, ...]] = {
    "Image Make": ("Make",),
    "Image Model": ("Model", "IFD0:Model", "EXIF:Model"),
    "EXIF ExposureTime": ("ExposureTime", "EXIF:ExposureTime"),
    "EXIF FNumber": ("FNumber", "EXIF:FNumber"),
    "EXIF ISOSpeedRatings": ("ISO", "ISOSpeedRatings", "EXIF:ISO"),
    "EXIF PhotographicSensitivity": ("ISO", "PhotographicSensitivity"),
    "EXIF FocalLength": ("FocalLength", "EXIF:FocalLength"),
    "EXIF FocalLengthIn35mmFilm": ("FocalLengthIn35mmFormat",),
    "EXIF LensModel": ("LensModel", "EXIF:LensModel"),
    "MakerNote LensModel": ("LensModel",),
    "EXIF DateTimeOriginal": ("DateTimeOriginal", "EXIF:DateTimeOriginal"),
    "Image ImageDescription": ("ImageDescription", "IFD0:ImageDescription"),
}


def _flatten_exifread_tags(path: str, tags: dict) -> dict:
    """将 exifread 解析结果转换为 exiftool -G1 风格平坦字典（仅拍摄/对焦字段）。"""
    rec: dict = {"SourceFile": path}
    if not isinstance(tags, dict):
        return rec

    for src_key, out_keys in _PRINTABLE_KEY_MAP.items():
        tag = tags.get(src_key)
        if tag is None:
            continue
        text = _tag_printable(tag)
        if not text:
            continue
        for out_key in out_keys:
            rec.setdefault(out_key, text)

    # 方向必须使用数值（printable 是 "Horizontal (normal)" 之类文本，无法用于焦点框旋转）。
    orientation_tag = tags.get("Image Orientation")
    if orientation_tag is not None:
        orientation_num = _tag_first_int(orientation_tag)
        if orientation_num is not None:
            rec["Orientation"] = orientation_num
            rec["IFD0:Orientation"] = orientation_num

    # 焦点坐标空间尺寸（Sony MakerNote 对焦块以该尺寸为基准）。
    for src_key, out_key in (
        ("EXIF ExifImageWidth", "ExifImageWidth"),
        ("EXIF ExifImageLength", "ExifImageHeight"),
    ):
        tag = tags.get(src_key)
        if tag is None:
            continue
        num = _tag_first_int(tag)
        if num is not None:
            rec[out_key] = num

    # Sony 对焦块：0x2027 -> FocusLocation, 0x204A -> FocusLocation2。
    for src_key, out_key in (
        ("MakerNote Tag 0x2027", "FocusLocation"),
        ("MakerNote Tag 0x204A", "FocusLocation2"),
    ):
        tag = tags.get(src_key)
        if tag is None:
            continue
        numbers = _tag_int_list(tag)
        if numbers:
            rec.setdefault(out_key, _join_numbers(numbers))

    # 0x2037 -> FocusFrameSize（按小端 uint16 重组的对焦框像素尺寸）。
    ffs_tag = tags.get("MakerNote Tag 0x2037")
    if ffs_tag is not None:
        decoded = _decode_byte_pairs_le(_tag_int_list(ffs_tag))
        if decoded:
            rec.setdefault("FocusFrameSize", _join_numbers(decoded))

    return rec


def _read_exifread_tags_from_file(path: str) -> dict | None:
    if exifread is None:
        return None
    try:
        with open(path, "rb") as handle:
            tags = exifread.process_file(handle, details=True, extract_thumbnail=False)
    except Exception:
        return None
    return tags if isinstance(tags, dict) and tags else None


def _record_has_capture_fields(rec: dict) -> bool:
    return any(str(rec.get(key) or "").strip() for key in _CAPTURE_PRESENCE_KEYS)


def fast_read_one(path: str) -> dict | None:
    """读取单个文件的拍摄/对焦字段；不支持的格式或读取失败返回 None。"""
    ext = Path(path).suffix.lower()
    if ext not in FAST_READER_EXTENSIONS:
        return None
    tags = _read_exifread_tags_from_file(path)
    if not tags:
        return None
    rec = _flatten_exifread_tags(path, tags)
    if not _record_has_capture_fields(rec):
        return None
    return rec


def fast_read_browser_metadata(paths: list[str]) -> tuple[dict[str, dict], list[str]]:
    """批量进程内读取拍摄/对焦字段。

    返回 ``(records, incomplete_paths)``：
    - ``records``: ``{normpath: flat_dict}``，仅含成功取到拍摄字段的文件；
    - ``incomplete_paths``: 原始路径列表，需 exiftool 兜底（格式不支持、读取失败或字段不齐）。
    """
    records: dict[str, dict] = {}
    incomplete: list[str] = []
    if exifread is None:
        return records, list(paths)
    for path in paths:
        try:
            rec = fast_read_one(path)
        except Exception as exc:  # pragma: no cover - defensive
            _log.debug("[fast_reader] read failed path=%r err=%s", path, exc)
            rec = None
        if rec is None:
            incomplete.append(path)
            continue
        records[os.path.normpath(path)] = rec
    return records, incomplete


__all__ = [
    "FAST_READER_EXTENSIONS",
    "fast_read_one",
    "fast_read_browser_metadata",
]
