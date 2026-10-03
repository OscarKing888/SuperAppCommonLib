# -*- coding: utf-8 -*-
"""按拍摄时间计算连拍分组，结果与 SuperPicky 的 ``burst_id`` / ``burst_position`` 兼容。

规则与 SuperPicky ``BurstDetector.detect_groups_by_time_only`` 一致：同一目录内按
亚秒精度拍摄时间排序，相邻两张间隔不超过阈值即视为同一连拍，张数达到下限才成组。
``burst_id`` 在每个目录内从 1 编号（文件浏览器按「目录 + burst_id」分组），
``burst_position`` 为组内从 1 开始的序号。

同目录同 stem 的文件（如 RAW+JPG）共用一个 XMP sidecar，视为同一次拍摄。
结果只写入同 stem XMP sidecar 的 ``XMP-superpicky:burst_id`` / ``burst_position``；
report.db 只读。不在连拍中的照片若 report.db 仍有连拍值，写入 ``0``（SuperPicky 约定
0 = 非连拍）覆盖；仅 sidecar 有旧值则删除；两者都没有则不创建 sidecar。
"""
from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

DEFAULT_MAX_GAP_MS = 250
DEFAULT_MIN_COUNT = 4
# 缺少亚秒时间时只有秒级精度，同一连拍相邻两张可能相差整 1 秒。
NO_SUBSEC_MAX_GAP_MS = 1000

BURST_ID_KEY = "burst_id"
BURST_POSITION_KEY = "burst_position"
BURST_ID_FIELD = f"XMP-superpicky:{BURST_ID_KEY}"
BURST_POSITION_FIELD = f"XMP-superpicky:{BURST_POSITION_KEY}"
NOT_BURST_VALUE = "0"

CAPTURE_TIME_TAGS = [
    "-Composite:SubSecDateTimeOriginal",
    "-DateTimeOriginal",
    "-SubSecTimeOriginal",
    "-Composite:SubSecCreateDate",
    "-CreateDate",
]
# 优先带亚秒的组合时间：exiftool -n 会把 SubSecTimeOriginal "045" 读成整数 45。
_COMPOSITE_TIME_KEYS = (
    "Composite:SubSecDateTimeOriginal",
    "SubSecDateTimeOriginal",
)
_BASE_TIME_KEYS = (
    "ExifIFD:DateTimeOriginal",
    "EXIF:DateTimeOriginal",
    "XMP-exif:DateTimeOriginal",
    "DateTimeOriginal",
)
_SUBSEC_KEYS = (
    "ExifIFD:SubSecTimeOriginal",
    "EXIF:SubSecTimeOriginal",
    "XMP-exif:SubSecTimeOriginal",
    "SubSecTimeOriginal",
)
_FALLBACK_TIME_KEYS = (
    "Composite:SubSecCreateDate",
    "SubSecCreateDate",
    "ExifIFD:CreateDate",
    "EXIF:CreateDate",
    "XMP-xmp:CreateDate",
    "CreateDate",
)
_DATETIME_RE = re.compile(
    r"(\d{4})[:\-](\d{1,2})[:\-](\d{1,2})[ T](\d{1,2}):(\d{1,2}):(\d{1,2})(?:[.,](\d+))?"
)


@dataclass(frozen=True)
class CaptureTime:
    seconds: float
    has_subsec: bool


@dataclass
class BurstPlanStats:
    directories: int = 0
    shots: int = 0
    timed_shots: int = 0
    groups: int = 0
    burst_shots: int = 0


def _first_value(rec: Mapping, keys: Iterable[str]):
    for key in keys:
        value = rec.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _parse_datetime_text(value) -> CaptureTime | None:
    match = _DATETIME_RE.search(str(value or ""))
    if not match:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    try:
        dt = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    # 忽略时区：同一目录的照片来自同一时钟，只比较相对间隔。
    seconds = float(calendar.timegm(dt.timetuple()))
    fraction = match.group(7)
    if fraction:
        seconds += float(f"0.{fraction}")
    return CaptureTime(seconds, bool(fraction))


