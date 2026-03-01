# -*- coding: utf-8 -*-
"""
send_to_app 配置：从独立的 extern_app.json 读取/写入外部应用列表。
配置文件与主程序同目录（或由调用方指定 config_dir）。跨平台：Windows / macOS。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

CONFIG_FILENAME = "extern_app.json"


def _default_config_dir() -> str:
    """与 main 一致：脚本或可执行文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv else "."))


def get_config_path(config_dir: str | None = None) -> str:
    """返回 extern_app.json 的完整路径。config_dir 为空时使用默认程序目录。"""
    base = config_dir if config_dir else _default_config_dir()
    return os.path.join(base, CONFIG_FILENAME)


def load_config(config_path: str | None = None, config_dir: str | None = None) -> dict[str, Any]:
    """
    加载外部应用配置。优先使用 config_path（可为文件路径或目录）；
    若为目录或未传，则用 config_dir 或默认目录下的 extern_app.json。
    返回格式: {"apps": [{"name": str, "path": str}, ...]}
    """
    if config_path and os.path.isfile(config_path):
        path = config_path
    else:
        dir_ = config_dir if config_dir else (config_path if config_path and os.path.isdir(config_path) else None)
        path = get_config_path(dir_)
    out: dict[str, Any] = {"apps": []}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "apps" in data and isinstance(data["apps"], list):
            out["apps"] = [
                {"name": str(item.get("name", "")), "path": str(item.get("path", ""))}
                for item in data["apps"]
                if isinstance(item, dict)
            ]
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
    data = {"apps": [{"name": str(a.get("name", "")), "path": str(a.get("path", ""))} for a in apps]}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        raise
