# -*- coding: utf-8 -*-
"""GBIF 稀有度、IUCN 元数据及可配置徽章；不依赖 Qt 或 SuperPicky。"""
from __future__ import annotations

import math
import re

from .bird_pinyin import bird_name

RARITY_FIELD = "gbif_rarity_100"
IUCN_FIELD = "iucn_category"
# 绑定已确认鸟名，同时标记本次返回的空值，防止旧报告回填成另一鸟种的等级。
RARITY_SOURCE_FIELD = "birdid_rarity_source"
RARITY_MISSING_FIELD = "birdid_rarity_missing"
RARITY_COMPAT_FIELDS = {RARITY_FIELD: "XMP-iptcExt:Event", IUCN_FIELD: "XMP-iptcCore:IntellectualGenre"}
# 档位分界沿用 SuperPicky core/rarity_tier.py；名称是可定制的展示文案。
RARITY_LEVELS = (
    ("common", "0 ≤ 分数 < 8", "普通", "#64748B"),
    ("uncommon", "8 ≤ 分数 < 25", "少见", "#15803D"),
    ("rare", "25 ≤ 分数 < 50", "稀有", "#2563EB"),
    ("epic", "50 ≤ 分数 < 75", "史诗", "#9333EA"),
    ("legendary", "75 ≤ 分数 ≤ 100", "传奇", "#C2410C"),
    ("unknown", "缺失或无效", "未知", "#6B7280"),
)
RARITY_DEFAULT_OPTIONS = {
    f"rarity_badge_{key}_{field}": value
    for key, _range, text, background in RARITY_LEVELS
    for field, value in (("text", text), ("background", background), ("foreground", "#FFFFFF"))
}
IUCN_LABELS = {"LC": "无危", "NT": "近危", "VU": "易危", "EN": "濒危", "CR": "极危",
               "EW": "野外灭绝", "EX": "灭绝", "DD": "数据缺乏", "NE": "未评估"}


def rarity_score(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 100 else None


def rarity_level(value) -> str:
    score = rarity_score(value)
    if score is None:
        return "unknown"
    for level, upper in zip(RARITY_LEVELS, (8, 25, 50, 75, 101)):
        if score < upper:
            return level[0]
    return "unknown"


def normalize_rarity_options(source: dict | None) -> dict[str, str]:
    source = source if isinstance(source, dict) else {}
    result = dict(RARITY_DEFAULT_OPTIONS)
    for key in result:
        value = source.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if key.endswith("_text"):
            if value and len(value) <= 32 and not any(ord(c) < 32 for c in value):
                result[key] = value
        elif re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            result[key] = value.upper()
    return result


def rarity_metadata(metadata: dict) -> tuple[float | None, str]:
    """侧车优先；已记录的 null 不得从报告/旧兼容字段重新补成有值。"""
    marker_key = next((key for key in (f"XMP-superpicky:{RARITY_SOURCE_FIELD}", RARITY_SOURCE_FIELD)
                       if metadata.get(key)), None)
    if marker_key:
        source = str(metadata[marker_key]).strip()
        if source != bird_name(metadata):
            return None, ""
        prefix = "XMP-superpicky:" if marker_key.startswith("XMP-") else ""
        missing = str(metadata.get(prefix + RARITY_MISSING_FIELD) or "").split(",")
        score = None if RARITY_FIELD in missing else metadata.get(prefix + RARITY_FIELD)
        category = None if IUCN_FIELD in missing else metadata.get(prefix + IUCN_FIELD)
    else:
        def first(field):
            compat = RARITY_COMPAT_FIELDS[field]
            xml_compat = compat.replace("iptcExt:", "Iptc4xmpExt:").replace("iptcCore:", "Iptc4xmpCore:")
            for key in (f"XMP-superpicky:{field}", xml_compat, compat, field, f"report.{field}"):
                value = metadata.get(key)
                if value is not None and value != "":
                    if isinstance(value, (list, tuple)):
                        value = value[0] if value else None
                    return value
            return None
        score, category = first(RARITY_FIELD), first(IUCN_FIELD)
    return rarity_score(score), category.strip() if isinstance(category, str) else ""
