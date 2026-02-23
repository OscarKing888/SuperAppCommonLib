# -*- coding: utf-8 -*-
"""
EXIF 模块配置：从 exif.cfg 或外部 override 路径加载/保存。
"""
from __future__ import annotations

import json
import os


def _module_cfg_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "exif.cfg")


def load_exif_settings(override_path: str | None = None) -> dict:
    """读取 EXIF 配置：先读模块 exif.cfg 作为默认，若提供 override_path 且文件存在则用其合并覆盖。"""
    base = {}
    try:
        p = _module_cfg_path()
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                base = json.load(f)
        if not isinstance(base, dict):
            base = {}
    except Exception:
        pass
    if override_path and os.path.isfile(override_path):
        try:
            with open(override_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in (
                    "exif_tag_priority",
                    "exif_tag_label_chinese",
                    "exif_tag_hidden",
                    "exif_tag_names_zh",
                    "hyperfocal_coc_mm",
                    "exif_tag_name_token_map_zh",
                ):
                    if k in data:
                        base[k] = data[k]
        except Exception:
            pass
    return base


def save_exif_settings_to_path(override_path: str, key: str, value) -> None:
    """将单个配置键写入 override 文件（先读全量再合并后写回）。"""
    data = {}
    if os.path.isfile(override_path):
        try:
            with open(override_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    data[key] = value
    try:
        with open(override_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
