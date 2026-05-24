# -*- coding: utf-8 -*-
"""
file_browser._browser
=====================
目录树浏览器（DirectoryBrowserWidget）与图像文件列表面板（FileListPanel）。

本模块自包含，仅依赖：
- PyQt5 / PyQt6
- Pillow（PIL）
- piexif
- rawpy（可选，用于 RAW 缩略图）
- app_common.exif_io.read_batch_metadata
"""
from __future__ import annotations

import concurrent.futures as _futures
from collections import deque
from dataclasses import dataclass, field
import hashlib
import html
import io as _io
import os
import queue as _queue
import sys
import threading
import time as _time
import unicodedata
from pathlib import Path

# ── Qt 导入 ───────────────────────────────────────────────────────────────────
try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QListView, QTreeView,
        QMenu, QProgressBar, QToolButton, QHeaderView, QAbstractItemView,
        QTreeWidget, QTreeWidgetItem, QStyleOptionViewItem, QStyle,
        QStyledItemDelegate, QStackedWidget, QSlider, QMessageBox, QComboBox,
        QApplication, QToolTip,
    )
    from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QRect, QTimer, QUrl, QMimeData, QPoint, QEvent, QAbstractListModel, QAbstractTableModel, QModelIndex, QItemSelectionModel, QSortFilterProxyModel
    from PyQt6.QtGui import (
        QPixmap, QImage, QFont, QColor, QIcon, QPainter, QBrush,
        QKeySequence, QShortcut,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QListView, QTreeView,
        QMenu, QProgressBar, QToolButton, QHeaderView, QAbstractItemView,
        QTreeWidget, QTreeWidgetItem, QStyleOptionViewItem, QStyle,
        QStyledItemDelegate, QStackedWidget, QSlider, QMessageBox, QComboBox,
        QApplication, QShortcut, QToolTip,
    )
    from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal, QRect, QTimer, QUrl, QMimeData, QPoint, QEvent, QAbstractListModel, QAbstractTableModel, QModelIndex, QItemSelectionModel, QSortFilterProxyModel
    from PyQt5.QtGui import (
        QPixmap, QImage, QFont, QColor, QIcon, QPainter, QBrush,
        QKeySequence,
    )

from app_common.exif_io import (
    DEFAULT_METADATA_TAGS,
    find_xmp_sidecar,
    inject_metadata_cache,
    read_batch_metadata,
    run_exiftool_assignments,
)
from app_common.exif_io.photo_meta import PhotoMetaDataProxy, extract_exposure_settings
from app_common.focus_calc import (
    extract_focus_box_for_display,
    resolve_focus_camera_type_from_metadata,
)
from app_common.log import get_logger
from app_common.file_utils import reveal_in_file_manager, move_to_trash, move_empty_dirs_to_trash
from app_common.send_to_app import get_external_apps, send_files_to_app
from app_common.report_db import (
    ReportDB,
    report_row_to_exiftool_style,
    get_preview_path_for_file,
    find_report_root,
)
from app_common.superviewer_user_options import (
    KEY_NAVIGATION_FPS_OPTIONS,
    apply_runtime_user_options,
    get_key_navigation_fps,
    get_persistent_thumb_max_size,
    get_persistent_thumb_sizes,
    get_preferred_persistent_thumb_sizes,
    get_persistent_thumb_workers,
    get_runtime_user_options,
    get_thumbnail_loader_workers,
    save_user_options,
)
from app_common.ui_style.styles import COLORS
from app_common import thumb_stream

_log = get_logger("file_browser")

# ── 支持的图像扩展名 ───────────────────────────────────────────────────────────
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif",
    ".heic", ".heif", ".hif",
    # Canon
    ".cr2", ".cr3", ".crw",
    # Nikon
    ".nef", ".nrw",
    # Sony
    ".arw", ".srf", ".sr2",
    # Panasonic
    ".rw2", ".raw",
    # Olympus
    ".orf", ".ori",
    # Fujifilm
    ".raf",
    # Adobe / Leica 等
    ".dng",
    # Pentax
    ".pef", ".ptx",
    # Sigma
    ".x3f",
    # Leica
    ".rwl",
    # 其他常见 RAW
    ".3fr", ".dcr", ".kdc", ".mef", ".mrw", ".rwz",
)
IMAGE_EXTENSIONS = tuple(dict.fromkeys(e.lower() for e in IMAGE_EXTENSIONS))
RAW_EXTENSIONS = frozenset(
    e for e in IMAGE_EXTENSIONS
    if e not in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif",
                 ".heic", ".heif", ".hif")
)

# ── Qt 兼容常量 ────────────────────────────────────────────────────────────────
try:
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    _AlignCenter = Qt.AlignCenter  # type: ignore[attr-defined]

try:
    _AscendingOrder = Qt.SortOrder.AscendingOrder
except AttributeError:
    _AscendingOrder = Qt.AscendingOrder  # type: ignore[attr-defined]

try:
    _UserRole = Qt.ItemDataRole.UserRole
except AttributeError:
    _UserRole = Qt.UserRole  # type: ignore[attr-defined]

try:
    _DisplayRole = Qt.ItemDataRole.DisplayRole
    _ToolTipRole = Qt.ItemDataRole.ToolTipRole
    _ForegroundRole = Qt.ItemDataRole.ForegroundRole
    _BackgroundRole = Qt.ItemDataRole.BackgroundRole
    _TextAlignmentRole = Qt.ItemDataRole.TextAlignmentRole
except AttributeError:
    _DisplayRole = Qt.DisplayRole  # type: ignore[attr-defined]
    _ToolTipRole = Qt.ToolTipRole  # type: ignore[attr-defined]
    _ForegroundRole = Qt.ForegroundRole  # type: ignore[attr-defined]
    _BackgroundRole = Qt.BackgroundRole  # type: ignore[attr-defined]
    _TextAlignmentRole = Qt.TextAlignmentRole  # type: ignore[attr-defined]

_orient = getattr(Qt, "Orientation", None)
_Horizontal = getattr(_orient, "Horizontal", None) if _orient else None
if _Horizontal is None:
    _Horizontal = getattr(Qt, "Horizontal", 1)

try:
    _ViewModeIcon = QListView.ViewMode.IconMode
except AttributeError:
    _ViewModeIcon = QListView.IconMode  # type: ignore[attr-defined]

try:
    _SingleSelection = QAbstractItemView.SelectionMode.SingleSelection
except AttributeError:
    _SingleSelection = QAbstractItemView.SingleSelection  # type: ignore[attr-defined]

try:
    _ExtendedSelection = QAbstractItemView.SelectionMode.ExtendedSelection
except AttributeError:
    _ExtendedSelection = QAbstractItemView.ExtendedSelection  # type: ignore[attr-defined]

try:
    _SelectRows = QAbstractItemView.SelectionBehavior.SelectRows