def capture_time_from_metadata(rec: Mapping | None) -> CaptureTime | None:
    """从 exiftool ``-G1`` 风格记录取拍摄时间（秒，带亚秒时 ``has_subsec``）。"""
    if not isinstance(rec, Mapping):
        return None
    parsed = _parse_datetime_text(_first_value(rec, _COMPOSITE_TIME_KEYS))
    if parsed is not None and parsed.has_subsec:
        return parsed
    base = _parse_datetime_text(_first_value(rec, _BASE_TIME_KEYS))
    if base is not None:
        if base.has_subsec:
            return base
        subsec = _first_value(rec, _SUBSEC_KEYS)
        # 只有文本亚秒可信；整数已丢失前导零。
        digits = re.sub(r"\D+", "", subsec) if isinstance(subsec, str) else ""
        if digits:
            return CaptureTime(base.seconds + float(f"0.{digits}"), True)
        return parsed or base
    if parsed is not None:
        return parsed
    return _parse_datetime_text(_first_value(rec, _FALLBACK_TIME_KEYS))


def read_capture_times(paths: list[str], *, cancel_event=None) -> dict[str, CaptureTime | None]:
    """一次 exiftool 调用读取一批文件的拍摄时间；不可用或失败时对应值为 None。

    时间标签都在 EXIF IFD，``-fast2`` 跳过 MakerNote 与文件尾扫描。
    """
    import json

    from app_common.exif_io.exiftool_path import get_exiftool_executable_path
    from app_common.exif_io.exiftool_runner import run_exiftool

    norm_paths = [os.path.normpath(p) for p in paths]
    result: dict[str, CaptureTime | None] = {p: None for p in norm_paths}
    executable = get_exiftool_executable_path()
    if not norm_paths or not executable:
        return result
    args = ["-fast2", "-j", "-G1", "-n", "-charset", "filename=UTF8", *CAPTURE_TIME_TAGS, *norm_paths]
    cp = run_exiftool(executable, args, text=True, encoding="utf-8", errors="replace", cancel_event=cancel_event)
    stdout = (cp.stdout or "").strip()
    if not stdout:
        return result
    for rec in json.loads(stdout):
        if isinstance(rec, dict):
            source = os.path.normpath(str(rec.get("SourceFile") or ""))
            if source in result:
                result[source] = capture_time_from_metadata(rec)
    return result


def collect_directory_images(directory: str, *, recursive: bool = False) -> list[str]:
    """列出目录内受支持的图片（跳过隐藏文件/目录），按路径排序。"""
    from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

    found: list[str] = []
    if not os.path.isdir(directory):
        return found
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")) if recursive else []
        for name in sorted(filenames):
            if not name.startswith(".") and os.path.splitext(name)[1].lower() in SUPPORTED_IMAGE_EXTENSIONS:
                found.append(os.path.normpath(os.path.join(dirpath, name)))
    return found


def _shot_key(path: str) -> tuple[str, str]:
    norm = os.path.normpath(path)
    return (
        os.path.normcase(os.path.dirname(norm)),
        os.path.normcase(os.path.splitext(os.path.basename(norm))[0]),
    )


def group_shot_paths(paths: Iterable[str]) -> list[list[str]]:
    """同目录同 stem 的文件归为一次拍摄，保持输入顺序。"""
    shots: dict[tuple[str, str], list[str]] = {}
    for path in paths:
        norm = os.path.normpath(path)
        shots.setdefault(_shot_key(norm), []).append(norm)
    return list(shots.values())


def _shot_time(paths: list[str], capture_times: Mapping[str, CaptureTime | None]) -> CaptureTime | None:
    found = [capture_times.get(path) for path in paths]
    found = [item for item in found if item is not None]
    if not found:
        return None
    with_subsec = [item for item in found if item.has_subsec]
    return min(with_subsec or found, key=lambda item: item.seconds)


