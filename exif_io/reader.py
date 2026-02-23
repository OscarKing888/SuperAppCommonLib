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
                if tag != "GPSInfo":
                    metadata[tag] = value
                    continue
                if not isinstance(value, dict):
                    continue
                gps_info = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in value.items()}
                metadata["GPSInfo"] = gps_info
                lat = _dms_to_degree(gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef"))
                lon = _dms_to_degree(gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef"))
                if lat is not None:
                    metadata["GPSLatitude"] = lat
                if lon is not None:
                    metadata["GPSLongitude"] = lon
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