except AttributeError:
    _SelectRows = QAbstractItemView.SelectRows  # type: ignore[attr-defined]

try:
    _ItemIsEnabled = Qt.ItemFlag.ItemIsEnabled
    _ItemIsSelectable = Qt.ItemFlag.ItemIsSelectable
    _NoItemFlags = Qt.ItemFlag.NoItemFlags
except AttributeError:
    _ItemIsEnabled = Qt.ItemIsEnabled  # type: ignore[attr-defined]
    _ItemIsSelectable = Qt.ItemIsSelectable  # type: ignore[attr-defined]
    _NoItemFlags = Qt.NoItemFlags  # type: ignore[attr-defined]

try:
    _ScrollPerPixel = QAbstractItemView.ScrollMode.ScrollPerPixel
except AttributeError:
    _ScrollPerPixel = QAbstractItemView.ScrollPerPixel  # type: ignore[attr-defined]

try:
    _PositionAtCenter = QAbstractItemView.ScrollHint.PositionAtCenter
except AttributeError:
    _PositionAtCenter = QAbstractItemView.PositionAtCenter  # type: ignore[attr-defined]

try:
    _SelectCurrent = QItemSelectionModel.SelectionFlag.SelectCurrent
    _ClearAndSelect = QItemSelectionModel.SelectionFlag.ClearAndSelect
    _Select = QItemSelectionModel.SelectionFlag.Select
except AttributeError:
    _SelectCurrent = QItemSelectionModel.SelectCurrent  # type: ignore[attr-defined]
    _ClearAndSelect = QItemSelectionModel.ClearAndSelect  # type: ignore[attr-defined]
    _Select = QItemSelectionModel.Select  # type: ignore[attr-defined]

try:
    _QImageRGB888 = QImage.Format.Format_RGB888
except AttributeError:
    _QImageRGB888 = QImage.Format_RGB888  # type: ignore[attr-defined]

try:
    _TicksBelow = QSlider.TickPosition.TicksBelow
except AttributeError:
    _TicksBelow = QSlider.TicksBelow  # type: ignore[attr-defined]

try:
    _PainterAntialiasing = QPainter.RenderHint.Antialiasing
except AttributeError:
    _PainterAntialiasing = QPainter.Antialiasing  # type: ignore[attr-defined]

try:
    _NoPen = Qt.PenStyle.NoPen
except AttributeError:
    _NoPen = Qt.NoPen  # type: ignore[attr-defined]

try:
    _LeftButton = Qt.MouseButton.LeftButton
except AttributeError:
    _LeftButton = Qt.LeftButton  # type: ignore[attr-defined]

try:
    _SplitHCursor = Qt.CursorShape.SplitHCursor
    _ArrowCursor = Qt.CursorShape.ArrowCursor
except AttributeError:
    _SplitHCursor = Qt.SplitHCursor  # type: ignore[attr-defined]
    _ArrowCursor = Qt.ArrowCursor  # type: ignore[attr-defined]

try:
    _ResizeStretch = QHeaderView.ResizeMode.Stretch
    _ResizeInteractive = QHeaderView.ResizeMode.Interactive
    _ResizeToContents = QHeaderView.ResizeMode.ResizeToContents
except AttributeError:
    _ResizeStretch = QHeaderView.Stretch  # type: ignore[attr-defined]
    _ResizeInteractive = QHeaderView.Interactive  # type: ignore[attr-defined]
    _ResizeToContents = QHeaderView.ResizeToContents  # type: ignore[attr-defined]

# 自定义 item data role（UserRole + 偏移量）
_SortRole = int(_UserRole) + 10
_MetaColorRole = int(_UserRole) + 1
_MetaRatingRole = int(_UserRole) + 2
_MetaPickRole = int(_UserRole) + 3    # Pick/Reject 旗标：1=精选, 0=无, -1=排除
_MetaFocusRole = int(_UserRole) + 4
_ThumbPixmapRole = int(_UserRole) + 20
_ThumbSizeRole = int(_UserRole) + 21
_MetaSpeciesCnRole = int(_UserRole) + 22

_TREE_COL_SEQ = -1
_TREE_COL_NAME = 0
_TREE_COL_COMMENT = 1
_TREE_COL_STAR = 2
_TREE_COL_TAGS = 3
_TREE_COL_TITLE = _TREE_COL_COMMENT
_TREE_COL_COLOR = _TREE_COL_TAGS
_TREE_COL_SHARP = _TREE_COL_TAGS
_TREE_COL_AESTHETIC = _TREE_COL_TAGS
_TREE_COL_FOCUS = _TREE_COL_TAGS
_TREE_COL_SHUTTER = _TREE_COL_TAGS
_TREE_COL_ISO = _TREE_COL_TAGS
_TREE_COL_APERTURE = _TREE_COL_TAGS
_FILE_TABLE_HEADERS = ["文件名", "注释", "星级", "标签"]
_FILE_TAG_DISPLAY_SEPARATOR = "、"
_SUPERBIRDSTAMP_CAMERA_METADATA_TAGS = [
    "-ExifIFD:ExposureTime",
    "-EXIF:ExposureTime",
    "-XMP-exif:ExposureTime",
    "-Composite:ShutterSpeed",
    "-ExifIFD:ISO",
    "-EXIF:ISO",
    "-XMP-exif:PhotographicSensitivity",
    "-XMP-exif:ISOSpeedRatings",
    "-ExifIFD:FNumber",
    "-EXIF:FNumber",
    "-XMP-exif:FNumber",
    "-Composite:Aperture",
]