def plan_bursts(
    paths: Iterable[str],
    capture_times: Mapping[str, CaptureTime | None],
    *,
    max_gap_ms: float = DEFAULT_MAX_GAP_MS,
    min_count: int = DEFAULT_MIN_COUNT,
) -> tuple[dict[str, tuple[int, int] | None], BurstPlanStats]:
    """返回 ``{path: (burst_id, burst_position) 或 None}``，每个目录独立编号。"""
    min_count = max(2, int(min_count))
    max_gap_s = max(0.0, float(max_gap_ms)) / 1000.0
    no_subsec_gap_s = max(max_gap_s, NO_SUBSEC_MAX_GAP_MS / 1000.0)
    norm_times = {os.path.normpath(p): t for p, t in capture_times.items()}
    shots = group_shot_paths(paths)
    result: dict[str, tuple[int, int] | None] = {path: None for shot in shots for path in shot}
    stats = BurstPlanStats(shots=len(shots))

    by_dir: dict[str, list[tuple[CaptureTime, str, list[str]]]] = {}
    seen_dirs: set[str] = set()
    for shot in shots:
        dir_key, stem_key = _shot_key(shot[0])
        seen_dirs.add(dir_key)
        when = _shot_time(shot, norm_times)
        if when is None:
            continue
        stats.timed_shots += 1
        by_dir.setdefault(dir_key, []).append((when, stem_key, shot))
    stats.directories = len(seen_dirs)

    for dir_key in sorted(by_dir):
        timed = sorted(by_dir[dir_key], key=lambda item: (item[0].seconds, item[1]))
        groups: list[list[list[str]]] = []
        current = [timed[0]]
        for prev, curr in zip(timed, timed[1:]):
            limit = max_gap_s if prev[0].has_subsec and curr[0].has_subsec else no_subsec_gap_s
            # 微小浮点误差不应把恰好等于阈值的间隔拆开。
            if curr[0].seconds - prev[0].seconds <= limit + 1e-6:
                current.append(curr)
                continue
            if len(current) >= min_count:
                groups.append([item[2] for item in current])
            current = [curr]
        if len(current) >= min_count:
            groups.append([item[2] for item in current])
        for burst_id, group in enumerate(groups, start=1):
            stats.groups += 1
            stats.burst_shots += len(group)
            for position, shot in enumerate(group, start=1):
                for path in shot:
                    result[path] = (burst_id, position)
    return result, stats


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def sidecar_burst_fields(
    assignment: tuple[int, int] | None,
    sidecar_values: tuple[str, str],
    report_has_burst: bool,
) -> dict[str, str] | None:
    """决定一次拍摄要写入 sidecar 的字段；无需改动时返回 None。

    ``sidecar_values`` 为现有 sidecar 的 ``(burst_id, burst_position)`` 文本。
    """
    current = (_text(sidecar_values[0]), _text(sidecar_values[1]))
    if assignment is not None:
        desired = (str(int(assignment[0])), str(int(assignment[1])))
    elif report_has_burst:
        desired = (NOT_BURST_VALUE, NOT_BURST_VALUE)
    elif any(current):
        desired = ("", "")
    else:
        return None
    if desired == current:
        return None
    return {BURST_ID_FIELD: desired[0], BURST_POSITION_FIELD: desired[1]}


def browser_meta_updates(fields: Mapping[str, str]) -> dict[str, str]:
    """写入成功后合并进文件浏览器内存元数据的键（XMP 优先于 report.db）。"""
    updates: dict[str, str] = {}
    for field, value in fields.items():
        name = field.partition(":")[2]
        updates[field] = value
        updates[name] = value
        updates[f"report.{name}"] = value
    return updates


def _row_has_burst(row) -> bool:
    if not isinstance(row, Mapping):
        return False
    for key in (BURST_ID_KEY, BURST_POSITION_KEY):
        text = _text(row.get(key))
        if not text:
            continue
        try:
            if int(float(text)) > 0:
                return True
        except ValueError:
            continue
    return False


@dataclass
class BurstWriteOutcome:
    paths: list[str]
    fields: dict[str, str] | None
    written: bool = False


def write_burst_plan(
    plan: Mapping[str, tuple[int, int] | None],
    *,
    xmp=None,
    report=None,
    cancelled: Callable[[], bool] = lambda: False,
    on_outcome: Callable[[BurstWriteOutcome, int, int], None] | None = None,
) -> int:
    """按 :func:`plan_bursts` 结果更新 sidecar；返回写入成功的拍摄数。"""
    from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
    from app_common.exif_io.writer import invalidate_metadata_cache

    xmp = xmp or PhotoMetaDataXMP()
    report = report or PhotoMetaDataReportDB()
    shots = group_shot_paths(plan.keys())
    total = len(shots)
    written = 0
    for index, shot in enumerate(shots, start=1):
        if cancelled():
            break
        lead = shot[0]
        assignment = plan.get(lead)
        try:
            rec = xmp.read(lead) or {}
        except Exception:
            rec = {}
        sidecar_values = (rec.get(BURST_ID_FIELD), rec.get(BURST_POSITION_FIELD))
        report_has_burst = False
        if assignment is None:
            try:
                report_has_burst = _row_has_burst(report.row_for(lead))
            except Exception:
                report_has_burst = False
        fields = sidecar_burst_fields(assignment, sidecar_values, report_has_burst)
        outcome = BurstWriteOutcome(shot, fields)
        if fields:
            try:
                outcome.written = bool(xmp.write(lead, fields))
            except Exception:
                outcome.written = False
            if outcome.written:
                written += 1
                invalidate_metadata_cache(shot)
        if on_outcome is not None:
            on_outcome(outcome, index, total)
    return written
