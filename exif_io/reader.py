# -*- coding: utf-8 -*-
"""
EXIF 读取：全量 exiftool（-j -G1 -n -a -u）与 Pillow 回退。
供模板/预览/CLI 等需要完整 EXIF 的调用方使用；文件列表等可用 writer.read_batch_metadata（限定标签）。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

from app_common.exif_io.exiftool_path import get_exiftool_executable_path

try:
    _PIL_IFD_ENUM = ExifTags.IFD
except Exception:
    _PIL_IFD_ENUM = None

_EXIF_IFD_TAG_ID = int(getattr(_PIL_IFD_ENUM, "Exif", 34665))
_GPS_IFD_TAG_ID = int(getattr(_PIL_IFD_ENUM, "GPSInfo", 34853))
_INTEROP_IFD_TAG_ID = int(getattr(_PIL_IFD_ENUM, "Interop", 40965))


def _ratio_to_float(value: Any) -> float:
    if isinstance(value, tuple) and len(value) == 2:
        num, den = value[0], value[1]
        if den == 0:
            return 0.0
        return float(num) / float(den)
    num = getattr(value, "numerator", None)
    den = getattr(value, "denominator", None)
    if num is not None and den not in (None, 0):
        return float(num) / float(den)
    return float(value)


def _dms_to_degree(values: Any, ref: str | None) -> float | None:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None
    try:
        d = _ratio_to_float(values[0])
        m = _ratio_to_float(values[1])
        s = _ratio_to_float(values[2])
    except Exception:
        return None
    degree = d + (m / 60.0) + (s / 3600.0)
    if ref and ref.upper() in {"S", "W"}:
        degree = -degree
    return degree


def _tag_name(tag_id: Any) -> str:
    try:
        return str(ExifTags.TAGS.get(int(tag_id), str(tag_id)))
    except Exception:
        return str(tag_id)


def _load_ifd(exif, ifd_tag_id: int) -> dict | None:
    getter = getattr(exif, "get_ifd", None)
    if not callable(getter):
        return None
    try:
        data = getter(ifd_tag_id)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _merge_ifd_tags(metadata: dict[str, Any], ifd_map: dict[Any, Any], *, skip_names: set[str] | None = None) -> None:
    skip_names = skip_names or set()
    for tag_id, value in ifd_map.items():
        name = _tag_name(tag_id)
        if not name or name in skip_names:
            continue
        metadata[name] = value


def _merge_gps_ifd(metadata: dict[str, Any], gps_ifd: dict[Any, Any]) -> None:
    gps_info = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_ifd.items()}
    metadata["GPSInfo"] = gps_info
    lat = _dms_to_degree(gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef"))
    lon = _dms_to_degree(gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef"))
    if lat is not None:
        metadata["GPSLatitude"] = lat
    if lon is not None:
        metadata["GPSLongitude"] = lon


def _xmp_rows_to_flat_dict(path: Path, xmp_rows: list[tuple[str, str, str]]) -> dict[str, Any]:
    rec: dict[str, Any] = {"SourceFile": str(path)}
    for group, name, value in xmp_rows:
        rec[f"{group}:{name}"] = value
    return rec


def _overlay_xmp_aliases(merged: dict[str, Any]) -> None:
    """
    将 XMP 常见字段同步到规范键名，便于 normalize 与 GUI/CLI 复用。
    XMP 存在时优先覆盖同语义字段；缺失字段仍保留内嵌 EXIF 结果。
    """
    if not isinstance(merged, dict):
        return

    def first(*keys: str) -> Any | None:
        for key in keys:
            if key in merged and merged.get(key) not in (None, "", " "):
                return merged.get(key)
        return None

    def set_from_xmp(target: str, *xmp_keys: str) -> None:
        value = first(*xmp_keys)
        if value is not None:
            merged[target] = value

    set_from_xmp("Make", "XMP-tiff:Make")
    set_from_xmp("Model", "XMP-tiff:Model")
    set_from_xmp("DateTimeOriginal", "XMP-exif:DateTimeOriginal")
    set_from_xmp("CreateDate", "XMP-xmp:CreateDate", "XMP-exif:DateTimeDigitized")
    set_from_xmp("FNumber", "XMP-exif:FNumber")
    set_from_xmp("ExposureTime", "XMP-exif:ExposureTime")
    set_from_xmp("FocalLength", "XMP-exif:FocalLength")
    set_from_xmp("FocalLengthIn35mmFormat", "XMP-exif:FocalLengthIn35mmFormat")
    set_from_xmp("PhotographicSensitivity", "XMP-exif:PhotographicSensitivity", "XMP-exif:ISOSpeedRatings")

    iso_val = first("XMP-exif:ISOSpeedRatings", "XMP-exif:PhotographicSensitivity")
    if iso_val is not None:
        merged["ISO"] = iso_val

    lens_val = first("XMP-aux:LensModel", "XMP-aux:Lens", "XMP-exifEX:LensModel")
    if lens_val is not None:
        merged["LensModel"] = lens_val
        merged.setdefault("Lens", lens_val)

    title_val = first("XMP-dc:Title", "XMP-dc:title")
    if title_val is not None:
        merged["XMP-dc:Title"] = title_val
        merged["XMP:Title"] = title_val
        merged["Title"] = title_val
    desc_val = first("XMP-dc:Description", "XMP-dc:description")
    if desc_val is not None:
        merged["XMP-dc:Description"] = desc_val
        merged["XMP:Description"] = desc_val
        merged["Description"] = desc_val

    country_val = first("XMP:Country", "XMP-photoshop:Country", "XMP-photoshop:Country-PrimaryLocationName")
    if country_val is not None:
        merged["XMP:Country"] = country_val


def _overlay_generic_aliases(merged: dict[str, Any]) -> None:
    """通用兜底别名：兼容 Pillow/不同相机的 EXIF 键名差异。"""
    if not isinstance(merged, dict):
        return

    def first(*keys: str) -> Any | None:
        for key in keys:
            if merged.get(key) not in (None, "", " "):
                return merged.get(key)
        return None

    # exiftool 分组键（IFD0:/ExifIFD:/Composite:/XMP-*）→ 常用裸键，降低不同读取路径差异。
    common_key_map: dict[str, tuple[str, ...]] = {
        "Make": ("IFD0:Make", "EXIF:Make", "XMP-tiff:Make"),
        "Model": ("IFD0:Model", "EXIF:Model", "XMP-tiff:Model"),
        "DateTimeOriginal": ("ExifIFD:DateTimeOriginal", "EXIF:DateTimeOriginal", "XMP-exif:DateTimeOriginal"),
        "CreateDate": ("ExifIFD:CreateDate", "EXIF:CreateDate", "XMP-xmp:CreateDate", "XMP-exif:DateTimeDigitized"),
        "FNumber": ("ExifIFD:FNumber", "EXIF:FNumber", "Composite:Aperture", "XMP-exif:FNumber"),
        "Aperture": ("Composite:Aperture",),
        "ExposureTime": ("ExifIFD:ExposureTime", "EXIF:ExposureTime", "Composite:ShutterSpeed", "XMP-exif:ExposureTime"),
        "ShutterSpeed": ("Composite:ShutterSpeed",),
        "ISO": ("ExifIFD:ISO", "EXIF:ISO", "XMP-exif:PhotographicSensitivity", "XMP-exif:ISOSpeedRatings"),
        "PhotographicSensitivity": ("ExifIFD:PhotographicSensitivity", "EXIF:PhotographicSensitivity", "XMP-exif:PhotographicSensitivity"),
        "ISOSpeedRatings": ("ExifIFD:ISOSpeedRatings", "EXIF:ISOSpeedRatings", "XMP-exif:ISOSpeedRatings"),
        "FocalLength": ("ExifIFD:FocalLength", "EXIF:FocalLength", "Composite:FocalLength", "XMP-exif:FocalLength"),
        "FocalLengthIn35mmFormat": ("ExifIFD:FocalLengthIn35mmFormat", "EXIF:FocalLengthIn35mmFormat", "Composite:FocalLength35efl", "XMP-exif:FocalLengthIn35mmFormat"),
        "FocalLength35efl": ("Composite:FocalLength35efl",),
        "LensModel": ("ExifIFD:LensModel", "EXIF:LensModel", "Composite:LensModel", "XMP-aux:LensModel", "XMP-aux:Lens", "XMP-exifEX:LensModel"),
        "Lens": ("Composite:Lens", "XMP-aux:Lens", "XMP:Lens"),
        "LensID": ("Composite:LensID", "ExifIFD:LensID", "EXIF:LensID"),
        "GPSLatitude": ("Composite:GPSLatitude", "XMP-exif:GPSLatitude"),
        "GPSLongitude": ("Composite:GPSLongitude", "XMP-exif:GPSLongitude"),
    }
    for target, candidates in common_key_map.items():
        if merged.get(target) not in (None, "", " "):
            continue
        value = first(*candidates)
        if value not in (None, "", " "):
            merged[target] = value

    if merged.get("ISO") in (None, "", " "):
        iso_fallback = merged.get("PhotographicSensitivity")
        if iso_fallback in (None, "", " "):
            iso_fallback = merged.get("ISOSpeedRatings")
        if iso_fallback not in (None, "", " "):
            merged["ISO"] = iso_fallback
    if merged.get("LensModel") in (None, "", " "):
        lens_fallback = merged.get("Lens")
        if lens_fallback not in (None, "", " "):
            merged["LensModel"] = lens_fallback


def extract_pillow_metadata(path: Path | str) -> dict[str, Any]:
    """使用 Pillow 读取 EXIF，返回平坦字典（键为 tag 名，与 normalize 兼容）。"""
    path = Path(path)
    metadata: dict[str, Any] = {"SourceFile": str(path)}
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return metadata
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                if tag not in {"GPSInfo", "ExifOffset", "InteropOffset"}:
                    metadata[tag] = value
                    continue
                if not isinstance(value, dict):
                    continue
                _merge_gps_ifd(metadata, value)

            # Pillow 的 image.getexif() 顶层通常只有 ExifOffset 指针；
            # 光圈/快门/ISO/焦距/镜头型号常在 ExifIFD，需显式展开。
            exif_ifd = _load_ifd(exif, _EXIF_IFD_TAG_ID)
            if exif_ifd:
                _merge_ifd_tags(metadata, exif_ifd)

            gps_ifd = _load_ifd(exif, _GPS_IFD_TAG_ID)
            if gps_ifd:
                _merge_gps_ifd(metadata, gps_ifd)

            interop_ifd = _load_ifd(exif, _INTEROP_IFD_TAG_ID)
            if interop_ifd:
                _merge_ifd_tags(metadata, interop_ifd)
    except Exception:
        pass
    return metadata


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _batch_read_exiftool_full(
    et_path: str,
    paths: list[Path],
    chunk_size: int = 128,
) -> dict[str, dict[str, Any]]:
    """全量读取：exiftool -j -G1 -n -a -u -api largefilesupport=1，返回 {normpath: rec}。"""
    result: dict[str, dict[str, Any]] = {}
    for chunk in _chunked(paths, chunk_size):
        cmd = [
            et_path,
            "-j",
            "-G1",
            "-n",
            "-a",
            "-u",
            "-charset",
            "filename=UTF8",
            "-api",
            "largefilesupport=1",
            *[os.path.normpath(str(p)) for p in chunk],
        ]
        try:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (FileNotFoundError, OSError):
            break
        if cp.returncode != 0 or not (cp.stdout or "").strip():
            continue
        try:
            payload = json.loads(cp.stdout)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            src = item.get("SourceFile")
            if not src:
                continue
            norm = os.path.normpath(src)
            result[norm] = item
    return result


def extract_many(
    paths: list[Path],
    mode: str = "auto",
    chunk_size: int = 128,
) -> dict[Path, dict[str, Any]]:
    """
    批量读取完整 EXIF（exiftool 全量 + Pillow 回退）。

    paths: 文件路径列表（Path 或可转为 Path 的对象）。
    mode: "auto" | "on" | "off"。auto=exiftool 可用则用，否则 Pillow；on=必须 exiftool；off=仅 Pillow。
    chunk_size: exiftool 单次调用的文件数上限。

    返回 dict[Path, dict]，键为 path.resolve(strict=False)，值与 normalize 等兼容（-G1 风格或 Pillow 键名）。
    """
    mode = mode.lower()
    if mode not in ("auto", "on", "off"):
        raise ValueError(f"invalid mode: {mode!r}")
    resolved_list: list[Path] = []
    for p in paths:
        try:
            res = Path(p).resolve(strict=False)
            if res not in resolved_list:
                resolved_list.append(res)
        except Exception:
            continue
    if not resolved_list:
        return {}

    et_path = get_exiftool_executable_path()
    use_exiftool = (mode in ("auto", "on") and bool(et_path))
    if mode == "on" and not et_path:
        raise RuntimeError("ExifTool is required but not found (exif_io path or PATH).")

    out: dict[Path, dict[str, Any]] = {}

    if use_exiftool:
        raw = _batch_read_exiftool_full(et_path, resolved_list, chunk_size=chunk_size)
        for p in resolved_list:
            norm = os.path.normpath(str(p))
            if norm in raw:
                out[p] = raw[norm]
            else:
                out[p] = extract_pillow_metadata(p)
    else:
        for p in resolved_list:
            out[p] = extract_pillow_metadata(p)

    return out


def extract_many_with_xmp_priority(
    paths: list[Path],
    mode: str = "auto",
    chunk_size: int = 128,
) -> dict[Path, dict[str, Any]]:
    """
    批量读取元数据：先读内嵌 EXIF（exiftool/Pillow），再用 sidecar XMP 覆盖同语义字段。
    返回键为 path.resolve(strict=False) 的字典，适合 GUI/CLI 直接喂给 normalize。
    """
    base_map = extract_many(paths, mode=mode, chunk_size=chunk_size)
    if not base_map:
        return {}

    try:
        from app_common.exif_io.xmp_sidecar import read_xmp_sidecar  # 局部导入避免循环
    except Exception:
        read_xmp_sidecar = None

    out: dict[Path, dict[str, Any]] = {}
    for resolved_path, base_rec in base_map.items():
        merged = dict(base_rec or {})
        merged.setdefault("SourceFile", str(resolved_path))
        if callable(read_xmp_sidecar):
            try:
                xmp_rows = read_xmp_sidecar(str(resolved_path))
            except Exception:
                xmp_rows = []
            if xmp_rows:
                merged.update(_xmp_rows_to_flat_dict(resolved_path, xmp_rows))
                _overlay_xmp_aliases(merged)
        _overlay_generic_aliases(merged)
        out[resolved_path] = merged
    return out


def extract_metadata_with_xmp_priority(path: Path | str, mode: str = "auto") -> dict[str, Any]:
    """
    读取单文件元数据：内嵌 EXIF 为底，sidecar XMP 存在时优先覆盖同名语义字段。

    说明：
    - XMP 常用于标题、评分、挑片状态，也可能包含 exif:/tiff:/aux: 拍摄参数；
    - sidecar 不一定包含完整拍摄参数，因此以“覆盖”而不是“完全替代”策略更稳妥。
    """
    source = Path(path)
    resolved = source.resolve(strict=False)
    merged_from_batch = False
    try:
        raw_map = extract_many_with_xmp_priority([resolved], mode=mode)
        merged = dict(raw_map.get(resolved) or {})
        merged_from_batch = True
    except Exception:
        merged = {}
    if not merged:
        merged = extract_pillow_metadata(source)
    merged.setdefault("SourceFile", str(source))
    if not merged_from_batch:
        try:
            from app_common.exif_io.xmp_sidecar import read_xmp_sidecar  # 局部导入避免循环

            xmp_rows = read_xmp_sidecar(str(source))
        except Exception:
            xmp_rows = []
        if xmp_rows:
            merged.update(_xmp_rows_to_flat_dict(source, xmp_rows))
            _overlay_xmp_aliases(merged)
    _overlay_generic_aliases(merged)

    return merged