def _merge_unique_text_groups(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


_SUPERBIRDSTAMP_BROWSER_METADATA_TAGS = _merge_unique_text_groups(
    DEFAULT_METADATA_TAGS,
    _SUPERBIRDSTAMP_CAMERA_METADATA_TAGS,
)
_SUPERBIRDSTAMP_BROWSER_METADATA_TAGS_SET = frozenset(_SUPERBIRDSTAMP_BROWSER_METADATA_TAGS)

# 缩略图尺寸档位（像素）
# 后台元数据读取：每块最大文件数（分块顺序读取，提升取消响应性）
_METADATA_CHUNK_SIZE = 150
# 主线程元数据显示分批大小（越小越流畅，越大越快）
_META_APPLY_BATCH_SIZE = 64
_META_APPLY_TIME_BUDGET_MS = 12.0


def _env_int(name: str, default: int = 0) -> int:
    try:
        v = os.environ.get(name, "")
        if v is None or str(v).strip() == "":
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


_DEBUG_FILE_LIST_LIMIT = max(0, _env_int("SuperViewer_DEBUG_FILE_LIST_LIMIT", 0))
_DEBUG_FILE_LIST_MATCH = (os.environ.get("SuperViewer_DEBUG_FILE_LIST_MATCH", "") or "").strip().lower()
_THUMB_PROFILE_ENABLED = _env_flag("SuperViewer_THUMB_PROFILE", True)
_THUMB_PROFILE_VERBOSE = _env_flag("SuperViewer_THUMB_PROFILE_VERBOSE", False)
_THUMB_PROFILE_REPORT_INTERVAL_S = max(0.25, _env_int("SuperViewer_THUMB_PROFILE_INTERVAL_MS", 1500) / 1000.0)
_THUMB_BOTTLENECK_SAMPLE_LIMIT = max(256, _env_int("SuperViewer_THUMB_BOTTLENECK_SAMPLE_LIMIT", 50000))
_PERSISTENT_THUMB_CACHE_START_DELAY_MS = max(
    500,
    _env_int("SuperViewer_PERSISTENT_THUMB_DELAY_MS", 1800),
)
_FAST_PREVIEW_COMMIT_DELAY_MS = max(
    60,
    _env_int("SuperViewer_FAST_PREVIEW_COMMIT_DELAY_MS", 140),
)

_ACTUAL_PATH_CACHE: dict[str, str] = {}
_THUMB_BOTTLENECK_LOCK = threading.Lock()
_THUMB_BOTTLENECK_SAMPLES: dict[str, list[float]] = {
    "decode_ms": [],
    "flush_ms": [],
    "ready_wait_ms": [],
    "viewport_ms": [],
}


def _record_thumb_bottleneck_sample(metric: str, value_ms: float) -> None:
    if not _THUMB_PROFILE_ENABLED:
        return
    try:
        sample = float(value_ms)
    except Exception:
        return
    if sample <= 0.0:
        return
    with _THUMB_BOTTLENECK_LOCK:
        samples = _THUMB_BOTTLENECK_SAMPLES.setdefault(metric, [])
        if len(samples) >= _THUMB_BOTTLENECK_SAMPLE_LIMIT:
            return
        samples.append(sample)


def _log_thumb_bottleneck_summary() -> None:
    if not _THUMB_PROFILE_ENABLED:
        return
    with _THUMB_BOTTLENECK_LOCK:
        snapshot = {
            metric: list(samples)
            for metric, samples in _THUMB_BOTTLENECK_SAMPLES.items()
            if samples
        }
    if not snapshot:
        return
    for metric, samples in snapshot.items():
        ordered = sorted(samples)
        count = len(ordered)
        top_count = max(1, (count + 19) // 20)
        top_slice = ordered[-top_count:]
        p95 = ordered[max(0, count - top_count)]
        top_values = ",".join(f"{value:.1f}" for value in top_slice[-min(3, len(top_slice)):])
        _log.info(
            "[THUMB_PROFILE][summary] metric=%s samples=%s top5_count=%s avg=%.1fms p95=%.1fms top5_avg=%.1fms max=%.1fms top=%s",
            metric,
            count,
            top_count,
            sum(ordered) / max(1, count),
            p95,
            sum(top_slice) / max(1, len(top_slice)),
            top_slice[-1],
            top_values,
        )

_THUMB_SIZE_STEPS = [128, 256, 512, 1024]
_THUMB_CACHE_BASE_SIZE = max(_THUMB_SIZE_STEPS)
_JPEG_MIP_EXTENSIONS = frozenset({".jpg", ".jpeg"})
_STAR_SILVER_COLOR = "#c0c0c0"

# Lightroom 颜色标签 → (十六进制色, 列表/缩略图显示文本)
# 红=眼部对焦，绿=飞版；其余保持常规色名
_COLOR_LABEL_COLORS: dict[str, tuple[str, str]] = {
    "Red":    ("#c0392b", "眼焦"),
    "Yellow": ("#d4ac0d", "黄"),
    "Green":  ("#27ae60", "飞版"),
    "Blue":   ("#2980b9", "蓝"),
    "Purple": ("#8e44ad", "紫"),
    "White":  ("#bdc3c7", "白"),
    "Orange": ("#e67e22", "橙"),
}

# 对焦状态（XMP:Country 等）原始值 → 可读中文（精焦/合焦/偏移/失焦）
_FOCUS_STATUS_DISPLAY: dict[str, str] = {
    "BEST": "精焦",
    "IN FOCUS": "合焦",
    "OK": "合焦",
    "GOOD": "合焦",
    "OFF": "偏移",
    "MISS": "失焦",
    "OUT": "失焦",
    "BAD": "失焦",
}
_COLOR_SORT_ORDER: dict[str, int] = {
    k: i for i, k in enumerate(
        ["Red", "Orange", "Yellow", "Green", "Blue", "Purple", "White", ""]
    )
}

_FOCUS_STATUS_TEXT_COLORS: dict[str, str] = {
    "精焦": COLORS["success"],
    "合焦": COLORS["warning"],
    "偏移": COLORS["text_primary"],
    "失焦": COLORS["text_secondary"],
}
_FOCUS_FILTER_OPTIONS: tuple[str, ...] = tuple(_FOCUS_STATUS_TEXT_COLORS.keys())


def _format_optional_number(raw: str, fmt: str) -> str:
    """若 raw 可解析为数字则按 fmt 格式化，否则返回 strip 后的原文。"""
    s = str(raw).strip()
    if not s:
        return ""
    try:
        return fmt % float(s)
    except (ValueError, TypeError):
        return s


def _first_non_empty(*values):
    """返回首个非空 metadata 值，保留原始类型。"""
    for value in values:
        if value is None:
            continue
        if str(value).strip():
            return value
    return ""


def _normalise_metadata_tag_values(value) -> list[str]:
    """将 XMP/IPTC tag/subject 值收敛为有序去重字符串列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        text = str(value or "").strip()
        if not text:
            return []
        values = text.split(";") if ";" in text else [text]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _metadata_tags_from_meta(meta: dict | None) -> list[str]:
    if not isinstance(meta, dict):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for key in (
        "tags",
        "photo_tags",
        "XMP-dc:Subject",
        "XMP-dc:subject",
        "XMP:Subject",
        "Subject",
        "subject",
        "subjects",
        "IPTC:Keywords",
        "Keywords",
    ):
        for tag in _normalise_metadata_tag_values(meta.get(key)):
            if tag in seen:
                continue
            seen.add(tag)
            result.append(tag)
    return result


def _metadata_tags_display(meta: dict | None) -> str:
    return _FILE_TAG_DISPLAY_SEPARATOR.join(_metadata_tags_from_meta(meta))


def _metadata_comment_from_meta(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return ""
    value = _first_non_empty(
        meta.get("comment"),
        meta.get("description"),
        meta.get("Description"),
        meta.get("XMP-dc:Description"),
        meta.get("XMP-dc:description"),
        meta.get("XMP:Description"),
        meta.get("IFD0:ImageDescription"),
        meta.get("EXIF:ImageDescription"),
        meta.get("ExifIFD:UserComment"),
        meta.get("EXIF:UserComment"),
        meta.get("UserComment"),
        meta.get("IFD0:XPComment"),
        meta.get("IPTC:Caption-Abstract"),
        meta.get("caption"),
    )
    return str(value or "").strip()


def _parse_positive_fraction_or_float(raw) -> float | None:
    """兼容 1/2000、0.0005、f/5.6 等常见 EXIF 数值文本。"""
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.lower().replace("seconds", "").replace("second", "").replace("sec", "").strip()
    if text.startswith("f/"):
        text = text[2:].strip()
    if text.endswith("s"):
        text = text[:-1].strip()
    if "(" in text and ")" in text:
        text = text.split("(", 1)[0].strip()
    if not text:
        return None
    if "/" in text:
        left, _, right = text.partition("/")
        try:
            numerator = float(left.strip())
            denominator = float(right.strip())
        except (ValueError, TypeError):
            return None
        if denominator == 0:
            return None
        value = numerator / denominator
    else:
        try:
            value = float(text)
        except (ValueError, TypeError):
            return None
    return value if value > 0 else None


def _parse_optional_int(raw) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.upper().startswith("ISO"):
        text = text[3:].strip()
    try:
        value = int(float(text))
    except (ValueError, TypeError):
        return None
    return value if value >= 0 else None


def _format_shutter_value(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    seconds = _parse_positive_fraction_or_float(raw)
    if seconds is None:
        return text
    inverse = 1.0 / seconds if seconds > 0 else 0.0
    if seconds < 1 and inverse >= 2:
        denominator = round(inverse)
        if denominator > 0:
            return f"1/{denominator}s"
    return f"{seconds:g}s"


def _format_iso_value(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    iso_value = _parse_optional_int(raw)
    if iso_value is None:
        return text
    return str(iso_value)


def _format_aperture_value(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    aperture_value = _parse_positive_fraction_or_float(raw)
    if aperture_value is None:
        return text
    return f"f/{aperture_value:g}"


def _focus_status_to_display(raw: str) -> str:
    """对焦状态原始值 → 可读中文（精焦/合焦/偏移/失焦），已为中文则原样返回。"""
    s = str(raw).strip()
    if not s:
        return ""
    u = s.upper()
    if u in _FOCUS_STATUS_DISPLAY:
        return _FOCUS_STATUS_DISPLAY[u]
    if s in ("精焦", "合焦", "偏移", "失焦"):
        return s
    return s


def _qcolor_rgba_css(color_value: str, alpha: int) -> str:
    q = QColor(color_value)
    if not q.isValid():
        q = QColor(COLORS["text_secondary"])
    a = max(0, min(255, int(alpha)))
    return f"rgba({q.red()}, {q.green()}, {q.blue()}, {a})"


def _filter_badge_stylesheet(
    color_value: str,
    *,
    min_width: int = 42,
    checked_fg: str = "#f5f5f5",
) -> str:
    color = color_value or COLORS["text_secondary"]
    border = _qcolor_rgba_css(color, 180)
    bg = _qcolor_rgba_css(color, 28)
    hover_bg = _qcolor_rgba_css(color, 52)
    checked_bg = _qcolor_rgba_css(color, 108)
    return (
        "QToolButton {"
        f"font-size: 10px; padding: 1px 6px; min-width: {int(min_width)}px; "
        f"border-radius: 9px; border: 1px solid {border}; "
        f"background: {bg}; color: {color};"
        "}"
        "QToolButton:hover {"
        f"background: {hover_bg};"
        "}"
        "QToolButton:checked {"
        f"background: {checked_bg}; border: 1px solid {color}; color: {checked_fg};"
        "}"
    )


def apply_compact_filter_badge_menu(
    inline_buttons,
    menu_button,
    compact: bool,
    *,
    menu_text: str,
    menu_tooltip: str = "",
) -> None:
    """在空间不足时将一组过滤 badge 切换为单个下拉菜单按钮。"""
    for button in inline_buttons or ():
        if button is None:
            continue
        button.setVisible(not compact)
    if menu_button is None:
        return
    menu_button.setVisible(bool(compact))
    menu_button.setText(str(menu_text or ""))
    if menu_tooltip:
        menu_button.setToolTip(menu_tooltip)


def _focus_filter_button_stylesheet(status: str) -> str:
    color = _FOCUS_STATUS_TEXT_COLORS.get(status, COLORS["text_secondary"])
    checked_fg = "#111111" if status in ("??", "??") else "#f5f5f5"
    return _filter_badge_stylesheet(color, min_width=42, checked_fg=checked_fg)


# 右键菜单策略兼容常量
try:
    _CustomContextMenu = Qt.ContextMenuPolicy.CustomContextMenu
except AttributeError:
    _CustomContextMenu = Qt.CustomContextMenu  # type: ignore[attr-defined]

try:
    _EventResize = QEvent.Type.Resize
    _EventShow = QEvent.Type.Show
    _EventKeyPress = QEvent.Type.KeyPress
    _EventKeyRelease = QEvent.Type.KeyRelease
    _EventToolTip = QEvent.Type.ToolTip
    _EventWheel = QEvent.Type.Wheel
    _EventMouseButtonPress = QEvent.Type.MouseButtonPress
except AttributeError:
    _EventResize = QEvent.Resize  # type: ignore[attr-defined]
    _EventShow = QEvent.Show  # type: ignore[attr-defined]
    _EventKeyPress = QEvent.KeyPress  # type: ignore[attr-defined]
    _EventKeyRelease = QEvent.KeyRelease  # type: ignore[attr-defined]
    _EventToolTip = QEvent.ToolTip  # type: ignore[attr-defined]
    _EventWheel = QEvent.Wheel  # type: ignore[attr-defined]
    _EventMouseButtonPress = QEvent.MouseButtonPress  # type: ignore[attr-defined]

_KeyUp = getattr(Qt.Key, "Key_Up", None) or getattr(Qt, "Key_Up", None)
_KeyDown = getattr(Qt.Key, "Key_Down", None) or getattr(Qt, "Key_Down", None)
_KeyLeft = getattr(Qt.Key, "Key_Left", None) or getattr(Qt, "Key_Left", None)
_KeyRight = getattr(Qt.Key, "Key_Right", None) or getattr(Qt, "Key_Right", None)
_KeyDelete = getattr(Qt.Key, "Key_Delete", None) or getattr(Qt, "Key_Delete", None)
_KeyPeriod = getattr(Qt.Key, "Key_Period", None) or getattr(Qt, "Key_Period", None)
_KeyQ = getattr(Qt.Key, "Key_Q", None) or getattr(Qt, "Key_Q", None)
_KeyQuoteLeft = getattr(Qt.Key, "Key_QuoteLeft", None) or getattr(Qt, "Key_QuoteLeft", None)
_KeyAsciiTilde = getattr(Qt.Key, "Key_AsciiTilde", None) or getattr(Qt, "Key_AsciiTilde", None)
_KeyRatingDigits = {}
for _digit in range(1, 6):
    _key = getattr(Qt.Key, f"Key_{_digit}", None) or getattr(Qt, f"Key_{_digit}", None)
    if _key is not None:
        _KeyRatingDigits[_key] = _digit
        try:
            _KeyRatingDigits[int(_key)] = _digit
        except Exception:
            pass
_ShiftModifier = (
    getattr(Qt.KeyboardModifier, "ShiftModifier", None)
    or getattr(Qt, "ShiftModifier", None)
)
_ControlModifier = (
    getattr(Qt.KeyboardModifier, "ControlModifier", None)
    or getattr(Qt, "ControlModifier", None)
)
_AltModifier = (
    getattr(Qt.KeyboardModifier, "AltModifier", None)
    or getattr(Qt, "AltModifier", None)
)
_MetaModifier = (
    getattr(Qt.KeyboardModifier, "MetaModifier", None)
    or getattr(Qt, "MetaModifier", None)
)
try:
    _WindowShortcut = Qt.ShortcutContext.WindowShortcut
except AttributeError:
    _WindowShortcut = Qt.WindowShortcut  # type: ignore[attr-defined]

try:
    _WidgetWithChildrenShortcut = Qt.ShortcutContext.WidgetWithChildrenShortcut
except AttributeError:
    _WidgetWithChildrenShortcut = Qt.WidgetWithChildrenShortcut  # type: ignore[attr-defined]


def _platform_copy_key_sequence() -> "QKeySequence":
    """Return the native Copy shortcut sequence for macOS / Windows."""
    standard_key = None
    standard_key_enum = getattr(QKeySequence, "StandardKey", None)
    if standard_key_enum is not None:
        standard_key = getattr(standard_key_enum, "Copy", None)
    if standard_key is None:
        standard_key = getattr(QKeySequence, "Copy", None)
    if standard_key is not None:
        try:
            bindings = QKeySequence.keyBindings(standard_key)
            if bindings:
                return bindings[0]
        except Exception:
            pass
        try:
            return QKeySequence(standard_key)
        except Exception:
            pass
    return QKeySequence("Ctrl+C")


def _platform_cut_key_sequence() -> "QKeySequence":
    """Return the native Cut shortcut sequence for macOS / Windows."""
    standard_key = None
    standard_key_enum = getattr(QKeySequence, "StandardKey", None)
    if standard_key_enum is not None:
        standard_key = getattr(standard_key_enum, "Cut", None)
    if standard_key is None:
        standard_key = getattr(QKeySequence, "Cut", None)
    if standard_key is not None:
        try:
            bindings = QKeySequence.keyBindings(standard_key)
            if bindings:
                return bindings[0]
        except Exception:
            pass
        try:
            return QKeySequence(standard_key)
        except Exception:
            pass
    return QKeySequence("Ctrl+X")


def _platform_paste_key_sequence() -> "QKeySequence":
    """Return the native Paste shortcut sequence for macOS / Windows."""
    standard_key = None
    standard_key_enum = getattr(QKeySequence, "StandardKey", None)
    if standard_key_enum is not None:
        standard_key = getattr(standard_key_enum, "Paste", None)
    if standard_key is None:
        standard_key = getattr(QKeySequence, "Paste", None)
    if standard_key is not None:
        try:
            bindings = QKeySequence.keyBindings(standard_key)
            if bindings:
                return bindings[0]
        except Exception:
            pass
        try:
            return QKeySequence(standard_key)
        except Exception:
            pass
    return QKeySequence("Ctrl+V")


def _key_sequence_native_text(sequence: "QKeySequence") -> str:
    try:
        native_format = QKeySequence.SequenceFormat.NativeText
    except AttributeError:
        native_format = QKeySequence.NativeText  # type: ignore[attr-defined]
    try:
        text = sequence.toString(native_format)
    except Exception:
        text = sequence.toString()
    if text:
        return text
    return "⌘C" if sys.platform == "darwin" else "Ctrl+C"


def _apply_context_menu_shortcut(action, sequence: "QKeySequence") -> None:
    """Attach a shortcut to a context-menu action and force it to be displayed."""
    if action is None:
        return
    action.setShortcut(sequence)
    try:
        action.setShortcutContext(_WidgetWithChildrenShortcut)
    except Exception:
        pass
    try:
        action.setShortcutVisibleInContextMenu(True)
        return
    except Exception:
        pass
    text = str(action.text() or "")
    if "\t" not in text:
        shortcut_text = _key_sequence_native_text(sequence)
        if shortcut_text:
            action.setText(f"{text}\t{shortcut_text}")


def _key_matches(value, key) -> bool:
    if key is None:
        return False
    if value == key:
        return True
    try:
        return int(value) == int(key)
    except Exception:
        return False

try:
    _StateSelected = QStyle.StateFlag.State_Selected
    _StateMouseOver = QStyle.StateFlag.State_MouseOver
except AttributeError:
    _StateSelected = QStyle.State_Selected  # type: ignore[attr-defined]
    _StateMouseOver = QStyle.State_MouseOver  # type: ignore[attr-defined]

try:
    _ElideRight = Qt.TextElideMode.ElideRight
except AttributeError:
    _ElideRight = Qt.ElideRight  # type: ignore[attr-defined]

try:
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
except AttributeError:
    _KeepAspectRatio = Qt.KeepAspectRatio  # type: ignore[attr-defined]

try:
    _SmoothTransformation = Qt.TransformationMode.SmoothTransformation
except AttributeError:
    _SmoothTransformation = Qt.SmoothTransformation  # type: ignore[attr-defined]

# ── 系统文件管理器工具函数 ────────────────────────────────────────────────────────

def _path_key(path: str) -> str:
    """Normalize path for case-insensitive comparison on Windows."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _thumb_cache_key(path: str) -> str:
    return _path_key(path)


def _thumb_disk_cache_dir() -> str:
    """Return persistent cache directory for thumbnails (cross-platform)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(base, "SuperViewer", "thumb_cache")


def _thumb_disk_cache_path(path: str, mtime: float, size: int) -> str:
    """Full path to cached thumbnail file; path must be absolute/normalized for stable key."""
    cache_dir = _thumb_disk_cache_dir()
    raw = f"{_path_key(path)}\0{mtime}\0{size}"
    name = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] + ".jpg"
    return os.path.join(cache_dir, name)


def _persistent_thumb_cache_max_size() -> int:
    override = _env_int("SuperViewer_PERSISTENT_THUMB_SIZE", 0)
    if override in (128, 256, 512, 1024):
        return override
    return get_persistent_thumb_max_size()


def _persistent_thumb_cache_sizes() -> list[int]:
    return get_persistent_thumb_sizes(_persistent_thumb_cache_max_size())


def _effective_persistent_thumb_cache_sizes(preferred_size: int | None = None) -> list[int]:
    sizes: list[int] = []
    seen: set[int] = set()
    for size in _persistent_thumb_cache_sizes():
        size_int = int(size)
        if size_int not in _THUMB_SIZE_STEPS or size_int in seen:
            continue
        seen.add(size_int)
        sizes.append(size_int)
    if preferred_size is not None:
        try:
            preferred = int(preferred_size)
        except Exception:
            preferred = 0
        if preferred in _THUMB_SIZE_STEPS and preferred not in seen:
            sizes.append(preferred)
    return sorted(sizes)


def _preferred_persistent_thumb_sizes_for_request(
    requested_size: int,
    candidate_sizes: list[int] | tuple[int, ...] | None = None,
) -> list[int]:
    if candidate_sizes is None:
        return get_preferred_persistent_thumb_sizes(requested_size, _persistent_thumb_cache_max_size())
    normalized = sorted(
        {
            int(size)
            for size in (candidate_sizes or [])
            if int(size) in _THUMB_SIZE_STEPS
        }
    )
    if not normalized:
        return []
    req = max(1, int(requested_size))
    larger = [size for size in normalized if size >= req]
    smaller = [size for size in normalized if size < req]
    return larger + list(reversed(smaller))


def _persistent_thumb_cache_worker_count() -> int:
    override = _env_int("SuperViewer_PERSISTENT_THUMB_WORKERS", 0)
    if override > 0:
        return max(1, override)
    return max(1, get_persistent_thumb_workers())


def _persistent_thumb_cache_dirname(size: int) -> str:
    return f"thumb_preview_{int(size)}"


def _find_superpicky_dir(current_dir: str, max_levels: int | None = None) -> str:
    """向上查找最近的现有 .superpicky 目录；找不到时返回空字符串。

    缓存写入方只应在用户已经有 .superpicky 的目录树内创建 cache 子目录，
    不应为了缓存主动创建新的 .superpicky 根目录。
    """
    if not current_dir:
        return ""
    candidate = os.path.normpath(current_dir)
    if os.path.basename(candidate) == ".superpicky" and os.path.isdir(candidate):
        return candidate
    depth = 0
    while candidate:
        if max_levels is not None and depth > max_levels:
            break
        superpicky = os.path.join(candidate, ".superpicky")
        if os.path.isdir(superpicky):
            return superpicky
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
        depth += 1
    return ""


def _superpicky_cache_root_dir(current_dir: str | None) -> str:
    """返回持有 .superpicky 的 root 目录；找不到现有 .superpicky 时返回空。"""
    superpicky_dir = _find_superpicky_dir(current_dir or "")
    if not superpicky_dir:
        return ""
    return os.path.dirname(superpicky_dir)


def _preview_cache_target_for_file(path: str, current_dir: str | None) -> str:
    if not path or not current_dir:
        return ""
    superpicky_dir = _find_superpicky_dir(current_dir)
    if not superpicky_dir:
        return ""
    preview_dir = os.path.join(superpicky_dir, "cache", "temp_preview")
    stem = os.path.splitext(os.path.basename(path))[0]
    if not stem:
        return ""
    return os.path.normpath(os.path.join(preview_dir, f"{stem}.jpg"))


def _existing_preview_cache_path_for_file(path: str, current_dir: str | None) -> str:
    preview_path = _preview_cache_target_for_file(path, current_dir)
    if preview_path and os.path.isfile(preview_path):
        return preview_path
    return ""


def _persistent_thumb_cache_dir(current_dir: str | None, size: int) -> str:
    if not current_dir:
        return ""
    superpicky_dir = _find_superpicky_dir(current_dir)
    if not superpicky_dir:
        return ""
    return os.path.join(superpicky_dir, "cache", _persistent_thumb_cache_dirname(size))


def _persistent_thumb_cache_filename_for_file(path: str, current_dir: str | None = None) -> str:
    """生成平铺缓存文件名，优先保留相对路径信息以避免子目录重名冲突。"""
    norm_path = os.path.normpath(path)
    name = ""
    if current_dir:
        try:
            rel = os.path.relpath(norm_path, os.path.normpath(current_dir))
        except Exception:
            rel = ""
        if rel and rel != os.curdir and rel != os.pardir and not rel.startswith(os.pardir + os.sep):
            name = rel.replace("\\", "__").replace("/", "__")
    if not name:
        name = os.path.basename(norm_path)
    if not name:
        digest = hashlib.sha1(_path_key(path).encode("utf-8")).hexdigest()[:12]
        return f"{digest}.thumb.jpg"
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    max_base_length = 180
    if len(name) > max_base_length:
        digest = hashlib.sha1(_path_key(path).encode("utf-8")).hexdigest()[:8]
        stem, ext = os.path.splitext(name)
        keep = max(24, max_base_length - len(ext) - len(digest) - 2)
        name = f"{stem[:keep]}__{digest}{ext}"
    return f"{name}.thumb.jpg"


def _legacy_persistent_thumb_cache_path_for_file(path: str, current_dir: str | None, size: int) -> str:
    cache_dir = _persistent_thumb_cache_dir(current_dir, size)
    if not cache_dir or not path:
        return ""
    digest = hashlib.sha1(_path_key(path).encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, digest[:2], f"{digest}.jpg")


def _persistent_thumb_cache_path_for_file(path: str, current_dir: str | None, size: int) -> str:
    cache_dir = _persistent_thumb_cache_dir(current_dir, size)
    if not cache_dir or not path:
        return ""
    cache_root_dir = _superpicky_cache_root_dir(current_dir)
    return os.path.join(cache_dir, _persistent_thumb_cache_filename_for_file(path, cache_root_dir or current_dir))


def _migrate_legacy_persistent_thumb_cache_path(target_path: str, legacy_path: str) -> str:
    """将旧的二级目录缓存迁移到新的平铺目录；失败时仍回退使用旧路径。"""
    if not target_path or not legacy_path or not os.path.isfile(legacy_path):
        return ""
    if os.path.isfile(target_path):
        return target_path
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        os.replace(legacy_path, target_path)
        try:
            os.rmdir(os.path.dirname(legacy_path))
        except Exception:
            pass
        return target_path
    except Exception:
        return legacy_path


def _thumb_source_stamp(path: str, auxiliary_path: str = "") -> float:
    stamp = 0.0
    for candidate in (path, auxiliary_path):
        if not candidate:
            continue
        try:
            stamp = max(stamp, float(os.path.getmtime(candidate)))
        except Exception:
            continue
    return stamp


def _existing_persistent_thumb_cache_path_for_exact_size(
    path: str,
    current_dir: str | None,
    size: int,
    source_stamp: float | None = None,
) -> str:
    cache_path = _persistent_thumb_cache_path_for_file(path, current_dir, size)
    if not cache_path or not os.path.isfile(cache_path):
        legacy_path = _legacy_persistent_thumb_cache_path_for_file(path, current_dir, size)
        if legacy_path and os.path.isfile(legacy_path):
            cache_path = _migrate_legacy_persistent_thumb_cache_path(cache_path, legacy_path)
        if not cache_path or not os.path.isfile(cache_path):
            return ""
    if source_stamp is None:
        source_stamp = _thumb_source_stamp(path)
    if source_stamp and source_stamp > 0:
        try:
            cache_stamp = float(os.path.getmtime(cache_path))
        except Exception:
            return ""
        if cache_stamp + 0.5 < source_stamp:
            return ""
    return cache_path


def _existing_persistent_thumb_cache_path_for_file(
    path: str,
    current_dir: str | None,
    *,
    requested_size: int,
    source_stamp: float | None = None,
    candidate_sizes: list[int] | tuple[int, ...] | None = None,
) -> str:
    for size in _preferred_persistent_thumb_sizes_for_request(
        requested_size,
        candidate_sizes=candidate_sizes,
    ):
        cache_path = _existing_persistent_thumb_cache_path_for_exact_size(
            path,
            current_dir,
            size,
            source_stamp=source_stamp,
        )
        if cache_path:
            return cache_path
    return ""


def _write_persistent_thumb_cache_image(
    target_path: str,
    qimg: "QImage",
    source_stamp: float | None = None,
) -> bool:
    if not target_path or qimg is None or qimg.isNull():
        return False
    tmp_path = f"{target_path}.tmp-{os.getpid()}-{threading.get_ident()}"
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if not qimg.save(tmp_path, "JPEG", 85):
            return False
        os.replace(tmp_path, target_path)
        if source_stamp and source_stamp > 0:
            try:
                os.utime(target_path, (source_stamp, source_stamp))
            except Exception:
                pass
        return True
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _resolve_thumb_source_path(path: str, report_cache: dict | None, current_dir: str | None) -> str:
    norm_path = os.path.normpath(path)
    stem = Path(norm_path).stem
    if stem and isinstance(report_cache, dict):
        row = report_cache.get(stem)
        if isinstance(row, dict):
            temp_jpeg_path = str(row.get("temp_jpeg_path") or "").strip()
            if temp_jpeg_path:
                candidate = (
                    os.path.normpath(temp_jpeg_path)
                    if os.path.isabs(temp_jpeg_path)
                    else os.path.normpath(os.path.join(current_dir, temp_jpeg_path))
                    if current_dir
                    else ""
                )
                if candidate and os.path.isfile(candidate):
                    return candidate
    preview_path = _existing_preview_cache_path_for_file(norm_path, current_dir)
    return preview_path or norm_path


def _read_thumb_from_disk_cache(path: str, mtime: float, size: int) -> "QImage | None":
    """Load thumbnail from disk cache if present and valid; returns QImage or None."""
    cache_path = _thumb_disk_cache_path(path, mtime, size)
    if not os.path.isfile(cache_path):
        return None
    try:
        from PIL import Image
        img = Image.open(cache_path)
        img.load()
        w, h = img.size
        if w > size or h > size:
            img.thumbnail((size, size), Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        data = img.tobytes("raw", "RGB")
        w, h = img.size
        qimg = QImage(data, w, h, w * 3, _QImageRGB888)
        return qimg.copy()
    except Exception:
        return None


def _schedule_thumb_disk_cache_write(cache_path: str, qimg: "QImage") -> None:
    """Schedule async write of QImage to cache_path (JPEG). Pass a copy if caller keeps using qimg."""
    img_copy = qimg.copy()

    def write():
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            if not img_copy.isNull():
                img_copy.save(cache_path, "JPEG", 85)
        except Exception:
            pass

    try:
        _get_thumb_disk_writer().submit(write)
    except Exception:
        pass


# Single-thread executor for disk thumbnail writes (lazy init)
_THUMB_DISK_WRITER_LOCK = threading.Lock()
_THUMB_DISK_WRITER: _futures.ThreadPoolExecutor | None = None


def _rgb_bytes_to_qimage(data: bytes, w: int, h: int) -> QImage:
    """将 thumb_stream 返回的 RGB 字节转为 QImage（主线程或 worker 线程均可）。"""
    return QImage(data, w, h, w * 3, _QImageRGB888).copy()


def _get_thumb_disk_writer() -> _futures.ThreadPoolExecutor:
    global _THUMB_DISK_WRITER
    with _THUMB_DISK_WRITER_LOCK:
        if _THUMB_DISK_WRITER is None:
            _THUMB_DISK_WRITER = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="thumb_disk")
        return _THUMB_DISK_WRITER


def _shutdown_thumb_disk_writer(wait: bool = True) -> None:
    global _THUMB_DISK_WRITER
    with _THUMB_DISK_WRITER_LOCK:
        executor = _THUMB_DISK_WRITER
        _THUMB_DISK_WRITER = None
    if executor is None:
        return
    try:
        executor.shutdown(wait=wait, cancel_futures=False)
    except Exception:
        pass


def _qimage_num_bytes(image: QImage | None) -> int:
    if image is None or image.isNull():
        return 0
    try:
        return int(image.sizeInBytes())
    except AttributeError:
        return int(image.byteCount())  # type: ignore[attr-defined]


def _scale_qimage_for_thumb(image: QImage, size: int) -> QImage:
    if image.isNull():
        return image
    if image.width() <= size and image.height() <= size:
        return image.copy()
    scaled = image.scaled(
        int(size),
        int(size),
        _KeepAspectRatio,
        _SmoothTransformation,
    )
    return scaled.copy()


def _thumbnail_loader_worker_count() -> int:
    try:
        override = int(str(os.environ.get("SuperViewer_THUMB_WORKERS", "")).strip() or "0")
    except Exception:
        override = 0
    if override > 0:
        return max(1, override)
    return max(1, get_thumbnail_loader_workers())


def _thumbnail_loader_batch_size(worker_count: int) -> int:
    try:
        override = int(str(os.environ.get("SuperViewer_THUMB_BATCH_SIZE", "")).strip() or "0")
    except Exception:
        override = 0
    if override > 0:
        return min(max(1, override), max(1, worker_count))
    return min(max(1, worker_count), max(4, (worker_count * 2 + 2) // 3))


def _get_cached_actual_path(path: str) -> str | None:
    if not path:
        return None
    actual = _ACTUAL_PATH_CACHE.get(_path_key(path))
    if actual:
        return os.path.normpath(actual)
    return None


def _set_cached_actual_path(source_path: str, actual_path: str) -> None:
    if not source_path or not actual_path:
        return
    _ACTUAL_PATH_CACHE[_path_key(source_path)] = os.path.normpath(actual_path)


def _is_same_or_child_path(parent: str, child: str) -> bool:
    """Whether child is parent itself or under parent."""
    try:
        parent_abs = os.path.normpath(os.path.abspath(parent))
        child_abs = os.path.normpath(os.path.abspath(child))
        if _path_key(parent_abs) == _path_key(child_abs):
            return True
        common = os.path.commonpath([parent_abs, child_abs])
        return _path_key(common) == _path_key(parent_abs)
    except Exception:
        return False


def _resolve_report_full_path(row: dict, report_root: str, fallback_dir: str) -> str | None:
    """Resolve full file path from report row current_path/original_path.

    report.db may have been created on Windows, so current_path / original_path
    can use backslashes as separators.  Normalise them to the OS-native separator
    before any path operation so that os.path.join / os.path.normpath work
    correctly on macOS/Linux.
    """
    cp = row.get("current_path")
    if not cp or not str(cp).strip():
        return None

    cp_text = str(cp).strip().replace("\\", os.sep)
    if os.path.isabs(cp_text):
        full_path = os.path.normpath(cp_text)
    else:
        base_dir = report_root or fallback_dir
        full_path = os.path.normpath(os.path.join(base_dir, cp_text))

    op = row.get("original_path")
    if op and str(op).strip():
        ext_orig = Path(str(op).strip().replace("\\", os.sep)).suffix
        if ext_orig:
            full_path = str(Path(full_path).with_suffix(ext_orig))
    return full_path


def _get_report_current_path_raw(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    raw = row.get("_current_path_report_raw")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    cp = row.get("current_path")
    return str(cp).strip() if cp is not None else ""


def _normalize_report_row_paths(row: dict) -> dict:
    if not isinstance(row, dict):
        return row
    out = dict(row)
    cp_text = str(out.get("current_path") or "").strip().replace("\\", os.sep)
    op_text = str(out.get("original_path") or "").strip().replace("\\", os.sep)
    out["_current_path_report_raw"] = cp_text
    if cp_text.lower().endswith(".xmp") and op_text:
        ext_orig = Path(op_text).suffix
        if ext_orig:
            normalized = str(Path(cp_text).with_suffix(ext_orig))
            if normalized != cp_text:
                out["current_path"] = normalized
                _log.info(
                    "[_normalize_report_row_paths] filename=%r current_path=%r normalized=%r original_path=%r",
                    out.get("filename"),
                    cp_text,
                    normalized,
                    op_text,
                )
    return out


def _norm_rel_path_for_match(path_text: str) -> str:
    """Normalize relative path text for prefix matching."""
    s = str(path_text or "").strip()
    if not s:
        return ""
    s = s.replace("/", os.sep).replace("\\", os.sep)
    s = os.path.normpath(s)
    while s.startswith("." + os.sep):
        s = s[2:]
    if s == ".":
        s = ""
    return os.path.normcase(s)


def _select_report_scope_files(
    selected_dir: str,
    report_root: str,
    full_report_cache: dict,
) -> tuple[list, dict]:
    """Filter full report cache down to the selected directory subtree."""
    files: list = []
    selected_report_cache: dict = {}
    selected_dir = os.path.normpath(selected_dir)
    report_root = os.path.normpath(report_root)
    selected_rel = ""
    if _is_same_or_child_path(report_root, selected_dir):
        try:
            selected_rel = os.path.relpath(selected_dir, report_root)
        except Exception:
            selected_rel = ""
    selected_rel_norm = _norm_rel_path_for_match(selected_rel)

    for stem, row in sorted(full_report_cache.items(), key=lambda kv: (kv[0].lower() if kv[0] else "")):
        cp_text = str(row.get("current_path") or "").strip()
        if selected_rel_norm and cp_text and not os.path.isabs(cp_text):
            cp_norm = _norm_rel_path_for_match(cp_text)
            if cp_norm != selected_rel_norm and not cp_norm.startswith(selected_rel_norm + os.sep):
                continue
        full_path = _resolve_report_full_path(row, report_root, selected_dir)
        if not full_path:
            continue
        if not _is_same_or_child_path(selected_dir, full_path):
            continue
        files.append(full_path)
        selected_report_cache[stem] = row
    return files, selected_report_cache


def _exec_menu(menu: "QMenu", global_pos) -> None:
    """兼容 PyQt5/6 的 QMenu.exec() 调用。"""
    try:
        menu.exec(global_pos)
    except TypeError:
        menu.exec_(global_pos)  # type: ignore[attr-defined]


# ── RAW 缩略图工具函数 ─────────────────────────────────────────────────────────

def _get_raw_thumbnail(path: str) -> bytes | None:
    """从 RAW 文件中提取嵌入 JPEG 缩略图字节，失败返回 None。"""
    if Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    try:
        import piexif
        data = piexif.load(path)
        thumb = data.get("thumbnail")
        if isinstance(thumb, bytes) and len(thumb) > 100:
            return thumb
    except Exception:
        pass
    try:
        import rawpy
        with rawpy.imread(path) as rp:
            thumb = rp.extract_thumb()
        if thumb is None:
            return None
        if hasattr(rawpy, "ThumbFormat") and thumb.format == rawpy.ThumbFormat.JPEG:
            if isinstance(thumb.data, bytes):
                return thumb.data
    except Exception:
        pass
    return None


def _load_thumbnail_image(path: str, size: int) -> "QImage | None":
    """
    线程安全的缩略图生成，返回 QImage（不使用 QPixmap）。
    先查磁盘缓存；未命中则调用 thumb_stream.load_thumbnail_rgb 解码，再异步写入磁盘缓存。
    """
    try:
        mtime = 0.0
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            pass
        disk_cached = _read_thumb_from_disk_cache(path, mtime, size)
        if disk_cached is not None and not disk_cached.isNull():
            return disk_cached
        result = thumb_stream.load_thumbnail_rgb(path, size)
        if result is None:
            return None
        data, w, h = result
        out = _rgb_bytes_to_qimage(data, w, h)
        cache_path = _thumb_disk_cache_path(path, mtime, size)
        _schedule_thumb_disk_cache_write(cache_path, out)
        return out
    except Exception:
        return None


# ── 可排序树节点 ───────────────────────────────────────────────────────────────

def _collect_image_files_impl(dir_path: str, recursive: bool) -> list:
    """
    收集目录下支持的图像文件路径。
    recursive=True 时递归遍历所有子目录；不进入以 . 开头的目录（如 .superpicky）。
    """
    files: list = []
    try:
        if recursive:
            for root, dirs, names in os.walk(dir_path, topdown=True):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in sorted(names, key=str.lower):
                    if Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                        files.append(os.path.join(root, name))
        else:
            for entry in sorted(os.scandir(dir_path), key=lambda e: e.name.lower()):
                if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS:
                    files.append(entry.path)
    except (PermissionError, OSError):
        pass
    return files


# ── 目录树浏览器 ───────────────────────────────────────────────────────────────


__all__ = [name for name in globals() if not name.startswith('__')]
