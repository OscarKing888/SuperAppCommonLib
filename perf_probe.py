# -*- coding: utf-8 -*-
"""Runtime-controlled performance probe logging."""
from __future__ import annotations

import os
import time


_ENV_VARS = ("SuperViewer_PERF_PROBES", "BIRDSTAMP_PERF_PROBES")


def _parse_env_flag(value: str | None) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _env_flag_value() -> bool | None:
    for env_var in _ENV_VARS:
        parsed = _parse_env_flag(os.environ.get(env_var))
        if parsed is not None:
            return parsed
    return None


def perf_probes_enabled() -> bool:
    env_value = _env_flag_value()
    if env_value is not None:
        return bool(env_value)
    try:
        from app_common.superviewer_user_options import get_perf_probes_enabled

        return bool(get_perf_probes_enabled())
    except Exception:
        return False


def perf_counter() -> float:
    return time.perf_counter()


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - float(start or 0.0)) * 1000.0


def perf_log(logger, message: str, *args) -> None:
    if not perf_probes_enabled():
        return
    try:
        logger.info("[PERF_PROBE] " + message, *args)
    except Exception:
        pass
