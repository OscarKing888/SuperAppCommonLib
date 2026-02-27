# -*- coding: utf-8 -*-
"""Camera-aware focus point / focus box extraction utilities (no GUI deps).

This module is intentionally pure-Python so it can be reused by GUI, CLI and
tests without importing PyQt-heavy modules.
"""
from __future__ import annotations

from enum import Enum
import re
from typing import Any, Callable

DEFAULT_FOCUS_BOX_SHORT_EDGE_RATIO = 0.12


class CameraFocusType(str, Enum):
    """Camera focus metadata parsing strategy / camera family discriminator."""

    UNKNOWN = "unknown"
    SONY_GENERIC = "sony_generic"
    ILCE_A1M2 = "ilce_a1m2"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for codec in ("utf-8", "utf-16le", "latin1"):
            try:
                value = value.decode(codec, errors="ignore")
                break
            except Exception:
                continue
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v).strip()]
        value = " ".join(items)
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def normalize_lookup(raw: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        key_text = str(key).strip().lower()
        if not key_text:
            continue
        lookup.setdefault(key_text, value)
        if ":" in key_text:
            lookup.setdefault(key_text.split(":")[-1], value)
    return lookup


def _extract_numbers(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        out: list[float] = []
        for item in value:
            out.extend(_extract_numbers(item))
        return out
    tokens = re.findall(r"[-+]?\d+(?:\.\d+)?", str(value))
    out = []
    for token in tokens:
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _is_dimension_like(value: float, size: int) -> bool:
    if size <= 0 or value <= 0:
        return False
    if value <= 1.0:
        return False
    size_f = float(size)
    return abs(value - size_f) <= 3.0 or abs(value - (size_f + 1.0)) <= 3.0


def _normalize_focus_coordinate(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    if x > 1.0 or y > 1.0:
        if width > 0 and height > 0:
            return (clamp01(x / float(width)), clamp01(y / float(height)))
    return (clamp01(x), clamp01(y))


def _decode_focus_numbers_layout(
    numbers: list[float], width: int, height: int
) -> tuple[float, float, float | None, float | None] | None:
    if len(numbers) < 2:
        return None
    if len(numbers) >= 4 and _is_dimension_like(numbers[0], width) and _is_dimension_like(numbers[1], height):
        center_x = numbers[2]
        center_y = numbers[3]
        span_start = 4
    else:
        center_x = numbers[0]
        center_y = numbers[1]
        span_start = 2
    span_x: float | None = None
    span_y: float | None = None
    if len(numbers) >= span_start + 2:
        span_x = numbers[span_start]
        span_y = numbers[span_start + 1]
    elif len(numbers) >= span_start + 1:
        span_x = numbers[span_start]
        span_y = numbers[span_start]
    return (center_x, center_y, span_x, span_y)


def _extract_focus_frame_size(value: Any) -> tuple[float, float] | None:
    numbers = _extract_numbers(value)
    if len(numbers) < 2:
        return None
    width = numbers[0]
    height = numbers[1]
    if width <= 0 or height <= 0:
        return None
    return (float(width), float(height))


def _normalize_focus_span(value: float | None, full_size: int, fallback: float) -> float:
    if full_size <= 0:
        return max(0.01, min(1.0, fallback))
    if value is None or value <= 0:
        return max(0.01, min(1.0, fallback))
    span = float(value)
    if span > 1.0:
        span = span / float(full_size)
    return max(0.01, min(1.0, span))


def _focus_box_from_center(center_x: float, center_y: float, span_x: float, span_y: float) -> tuple[float, float, float, float]:
    cx = clamp01(center_x)
    cy = clamp01(center_y)
    sx = max(0.01, min(1.0, span_x))
    sy = max(0.01, min(1.0, span_y))
    half_x = sx * 0.5
    half_y = sy * 0.5
    left = cx - half_x
    right = cx + half_x
    top = cy - half_y
    bottom = cy + half_y
    if left < 0.0:
        right = min(1.0, right - left)
        left = 0.0
    if right > 1.0:
        left = max(0.0, left - (right - 1.0))
        right = 1.0
    if top < 0.0:
        bottom = min(1.0, bottom - top)
        top = 0.0
    if bottom > 1.0:
        top = max(0.0, top - (bottom - 1.0))
        bottom = 1.0
    return (left, top, right, bottom)


def _focus_box_from_numbers(
    numbers: list[float],
    width: int,
    height: int,
    fallback_span_px: tuple[float, float] | None = None,
) -> tuple[float, float, float, float] | None:
    if width <= 0 or height <= 0:
        return None
    decoded = _decode_focus_numbers_layout(numbers, width, height)
    if decoded is None:
        return None
    x, y, span_x_raw, span_y_raw = decoded
    center_x, center_y = _normalize_focus_coordinate(x, y, width, height)
    default_side_px = max(24.0, min(width, height) * DEFAULT_FOCUS_BOX_SHORT_EDGE_RATIO)
    if fallback_span_px is not None and fallback_span_px[0] > 0 and fallback_span_px[1] > 0:
        fallback_span_x = fallback_span_px[0] / float(width)
        fallback_span_y = fallback_span_px[1] / float(height)
    else:
        fallback_span_x = default_side_px / float(width)
        fallback_span_y = default_side_px / float(height)
    span_x = _normalize_focus_span(span_x_raw, width, fallback_span_x)
    span_y = _normalize_focus_span(span_y_raw, height, fallback_span_y)
    return _focus_box_from_center(center_x, center_y, span_x, span_y)


_SONY_MAKERNOTE_FOCUS_BLOCK_KEYS: tuple[str, ...] = (
    "makernote tag 0x2027",
    "makernote tag 0x204a",
)


def _focus_point_from_dimension_prefixed_block(numbers: list[float]) -> tuple[float, float] | None:
    """
    解析 Sony MakerNote 里常见的 [block_w, block_h, x, y, ...] 结构。
    block_w/block_h 是坐标系尺寸，x/y 为焦点中心（像素）。
    """
    if len(numbers) < 4:
        return None
    block_w = float(numbers[0])
    block_h = float(numbers[1])
    x = float(numbers[2])
    y = float(numbers[3])
    if block_w <= 0 or block_h <= 0:
        return None
    return (clamp01(x / block_w), clamp01(y / block_h))


def _focus_box_from_dimension_prefixed_block(
    numbers: list[float],
    fallback_span_px: tuple[float, float] | None = None,
) -> tuple[float, float, float, float] | None:
    """
    解析 Sony MakerNote 焦点块 [block_w, block_h, x, y, (opt)w, (opt)h]。
    """
    if len(numbers) < 4:
        return None
    block_w = int(round(float(numbers[0])))
    block_h = int(round(float(numbers[1])))
    if block_w <= 0 or block_h <= 0:
        return None
    payload = [float(numbers[0]), float(numbers[1]), float(numbers[2]), float(numbers[3])]
    if len(numbers) >= 6:
        payload.extend([float(numbers[4]), float(numbers[5])])
    return _focus_box_from_numbers(payload, block_w, block_h, fallback_span_px=fallback_span_px)


def _normalize_camera_model_key(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()


_CAMERA_MODEL_TO_FOCUS_TYPE: dict[str, CameraFocusType] = {
    # Sony Alpha 1 II: users may see different spellings depending on toolchain.
    "ILCE_A1M2": CameraFocusType.ILCE_A1M2,
    "ILCE_1M2": CameraFocusType.ILCE_A1M2,
    "ILCEA1M2": CameraFocusType.ILCE_A1M2,
    "ILCE1M2": CameraFocusType.ILCE_A1M2,
}


def resolve_focus_camera_type(camera_model: Any, *, camera_make: Any = None) -> CameraFocusType:
    """Resolve a focus extraction camera type from model/make metadata text."""
    model_key = _normalize_camera_model_key(camera_model)
    if model_key in _CAMERA_MODEL_TO_FOCUS_TYPE:
        return _CAMERA_MODEL_TO_FOCUS_TYPE[model_key]

    make_key = _normalize_camera_model_key(camera_make)
    # Conservative family fallback: Sony mirrorless/compact model prefixes.
    if make_key == "SONY" or model_key.startswith(("ILCE_", "ILME_", "DSC_", "ZV_")):
        return CameraFocusType.SONY_GENERIC
    return CameraFocusType.UNKNOWN


def resolve_focus_camera_type_from_metadata(raw: dict[str, Any]) -> CameraFocusType:
    lookup = normalize_lookup(raw)
    make_value = lookup.get("make") or lookup.get("manufacturer")
    model_value = (
        lookup.get("model")
        or lookup.get("cameramodelname")
        or lookup.get("camera model name")
        or lookup.get("cameramodel")
    )
    return resolve_focus_camera_type(model_value, camera_make=make_value)


def _coerce_camera_type(
    camera_type: CameraFocusType | str | None,
    *,
    raw: dict[str, Any],
) -> CameraFocusType:
    if camera_type is None:
        return resolve_focus_camera_type_from_metadata(raw)
    if isinstance(camera_type, CameraFocusType):
        return camera_type
    text = str(camera_type).strip()
    if not text:
        return resolve_focus_camera_type_from_metadata(raw)
    normalized = text.lower()
    for item in CameraFocusType:
        if normalized in {item.value.lower(), item.name.lower()}:
            return item
    return resolve_focus_camera_type(text)


def _extract_focus_point_sony(raw: dict[str, Any], width: int, height: int) -> tuple[float, float] | None:
    if width <= 0 or height <= 0:
        return None
    lookup = normalize_lookup(raw)
    key_pairs = [
        ("composite:focusx", "composite:focusy"),
        ("focusx", "focusy"),
        ("regioninfo:regionsregionlistregionareax", "regioninfo:regionsregionlistregionareay"),
        ("regionareax", "regionareay"),
    ]
    for x_key, y_key in key_pairs:
        if x_key in lookup and y_key in lookup:
            xs = _extract_numbers(lookup[x_key])
            ys = _extract_numbers(lookup[y_key])
            if xs and ys:
                x, y = xs[0], ys[0]
                if x > 1.0 or y > 1.0:
                    return (clamp01(x / float(width)), clamp01(y / float(height)))
                return (clamp01(x), clamp01(y))
    for key in _SONY_MAKERNOTE_FOCUS_BLOCK_KEYS:
        if key not in lookup:
            continue
        point = _focus_point_from_dimension_prefixed_block(_extract_numbers(lookup[key]))
        if point is not None:
            return point
    for key in ("subjectarea", "subjectlocation", "focuslocation", "focuslocation2", "afpoint"):
        if key not in lookup:
            continue
        nums = _extract_numbers(lookup[key])
        decoded = _decode_focus_numbers_layout(nums, width, height)
        if decoded is None:
            continue
        x, y, _span_x, _span_y = decoded
        return _normalize_focus_coordinate(x, y, width, height)
    return None


def _extract_focus_box_sony(raw: dict[str, Any], width: int, height: int) -> tuple[float, float, float, float] | None:
    if width <= 0 or height <= 0:
        return None
    lookup = normalize_lookup(raw)
    focus_frame_span_px: tuple[float, float] | None = None
    for key in ("focusframesize", "focusframesize2"):
        if key not in lookup:
            continue
        parsed = _extract_focus_frame_size(lookup[key])
        if parsed is not None:
            focus_frame_span_px = parsed
            break
    subject_area = lookup.get("subjectarea")
    if subject_area is not None:
        box = _focus_box_from_numbers(_extract_numbers(subject_area), width, height, fallback_span_px=focus_frame_span_px)
        if box is not None:
            return box
    for key in _SONY_MAKERNOTE_FOCUS_BLOCK_KEYS:
        if key not in lookup:
            continue
        box = _focus_box_from_dimension_prefixed_block(
            _extract_numbers(lookup[key]),
            fallback_span_px=focus_frame_span_px,
        )
        if box is not None:
            return box
    box_key_groups = [
        ("composite:focusx", "composite:focusy", "composite:focusw", "composite:focush"),
        ("focusx", "focusy", "focusw", "focush"),
        (
            "regioninfo:regionsregionlistregionareax",
            "regioninfo:regionsregionlistregionareay",
            "regioninfo:regionsregionlistregionareaw",
            "regioninfo:regionsregionlistregionareah",
        ),
        ("regionareax", "regionareay", "regionareaw", "regionareah"),
    ]
    for x_key, y_key, w_key, h_key in box_key_groups:
        if x_key not in lookup or y_key not in lookup:
            continue
        xs = _extract_numbers(lookup[x_key])
        ys = _extract_numbers(lookup[y_key])
        if not xs or not ys:
            continue
        nums = [xs[0], ys[0]]
        ws = _extract_numbers(lookup.get(w_key))
        hs = _extract_numbers(lookup.get(h_key))
        if ws and hs:
            nums.extend([ws[0], hs[0]])
        box = _focus_box_from_numbers(nums, width, height, fallback_span_px=focus_frame_span_px)
        if box is not None:
            return box
    for key in ("subjectlocation", "focuslocation", "focuslocation2", "afpoint"):
        if key not in lookup:
            continue
        box = _focus_box_from_numbers(_extract_numbers(lookup[key]), width, height, fallback_span_px=focus_frame_span_px)
        if box is not None:
            return box
    focus_point = _extract_focus_point_sony(raw, width, height)
    if focus_point is None:
        return None
    default_side_px = max(24.0, min(width, height) * DEFAULT_FOCUS_BOX_SHORT_EDGE_RATIO)
    return _focus_box_from_center(
        focus_point[0],
        focus_point[1],
        default_side_px / float(width),
        default_side_px / float(height),
    )


_FocusPointExtractor = Callable[[dict[str, Any], int, int], tuple[float, float] | None]
_FocusBoxExtractor = Callable[[dict[str, Any], int, int], tuple[float, float, float, float] | None]

# 当前仅实现 Sony 系列元数据提取；未知机型暂时走相同算法以保持兼容。
_FOCUS_POINT_EXTRACTORS: dict[CameraFocusType, _FocusPointExtractor] = {
    CameraFocusType.UNKNOWN: _extract_focus_point_sony,
    CameraFocusType.SONY_GENERIC: _extract_focus_point_sony,
    CameraFocusType.ILCE_A1M2: _extract_focus_point_sony,
}
_FOCUS_BOX_EXTRACTORS: dict[CameraFocusType, _FocusBoxExtractor] = {
    CameraFocusType.UNKNOWN: _extract_focus_box_sony,
    CameraFocusType.SONY_GENERIC: _extract_focus_box_sony,
    CameraFocusType.ILCE_A1M2: _extract_focus_box_sony,
}


def get_focus_point(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float] | None:
    """Return normalized focus point from metadata using camera-aware strategy."""
    resolved = _coerce_camera_type(camera_type, raw=raw)
    extractor = _FOCUS_POINT_EXTRACTORS.get(resolved) or _FOCUS_POINT_EXTRACTORS[CameraFocusType.UNKNOWN]
    return extractor(raw, width, height)


def extract_focus_box(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float, float, float] | None:
    """Return normalized focus box from metadata using camera-aware strategy."""
    resolved = _coerce_camera_type(camera_type, raw=raw)
    extractor = _FOCUS_BOX_EXTRACTORS.get(resolved) or _FOCUS_BOX_EXTRACTORS[CameraFocusType.UNKNOWN]
    return extractor(raw, width, height)


__all__ = [
    "CameraFocusType",
    "DEFAULT_FOCUS_BOX_SHORT_EDGE_RATIO",
    "clamp01",
    "normalize_lookup",
    "resolve_focus_camera_type",
    "resolve_focus_camera_type_from_metadata",
    "get_focus_point",
    "extract_focus_box",
]
