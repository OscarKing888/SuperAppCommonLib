# -*- coding: utf-8 -*-
"""
从 about.cfg 或外部配置文件加载"关于"信息及配套图片列表。
"""
from __future__ import annotations

import json
import os

_DEFAULT_ABOUT = {
    "app_name": "{app_name}",
    "version": "{version}",
    "作者": "徒步追鸟(osk.ch)",
    "我的小红书": "https://xhslink.com/m/A2cowPsYj8P",
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


def _load_raw_cfg(path: str) -> dict:
    """读取 JSON 配置文件，返回顶层字典；失败时返回空字典。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_about_from_file(path: str) -> dict:
    out = {}
    data = _load_raw_cfg(path)
    about = data.get("about") if isinstance(data.get("about"), dict) else {}
    for k, v in about.items():
        if isinstance(v, str) and v.strip():
            out[k] = _sanitize(v)
    return out


def _load_images_from_file(path: str, base_dir: str | None = None) -> list[dict]:
    """从配置文件中读取 images 列表，解析并返回规范化的图片项列表。

    每个图片项字段：
      - path (str): 图片文件路径（相对于 base_dir 或绝对路径）
      - label (str): 图片下方的说明文字（可为空）
      - size (int): 显示宽度（像素），默认 120
      - url (str): 点击后打开的链接（可为空）
    """
    data = _load_raw_cfg(path)
    raw_list = data.get("images")
    if not isinstance(raw_list, list):
        return []
    _base = base_dir or os.path.dirname(os.path.abspath(path))
    result: list[dict] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path", "")
        if not raw_path or not isinstance(raw_path, str):
            continue
        resolved = raw_path if os.path.isabs(raw_path) else os.path.normpath(os.path.join(_base, raw_path))
        result.append({
            "path": resolved,
            "label": _sanitize(str(item.get("label", ""))),
            "size": max(32, int(item.get("size", 120))),
            "url": _sanitize(str(item.get("url", ""))),
        })
    return result


def _module_cfg_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "about.cfg")


def _apply_substitutions(info: dict, subs: dict[str, str]) -> dict:
    """将 info 中所有字符串值内的 ``{key}`` 占位符替换为 subs 中对应的值。"""
    if not subs:
        return info
    result: dict = {}
    for k, v in info.items():
        if isinstance(v, str):
            for placeholder, replacement in subs.items():
                v = v.replace(f"{{{placeholder}}}", replacement)
        result[k] = v
    return result


def load_about_images(
    override_path: str | None = None,
    *,
    base_dir: str | None = None,
) -> list[dict]:
    """加载关于对话框中要展示的图片列表（如二维码、网站图片等）。

    优先读取模块内 about.cfg，若提供 override_path 且文件存在则用其 images 替换。
    路径解析：相对路径以所在配置文件目录为基准；也可通过 base_dir 显式指定。

    :param override_path: 可选，外部配置文件路径
    :param base_dir: 可选，相对路径的解析基准目录（默认为配置文件所在目录）
    :return: 图片项列表，每项含 path / label / size / url
    """
    images = _load_images_from_file(_module_cfg_path(), base_dir=base_dir)
    if override_path and os.path.isfile(override_path):
        override_images = _load_images_from_file(override_path, base_dir=base_dir)
        if override_images:
            images = override_images
    return images


def load_about_info(
    override_path: str | None = None,
    *,
    app_name: str | None = None,
    version: str | None = None,
) -> dict:
    """
    加载"关于"信息：以模块内 about.cfg 为默认，若提供 override_path 且文件存在则用其 about 覆盖/补充。
    最后将 ``{app_name}`` / ``{version}`` 占位符替换为传入的实际值。

    :param override_path: 可选，外部配置文件路径（JSON，含 "about" 键）
    :param app_name: 应用名称，替换 cfg 中的 ``{app_name}`` 占位符
    :param version: 版本字符串，替换 cfg 中的 ``{version}`` 占位符
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
    subs: dict[str, str] = {}
    if app_name:
        subs["app_name"] = app_name
    if version:
        subs["version"] = version
    return _apply_substitutions(base, subs)
