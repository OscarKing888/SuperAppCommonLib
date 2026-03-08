# -*- coding: utf-8 -*-
"""
send_to_app 配置：从独立的 extern_app.json 读取/写入外部应用列表。
默认写入用户目录（也可由调用方通过 config_dir 指定）。跨平台：Windows / macOS。
支持可选 app_id，用于按本地 socket 协议热发送到已运行实例。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

CONFIG_FILENAME = "extern_app.json"
APP_CONFIG_DIRNAME = "SuperViewer"
LEGACY_APP_CONFIG_DIRNAMES = ("BirdStamp",)


def _normalize_app_entry(item: Any) -> dict[str, str] | None:
    """规范化单个外部应用配置，兼容可选 app_id 字段。"""
    if not isinstance(item, dict):
        return None
    normalized = {
        "name": str(item.get("name", "")),
        "path": str(item.get("path", "")),
    }
    app_id = str(item.get("app_id", "")).strip()
    if app_id:
        normalized["app_id"] = app_id
    return normalized


def _build_user_config_dir(app_dir_name: str) -> str:
    """按应用目录名返回跨平台用户配置目录。"""
    if sys.platform == "win32":
        base = (
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        )
        return os.path.join(base, app_dir_name)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_dir_name)
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
        app_dir_name,
    )


def _user_config_dir() -> str:
    """返回当前应用默认使用的用户配置目录。"""
    return _build_user_config_dir(APP_CONFIG_DIRNAME)


def _local_config_dir() -> str:
    """返回历史版本曾使用的程序目录（源码运行时为脚本目录，打包后为可执行文件目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv else "."))


def _legacy_config_paths() -> list[str]:
    """兼容旧版本：曾将 extern_app.json 放在程序目录或旧的用户目录名下。"""
    candidates: list[str] = []
    for legacy_dir_name in LEGACY_APP_CONFIG_DIRNAMES:
        legacy_user_path = os.path.join(_build_user_config_dir(legacy_dir_name), CONFIG_FILENAME)
        if os.path.isfile(legacy_user_path):
            candidates.append(legacy_user_path)
    legacy_local_path = os.path.join(_local_config_dir(), CONFIG_FILENAME)
    if os.path.isfile(legacy_local_path):
        candidates.append(legacy_local_path)
    return candidates


def _default_config_dir() -> str:
    """默认使用用户可写目录，避免配置文件落到源码目录或 macOS app bundle。"""
    return _user_config_dir()


def get_config_path(config_dir: str | None = None) -> str:
    """返回 extern_app.json 的完整路径。config_dir 为空时使用默认用户目录。"""
    base = config_dir if config_dir else _default_config_dir()
    return os.path.join(base, CONFIG_FILENAME)


def load_config(config_path: str | None = None, config_dir: str | None = None) -> dict[str, Any]:
    """
    加载外部应用配置。优先使用 config_path（可为文件路径或目录）；
    若为目录或未传，则用 config_dir 或默认目录下的 extern_app.json。
    返回格式: {"apps": [{"name": str, "path": str, "app_id": str?}, ...]}
    """
    if config_path and os.path.isfile(config_path):
        path = config_path
    else:
        dir_ = config_dir if config_dir else (config_path if config_path and os.path.isdir(config_path) else None)
        path = get_config_path(dir_)
    out: dict[str, Any] = {"apps": []}
    if not os.path.isfile(path):
        if config_path is None and config_dir is None:
            for legacy in _legacy_config_paths():
                if os.path.normcase(os.path.normpath(legacy)) == os.path.normcase(os.path.normpath(path)):
                    continue
                path = legacy
                break
            else:
                return out
        else:
            return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "apps" in data and isinstance(data["apps"], list):
            out["apps"] = [entry for item in data["apps"] if (entry := _normalize_app_entry(item)) is not None]
    except Exception:
        pass
    return out


def save_config(apps: list[dict[str, str]], config_path: str | None = None, config_dir: str | None = None) -> None:
    """将外部应用列表写入 extern_app.json。"""
    if config_path and not os.path.isdir(config_path):
        path = config_path
    else:
        dir_ = config_dir if config_dir else (config_path if config_path and os.path.isdir(config_path) else None)
        path = get_config_path(dir_)
    data = {"apps": [entry for app in apps if (entry := _normalize_app_entry(app)) is not None]}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        raise
