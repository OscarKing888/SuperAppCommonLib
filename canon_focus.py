# -*- coding: utf-8 -*-
"""Canon AFInfo/AFInfo2 geometry, independent of image decoding and Qt.

ExifTool -n supplies signed 16-bit mask words, not point indices. Printed
metadata is accepted when AFAreaMode/FocusMode identifies print conversion.
See https://exiftool.org/TagNames/Canon.html#AFInfo and Canon.pm AFInfo2.
"""
from __future__ import annotations

import re
from typing import Any


# 与批量元数据读取共用；不能只取 Focus* 而漏掉佳能的 AF 数组。
CANON_FOCUS_TAGS = (
    "-FocusMode", "-AFAreaMode", "-NumAFPoints", "-ValidAFPoints",
    "-AFImageWidth", "-AFImageHeight", "-AFAreaWidth", "-AFAreaHeight",
    "-AFAreaWidths", "-AFAreaHeights", "-AFAreaXPositions", "-AFAreaYPositions",
    "-AFPointsInFocus", "-AFPointsSelected",
)


def _integers(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            parsed = _integers(item)
            if not parsed:
                return []
            result.extend(parsed)
        return result
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+(?:[\s,]+[+-]?\d+)*", text):
        return []
    return [int(token) for token in re.split(r"[\s,]+", text)]


def _indices(value: Any, count: int, *, printed: bool) -> list[int]:
    numbers = _integers(value)
    if printed:
        return sorted({number for number in numbers if 0 <= number < count})
    if len(numbers) != (count + 15) // 16:
        return []
    if any(word < -32768 or word > 65535 for word in numbers):
        return []
    # -32768 等负数只是 int16s 表示；不能将其当作负的焦点编号。
    return [index for index in range(count)
            if (numbers[index // 16] & 0xffff) & (1 << (index % 16))]


def extract_canon_focus_box(
    raw: dict[str, Any], width: int, height: int,
) -> tuple[float, float, float, float] | None:
    """Union of active AF rectangles in unrotated camera-image coordinates.

    Prefer confirmed points, then selected points. Invalid/padded entries and
    manual-focus records never manufacture a centre box. AFImageWidth/Height,
    not the thumbnail or sensor dimensions, define the coordinate system.
    """
    if width <= 0 or height <= 0:
        return None
    lookup = {str(key).lower(): value for key, value in raw.items()}

    def value(name):
        # CR3 可同时带 Canon 与 Track4，优先相机 MakerNote，避免被通用别名覆盖。
        for prefix in ("canon:", "track4:", "makernotes:", ""):
            key = prefix + name.lower()
            if key in lookup:
                return lookup[key]
        return None

    def integer(name, default=0):
        numbers = _integers(value(name))
        return numbers[0] if len(numbers) == 1 else default

    area_mode, focus_mode = value("AFAreaMode"), value("FocusMode")
    if (integer("AFAreaMode", -1) == 0 or integer("FocusMode", -1) in (3, 6)
            or "manual" in str(area_mode).lower() or "manual" in str(focus_mode).lower()):
        return None
    # 所有应用读取器使用 -n；外部文本 JSON 用模式名称明确区分编号 0 与位掩码 0。
    printed = any(isinstance(mode, str) and "af" in mode.lower()
                  for mode in (area_mode, focus_mode))
    count = integer("NumAFPoints")
    valid = integer("ValidAFPoints", count)
    af_width, af_height = integer("AFImageWidth"), integer("AFImageHeight")
    if not (0 < valid <= count <= 65535 and af_width > 0 and af_height > 0):
        return None
    xs, ys = _integers(value("AFAreaXPositions")), _integers(value("AFAreaYPositions"))
    widths, heights = _integers(value("AFAreaWidths")), _integers(value("AFAreaHeights"))
    if value("AFAreaWidths") is None:
        widths = _integers(value("AFAreaWidth")) * valid
    if value("AFAreaHeights") is None:
        heights = _integers(value("AFAreaHeight")) * valid
    limit = min(valid, len(xs), len(ys), len(widths), len(heights))
    model = str(lookup.get("ifd0:model", lookup.get("model", ""))).lower()
    y_direction = 1 if "powershot" in model else -1

    for mask_name in ("AFPointsInFocus", "AFPointsSelected"):
        boxes = []
        for index in _indices(value(mask_name), count, printed=printed):
            if index >= limit:
                continue
            w, h = widths[index], heights[index]
            cx = af_width / 2.0 + xs[index]
            cy = af_height / 2.0 + y_direction * ys[index]
            if not (0 < w <= af_width and 0 < h <= af_height
                    and 0 <= cx <= af_width and 0 <= cy <= af_height):
                continue
            boxes.append((max(0.0, (cx - w / 2.0) / af_width),
                          max(0.0, (cy - h / 2.0) / af_height),
                          min(1.0, (cx + w / 2.0) / af_width),
                          min(1.0, (cy + h / 2.0) / af_height)))
        if boxes:
            return (min(box[0] for box in boxes), min(box[1] for box in boxes),
                    max(box[2] for box in boxes), max(box[3] for box in boxes))
    return None


def extract_canon_focus_point(
    raw: dict[str, Any], width: int, height: int,
) -> tuple[float, float] | None:
    box = extract_canon_focus_box(raw, width, height)
    if box is None:
        return None
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
