# -*- coding: utf-8 -*-
"""app_common.stat – simple timing/statistics for performance diagnosis.

Usage::
    from app_common.stat import stat_begin, stat_end, stat_span, stat_report, stat_reset

    # Context manager (recommended)
    with stat_span("load_preview"):
        load_preview_source()

    # Manual
    stat_begin("setup_ui")
    setup_ui()
    stat_end("setup_ui")

    # After run, print or log
    stat_report()  # prints to stderr
    lines = stat_report(return_lines=True)  # returns list of strings
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

# (start_time, end_time | None) per name
_span_start: dict[str, float] = {}
_span_elapsed: dict[str, float] = {}  # name -> total seconds (when ended)


def stat_reset() -> None:
    """Clear all recorded spans."""
    _span_start.clear()
    _span_elapsed.clear()


def stat_begin(name: str) -> None:
    """Start a named span."""
    _span_start[name] = time.perf_counter()
    if name in _span_elapsed:
        del _span_elapsed[name]


def stat_end(name: str) -> float | None:
    """End a named span; return elapsed seconds or None if not started."""
    if name not in _span_start:
        return None
    elapsed = time.perf_counter() - _span_start.pop(name)
    _span_elapsed[name] = _span_elapsed.get(name, 0) + elapsed
    return elapsed


@contextmanager
def stat_span(name: str) -> Generator[None, None, None]:
    """Context manager to record elapsed time for a named span."""
    stat_begin(name)
    try:
        yield
    finally:
        stat_end(name)


def stat_report(*, return_lines: bool = False) -> list[str] | None:
    """Print all spans to stderr (name: seconds), or return list of lines."""
    lines: list[str] = []
    for k in sorted(_span_elapsed.keys()):
        t = _span_elapsed[k]
        lines.append(f"[stat] {k}: {t:.3f}s")
    if return_lines:
        return lines
    for line in lines:
        print(line, file=__import__("sys").stderr, flush=True)
    return None
