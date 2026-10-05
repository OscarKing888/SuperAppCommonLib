# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import sys
import threading

USER_OPTIONS_FILENAME = "SuperViewerUser.cfg"
PERSISTENT_THUMB_SIZE_LEVELS = (128, 256, 512, 1024, 2048)
KEY_NAVIGATION_FPS_OPTIONS = (1, 2, 4, 8, 10, 12, 13, 15, 20, 24, 25, 30, 40, 45, 50, 60, 120)
KEY_PERF_PROBES_ENABLED = "perf_probes_enabled"
# Birds measured per photo by bird sharpness; 0 = no limit (birds on the focus box go first when limited).
KEY_BIRD_SHARPNESS_MAX_BIRDS = "bird_sharpness_max_birds"
BIRD_SHARPNESS_MAX_BIRDS_LIMIT = 999
# How bird sharpness reads blur from the strongest edges: "standard" (default) or "dense".
KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR = "bird_sharpness_edge_estimator"
BIRD_SHARPNESS_EDGE_ESTIMATORS = ("standard", "dense")
# No-bird tiling. Whole image (no focus point): tile side in px. Manual focus: tiles of the
# frame centre (side % of the frame), the sharpest % of the measurable tiles decide.
KEY_BIRD_SHARPNESS_FULL_TILE = "bird_sharpness_full_tile"
KEY_BIRD_SHARPNESS_MF_CENTER = "bird_sharpness_mf_center"
KEY_BIRD_SHARPNESS_MF_CENTER_PERCENT = "bird_sharpness_mf_center_percent"
KEY_BIRD_SHARPNESS_MF_TILE = "bird_sharpness_mf_tile"
KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT = "bird_sharpness_mf_sharpest_percent"
# Detection models (file names, "auto" = the built-in choice) and SAM2 mask refinement ("" = off).
KEY_BIRD_SHARPNESS_DETECTOR = "bird_sharpness_detector"
KEY_BIRD_SHARPNESS_SAM_MODEL = "bird_sharpness_sam_model"
KEY_BIRD_SHARPNESS_SAM_SCOPE = "bird_sharpness_sam_scope"
BIRD_SHARPNESS_SAM_SCOPES = ("rechecked", "all")
# Enhanced bird search when no bird is found: zoomed overlapping windows over the centre region.
KEY_BIRD_SHARPNESS_ENH_MODE = "bird_sharpness_enhanced_mode"
BIRD_SHARPNESS_ENH_MODES = ("off", "manual", "nobird")
KEY_BIRD_SHARPNESS_ENH_REGION_PERCENT = "bird_sharpness_enhanced_region_percent"
KEY_BIRD_SHARPNESS_ENH_GRID = "bird_sharpness_enhanced_grid"
KEY_BIRD_SHARPNESS_ENH_IMGSZ = "bird_sharpness_enhanced_imgsz"
KEY_BIRD_SHARPNESS_ENH_MIN_CONF_PERCENT = "bird_sharpness_enhanced_min_conf_percent"
KEY_BIRD_SHARPNESS_ENH_LIFT = "bird_sharpness_enhanced_lift"
_MODEL_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.pt$")
# key -> (default, min, max); keep in step with bird_sharpness.params.AnalysisParams
BIRD_SHARPNESS_INT_LIMITS = {
    KEY_BIRD_SHARPNESS_FULL_TILE: (1024, 128, 4096),
    KEY_BIRD_SHARPNESS_MF_CENTER: (1, 0, 1),
    KEY_BIRD_SHARPNESS_MF_CENTER_PERCENT: (50, 10, 100),
    KEY_BIRD_SHARPNESS_MF_TILE: (256, 32, 2048),
    KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT: (10, 1, 100),
    KEY_BIRD_SHARPNESS_ENH_REGION_PERCENT: (70, 20, 100),
    KEY_BIRD_SHARPNESS_ENH_GRID: (2, 1, 6),
    KEY_BIRD_SHARPNESS_ENH_IMGSZ: (1024, 320, 2048),
    KEY_BIRD_SHARPNESS_ENH_MIN_CONF_PERCENT: (50, 5, 95),
    KEY_BIRD_SHARPNESS_ENH_LIFT: (1, 0, 1),
}
# key -> (default, allowed values or None for a model file name)
BIRD_SHARPNESS_TEXT_CHOICES = {
    KEY_BIRD_SHARPNESS_DETECTOR: ("auto", None),
    KEY_BIRD_SHARPNESS_SAM_MODEL: ("", None),
    KEY_BIRD_SHARPNESS_SAM_SCOPE: ("rechecked", BIRD_SHARPNESS_SAM_SCOPES),
    KEY_BIRD_SHARPNESS_ENH_MODE: ("off", BIRD_SHARPNESS_ENH_MODES),
}
# bird_sharpness.params.AnalysisParams.as_params() name -> user option key (the one mapping between them)
BIRD_SHARPNESS_PARAM_KEYS = {
    "max_birds": KEY_BIRD_SHARPNESS_MAX_BIRDS, "edge_estimator": KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR,
    "detector": KEY_BIRD_SHARPNESS_DETECTOR, "sam_model": KEY_BIRD_SHARPNESS_SAM_MODEL,
    "sam_scope": KEY_BIRD_SHARPNESS_SAM_SCOPE, "enh_mode": KEY_BIRD_SHARPNESS_ENH_MODE,
    "enh_region_percent": KEY_BIRD_SHARPNESS_ENH_REGION_PERCENT, "enh_grid": KEY_BIRD_SHARPNESS_ENH_GRID,
    "enh_imgsz": KEY_BIRD_SHARPNESS_ENH_IMGSZ, "enh_min_conf_percent": KEY_BIRD_SHARPNESS_ENH_MIN_CONF_PERCENT,
    "enh_lift": KEY_BIRD_SHARPNESS_ENH_LIFT, "full_tile": KEY_BIRD_SHARPNESS_FULL_TILE,
    "mf_center": KEY_BIRD_SHARPNESS_MF_CENTER, "mf_center_percent": KEY_BIRD_SHARPNESS_MF_CENTER_PERCENT,
    "mf_tile": KEY_BIRD_SHARPNESS_MF_TILE, "mf_sharpest_percent": KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT,
}
_BOOL_PARAMS = ("mf_center", "enh_lift")
DENOISE_DEFAULT_OPTIONS = {
    "denoise_output_mode": "source_subdir",
    "denoise_subdir": "denoised",
    "denoise_output_directory": "",
    "denoise_format": "tiff",
    "denoise_strength": 100,
    "denoise_device": "auto",
    "denoise_workers": 2,
}

