"""Shared metadata-to-badge registry for Qt information panels and image overlays.

Conservation colors: Cornell Lab Lichen .Badge.u-color-constatus-* palette,
https://birdsoftheworld.org/static/themes/base/public/dist/lichen-d2bf811f236a56ef92f98fa42e8be82b.css
Category meanings: https://support.ebird.org/en/support/solutions/articles/48001189976
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .bird_rarity import (
    IUCN_FIELD, RARITY_FIELD, RARITY_COMPAT_FIELDS, RARITY_DEFAULT_OPTIONS,
    RARITY_LEVELS, normalize_badge_options, rarity_level, rarity_metadata,
)

# Code, description, label, background, foreground. Keep missing separate from NE/LC.
IUCN_LEVELS = (
    ("lc", "LC", "LC · 无危", "#68BB53", "#2E261F"),
    ("nt", "NT", "NT · 近危", "#D1E744", "#2E261F"),
    ("vu", "VU", "VU · 易危", "#E5D42D", "#2E261F"),
    ("en", "EN", "EN · 濒危", "#E77B4D", "#2E261F"),
    ("cr", "CR", "CR · 极危", "#B61917", "#FFFFFF"),
    ("cr_pe", "CR(PE)", "CR(PE) · 可能灭绝", "#B61917", "#FFFFFF"),
    ("cr_pew", "CR(PEW)", "CR(PEW) · 可能野外灭绝", "#B61917", "#FFFFFF"),
    ("ew", "EW", "EW · 野外灭绝", "#542344", "#FFFFFF"),
    ("ex", "EX", "EX · 灭绝", "#000000", "#FFFFFF"),
    ("dd", "DD", "DD · 数据缺乏", "#595959", "#FFFFFF"),
    ("ne", "NE", "NE · 未评估", "#CCCCCC", "#2E261F"),
    ("unknown", "缺失或无效", "未知", "#6B7280", "#FFFFFF"),
)
IUCN_DEFAULT_OPTIONS = {
    f"iucn_badge_{key}_{field}": value
    for key, _code, text, background, foreground in IUCN_LEVELS
    for field, value in (("text", text), ("background", background), ("foreground", foreground))
}


def iucn_level(value) -> str:
    code = str(value or "").strip().upper().replace(" ", "")
    return next((key for key, candidate, *_ in IUCN_LEVELS if candidate == code), "unknown")


@dataclass(frozen=True)
class BadgeDefinition:
    prefix: str
    field: str
    aliases: tuple[str, ...]
    levels: tuple
    defaults: dict[str, str]
    level_for: Callable
    value_from_metadata: Callable


def _aliases(field, xml_compat):
    return (field, f"report.{field}", f"XMP-superpicky:{field}", RARITY_COMPAT_FIELDS[field], xml_compat)


BADGE_DEFINITIONS = {
    "rarity": BadgeDefinition("rarity_badge", RARITY_FIELD,
        _aliases(RARITY_FIELD, "XMP-Iptc4xmpExt:Event"), RARITY_LEVELS,
        RARITY_DEFAULT_OPTIONS, rarity_level, lambda metadata: rarity_metadata(metadata)[0]),
    "iucn": BadgeDefinition("iucn_badge", IUCN_FIELD,
        _aliases(IUCN_FIELD, "XMP-Iptc4xmpCore:IntellectualGenre"), IUCN_LEVELS,
        IUCN_DEFAULT_OPTIONS, iucn_level, lambda metadata: rarity_metadata(metadata)[1]),
}
METADATA_BADGE_DEFAULT_OPTIONS = {key: value for definition in BADGE_DEFINITIONS.values()
                                for key, value in definition.defaults.items()}


def normalize_metadata_badge_options(options=None):
    return normalize_badge_options(options, METADATA_BADGE_DEFAULT_OPTIONS)


def badge_kind_for_field(field: str) -> str | None:
    field = str(field or "").strip().strip("{}").strip()
    return next((kind for kind, definition in BADGE_DEFINITIONS.items() if field in definition.aliases), None)


def metadata_badge_value(kind: str, metadata: dict):
    return BADGE_DEFINITIONS[kind].value_from_metadata(metadata)


def metadata_badge_style(kind: str, value, options=None, *, level=None):
    definition = BADGE_DEFINITIONS[kind]
    options = normalize_badge_options(options, definition.defaults)
    prefix = f"{definition.prefix}_{level or definition.level_for(value)}_"
    return tuple(options[prefix + field] for field in ("text", "background", "foreground"))
