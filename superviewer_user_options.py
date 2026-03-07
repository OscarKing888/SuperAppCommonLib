# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import threading

USER_OPTIONS_FILENAME = "SuperViewerUser.cfg"
PERSISTENT_THUMB_SIZE_LEVELS = (128, 256, 512)
KEY_NAVIGATION_FPS_OPTIONS = (8, 10, 12, 13, 15, 20, 24, 25, 30, 40, 48, 50, 60, 120)

_OPTIONS_LOCK = threading.RLock()
_DEFAULT_CPU_COUNT = max(1, os.cpu_count() or 1)
_DEFAULT_OPTIONS = {
    "thumbnail_loader_workers": _DEFAULT_CPU_COUNT,
    "persistent_thumb_workers": _DEFAULT_CPU_COUNT,
    "persistent_thumb_max_size": 128,
    "key_navigation_fps": 24,
    "keep_view_on_switch": 1,
}
_RUNTIME_OPTIONS = dict(_DEFAULT_OPTIONS)


def _get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if not app_dir:
        app_dir = os.getcwd()
    return app_dir


def get_user_options_path() -> str:
    return os.path.join(_get_app_dir(), USER_OPTIONS_FILENAME)


def normalize_user_options(data: dict | None) -> dict[str, int]:
    source = data if isinstance(data, dict) else {}
    normalized = dict(_DEFAULT_OPTIONS)

    try:
        value = int(source.get("thumbnail_loader_workers", normalized["thumbnail_loader_workers"]) or 0)
    except Exception:
        value = normalized["thumbnail_loader_workers"]
    normalized["thumbnail_loader_workers"] = max(1, value)

    try:
        value = int(source.get("persistent_thumb_workers", normalized["persistent_thumb_workers"]) or 0)
    except Exception:
        value = normalized["persistent_thumb_workers"]
    normalized["persistent_thumb_workers"] = max(1, value)

    try:
        value = int(source.get("persistent_thumb_max_size", normalized["persistent_thumb_max_size"]) or 0)
    except Exception:
        value = normalized["persistent_thumb_max_size"]
    if value not in PERSISTENT_THUMB_SIZE_LEVELS:
        value = normalized["persistent_thumb_max_size"]
    normalized["persistent_thumb_max_size"] = value

    try:
        value = int(source.get("key_navigation_fps", normalized["key_navigation_fps"]) or 0)
    except Exception:
        value = normalized["key_navigation_fps"]
    if value not in KEY_NAVIGATION_FPS_OPTIONS:
        value = normalized["key_navigation_fps"]
    normalized["key_navigation_fps"] = value

    try:
        value = int(source.get("keep_view_on_switch", 1) or 0)
    except Exception:
        value = 1
    normalized["keep_view_on_switch"] = max(0, min(1, value))

    return normalized


def load_user_options(path: str | None = None) -> dict[str, int]:
    cfg_path = path or get_user_options_path()
    if not os.path.isfile(cfg_path):
        return dict(_DEFAULT_OPTIONS)
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(_DEFAULT_OPTIONS)
    return normalize_user_options(data if isinstance(data, dict) else None)


def save_user_options(data: dict | None, path: str | None = None) -> dict[str, int]:
    normalized = normalize_user_options(data)
    cfg_path = path or get_user_options_path()
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    return normalized


def apply_runtime_user_options(data: dict | None) -> dict[str, int]:
    normalized = normalize_user_options(data)
    with _OPTIONS_LOCK:
        _RUNTIME_OPTIONS.clear()
        _RUNTIME_OPTIONS.update(normalized)
        return dict(_RUNTIME_OPTIONS)


def reload_runtime_user_options() -> dict[str, int]:
    return apply_runtime_user_options(load_user_options())


def get_runtime_user_options() -> dict[str, int]:
    with _OPTIONS_LOCK:
        return dict(_RUNTIME_OPTIONS)


def get_thumbnail_loader_workers() -> int:
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS["thumbnail_loader_workers"])


def get_persistent_thumb_workers() -> int:
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS["persistent_thumb_workers"])


def get_persistent_thumb_max_size() -> int:
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS["persistent_thumb_max_size"])


def get_key_navigation_fps() -> int:
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS["key_navigation_fps"])


def get_keep_view_on_switch() -> bool:
    with _OPTIONS_LOCK:
        return bool(_RUNTIME_OPTIONS.get("keep_view_on_switch", 1))


def get_persistent_thumb_sizes(max_size: int | None = None) -> list[int]:
    cap = int(max_size or get_persistent_thumb_max_size())
    if cap not in PERSISTENT_THUMB_SIZE_LEVELS:
        cap = get_persistent_thumb_max_size()
    return [size for size in PERSISTENT_THUMB_SIZE_LEVELS if size <= cap]


def get_preferred_persistent_thumb_sizes(requested_size: int, max_size: int | None = None) -> list[int]:
    sizes = get_persistent_thumb_sizes(max_size)
    if not sizes:
        return []
    req = max(1, int(requested_size))
    larger = [size for size in sizes if size >= req]
    smaller = [size for size in sizes if size < req]
    return larger + list(reversed(smaller))


apply_runtime_user_options(None)
