# -*- coding: utf-8 -*-
"""
从 about.cfg 或外部配置文件加载“关于”信息。
"""
from __future__ import annotations

import json
import os

_DEFAULT_ABOUT = {
    "app_name": "SuperEXIF",
    "version": "1.0.0",
    "作者": "osk.ch",
    "网站": "https://xhslink.com/m/A2cowPsYj8P",
}


def _sanitize(s: str) -> str:
    """清理用于界面显示的字符串。"""
    if not s or not isinstance(s, str):
        return ""
    result = []
    for c in s:
        code = ord(c)
        if code == 0:
            result.append(" ")
        elif code < 32 and c not in "\t\n\r":
            result.append(" ")
        else:
            result.append(c)
    return "".join(result).strip()


def _load_about_from_file(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        about = data.get("about") if isinstance(data.get("about"), dict) else {}
        for k, v in about.items():
            if isinstance(v, str) and v.strip():
                out[k] = _sanitize(v)
    except (OSError, json.JSONDecodeError):
        pass
    return out


def _module_cfg_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "about.cfg")


def load_about_info(override_path: str | None = None) -> dict:
    """
    加载“关于”信息：以模块内 about.cfg 为默认，若提供 override_path 且文件存在则用其 about 覆盖/补充。

    :param override_path: 可选，外部配置文件路径（JSON，含 "about" 键）
    :return: 关于信息字典，至少包含 app_name、version 等
    """
    base = _load_about_from_file(_module_cfg_path())
    for key, val in _DEFAULT_ABOUT.items():
        if key not in base:
            base[key] = val
    if override_path and os.path.isfile(override_path):
        over = _load_about_from_file(override_path)
        for k, v in over.items():
            base[k] = v
    return base
