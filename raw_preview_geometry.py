# -*- coding: utf-8 -*-
"""Map camera-preview focus coordinates onto uncropped LibRaw output.

The camera JPEG uses LibRaw's default/inset crop, while postprocess() may
include additional active pixels. Geometry travels with decoded pixels and
must never be saved as source EXIF or put in the shared JPEG thumbnail cache.
"""
from __future__ import annotations

import math

from app_common.focus_calc import transform_focus_box_by_orientation

RAW_FOCUS_CROP_KEY = "raw_camera_crop_box"


def rawpy_camera_crop_box(sizes) -> tuple[float, float, float, float] | None:
    """Return the inset crop in oriented, normalized postprocess coordinates.

    LibRaw raw_inset_crops use raw-sensor coordinates, whereas postprocess
    removes top_margin/left_margin. Older rawpy versions lack crop fields;
    incomplete/out-of-range metadata must leave the existing mapping alone.
    """
    try:
        width, height = int(sizes.width), int(sizes.height)
        left = int(sizes.crop_left_margin) - int(sizes.left_margin)
        top = int(sizes.crop_top_margin) - int(sizes.top_margin)
        right, bottom = left + int(sizes.crop_width), top + int(sizes.crop_height)
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            return None
        # dcraw/LibRaw flip bits: transpose (4), vertical (2), horizontal (1).
        orientation = {0: 1, 1: 2, 2: 4, 3: 3, 4: 5, 5: 8, 6: 6, 7: 7}[int(sizes.flip)]
        return transform_focus_box_by_orientation(
            (left / width, top / height, right / width, bottom / height), orientation)
    except (AttributeError, TypeError, ValueError, KeyError, ZeroDivisionError):
        return None


def map_camera_focus_box(focus_box, camera_crop_box):
    """Map an already display-oriented camera focus box into full RAW output."""
    if focus_box is None or camera_crop_box is None:
        return focus_box
    try:
        left, top, right, bottom = (float(v) for v in camera_crop_box)
        if not all(math.isfinite(v) for v in (left, top, right, bottom)):
            return focus_box
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            return focus_box
        x0, y0, x1, y1 = focus_box
        return (left + x0 * (right - left), top + y0 * (bottom - top),
                left + x1 * (right - left), top + y1 * (bottom - top))
    except (TypeError, ValueError):
        return focus_box