_OPTIONS_LOCK = threading.RLock()
_DEFAULT_CPU_COUNT = max(1, os.cpu_count() or 1)
_DEFAULT_METADATA_WORKERS = max(1, min(8, _DEFAULT_CPU_COUNT // 4 or 1))
_DEFAULT_PERSISTENT_THUMB_WORKERS = max(1, _DEFAULT_CPU_COUNT - _DEFAULT_METADATA_WORKERS)
_DEFAULT_OPTIONS = {
    "thumbnail_loader_workers": _DEFAULT_CPU_COUNT,
    "metadata_loader_workers": _DEFAULT_METADATA_WORKERS,
    "persistent_thumb_workers": _DEFAULT_PERSISTENT_THUMB_WORKERS,
    "persistent_thumb_max_size": 128,
    "key_navigation_fps": 24,
    "keep_view_on_switch": 1,
    KEY_PERF_PROBES_ENABLED: 0,
    KEY_BIRD_SHARPNESS_MAX_BIRDS: 0,
    KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR: "standard",
    **{key: limits[0] for key, limits in BIRD_SHARPNESS_INT_LIMITS.items()},
    **{key: choice[0] for key, choice in BIRD_SHARPNESS_TEXT_CHOICES.items()},
    **DENOISE_DEFAULT_OPTIONS,
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


def valid_denoise_subdir(value) -> bool:
    """子目录是单个跨平台名称，禁止路径逃逸和 Windows 保留文件名。"""
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and value not in {".", ".."} and not value.endswith(".")
            and not any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
            and not re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I))


def normalize_user_options(data: dict | None) -> dict[str, int | str]:
    source = data if isinstance(data, dict) else {}
    normalized = dict(_DEFAULT_OPTIONS)
    metadata_missing = "metadata_loader_workers" not in source
    persistent_missing = "persistent_thumb_workers" not in source

    try:
        value = int(source.get("thumbnail_loader_workers", normalized["thumbnail_loader_workers"]) or 0)
    except Exception:
        value = normalized["thumbnail_loader_workers"]
    normalized["thumbnail_loader_workers"] = max(1, value)

    try:
        value = int(source.get("metadata_loader_workers", normalized["metadata_loader_workers"]) or 0)
    except Exception:
        value = normalized["metadata_loader_workers"]
    normalized["metadata_loader_workers"] = max(1, value)

    try:
        value = int(source.get("persistent_thumb_workers", normalized["persistent_thumb_workers"]) or 0)
    except Exception:
        value = normalized["persistent_thumb_workers"]
    if metadata_missing and not persistent_missing and value == _DEFAULT_CPU_COUNT:
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

    try:
        value = int(source.get(KEY_PERF_PROBES_ENABLED, normalized[KEY_PERF_PROBES_ENABLED]) or 0)
    except Exception:
        value = normalized[KEY_PERF_PROBES_ENABLED]
    normalized[KEY_PERF_PROBES_ENABLED] = max(0, min(1, value))

    try:
        value = int(source.get(KEY_BIRD_SHARPNESS_MAX_BIRDS, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        value = 0
    normalized[KEY_BIRD_SHARPNESS_MAX_BIRDS] = max(0, min(BIRD_SHARPNESS_MAX_BIRDS_LIMIT, value))
    value = source.get(KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR)
    if isinstance(value, str) and value in BIRD_SHARPNESS_EDGE_ESTIMATORS:
        normalized[KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR] = value
    for key, (default, low, high) in BIRD_SHARPNESS_INT_LIMITS.items():
        try:
            value = int(source.get(key, default))
        except (TypeError, ValueError, OverflowError):
            value = default
        normalized[key] = max(low, min(high, value))
    for key, (default, allowed) in BIRD_SHARPNESS_TEXT_CHOICES.items():
        value = source.get(key, default)
        if not isinstance(value, str):
            value = default
        value = value.strip()
        if allowed is not None:
            ok = value in allowed
        else:  # a model file name; "auto" / "" are the built-in choices
            ok = value in ("auto", "") or bool(_MODEL_FILE_RE.match(value))
        normalized[key] = value if ok else default

    for key, allowed in (
        ("denoise_output_mode", {"source_subdir", "fixed", "ask"}),
        ("denoise_format", {"tiff", "jpeg"}),
        ("denoise_device", {"auto", "cpu", "cuda", "mps"}),
    ):
        value = source.get(key)
        if isinstance(value, str) and value in allowed:
            normalized[key] = value
    if valid_denoise_subdir(source.get("denoise_subdir")):
        normalized["denoise_subdir"] = source["denoise_subdir"]
    directory = source.get("denoise_output_directory", "")
    if isinstance(directory, str) and "\x00" not in directory:
        normalized["denoise_output_directory"] = directory.strip()
    for key, maximum in (("denoise_strength", 100), ("denoise_workers", 4)):
        try:
            value = int(source.get(key, normalized[key]))
        except (TypeError, ValueError, OverflowError):
            value = normalized[key]
        normalized[key] = max(0 if key == "denoise_strength" else 1, min(maximum, value))

    return normalized


def load_user_options(path: str | None = None) -> dict[str, int | str]:
    cfg_path = path or get_user_options_path()
    if not os.path.isfile(cfg_path):
        return dict(_DEFAULT_OPTIONS)
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(_DEFAULT_OPTIONS)
    return normalize_user_options(data if isinstance(data, dict) else None)


def save_user_options(data: dict | None, path: str | None = None) -> dict[str, int | str]:
    normalized = normalize_user_options(data)
    cfg_path = path or get_user_options_path()
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    return normalized


def apply_runtime_user_options(data: dict | None) -> dict[str, int | str]:
    normalized = normalize_user_options(data)
    with _OPTIONS_LOCK:
        _RUNTIME_OPTIONS.clear()
        _RUNTIME_OPTIONS.update(normalized)
        return dict(_RUNTIME_OPTIONS)


def reload_runtime_user_options() -> dict[str, int | str]:
    return apply_runtime_user_options(load_user_options())


def get_runtime_user_options() -> dict[str, int | str]:
    with _OPTIONS_LOCK:
        return dict(_RUNTIME_OPTIONS)


def get_thumbnail_loader_workers() -> int:
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS["thumbnail_loader_workers"])


def get_metadata_loader_workers() -> int:
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS["metadata_loader_workers"])


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


def get_perf_probes_enabled() -> bool:
    with _OPTIONS_LOCK:
        return bool(_RUNTIME_OPTIONS.get(KEY_PERF_PROBES_ENABLED, 0))


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


def get_bird_sharpness_max_birds() -> int:
    """Birds measured per photo by bird sharpness; 0 means no limit."""
    with _OPTIONS_LOCK:
        return int(_RUNTIME_OPTIONS[KEY_BIRD_SHARPNESS_MAX_BIRDS])


def get_bird_sharpness_edge_estimator() -> str:
    with _OPTIONS_LOCK:
        return str(_RUNTIME_OPTIONS[KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR])


def get_bird_sharpness_params() -> dict:
    """Every bird sharpness analysis option as ``bird_sharpness.params.AnalysisParams`` names."""
    with _OPTIONS_LOCK:
        out = {name: _RUNTIME_OPTIONS[key] for name, key in BIRD_SHARPNESS_PARAM_KEYS.items()}
    for name in _BOOL_PARAMS:
        out[name] = bool(out[name])
    return out


def bird_sharpness_params_to_options(params: dict) -> dict:
    """User option entries for the known ``AnalysisParams`` names in ``params`` (others ignored)."""
    out = {}
    for name, key in BIRD_SHARPNESS_PARAM_KEYS.items():
        if name in params:
            value = params[name]
            out[key] = int(value) if isinstance(value, bool) else value
    return out


def get_bird_sharpness_tile_options() -> dict:
    """No-bird tiling as ``bird_sharpness.metrics.TileOptions`` keyword arguments."""
    with _OPTIONS_LOCK:
        o = _RUNTIME_OPTIONS
        return {"full_tile": int(o[KEY_BIRD_SHARPNESS_FULL_TILE]),
                "mf_center": bool(o[KEY_BIRD_SHARPNESS_MF_CENTER]),
                "mf_center_percent": int(o[KEY_BIRD_SHARPNESS_MF_CENTER_PERCENT]),
                "mf_tile": int(o[KEY_BIRD_SHARPNESS_MF_TILE]),
                "mf_sharpest_percent": int(o[KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT])}
