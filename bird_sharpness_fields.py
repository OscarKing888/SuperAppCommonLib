"""Shared XMP field names and display rules for bird sharpness detection results.

The detector lives in the SuperBirdTools ``bird_sharpness`` package; this module
only holds the storage contract (``XMP-superpicky:bird_sharpness_*``) and the
labels/colours both apps use to show a stored verdict, so the file browser does
not need to import OpenCV/Torch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple

XMP_GROUP = "XMP-superpicky"

FIELD_VERDICT = "bird_sharpness_verdict"
FIELD_SCORE = "bird_sharpness_score"
FIELD_HEAD_SIGMA = "bird_sharpness_head_sigma"
FIELD_BODY_SIGMA = "bird_sharpness_body_sigma"
FIELD_MOTION_RATIO = "bird_sharpness_motion_ratio"
FIELD_EYE_VISIBILITY = "bird_sharpness_eye_visibility"
FIELD_VERSION = "bird_sharpness_version"

ALL_FIELDS = (
    FIELD_VERDICT,
    FIELD_SCORE,
    FIELD_HEAD_SIGMA,
    FIELD_BODY_SIGMA,
    FIELD_MOTION_RATIO,
    FIELD_EYE_VISIBILITY,
    FIELD_VERSION,
)

# Existing SuperPicky sharpness slot (0..1000, written as "%06.2f").
SHARPNESS_XMP_KEY = "XMP-photoshop:City"

VERDICT_SHARP = "sharp"
VERDICT_USABLE = "usable"
VERDICT_SOFT = "soft"
VERDICT_MOTION = "motion"
VERDICT_NO_EYE = "no_eye"
VERDICT_NO_BIRD = "no_bird"
VERDICT_ERROR = "error"


class VerdictStyle(NamedTuple):
    label: str
    color: str
    rank: int  # sort order: lower = sharper


VERDICT_STYLES: dict[str, VerdictStyle] = {
    VERDICT_SHARP: VerdictStyle("清晰", "#2e9d4f", 0),
    VERDICT_USABLE: VerdictStyle("可用", "#c99a06", 1),
    VERDICT_SOFT: VerdictStyle("失焦", "#d64545", 2),
    VERDICT_MOTION: VerdictStyle("运动模糊", "#d64545", 3),
    VERDICT_NO_EYE: VerdictStyle("无鸟眼", "#8a8f98", 4),
    VERDICT_NO_BIRD: VerdictStyle("无鸟", "#8a8f98", 5),
    VERDICT_ERROR: VerdictStyle("失败", "#8a8f98", 6),
}


def xmp_key(field: str) -> str:
    return f"{XMP_GROUP}:{field}"


def _meta_value(meta: Mapping[str, Any] | None, field: str) -> Any:
    if not meta:
        return None
    for key in (xmp_key(field), field, f"report.{field}"):
        value = meta.get(key)
        if value is not None and (not isinstance(value, str) or value.strip()):
            return value
    return None


def _optional_float(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None


@dataclass(frozen=True)
class BirdSharpnessDisplay:
    # dataclass rather than tuple: Qt item roles would turn a tuple into a plain list.
    verdict: str
    head_sigma: float | None
    body_sigma: float | None

    @property
    def style(self) -> VerdictStyle | None:
        return VERDICT_STYLES.get(self.verdict)

    @property
    def label(self) -> str:
        style = self.style
        return style.label if style else self.verdict

    @property
    def sigma(self) -> float | None:
        return self.head_sigma if self.head_sigma is not None else self.body_sigma

    def text(self) -> str:
        """Compact list/thumbnail text such as ``清晰 0.62``."""
        sigma = self.sigma
        if sigma is None or self.verdict in (VERDICT_NO_BIRD, VERDICT_ERROR):
            return self.label
        return f"{self.label} {sigma:.2f}"

    def sort_key(self) -> tuple:
        style = self.style
        rank = style.rank if style else len(VERDICT_STYLES)
        sigma = self.sigma
        return (rank, sigma if sigma is not None else 99.0)


def bird_sharpness_from_meta(meta: Mapping[str, Any] | None) -> BirdSharpnessDisplay | None:
    """Return the stored verdict for display, or ``None`` when never analysed."""
    verdict = _meta_value(meta, FIELD_VERDICT)
    if verdict is None:
        return None
    verdict_text = str(verdict).strip().lower()
    if not verdict_text:
        return None
    return BirdSharpnessDisplay(
        verdict_text,
        _optional_float(_meta_value(meta, FIELD_HEAD_SIGMA)),
        _optional_float(_meta_value(meta, FIELD_BODY_SIGMA)),
    )


def browser_meta_fields(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Raw-key subset kept in the file browser's per-path metadata cache."""
    result: dict[str, Any] = {}
    for field in (FIELD_VERDICT, FIELD_HEAD_SIGMA, FIELD_BODY_SIGMA):
        value = _meta_value(meta, field)
        if value is not None:
            result[field] = value
    return result
