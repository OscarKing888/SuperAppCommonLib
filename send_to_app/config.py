# -*- coding: utf-8 -*-
"""
send_to_app 配置：从独立的 extern_app.json 读取/写入外部应用列表。
默认写入用户目录（也可由调用方通过 config_dir 指定）。跨平台：Windows / macOS。
支持可选 app_id，用于按本地 socket 协议热发送到已运行实例。

SuperViewer 会在加载配置时顺带检查同级目录是否存在 SuperBirdStamp，
若存在则自动补充到 extern_app.json，减少首次使用时的手工配置。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

CONFIG_FILENAME = "extern_app.json"
APP_CONFIG_DIRNAME = "SuperViewer"
LEGACY_APP_CONFIG_DIRNAMES = ("BirdStamp",)
AUTO_BIRDSTAMP_APP_NAME = "SuperBirdStamp"
AUTO_BIRDSTAMP_APP_ID = "birdstamp"


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


def _normalize_compare_path(path: str) -> str:
    """将应用路径归一化为便于比较的形式，兼容 macOS .app 简写路径。"""
    text = str(path or "").strip()
    if not text:
        return ""
    candidate = os.path.abspath(text)
    if sys.platform == "darwin" and not candidate.lower().endswith(".app"):
        app_bundle = candidate + ".app"
        if os.path.isdir(app_bundle):
            candidate = app_bundle
        elif os.path.isdir(candidate):
            folder_name = os.path.basename(candidate)
            nested_bundle = os.path.join(candidate, folder_name + ".app")
            if os.path.isdir(nested_bundle):
                candidate = nested_bundle
    return os.path.normcase(os.path.normpath(candidate))


def _mac_bundle_root_from_executable(executable_path: str) -> str | None:
    """从 macOS 可执行文件路径回溯 .app bundle，再返回 bundle 所在目录。"""
    current = os.path.abspath(executable_path)
    while True:
        if current.lower().endswith(".app"):
            return os.path.dirname(current)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _candidate_probe_roots() -> list[str]:
    """返回自动探测外部应用时需要检查的根目录列表。"""
    roots: list[str] = []

    def add_root(path: str | None) -> None:
        text = str(path or "").strip()
        if not text:
            return
        normalized = os.path.normcase(os.path.normpath(os.path.abspath(text)))
        if normalized not in roots:
            roots.append(normalized)

    local_dir = _local_config_dir()
    add_root(local_dir)
    add_root(os.path.dirname(local_dir))

    launch_path = os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else (sys.argv[0] if sys.argv else ".")
    )
    add_root(os.path.dirname(launch_path))
    add_root(os.path.dirname(os.path.dirname(launch_path)))

    if sys.platform == "darwin":
        bundle_root = _mac_bundle_root_from_executable(launch_path)
        add_root(bundle_root)

    if not getattr(sys, "frozen", False):
        add_root(os.getcwd())
        add_root(os.path.dirname(os.getcwd()))

    return roots


def _candidate_birdstamp_paths() -> list[str]:
    """返回按当前平台推断出来的 SuperBirdStamp 候选路径。"""
    relative_candidates: tuple[str, ...]
    if sys.platform == "darwin":
        relative_candidates = (
            "SuperBirdStamp.app",
            os.path.join("dist", "SuperBirdStamp.app"),
        )
    elif sys.platform == "win32":
        relative_candidates = (
            "SuperBirdStamp.exe",
            os.path.join("SuperBirdStamp", "SuperBirdStamp.exe"),
            os.path.join("dist", "SuperBirdStamp.exe"),
            os.path.join("dist", "SuperBirdStamp", "SuperBirdStamp.exe"),
        )
    else:
        relative_candidates = (
            "SuperBirdStamp",
            os.path.join("dist", "SuperBirdStamp"),
        )

    candidates: list[str] = []
    for root in _candidate_probe_roots():
        for relative in relative_candidates:
            path = os.path.normpath(os.path.join(root, relative))
            if path not in candidates:
                candidates.append(path)
    return candidates


def _discover_birdstamp_app() -> dict[str, str] | None:
    """若同级目录存在 SuperBirdStamp，则返回自动补充的外部应用项。"""
    for path in _candidate_birdstamp_paths():
        if path.lower().endswith(".app"):
            exists = os.path.isdir(path)
        else:
            exists = os.path.isfile(path)
        if not exists:
            continue
        return {
            "name": AUTO_BIRDSTAMP_APP_NAME,
            "path": path,
            "app_id": AUTO_BIRDSTAMP_APP_ID,
        }
    return None


def _merge_auto_app(apps: list[dict[str, str]], auto_app: dict[str, str]) -> bool:
    """
    将自动发现的应用合并到现有配置中。

    - 已有相同路径时，仅补齐缺失的 app_id / name。
    - 已有相同 app_id 时，保留用户自定义路径，只补齐缺失名称。
    - 都不存在时追加新项。
    """
    auto_path = _normalize_compare_path(auto_app.get("path", ""))
    auto_app_id = str(auto_app.get("app_id", "")).strip()
    for index, app in enumerate(apps):
        existing = dict(app)
        existing_path = _normalize_compare_path(existing.get("path", ""))
        existing_app_id = str(existing.get("app_id", "")).strip()
        changed = False

        if auto_path and existing_path == auto_path:
            if auto_app_id and not existing_app_id:
                existing["app_id"] = auto_app_id
                changed = True
            if not str(existing.get("name", "")).strip():
                existing["name"] = auto_app["name"]
                changed = True
            if changed:
                apps[index] = existing
            return changed

        if auto_app_id and existing_app_id.lower() == auto_app_id.lower():
            if not str(existing.get("name", "")).strip():
                existing["name"] = auto_app["name"]
                changed = True
            if changed:
                apps[index] = existing
            return changed

    apps.append(dict(auto_app))
    return True


def _ensure_auto_external_apps(apps: list[dict[str, str]]) -> bool:
    """自动探测并补充内置推荐的外部应用。"""
    auto_app = _discover_birdstamp_app()
    if not auto_app:
        return False
    return _merge_auto_app(apps, auto_app)


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
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "apps" in data and isinstance(data["apps"], list):
                out["apps"] = [entry for item in data["apps"] if (entry := _normalize_app_entry(item)) is not None]
        except Exception:
            pass
    if _ensure_auto_external_apps(out["apps"]):
        try:
            save_config(out["apps"], config_path=path)
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
