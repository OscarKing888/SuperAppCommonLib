# -*- coding: utf-8 -*-
"""从 UTF-8 about.cfg 加载信息和图片；覆盖层的相对路径始终属于该配置。"""
from __future__ import annotations

import json
import os

from app_common.log import get_logger

_log = get_logger("about_dialog")
_DEFAULT_ABOUT = {"app_name": "{app_name}", "version": "{version}", "作者": "追鸟奇遇记(osk.ch)"}


def _sanitize(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(" " if ord(c) < 32 and c not in "\t\n\r" else c for c in s).strip()


def _load_raw_cfg(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            return data
        _log.warning("About config %s must contain a JSON object", path)
    except json.JSONDecodeError as exc:
        _log.warning("Invalid JSON in about config %s at line %d column %d: %s", path, exc.lineno, exc.colno, exc.msg)
    except (OSError, UnicodeError) as exc:
        _log.warning("Unable to read about config %s: %s", path, exc)
    return {}


def _module_cfg_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "about.cfg")


def _config_layers(override_path, override_paths):
    for path in (_module_cfg_path(), override_path, *override_paths):
        if path and os.path.isfile(path):
            yield os.fspath(path), _load_raw_cfg(path)


def _apply_substitutions(info: dict, subs: dict[str, str]) -> dict:
    result = dict(info)
    for key, value in result.items():
        for placeholder, replacement in subs.items():
            value = value.replace(f"{{{placeholder}}}", replacement)
        result[key] = value
    return result


def _normalize_images(raw_list: list, path: str, base_dir: str | None = None) -> list[dict]:
    base = base_dir or os.path.dirname(os.path.abspath(path))
    result = []
    for item in raw_list:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"].strip():
            continue
        raw_path = item["path"]
        resolved = raw_path if os.path.isabs(raw_path) else os.path.normpath(os.path.join(base, raw_path))
        if not os.path.isfile(resolved):
            _log.warning("About image from %s does not exist: %s", path, resolved)
            continue
        try:
            size = max(32, min(2048, int(item.get("size", 120))))
        except (TypeError, ValueError, OverflowError):
            _log.warning("Invalid about image size in %s for %s; using 120", path, raw_path)
            size = 120
        result.append({"path": resolved, "label": _sanitize(item.get("label", "")),
                       "size": size, "url": _sanitize(item.get("url", ""))})
    return result


def load_about_images(override_path: str | None = None, *, base_dir: str | None = None,
                      override_paths=()) -> list[dict]:
    """按模块默认、应用、额外覆盖层顺序读取 images。

    缺少 images 时继承；显式 [] 时清空。坏图片不会恢复成其它配置的二维码。
    base_dir 仅兼容调用方对 override_path 的显式指定，不影响默认层/额外层。
    """
    images = []
    for path, data in _config_layers(override_path, override_paths):
        raw_list = data.get("images")
        if isinstance(raw_list, list):
            base = base_dir if override_path and path == os.fspath(override_path) else None
            images = _normalize_images(raw_list, path, base)
    return images


def load_about_info(override_path: str | None = None, *, app_name: str | None = None,
                    version: str | None = None, override_paths=()) -> dict:
    """按相同覆盖顺序加载信息，最后替换名称/版本占位符。空字段可隐藏默认项。"""
    info = dict(_DEFAULT_ABOUT)
    for _, data in _config_layers(override_path, override_paths):
        about = data.get("about")
        if isinstance(about, dict):
            info.update({_sanitize(k): _sanitize(v) for k, v in about.items()
                         if isinstance(k, str) and k.strip() and isinstance(v, str)})
    subs = {}
    if app_name is not None:
        subs["app_name"] = app_name
    if version is not None:
        subs["version"] = version
    return _apply_substitutions(info, subs)
