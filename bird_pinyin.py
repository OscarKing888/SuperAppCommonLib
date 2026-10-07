# -*- coding: utf-8 -*-
"""两款应用共用的 SuperPicky 带声调鸟名词表及元数据字段读取。"""
from __future__ import annotations

import threading

PINYIN_FIELD = "pinyin_name"
PINYIN_SOURCE_FIELD = "pinyin_name_source"
PINYIN_ALIASES = (PINYIN_FIELD, "bird_species_pinyin", "bird_pinyin", "pinyin")
_table: dict[str, str] | None = None
_lock = threading.Lock()


def _load_table() -> dict[str, str]:
    from ._bird_pinyin_data import PINYIN

    return PINYIN


def reset_cache() -> None:
    global _table
    with _lock:
        _table = None


def pinyin_for(chinese_name: str | None) -> str:
    """按完整鸟名查表，保留声调及特殊读音；不猜测未收录名称。"""
    name = (chinese_name or "").strip()
    if not name:
        return ""
    global _table
    if _table is None:
        with _lock:
            if _table is None:
                _table = _load_table()
    return _table.get(name, "")


def _text(value) -> str:
    if isinstance(value, dict):
        return _text(value.get("x-default", ""))
    return value.strip() if isinstance(value, str) else ""


def bird_name(metadata: dict) -> str:
    """显式侧车鸟名/标题优先，兼容列表缓存和旧报告；不拿文件名猜鸟种。"""
    for key in ("XMP-superpicky:bird_species_cn", "XMP-dc:Title", "XMP-dc:title",
                "bird_species_cn", "Title", "title", "XMP:Title", "IFD0:XPTitle",
                "IPTC:ObjectName", "report.bird_species_cn", "report.title"):
        value = _text(metadata.get(key))
        if value and value != "-":
            return value
    return ""


def stored_pinyin(metadata: dict, name: str | None = None) -> str:
    """仅取已保存拼音，不查表；有来源标记时拒绝其它鸟名的旧拼音。"""
    name = bird_name(metadata) if name is None else name
    source = _text(metadata.get(f"XMP-superpicky:{PINYIN_SOURCE_FIELD}") or metadata.get(PINYIN_SOURCE_FIELD))
    if source and source != name:
        return ""
    # 侧车来源标记存在时，缺失拼音也是已保存状态，不能回退到报告的旧鸟种拼音。
    prefixes = ("XMP-superpicky:",) if _text(metadata.get(f"XMP-superpicky:{PINYIN_SOURCE_FIELD}")) else (
        "XMP-superpicky:", "", "report."
    )
    for prefix in prefixes:
        for alias in PINYIN_ALIASES:
            value = _text(metadata.get(prefix + alias))
            if value:
                return value
    return ""
