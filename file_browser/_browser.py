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
from dataclasses import dataclass
import hashlib
import html
import io as _io
import os
import queue as _queue
import sys
import threading
import time as _time
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
    find_xmp_sidecar,
    inject_metadata_cache,
    read_batch_metadata,
    run_exiftool_assignments,
)
from app_common.log import get_logger
from app_common.file_utils import reveal_in_file_manager, move_to_trash, move_empty_dirs_to_trash
from app_common.send_to_app import get_external_apps, send_files_to_app
from app_common.report_db import (
    ReportDB,
    report_row_to_exiftool_style,
    EXIF_ONLY_FROM_REPORT_DB,
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

_TREE_COL_SEQ = 0
_TREE_COL_NAME = 1
_TREE_COL_TITLE = 2
_TREE_COL_COLOR = 3
_TREE_COL_STAR = 4
_TREE_COL_SHARP = 5
_TREE_COL_AESTHETIC = 6
_TREE_COL_FOCUS = 7
_FILE_TABLE_HEADERS = ["#", "文件名", "标题", "颜色", "星级", "锐度值", "美学评分", "对焦状态"]

# 缩略图尺寸档位（像素）
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
    _EventToolTip = QEvent.Type.ToolTip
except AttributeError:
    _EventResize = QEvent.Resize  # type: ignore[attr-defined]
    _EventShow = QEvent.Show  # type: ignore[attr-defined]
    _EventKeyPress = QEvent.KeyPress  # type: ignore[attr-defined]
    _EventToolTip = QEvent.ToolTip  # type: ignore[attr-defined]

_KeyUp = getattr(Qt.Key, "Key_Up", None) or getattr(Qt, "Key_Up", None)
_KeyDown = getattr(Qt.Key, "Key_Down", None) or getattr(Qt, "Key_Down", None)
_KeyLeft = getattr(Qt.Key, "Key_Left", None) or getattr(Qt, "Key_Left", None)
_KeyRight = getattr(Qt.Key, "Key_Right", None) or getattr(Qt, "Key_Right", None)
_ShiftModifier = (
    getattr(Qt.KeyboardModifier, "ShiftModifier", None)
    or getattr(Qt, "ShiftModifier", None)
)

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
    if override in (128, 256, 512):
        return override
    return get_persistent_thumb_max_size()


def _persistent_thumb_cache_sizes() -> list[int]:
    return get_persistent_thumb_sizes(_persistent_thumb_cache_max_size())


def _persistent_thumb_cache_worker_count() -> int:
    override = _env_int("SuperViewer_PERSISTENT_THUMB_WORKERS", 0)
    if override > 0:
        return max(1, override)
    return max(1, get_persistent_thumb_workers())


def _persistent_thumb_cache_dirname(size: int) -> str:
    return f"thumb_preview_{int(size)}"


def _preview_cache_target_for_file(path: str, current_dir: str | None) -> str:
    if not path or not current_dir:
        return ""
    root_dir = os.path.normpath(current_dir)
    superpicky_dir = os.path.join(root_dir, ".superpicky")
    if os.path.isdir(superpicky_dir):
        preview_dir = os.path.join(superpicky_dir, "cache", "temp_preview")
    else:
        preview_dir = os.path.join(root_dir, "temp_preview")
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
    root_dir = os.path.normpath(current_dir)
    superpicky_dir = os.path.join(root_dir, ".superpicky")
    if os.path.isdir(superpicky_dir):
        return os.path.join(superpicky_dir, "cache", _persistent_thumb_cache_dirname(size))
    return os.path.join(root_dir, _persistent_thumb_cache_dirname(size))


def _persistent_thumb_cache_path_for_file(path: str, current_dir: str | None, size: int) -> str:
    cache_dir = _persistent_thumb_cache_dir(current_dir, size)
    if not cache_dir or not path:
        return ""
    digest = hashlib.sha1(_path_key(path).encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, digest[:2], f"{digest}.jpg")


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
) -> str:
    for size in get_preferred_persistent_thumb_sizes(
        requested_size,
        _persistent_thumb_cache_max_size(),
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

class SortableTreeItem(QTreeWidgetItem):
    """支持数值感知排序的 QTreeWidgetItem（通过 _SortRole 存储排序键）。"""

    def __lt__(self, other: "QTreeWidgetItem") -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        sv = self.data(col, _SortRole)
        ov = other.data(col, _SortRole)
        if sv is not None and ov is not None:
            try:
                return sv < ov
            except TypeError:
                return str(sv) < str(ov)
        return (self.text(col) or "") < (other.text(col) or "")


@dataclass
class FileTableEntry:
    path: str
    name: str
    tooltip: str = ""
    mismatch: bool = False
    title: str = ""
    color: str = ""
    color_display: str = ""
    rating: int = 0
    pick: int = 0
    city: str = ""
    state: str = ""
    country: str = ""


class FileTableModel(QAbstractTableModel):
    """Flat file-list model for the list view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[FileTableEntry] = []
        self._row_by_path: dict[str, int] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(_FILE_TABLE_HEADERS)

    def headerData(self, section: int, orientation, role: int = int(_DisplayRole)):
        if role != _DisplayRole:
            return None
        try:
            horizontal = Qt.Orientation.Horizontal
        except AttributeError:
            horizontal = Qt.Horizontal  # type: ignore[attr-defined]
        if orientation == horizontal and 0 <= section < len(_FILE_TABLE_HEADERS):
            return _FILE_TABLE_HEADERS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return _NoItemFlags
        return _ItemIsEnabled | _ItemIsSelectable

    def _apply_meta_to_entry(self, entry: FileTableEntry, meta: dict | None) -> None:
        meta = meta or {}
        entry.title = str(meta.get("title", "") or "")
        entry.color = str(meta.get("color", "") or "")
        entry.color_display = _COLOR_LABEL_COLORS.get(entry.color, ("", ""))[1] or entry.color
        try:
            entry.rating = int(meta.get("rating", 0) or 0)
        except Exception:
            entry.rating = 0
        try:
            entry.pick = int(meta.get("pick", 0) or 0)
        except Exception:
            entry.pick = 0
        entry.city = str(meta.get("city", "") or "")
        entry.state = str(meta.get("state", "") or "")
        entry.country = str(meta.get("country", "") or "")

    def _build_entry(
        self,
        path: str,
        *,
        meta_cache: dict,
        tooltip_fn,
        mismatch_fn,
    ) -> FileTableEntry:
        norm = os.path.normpath(path)
        entry = FileTableEntry(
            path=path,
            name=Path(path).name,
            tooltip=tooltip_fn(path),
            mismatch=bool(mismatch_fn(path)),
        )
        self._apply_meta_to_entry(entry, meta_cache.get(norm, {}) if isinstance(meta_cache, dict) else {})
        return entry

    def _sort_value(self, entry: FileTableEntry, column: int):
        if column == _TREE_COL_SEQ:
            return 0
        if column == _TREE_COL_NAME:
            return entry.name.lower()
        if column == _TREE_COL_TITLE:
            return entry.title.lower()
        if column == _TREE_COL_COLOR:
            return _COLOR_SORT_ORDER.get(entry.color, 99)
        if column == _TREE_COL_STAR:
            if entry.pick == 1:
                return 10
            if entry.pick == -1:
                return -1
            return entry.rating
        if column == _TREE_COL_SHARP:
            return entry.city.lower()
        if column == _TREE_COL_AESTHETIC:
            return entry.state.lower()
        if column == _TREE_COL_FOCUS:
            return entry.country.lower()
        return ""

    def _display_value(self, entry: FileTableEntry, row: int, column: int) -> str:
        if column == _TREE_COL_SEQ:
            return str(row + 1)
        if column == _TREE_COL_NAME:
            return entry.name
        if column == _TREE_COL_TITLE:
            return entry.title
        if column == _TREE_COL_COLOR:
            return entry.color_display
        if column == _TREE_COL_STAR:
            if entry.pick == 1:
                return "🏳"
            if entry.pick == -1:
                return "🚫"
            return "★" * max(0, entry.rating)
        if column == _TREE_COL_SHARP:
            return entry.city
        if column == _TREE_COL_AESTHETIC:
            return entry.state
        if column == _TREE_COL_FOCUS:
            return entry.country
        return ""

    def data(self, index: QModelIndex, role: int = int(_DisplayRole)):
        if not index.isValid():
            return None
        row = index.row()
        column = index.column()
        if row < 0 or row >= len(self._entries):
            return None
        entry = self._entries[row]
        if role == _DisplayRole:
            return self._display_value(entry, row, column)
        if role == _UserRole:
            return entry.path
        if role == _ToolTipRole:
            return entry.tooltip
        if role == _SortRole:
            return self._sort_value(entry, column)
        if role == _TextAlignmentRole and column == _TREE_COL_SEQ:
            return int(_AlignCenter)
        if role == _ForegroundRole:
            if column == _TREE_COL_NAME and entry.mismatch:
                return QBrush(QColor("#c0392b"))
            if column == _TREE_COL_COLOR and entry.color in _COLOR_LABEL_COLORS:
                return QBrush(QColor("#333" if entry.color in ("Yellow", "White") else "#fff"))
            if column == _TREE_COL_FOCUS:
                focus_color = _FOCUS_STATUS_TEXT_COLORS.get(entry.country, "")
                if focus_color:
                    return QBrush(QColor(focus_color))
            return None
        if role == _BackgroundRole and column == _TREE_COL_COLOR and entry.color in _COLOR_LABEL_COLORS:
            hex_c, _label = _COLOR_LABEL_COLORS[entry.color]
            return QBrush(QColor(hex_c))
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self._entries = []
        self._row_by_path = {}
        self.endResetModel()

    def rebuild(
        self,
        paths: list[str],
        *,
        meta_cache: dict,
        tooltip_fn,
        mismatch_fn,
    ) -> None:
        entries = [
            self._build_entry(
                path,
                meta_cache=meta_cache,
                tooltip_fn=tooltip_fn,
                mismatch_fn=mismatch_fn,
            )
            for path in paths
        ]
        row_by_path = {os.path.normpath(entry.path): row for row, entry in enumerate(entries)}
        self.beginResetModel()
        self._entries = entries
        self._row_by_path = row_by_path
        self.endResetModel()

    def row_for_path(self, path: str) -> int | None:
        norm = os.path.normpath(path) if path else ""
        row = self._row_by_path.get(norm)
        if row is None or row < 0 or row >= len(self._entries):
            return None
        return row

    def index_for_path(self, path: str, column: int = 0) -> QModelIndex:
        row = self.row_for_path(path)
        if row is None:
            return QModelIndex()
        col = max(0, min(self.columnCount() - 1, int(column)))
        return self.index(row, col)

    def path_for_row(self, row: int) -> str | None:
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row].path

    def path_for_index(self, index: QModelIndex) -> str | None:
        if not index.isValid():
            return None
        return self.path_for_row(index.row())

    def all_paths(self) -> list[str]:
        return [entry.path for entry in self._entries]

    def set_meta_for_path(self, path: str, meta: dict | None) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        entry = self._entries[row]
        self._apply_meta_to_entry(entry, meta)
        left = self.index(row, _TREE_COL_TITLE)
        right = self.index(row, _TREE_COL_FOCUS)
        self.dataChanged.emit(left, right, [_DisplayRole, _SortRole, _ForegroundRole, _BackgroundRole])
        return True

    def set_tooltip_for_path(self, path: str, tooltip: str) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        self._entries[row].tooltip = tooltip
        left = self.index(row, 0)
        right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(left, right, [_ToolTipRole])
        return True

    def set_path_mismatch_for_path(self, path: str, mismatch: bool) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        self._entries[row].mismatch = bool(mismatch)
        idx = self.index(row, _TREE_COL_NAME)
        self.dataChanged.emit(idx, idx, [_ForegroundRole])
        return True


class FileTableSortProxyModel(QSortFilterProxyModel):
    """Sort proxy with robust comparison and display-only row numbering."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(_SortRole)
        try:
            self.setDynamicSortFilter(False)
        except Exception:
            pass

    def data(self, index: QModelIndex, role: int = int(_DisplayRole)):
        if role == _DisplayRole and index.isValid() and index.column() == _TREE_COL_SEQ:
            return str(index.row() + 1)
        return super().data(index, role)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        source = self.sourceModel()
        lv = source.data(left, _SortRole) if source is not None else None
        rv = source.data(right, _SortRole) if source is not None else None
        if lv is not None and rv is not None:
            try:
                return lv < rv
            except TypeError:
                return str(lv) < str(rv)
        return super().lessThan(left, right)


class FileTableView(QTreeView):
    """Compatibility wrapper while list mode migrates from QTreeWidget APIs."""

    def setHeaderLabels(self, labels) -> None:
        try:
            self._header_labels = list(labels)
        except Exception:
            self._header_labels = []


# ── 缩略图 delegate（颜色标签 + 星级徽章）─────────────────────────────────────

@dataclass(frozen=True)
class ThumbViewportEntry:
    path: str
    row: int


@dataclass
class ThumbnailListEntry:
    path: str
    name: str
    tooltip: str = ""
    mismatch: bool = False
    color: str = ""
    rating: int = 0
    pick: int = 0
    focus_status: str = ""
    species_cn: str = ""
    pixmap: QPixmap | None = None
    thumb_size: int = 0


class ThumbnailListModel(QAbstractListModel):
    """Thumbnail view model backed by explicit entry data instead of widget items."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[ThumbnailListEntry] = []
        self._row_by_path: dict[str, int] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._entries)

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return _NoItemFlags
        return _ItemIsEnabled | _ItemIsSelectable

    def data(self, index: QModelIndex, role: int = int(_DisplayRole)):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._entries):
            return None
        entry = self._entries[row]
        if role == _DisplayRole:
            return entry.name
        if role == _UserRole:
            return entry.path
        if role == _ToolTipRole:
            return entry.tooltip
        if role == _ForegroundRole:
            return QBrush(QColor("#c0392b")) if entry.mismatch else None
        if role == _MetaColorRole:
            return entry.color
        if role == _MetaRatingRole:
            return entry.rating
        if role == _MetaPickRole:
            return entry.pick
        if role == _MetaFocusRole:
            return entry.focus_status
        if role == _MetaSpeciesCnRole:
            return entry.species_cn
        if role == _ThumbPixmapRole:
            return entry.pixmap
        if role == _ThumbSizeRole:
            return entry.thumb_size
        return None

    def _build_entry(
        self,
        path: str,
        *,
        meta_cache: dict,
        tooltip_fn,
        mismatch_fn,
    ) -> ThumbnailListEntry:
        norm = os.path.normpath(path)
        meta = meta_cache.get(norm, {}) if isinstance(meta_cache, dict) else {}
        try:
            rating = int(meta.get("rating", 0) or 0)
        except Exception:
            rating = 0
        try:
            pick = int(meta.get("pick", 0) or 0)
        except Exception:
            pick = 0
        return ThumbnailListEntry(
            path=path,
            name=Path(path).name,
            tooltip=tooltip_fn(path),
            mismatch=bool(mismatch_fn(path)),
            color=str(meta.get("color", "")),
            rating=rating,
            pick=pick,
            focus_status=str(meta.get("country", "")),
            species_cn=str(meta.get("bird_species_cn", "")),
        )

    def clear(self) -> None:
        self.beginResetModel()
        self._entries = []
        self._row_by_path = {}
        self.endResetModel()

    def append_paths(
        self,
        paths: list[str],
        *,
        meta_cache: dict,
        tooltip_fn,
        mismatch_fn,
    ) -> int:
        if not paths:
            return 0
        start_row = len(self._entries)
        new_entries = [
            self._build_entry(
                path,
                meta_cache=meta_cache,
                tooltip_fn=tooltip_fn,
                mismatch_fn=mismatch_fn,
            )
            for path in paths
        ]
        self.beginInsertRows(QModelIndex(), start_row, start_row + len(new_entries) - 1)
        self._entries.extend(new_entries)
        for offset, entry in enumerate(new_entries):
            self._row_by_path[os.path.normpath(entry.path)] = start_row + offset
        self.endInsertRows()
        return len(new_entries)

    def rebuild(
        self,
        paths: list[str],
        *,
        meta_cache: dict,
        tooltip_fn,
        mismatch_fn,
    ) -> None:
        entries = [
            self._build_entry(
                path,
                meta_cache=meta_cache,
                tooltip_fn=tooltip_fn,
                mismatch_fn=mismatch_fn,
            )
            for path in paths
        ]
        row_by_path = {os.path.normpath(entry.path): row for row, entry in enumerate(entries)}
        self.beginResetModel()
        self._entries = entries
        self._row_by_path = row_by_path
        self.endResetModel()

    def row_for_path(self, path: str) -> int | None:
        norm = os.path.normpath(path) if path else ""
        row = self._row_by_path.get(norm)
        if row is None:
            return None
        if row < 0 or row >= len(self._entries):
            return None
        return row

    def index_for_path(self, path: str) -> QModelIndex:
        row = self.row_for_path(path)
        if row is None:
            return QModelIndex()
        return self.index(row, 0)

    def path_for_row(self, row: int) -> str | None:
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row].path

    def path_for_index(self, index: QModelIndex) -> str | None:
        if not index.isValid():
            return None
        return self.path_for_row(index.row())

    def all_paths(self) -> list[str]:
        return [entry.path for entry in self._entries]

    def has_current_pixmap(self, path: str, thumb_size: int) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        entry = self._entries[row]
        pixmap = entry.pixmap
        return isinstance(pixmap, QPixmap) and not pixmap.isNull() and int(entry.thumb_size or 0) == int(thumb_size)

    def set_meta_for_path(self, path: str, meta: dict | None) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        entry = self._entries[row]
        meta = meta or {}
        changed_roles: list[int] = []
        new_color = str(meta.get("color", ""))
        if entry.color != new_color:
            entry.color = new_color
            changed_roles.append(_MetaColorRole)
        try:
            new_rating = int(meta.get("rating", 0) or 0)
        except Exception:
            new_rating = 0
        if entry.rating != new_rating:
            entry.rating = new_rating
            changed_roles.append(_MetaRatingRole)
        try:
            new_pick = int(meta.get("pick", 0) or 0)
        except Exception:
            new_pick = 0
        if entry.pick != new_pick:
            entry.pick = new_pick
            changed_roles.append(_MetaPickRole)
        new_focus_status = str(meta.get("country", ""))
        if entry.focus_status != new_focus_status:
            entry.focus_status = new_focus_status
            changed_roles.append(_MetaFocusRole)
        new_species_cn = str(meta.get("bird_species_cn", ""))
        if entry.species_cn != new_species_cn:
            entry.species_cn = new_species_cn
            changed_roles.append(_MetaSpeciesCnRole)
        if not changed_roles:
            return False
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, changed_roles)
        return True

    def set_pixmap_for_path(self, path: str, pixmap: QPixmap | None, thumb_size: int) -> int | None:
        row = self.row_for_path(path)
        if row is None:
            return None
        entry = self._entries[row]
        entry.pixmap = pixmap if isinstance(pixmap, QPixmap) and not pixmap.isNull() else None
        entry.thumb_size = int(thumb_size if entry.pixmap is not None else 0)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [_ThumbPixmapRole, _ThumbSizeRole])
        return row

    def set_pixmaps_for_paths(
        self,
        updates: list[tuple[str, QPixmap | None, int]],
    ) -> list[int]:
        if not updates:
            return []
        changed_rows: list[int] = []
        for path, pixmap, thumb_size in updates:
            row = self.row_for_path(path)
            if row is None:
                continue
            entry = self._entries[row]
            entry.pixmap = pixmap if isinstance(pixmap, QPixmap) and not pixmap.isNull() else None
            entry.thumb_size = int(thumb_size if entry.pixmap is not None else 0)
            changed_rows.append(row)
        if not changed_rows:
            return []
        changed_rows = sorted(set(changed_rows))
        range_start = changed_rows[0]
        range_end = range_start
        for row in changed_rows[1:]:
            if row == range_end + 1:
                range_end = row
                continue
            self.dataChanged.emit(
                self.index(range_start, 0),
                self.index(range_end, 0),
                [_ThumbPixmapRole, _ThumbSizeRole],
            )
            range_start = row
            range_end = row
        self.dataChanged.emit(
            self.index(range_start, 0),
            self.index(range_end, 0),
            [_ThumbPixmapRole, _ThumbSizeRole],
        )
        return changed_rows

    def clear_pixmap_for_path(self, path: str) -> int | None:
        row = self.row_for_path(path)
        if row is None:
            return None
        self.clear_pixmap_for_row(row)
        return row

    def clear_pixmap_for_row(self, row: int) -> bool:
        if row < 0 or row >= len(self._entries):
            return False
        entry = self._entries[row]
        if entry.pixmap is None and int(entry.thumb_size or 0) == 0:
            return False
        entry.pixmap = None
        entry.thumb_size = 0
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [_ThumbPixmapRole, _ThumbSizeRole])
        return True

    def clear_all_pixmaps(self) -> int:
        changed = 0
        for row, entry in enumerate(self._entries):
            if entry.pixmap is None and int(entry.thumb_size or 0) == 0:
                continue
            entry.pixmap = None
            entry.thumb_size = 0
            changed += 1
        if changed and self._entries:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._entries) - 1, 0),
                [_ThumbPixmapRole, _ThumbSizeRole],
            )
        return changed

    def set_tooltip_for_path(self, path: str, tooltip: str) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        self._entries[row].tooltip = tooltip
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [_ToolTipRole])
        return True

    def set_path_mismatch(self, path: str, mismatch: bool) -> bool:
        row = self.row_for_path(path)
        if row is None:
            return False
        self._entries[row].mismatch = bool(mismatch)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [_ForegroundRole])
        return True


@dataclass(frozen=True)
class ThumbViewportRange:
    thumb_size: int
    start_row: int
    end_row: int
    grid_width: int
    grid_height: int
    total_items: int
    entries: tuple[ThumbViewportEntry, ...]

    @property
    def signature(self) -> tuple:
        return (
            self.thumb_size,
            self.start_row,
            self.end_row,
            len(self.entries),
            self.total_items,
            self.grid_width,
            self.grid_height,
        )


class ThumbnailItemDelegate(QStyledItemDelegate):
    """Custom thumbnail delegate with aspect-fit preview and lightweight badges."""

    def sizeHint(self, option, index):
        widget = option.widget
        if widget is not None:
            grid = widget.gridSize()
            if grid.isValid():
                return grid
        return super().sizeHint(option, index)

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & _StateSelected)
        hovered = bool(opt.state & _StateMouseOver)
        name = str(index.data() or "")
        color_label = index.data(_MetaColorRole)
        rating = index.data(_MetaRatingRole)
        pick = index.data(_MetaPickRole)
        focus_status = str(index.data(_MetaFocusRole) or "").strip()
        species_cn = str(index.data(_MetaSpeciesCnRole) or "").strip()
        pixmap = index.data(_ThumbPixmapRole)
        if not isinstance(pixmap, QPixmap):
            pixmap = None

        painter.save()
        try:
            if selected:
                painter.fillRect(opt.rect, opt.palette.highlight())
            elif hovered:
                painter.fillRect(opt.rect, QColor(255, 255, 255, 16))

            painter.setRenderHint(_PainterAntialiasing)
            cell = opt.rect.adjusted(6, 6, -6, -6)
            fm = painter.fontMetrics()
            name_height = fm.lineSpacing() + 6
            thumb_rect = QRect(
                cell.left(),
                cell.top(),
                cell.width(),
                max(24, cell.height() - name_height - 6),
            )
            draw_rect = QRect(thumb_rect)

            painter.setBrush(QBrush(QColor(45, 45, 45)))
            painter.setPen(QColor(70, 70, 70))
            painter.drawRoundedRect(thumb_rect, 6, 6)

            if species_cn:
                pad = 4
                font_species = QFont(opt.font)
                font_species.setPixelSize(max(9, min(11, thumb_rect.width() // 12)))
                painter.setFont(font_species)
                fm_s = painter.fontMetrics()
                max_w = max(40, thumb_rect.width() - pad * 2)
                elided_cn = fm_s.elidedText(species_cn, _ElideRight, max_w)
                tw = min(max_w, fm_s.horizontalAdvance(elided_cn) if hasattr(fm_s, "horizontalAdvance") else fm_s.width(elided_cn)) + 8
                th = fm_s.lineSpacing() + 4
                badge_cn = QRect(thumb_rect.left() + pad, thumb_rect.top() + pad, tw, th)
                painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge_cn, 4, 4)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(badge_cn.adjusted(4, 0, -4, 0), _AlignCenter, elided_cn)

            if pixmap is not None and not pixmap.isNull():
                pw = max(1, pixmap.width())
                ph = max(1, pixmap.height())
                scale = min(thumb_rect.width() / float(pw), thumb_rect.height() / float(ph))
                draw_w = max(1, int(pw * scale))
                draw_h = max(1, int(ph * scale))
                draw_rect = QRect(
                    thumb_rect.left() + (thumb_rect.width() - draw_w) // 2,
                    thumb_rect.top() + (thumb_rect.height() - draw_h) // 2,
                    draw_w,
                    draw_h,
                )
                painter.drawPixmap(draw_rect, pixmap)

            has_color = bool(color_label and color_label in _COLOR_LABEL_COLORS)
            if pick == 1:
                right_badge_text = "🏆"
                right_badge_bg = QColor(0, 0, 0, 160)
                right_badge_fg = QColor(COLORS["star_gold"])
            elif pick == -1:
                right_badge_text = "🚫"
                right_badge_bg = QColor(0, 0, 0, 160)
                right_badge_fg = QColor("#ffffff")
            elif isinstance(rating, int) and rating > 0:
                right_badge_text = "★" * min(5, rating)
                right_badge_bg = QColor(0, 0, 0, 140)
                right_badge_fg = QColor(_STAR_SILVER_COLOR)
            else:
                right_badge_text = ""

            if has_color:
                hex_c, cn = _COLOR_LABEL_COLORS[color_label]
                bw, bh = 30, 16
                badge = QRect(draw_rect.left() + 2, draw_rect.bottom() - bh - 2, bw, bh)
                painter.setBrush(QBrush(QColor(hex_c)))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge, 4, 4)
                painter.setPen(QColor("#333" if color_label in ("Yellow", "White") else "#fff"))
                f = QFont(opt.font)
                f.setPixelSize(9)
                painter.setFont(f)
                painter.drawText(badge, _AlignCenter, cn)

            if right_badge_text:
                f2 = QFont(opt.font)
                f2.setPixelSize(11)
                painter.setFont(f2)
                fm2 = painter.fontMetrics()
                try:
                    sw = fm2.horizontalAdvance(right_badge_text)
                except AttributeError:
                    sw = fm2.width(right_badge_text)
                bw2, bh2 = sw + 10, 16
                badge2 = QRect(draw_rect.right() - bw2 - 2, draw_rect.top() + 2, bw2, bh2)
                painter.setBrush(QBrush(right_badge_bg))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge2, 4, 4)
                painter.setPen(right_badge_fg)
                painter.drawText(badge2, _AlignCenter, right_badge_text)

            if focus_status:
                focus_color = _FOCUS_STATUS_TEXT_COLORS.get(focus_status, COLORS["text_secondary"])
                focus_font = QFont(opt.font)
                focus_font.setPixelSize(max(10, opt.font.pixelSize() if opt.font.pixelSize() > 0 else 10))
                painter.setFont(focus_font)
                fm3 = painter.fontMetrics()
                try:
                    sw3 = fm3.horizontalAdvance(focus_status)
                except AttributeError:
                    sw3 = fm3.width(focus_status)
                bw3, bh3 = sw3 + 10, 16
                badge3 = QRect(draw_rect.right() - bw3 - 2, draw_rect.bottom() - bh3 - 2, bw3, bh3)
                painter.setBrush(QBrush(QColor(0, 0, 0, 150)))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge3, 4, 4)
                painter.setPen(QColor(focus_color))
                painter.drawText(badge3, _AlignCenter, focus_status)

            text_rect = QRect(cell.left(), thumb_rect.bottom() + 4, cell.width(), name_height)
            text_color = opt.palette.highlightedText().color() if selected else opt.palette.text().color()
            painter.setPen(text_color)
            painter.setFont(opt.font)
            elided = fm.elidedText(name, _ElideRight, text_rect.width())
            painter.drawText(text_rect, _AlignCenter, elided)
        finally:
            painter.restore()


def _compute_thumb_cache_max_bytes() -> int:
    """Budget for the thumbnail QImage memory cache.

    Hard cap: 16 GB.  On machines where physical RAM is detectable we also
    limit to 25 % of total RAM so the app doesn't starve the OS on small
    machines (e.g. 16 GB system → 4 GB cache; 64 GB system → 16 GB cache).
    """
    hard_cap = 48 * 1024 * 1024 * 1024  # 16 GB
    total_ram = 0
    try:
        import psutil  # optional dependency
        total_ram = psutil.virtual_memory().total
    except Exception:
        pass
    if total_ram <= 0:
        try:
            # POSIX fallback (macOS / Linux)
            total_ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            pass
    if total_ram > 0:
        return min(hard_cap, int(total_ram * 0.25))
    return hard_cap


_THUMB_CACHE_MAX_BYTES_DEFAULT = _compute_thumb_cache_max_bytes()
_THUMB_MODEL_APPEND_BATCH_SIZE = 160
_THUMB_MODEL_APPEND_BUDGET_S = 0.008


class ThumbnailMemoryCache:
    """Thread-safe thumbnail cache with JPEG mip levels, max-size fallback for others, and LRU eviction."""

    def __init__(self, max_bytes: int | None = None) -> None:
        self._lock = threading.RLock()
        self._jpeg_mips: dict[tuple[str, int], QImage] = {}
        self._base_images: dict[str, QImage] = {}
        self._bytes: int = 0
        self._max_bytes = int(max_bytes or _THUMB_CACHE_MAX_BYTES_DEFAULT)
        self._lru_keys: list[tuple[str, object]] = []  # ("jpeg", (ckey, size)) | ("base", ckey)

    def _lru_key_jpeg(self, cache_key: str, requested_size: int) -> tuple[str, tuple[str, int]]:
        return ("jpeg", (cache_key, int(requested_size)))

    def _lru_key_base(self, cache_key: str) -> tuple[str, str]:
        return ("base", cache_key)

    def _evict_until_under_limit(self) -> None:
        while self._bytes > self._max_bytes and self._lru_keys:
            key = self._lru_keys.pop(0)
            if key[0] == "jpeg":
                img = self._jpeg_mips.pop(key[1], None)
            else:
                img = self._base_images.pop(key[1], None)
            if img is not None and not img.isNull():
                self._bytes -= _qimage_num_bytes(img)

    def _store_image(self, bucket: dict, key, image: QImage) -> None:
        old = bucket.get(key)
        if old is not None:
            self._bytes -= _qimage_num_bytes(old)
        stored = image.copy()
        bucket[key] = stored
        self._bytes += _qimage_num_bytes(stored)

    def _is_jpeg_like(self, path: str) -> bool:
        return Path(path).suffix.lower() in _JPEG_MIP_EXTENSIONS

    def get(self, source_path: str, requested_size: int) -> QImage | None:
        cache_key = _thumb_cache_key(source_path)
        with self._lock:
            if self._is_jpeg_like(source_path):
                k = self._lru_key_jpeg(cache_key, requested_size)
                if k in self._lru_keys:
                    self._lru_keys.remove(k)
                    self._lru_keys.append(k)
                cached = self._jpeg_mips.get((cache_key, int(requested_size)))
                return cached.copy() if cached is not None else None
            k = self._lru_key_base(cache_key)
            if k in self._lru_keys:
                self._lru_keys.remove(k)
                self._lru_keys.append(k)
            base = self._base_images.get(cache_key)
        if base is None:
            return None
        return _scale_qimage_for_thumb(base, requested_size)

    def put(self, source_path: str, requested_size: int, image: QImage) -> None:
        if image is None or image.isNull():
            return
        cache_key = _thumb_cache_key(source_path)
        with self._lock:
            if self._is_jpeg_like(source_path):
                jkey = (cache_key, int(requested_size))
                lru_k = self._lru_key_jpeg(cache_key, requested_size)
                if lru_k in self._lru_keys:
                    self._lru_keys.remove(lru_k)
                self._store_image(self._jpeg_mips, jkey, image)
                self._lru_keys.append(lru_k)
            else:
                lru_k = self._lru_key_base(cache_key)
                if lru_k in self._lru_keys:
                    self._lru_keys.remove(lru_k)
                self._store_image(self._base_images, cache_key, image)
                self._lru_keys.append(lru_k)
            self._evict_until_under_limit()

    def evict_other_dirs(self, current_dir_norm: str) -> int:
        """Evict all cached QImage entries whose file path does NOT belong to
        current_dir_norm (or any of its subdirectories).

        Called on every directory switch to implement folder-level FIFO eviction:
        the moment the user navigates away from a folder its cached thumbnails
        are freed, regardless of LRU age.  Within the new/current folder the
        existing byte-limit LRU eviction continues normally.

        current_dir_norm must be the same normalised absolute path that
        _thumb_cache_key() / _path_key() would produce for the directory.

        Returns the number of bytes freed.
        """
        prefix = current_dir_norm + os.sep  # e.g. "/photos/2024/"
        freed = 0
        with self._lock:
            # Collect stale LRU keys in one pass before mutating the dicts.
            stale = [
                lru_k for lru_k in self._lru_keys
                if not (lru_k[1][0] if lru_k[0] == "jpeg" else lru_k[1]).startswith(prefix)
            ]
            for lru_k in stale:
                if lru_k[0] == "jpeg":
                    img = self._jpeg_mips.pop(lru_k[1], None)
                else:
                    img = self._base_images.pop(lru_k[1], None)
                try:
                    self._lru_keys.remove(lru_k)
                except ValueError:
                    pass
                if img is not None and not img.isNull():
                    nb = _qimage_num_bytes(img)
                    self._bytes -= nb
                    freed += nb
        return freed

    def clear(self) -> dict[str, int]:
        with self._lock:
            stats = self.stats()
            self._jpeg_mips.clear()
            self._base_images.clear()
            self._lru_keys.clear()
            self._bytes = 0
        return stats

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "jpeg_levels": len(self._jpeg_mips),
                "base_images": len(self._base_images),
                "entries": len(self._jpeg_mips) + len(self._base_images),
                "bytes": int(self._bytes),
                "max_bytes": self._max_bytes,
            }


class ThumbnailLoader(QThread):
    """Background thumbnail loader with a priority queue and internal worker pool.

    Priority levels:
      PRIORITY_VISIBLE  (0) – currently visible items; processed first.
      PRIORITY_PREFETCH (1) – nearby but not yet visible; processed when idle.

    Thread-safety contract
    ----------------------
    ``enqueue()`` and ``promote()`` may be called from the main thread at any
    time, including while ``run()`` is executing.  The internal lock serialises
    mutations to the queued/loaded sets.  ``run()`` polls the priority queue
    with a short timeout so newly injected high-priority items are picked up
    within one batch cycle (≤ max_workers completions).
    """

    thumbnail_ready = pyqtSignal(int, str, object)  # (request_token, path, QImage)

    PRIORITY_VISIBLE  = 0  # noqa: E221
    PRIORITY_PREFETCH = 1

    def __init__(
        self,
        size: int,
        request_token: int,
        report_cache: dict | None = None,
        current_dir: str | None = None,
        thumb_cache: ThumbnailMemoryCache | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._size = int(size)
        self._request_token = int(request_token)
        self._report_cache = report_cache or {}
        self._current_dir = current_dir or ""
        self._thumb_cache = thumb_cache
        self._stop_flag = False
        self._executor: _futures.ThreadPoolExecutor | None = None
        self._max_workers = _thumbnail_loader_worker_count()
        self._batch_size = _thumbnail_loader_batch_size(self._max_workers)

        # Priority queue: items are (priority, seq, path)
        self._task_queue: _queue.PriorityQueue = _queue.PriorityQueue()
        self._queued:  set[str] = set()   # paths currently sitting in the queue
        self._loaded:  set[str] = set()   # paths already submitted to executor
        self._desired_paths: set[str] = set()
        self._seq = 0                      # monotonic counter for stable FIFO within same priority
        self._queue_lock = threading.Lock()
        self._profile_lock = threading.Lock()
        self._profile_enabled = _THUMB_PROFILE_ENABLED
        self._profile_started_at = _time.perf_counter()
        self._profile_enqueued_visible = 0
        self._profile_enqueued_prefetch = 0
        self._profile_promoted = 0
        self._profile_batches = 0
        self._profile_submitted = 0
        self._profile_completed = 0
        self._profile_memory_hits = 0
        self._profile_disk_hits = 0
        self._profile_progressive_paths = 0
        self._profile_single_shot_paths = 0
        self._profile_frames_emitted = 0
        self._profile_decode_total_s = 0.0
        self._profile_decode_max_s = 0.0
        self._profile_decode_max_path = ""

    # ── Public API (thread-safe) ─────────────────────────────────────────────

    def _profile_record_decode(
        self,
        path: str,
        *,
        elapsed_s: float,
        frames_emitted: int,
        memory_hit: bool = False,
        disk_hit: bool = False,
        progressive: bool = False,
        single_shot: bool = False,
    ) -> None:
        if not self._profile_enabled:
            return
        _record_thumb_bottleneck_sample("decode_ms", elapsed_s * 1000.0)
        with self._profile_lock:
            self._profile_completed += 1
            self._profile_frames_emitted += max(0, int(frames_emitted))
            self._profile_decode_total_s += max(0.0, float(elapsed_s))
            if elapsed_s > self._profile_decode_max_s:
                self._profile_decode_max_s = float(elapsed_s)
                self._profile_decode_max_path = path
            if memory_hit:
                self._profile_memory_hits += 1
            if disk_hit:
                self._profile_disk_hits += 1
            if progressive:
                self._profile_progressive_paths += 1
            if single_shot:
                self._profile_single_shot_paths += 1

    def profile_snapshot(self) -> dict[str, object]:
        with self._queue_lock:
            queue_size = int(self._task_queue.qsize())
            queued_count = len(self._queued)
            loaded_count = len(self._loaded)
        with self._profile_lock:
            return {
                "started_at": self._profile_started_at,
                "queue_size": queue_size,
                "queued_count": queued_count,
                "loaded_count": loaded_count,
                "enqueued_visible": self._profile_enqueued_visible,
                "enqueued_prefetch": self._profile_enqueued_prefetch,
                "promoted": self._profile_promoted,
                "batches": self._profile_batches,
                "submitted": self._profile_submitted,
                "completed": self._profile_completed,
                "memory_hits": self._profile_memory_hits,
                "disk_hits": self._profile_disk_hits,
                "progressive_paths": self._profile_progressive_paths,
                "single_shot_paths": self._profile_single_shot_paths,
                "frames_emitted": self._profile_frames_emitted,
                "decode_total_s": self._profile_decode_total_s,
                "decode_max_s": self._profile_decode_max_s,
                "decode_max_path": self._profile_decode_max_path,
            }

    @staticmethod
    def _normalize_unique_paths(paths: list[str] | None) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for path in paths or []:
            if not path:
                continue
            norm = os.path.normpath(path)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(norm)
        return result

    def set_desired_paths(
        self,
        visible_paths: list[str] | None = None,
        prefetch_paths: list[str] | None = None,
    ) -> None:
        desired = set(self._normalize_unique_paths(visible_paths))
        desired.update(self._normalize_unique_paths(prefetch_paths))
        with self._queue_lock:
            self._desired_paths = desired

    def replace_pending(
        self,
        visible_paths: list[str] | None = None,
        prefetch_paths: list[str] | None = None,
    ) -> int:
        visible_norms = self._normalize_unique_paths(visible_paths)
        visible_set = set(visible_norms)
        prefetch_norms = [
            norm
            for norm in self._normalize_unique_paths(prefetch_paths)
            if norm not in visible_set
        ]
        desired = set(visible_norms)
        desired.update(prefetch_norms)

        replaced = 0
        with self._queue_lock:
            self._task_queue = _queue.PriorityQueue()
            self._queued.clear()
            self._desired_paths = desired
            for norm in visible_norms:
                if norm in self._loaded:
                    continue
                self._seq += 1
                self._task_queue.put_nowait((self.PRIORITY_VISIBLE, self._seq, norm))
                self._queued.add(norm)
                replaced += 1
            for norm in prefetch_norms:
                if norm in self._loaded or norm in self._queued:
                    continue
                self._seq += 1
                self._task_queue.put_nowait((self.PRIORITY_PREFETCH, self._seq, norm))
                self._queued.add(norm)
                replaced += 1
        return replaced

    def wants_path(self, path: str) -> bool:
        norm = os.path.normpath(path)
        with self._queue_lock:
            return norm in self._desired_paths

    def _resolve_load_target_path(self, path: str) -> str:
        norm_path = os.path.normpath(path)
        source_path = _resolve_thumb_source_path(norm_path, self._report_cache, self._current_dir)
        source_stamp = _thumb_source_stamp(norm_path, source_path)
        persistent_path = _existing_persistent_thumb_cache_path_for_file(
            norm_path,
            self._current_dir,
            requested_size=self._size,
            source_stamp=source_stamp,
        )
        if persistent_path:
            return persistent_path
        return source_path

    def enqueue(self, paths: list[str], priority: int = PRIORITY_VISIBLE) -> int:
        """Add *paths* to the priority queue at *priority*.

        Paths that are already loaded or already sitting in the queue are
        skipped (no duplicates).  Returns the number of newly enqueued paths.
        """
        added = 0
        with self._queue_lock:
            for path in paths:
                norm = os.path.normpath(path)
                if norm in self._loaded or norm in self._queued:
                    continue
                self._seq += 1
                self._task_queue.put_nowait((priority, self._seq, norm))
                self._queued.add(norm)
                self._desired_paths.add(norm)
                added += 1
        if self._profile_enabled and added > 0:
            with self._profile_lock:
                if int(priority) == int(self.PRIORITY_VISIBLE):
                    self._profile_enqueued_visible += added
                else:
                    self._profile_enqueued_prefetch += added
        return added

    def promote(self, paths: list[str]) -> int:
        """Re-queue *paths* at ``PRIORITY_VISIBLE`` regardless of current state.

        If a path is already loaded it is skipped.  If it is already in the
        queue at a lower priority a second entry at priority 0 is inserted;
        the original lower-priority entry will be discarded when dequeued
        (detected via the ``_loaded`` set).  Returns the number of entries
        inserted.
        """
        promoted = 0
        with self._queue_lock:
            for path in paths:
                norm = os.path.normpath(path)
                if norm in self._loaded:
                    continue
                self._desired_paths.add(norm)
                self._seq += 1
                self._task_queue.put_nowait((self.PRIORITY_VISIBLE, self._seq, norm))
                self._queued.add(norm)  # idempotent; may already be present
                promoted += 1
        if self._profile_enabled and promoted > 0:
            with self._profile_lock:
                self._profile_promoted += promoted
        return promoted

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()
        with self._queue_lock:
            self._desired_paths.clear()

    def _load_single(self, path: str, emit_fn, *, allow_progressive: bool) -> None:
        """Decode one image progressively, calling emit_fn(path, QImage) for every
        available frame — coarse frames first, final high-quality frame last.

        emit_fn is called from the thread-pool worker thread.  Qt cross-thread
        signal delivery (queued connection) makes this safe: each call posts a
        QMetaCallEvent to the main thread's event loop instead of invoking the
        slot directly.

        Progressive pipeline for JPEG / RAW:
          1. Memory cache hit  → emit once, done (fastest path).
          2. Disk cache hit    → emit once, populate memory cache, done.
          3. Progressive feed  → emit BILINEAR intermediate frames as libjpeg
                                  decodes successive JPEG scans, then emit the
                                  final LANCZOS frame; cache that final frame.
        Non-JPEG / non-RAW falls back to a single-shot load.
        """
        path_to_load = os.path.normpath(path)
        load_started_at = _time.perf_counter()
        emitted_frames = 0

        def stopped() -> bool:
            return self._stop_flag or self.isInterruptionRequested()

        def safe_emit(qimg: QImage) -> None:
            nonlocal emitted_frames
            if not stopped() and not qimg.isNull() and self.wants_path(path_to_load):
                emitted_frames += 1
                emit_fn(self._request_token, path_to_load, qimg)

        if stopped():
            return

        cache = self._thumb_cache
        load_size = self._size
        load_target_path = self._resolve_load_target_path(path_to_load)

        # ── 1. Memory cache ──────────────────────────────────────────────────
        if cache is not None:
            cached = cache.get(path_to_load, load_size)
            if cached is not None and not cached.isNull():
                safe_emit(cached)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    memory_hit=True,
                )
                return

        ext = Path(load_target_path).suffix.lower()

        # ── 2. JPEG / RAW: disk cache then progressive pipeline ──────────────
        if ext in thumb_stream._JPEG_EXTENSIONS or ext in thumb_stream._RAW_EXTENSIONS:
            try:
                mtime = os.path.getmtime(load_target_path)
            except Exception:
                mtime = 0.0

            disk_img = _read_thumb_from_disk_cache(load_target_path, mtime, load_size)
            if disk_img is not None and not disk_img.isNull():
                if cache is not None:
                    cache.put(path_to_load, load_size, disk_img)
                safe_emit(disk_img)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    disk_hit=True,
                )
                return

            # Progressive decode – emit each frame as it arrives
            if not allow_progressive:
                qimg = _load_thumbnail_image(load_target_path, load_size)
                if qimg is None or qimg.isNull() or stopped():
                    return
                if cache is not None:
                    cache.put(path_to_load, load_size, qimg)
                    cached = cache.get(path_to_load, load_size)
                    if cached is not None and not cached.isNull():
                        qimg = cached
                safe_emit(qimg)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    single_shot=True,
                )
                return

            final_qimg: QImage | None = None
            for rgb_result in thumb_stream.iter_thumbnail_rgb_progressive(
                load_target_path, load_size, stopped
            ):
                if stopped():
                    return
                data, w, h = rgb_result
                qimg = _rgb_bytes_to_qimage(data, w, h)
                safe_emit(qimg)
                final_qimg = qimg

            if final_qimg is not None and not final_qimg.isNull():
                if cache is not None:
                    cache.put(path_to_load, load_size, final_qimg)
                cache_path = _thumb_disk_cache_path(load_target_path, mtime, load_size)
                _schedule_thumb_disk_cache_write(cache_path, final_qimg)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    progressive=True,
                )

        # ── 3. Other formats: single-shot load (handles disk cache internally) ─
        else:
            qimg = _load_thumbnail_image(load_target_path, load_size)
            if qimg is None or qimg.isNull() or stopped():
                return
            if cache is not None:
                cache.put(path_to_load, load_size, qimg)
                cached = cache.get(path_to_load, load_size)
                if cached is not None and not cached.isNull():
                    qimg = cached
            safe_emit(qimg)
            self._profile_record_decode(
                path_to_load,
                elapsed_s=_time.perf_counter() - load_started_at,
                frames_emitted=emitted_frames,
                single_shot=True,
            )

    def run(self) -> None:
        if self._task_queue.empty():
            return
        if self._profile_enabled:
            _log.info(
                "[THUMB_PROFILE][loader.start] token=%s size=%s workers=%s batch=%s initial_queue=%s",
                self._request_token,
                self._size,
                self._max_workers,
                self._batch_size,
                int(self._task_queue.qsize()),
            )
        else:
            _log.debug(
                "[ThumbnailLoader.run] START size=%s workers=%s batch=%s",
                self._size,
                self._max_workers,
                self._batch_size,
            )
        executor = _futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="thumb",
        )
        self._executor = executor
        # emit_fn is called from pool-worker threads; Qt queued-connection
        # delivery is thread-safe and routes each call to the main event loop.
        emit_fn = self.thumbnail_ready.emit

        try:
            while not self._stop_flag and not self.isInterruptionRequested():
                # ── Drain priority queue into one batch ──────────────────────
                # We submit max_workers items at a time so that new high-priority
                # items injected via promote() can jump ahead after each batch.
                batch: list[tuple[int, str]] = []
                while len(batch) < self._max_workers:
                    with self._queue_lock:
                        if len(batch) >= self._batch_size:
                            break
                        try:
                            priority, _, path = self._task_queue.get_nowait()
                        except _queue.Empty:
                            break
                        self._queued.discard(path)
                        if path in self._loaded:
                            continue  # duplicate from promote(); skip
                        self._loaded.add(path)
                    batch.append((priority, path))

                if not batch:
                    # Queue is empty; wait briefly for newly injected items.
                    _time.sleep(0.05)
                    if self._task_queue.empty():
                        break
                    continue
                if self._profile_enabled:
                    with self._profile_lock:
                        self._profile_batches += 1

                # ── Submit batch to thread-pool workers ──────────────────────
                future_map: dict[_futures.Future, str] = {}
                for priority, path in batch:
                    if self._stop_flag or self.isInterruptionRequested():
                        break
                    try:
                        f = executor.submit(
                            self._load_single,
                            path,
                            emit_fn,
                            allow_progressive=(priority == self.PRIORITY_VISIBLE),
                        )
                        future_map[f] = path
                        if self._profile_enabled:
                            with self._profile_lock:
                                self._profile_submitted += 1
                    except RuntimeError as e:
                        _log.info("[ThumbnailLoader.run] submit stopped path=%r: %s", path, e)
                        break

                # ── Wait for this batch before taking the next ───────────────
                # Waiting (rather than fire-and-forget) lets the priority queue
                # be checked again after each batch, so newly-visible items
                # injected via promote() are processed in the next iteration.
                for f in _futures.as_completed(future_map):
                    if self._stop_flag or self.isInterruptionRequested():
                        break
                    try:
                        f.result()
                    except Exception as e:
                        _log.warning(
                            "[ThumbnailLoader.run] failed path=%r: %s",
                            future_map[f], e,
                        )

        finally:
            if self._profile_enabled:
                snap = self.profile_snapshot()
                elapsed_s = max(0.001, _time.perf_counter() - self._profile_started_at)
                avg_decode_ms = 1000.0 * float(snap.get("decode_total_s", 0.0)) / max(1, int(snap.get("completed", 0)))
                _log.info(
                    "[THUMB_PROFILE][loader.end] token=%s elapsed=%.2fs visible=%s prefetch=%s promoted=%s batches=%s submitted=%s completed=%s queue=%s mem_hit=%s disk_hit=%s progressive=%s single=%s frames=%s avg_decode=%.1fms max_decode=%.1fms max_path=%r",
                    self._request_token,
                    elapsed_s,
                    snap.get("enqueued_visible", 0),
                    snap.get("enqueued_prefetch", 0),
                    snap.get("promoted", 0),
                    snap.get("batches", 0),
                    snap.get("submitted", 0),
                    snap.get("completed", 0),
                    snap.get("queue_size", 0),
                    snap.get("memory_hits", 0),
                    snap.get("disk_hits", 0),
                    snap.get("progressive_paths", 0),
                    snap.get("single_shot_paths", 0),
                    snap.get("frames_emitted", 0),
                    avg_decode_ms,
                    1000.0 * float(snap.get("decode_max_s", 0.0)),
                    snap.get("decode_max_path", ""),
                )
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
            self._executor = None
            _log.debug("[ThumbnailLoader.run] END")


class PersistentThumbCacheWorker(QThread):
    """Build restart-persistent small thumbnail JPEGs for the current directory."""

    progress_updated = pyqtSignal(int, int, int, int, int, str)
    finished_summary = pyqtSignal(int, int, int, int, int)

    def __init__(
        self,
        paths: list[str],
        current_dir: str,
        *,
        report_cache: dict | None = None,
        sizes: list[int] | tuple[int, ...] | None = None,
        worker_count: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = [os.path.normpath(p) for p in paths if p]
        self._current_dir = os.path.normpath(current_dir) if current_dir else ""
        self._report_cache = report_cache or {}
        normalized_sizes = sorted(
            {
                int(size)
                for size in (sizes or _persistent_thumb_cache_sizes())
                if int(size) in (128, 256, 512)
            }
        )
        self._sizes = tuple(normalized_sizes or _persistent_thumb_cache_sizes())
        self._worker_count = max(1, int(worker_count or _persistent_thumb_cache_worker_count()))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self.requestInterruption()

    def _process_path(self, source_path: str) -> tuple[str, int, int, int]:
        if self._stop_event.is_set():
            return source_path, 0, 0, 1
        load_target_path = _resolve_thumb_source_path(
            source_path,
            self._report_cache,
            self._current_dir,
        )
        source_stamp = _thumb_source_stamp(source_path, load_target_path)
        missing_sizes = [
            size
            for size in self._sizes
            if not _existing_persistent_thumb_cache_path_for_exact_size(
                source_path,
                self._current_dir,
                size,
                source_stamp=source_stamp,
            )
        ]
        if not missing_sizes:
            return source_path, 0, 1, 0
        if self._stop_event.is_set():
            return source_path, 0, 0, 1
        base_image = _load_thumbnail_image(load_target_path, max(missing_sizes))
        if base_image is None or base_image.isNull():
            return source_path, 0, 0, 1
        wrote_any = False
        for size in missing_sizes:
            if self._stop_event.is_set():
                break
            target_path = _persistent_thumb_cache_path_for_file(
                source_path,
                self._current_dir,
                size,
            )
            output_image = base_image if size >= max(missing_sizes) else _scale_qimage_for_thumb(base_image, size)
            if (
                target_path
                and not output_image.isNull()
                and _write_persistent_thumb_cache_image(
                    target_path,
                    output_image,
                    source_stamp=source_stamp,
                )
            ):
                wrote_any = True
        if wrote_any:
            return source_path, 1, 0, 0
        return source_path, 0, 0, 1

    def run(self) -> None:
        total = len(self._paths)
        processed = 0
        generated = 0
        skipped = 0
        failed = 0
        current_path = ""
        started_at = _time.perf_counter()
        last_emit_at = 0.0

        def emit_progress(force: bool = False) -> None:
            nonlocal last_emit_at
            now = _time.perf_counter()
            if (
                not force
                and processed < total
                and processed != 1
                and processed % 8 != 0
                and (now - last_emit_at) < 0.15
            ):
                return
            last_emit_at = now
            self.progress_updated.emit(
                processed,
                total,
                generated,
                skipped,
                failed,
                current_path,
            )

        _log.info(
            "[PersistentThumbCacheWorker.run] START dir=%r total=%s sizes=%s workers=%s",
            self._current_dir,
            total,
            list(self._sizes),
            self._worker_count,
        )
        executor: _futures.ThreadPoolExecutor | None = None
        try:
            executor = _futures.ThreadPoolExecutor(
                max_workers=self._worker_count,
                thread_name_prefix="thumb_preview",
            )
            futures = {
                executor.submit(self._process_path, source_path): source_path
                for source_path in self._paths
            }
            for future in _futures.as_completed(futures):
                current_path = futures.get(future, "") or current_path
                try:
                    _, generated_inc, skipped_inc, failed_inc = future.result()
                except Exception:
                    generated_inc = 0
                    skipped_inc = 0
                    failed_inc = 1
                processed += 1
                generated += generated_inc
                skipped += skipped_inc
                failed += failed_inc
                emit_progress()
                if self.isInterruptionRequested() or self._stop_event.is_set():
                    break
        finally:
            self._stop_event.set()
            if executor is not None:
                try:
                    executor.shutdown(wait=True, cancel_futures=True)
                except Exception:
                    pass
            emit_progress(force=True)
            self.finished_summary.emit(processed, total, generated, skipped, failed)
            _log.info(
                "[PersistentThumbCacheWorker.run] END dir=%r processed=%s/%s generated=%s skipped=%s failed=%s elapsed=%.2fs",
                self._current_dir,
                processed,
                total,
                generated,
                skipped,
                failed,
                _time.perf_counter() - started_at,
            )


class DirectoryScanWorker(QThread):
    """在后台执行目录扫描与 report.db 加载，完成后通过信号回传结果。"""

    scan_finished = pyqtSignal(str, object, object, object)  # (path, files_list, selected_report_cache, full_report_cache_or_none)

    def __init__(
        self,
        path: str,
        recursive: bool,
        report_root: str | None = None,
        report_cache_full: dict | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._recursive = recursive
        self._report_root = report_root
        self._report_cache_full = report_cache_full

    def run(self) -> None:
        _log.info(
            "[DirectoryScanWorker.run] START path=%r recursive=%s report_root=%r has_cached_full_report=%s",
            self._path,
            self._recursive,
            self._report_root,
            self._report_cache_full is not None,
        )
        report_cache: dict = {}
        full_report_cache: dict | None = self._report_cache_full
        report_source_available = self._report_cache_full is not None
        try:
            if self._report_cache_full is not None:
                report_cache = self._report_cache_full
                _log.info("[DirectoryScanWorker.run] reuse cached full report_cache %s entries", len(report_cache))
            else:
                db_dir = self._report_root or self._path
                db = ReportDB.open_if_exists(db_dir)
                if db:
                    report_source_available = True
                    full_report_cache = {}
                    try:
                        for row in db.get_all_photos():
                            r = _normalize_report_row_paths(dict(row))
                            stem = r.get("filename")
                            if stem is not None:
                                full_report_cache[stem] = r
                    finally:
                        db.close()
                    report_cache = full_report_cache
                _log.info("[DirectoryScanWorker.run] report_cache loaded %s entries", len(report_cache))
        except Exception as e:
            _log.warning("[DirectoryScanWorker.run] report load failed: %s", e)
        if self.isInterruptionRequested():
            _log.info("[DirectoryScanWorker.run] interrupted after report")
            return
        files: list = []
        if report_source_available and self._report_root:
            # 当 report.db 有记录时，用 DB 中 current_path（相对选中目录）拼出完整路径，扩展名用 original_path 的（如 .ARW）
            selected_dir = os.path.normpath(self._path)
            report_root = os.path.normpath(self._report_root)
            files, report_cache = _select_report_scope_files(
                selected_dir=selected_dir,
                report_root=report_root,
                full_report_cache=report_cache,
            )
            selected_rel = ""
            if _is_same_or_child_path(report_root, selected_dir):
                try:
                    selected_rel = os.path.relpath(selected_dir, report_root)
                except Exception:
                    selected_rel = ""
            selected_rel_norm = _norm_rel_path_for_match(selected_rel)
            _log.info(
                "[DirectoryScanWorker.run] selected scope files=%s selected_report_cache=%s selected_dir=%r selected_rel=%r report_root=%r",
                len(files), len(report_cache), selected_dir, selected_rel_norm or ".", report_root,
            )
            _log.info(
                "[DirectoryScanWorker.run] 使用 DB current_path 拼出完整路径构建文件列表 files=%s（跳过文件系统扫描）",
                len(files),
            )
            try:
                # In report mode the DB view is subtree-based even without UI filters,
                # so actual file supplementation must recurse under the selected dir.
                actual_files = _collect_image_files_impl(self._path, True)
                full_cache = full_report_cache or report_cache or {}
                existing = {_path_key(p) for p in files if p}
                file_index_by_stem = {Path(p).stem: i for i, p in enumerate(files) if p}
                supplemented = 0
                replaced = 0
                for actual_path in actual_files:
                    stem = Path(actual_path).stem
                    row = full_cache.get(stem)
                    if not isinstance(row, dict):
                        continue
                    actual_norm = os.path.normpath(actual_path)
                    actual_key = _path_key(actual_norm)
                    if actual_key in existing:
                        continue
                    existing_idx = file_index_by_stem.get(stem)
                    if existing_idx is not None:
                        old_path = files[existing_idx]
                        if old_path and not os.path.isfile(old_path):
                            old_key = _path_key(old_path)
                            files[existing_idx] = actual_norm
                            existing.discard(old_key)
                            existing.add(actual_key)
                            replaced += 1
                        continue
                    files.append(actual_norm)
                    existing.add(actual_key)
                    file_index_by_stem[stem] = len(files) - 1
                    supplemented += 1
                    report_cache[stem] = row
                _log.info(
                    "[DirectoryScanWorker.run] supplement actual files matched_by_stem=%s replaced_missing=%s total_files=%s selected_report_cache=%s",
                    supplemented,
                    replaced,
                    len(files),
                    len(report_cache),
                )
            except Exception as e:
                _log.warning("[DirectoryScanWorker.run] supplement actual files failed: %s", e)
            # Fallback: if DB-based approach produced no files, scan filesystem directly.
            # This handles empty/uninitialized report.db or mismatched paths.
            if not files:
                _log.warning(
                    "[DirectoryScanWorker.run] DB-based scan yielded 0 files, falling back to filesystem scan path=%r",
                    self._path,
                )
                try:
                    for root, dirs, names in os.walk(self._path, topdown=True):
                        if self.isInterruptionRequested():
                            return
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        for name in sorted(names, key=str.lower):
                            if Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                                files.append(os.path.join(root, name))
                except (PermissionError, OSError) as e:
                    _log.warning("[DirectoryScanWorker.run] fallback scan error: %s", e)
        else:
            try:
                if self._recursive:
                    for root, dirs, names in os.walk(self._path, topdown=True):
                        if self.isInterruptionRequested():
                            _log.info("[DirectoryScanWorker.run] interrupted during walk")
                            return
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        for name in sorted(names, key=str.lower):
                            if Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                                files.append(os.path.join(root, name))
                else:
                    for entry in sorted(os.scandir(self._path), key=lambda e: e.name.lower()):
                        if self.isInterruptionRequested():
                            return
                        if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS:
                            files.append(entry.path)
            except (PermissionError, OSError) as e:
                _log.warning("[DirectoryScanWorker.run] scan error: %s", e)
        _log.info("[DirectoryScanWorker.run] 目录扫描完成：列出 %s 个图像文件，report_cache %s 条，即将通知主线程加载 EXIF", len(files), len(report_cache))
        _log.info("[DirectoryScanWorker.run] scan done files=%s", len(files))
        if not self.isInterruptionRequested():
            self.scan_finished.emit(self._path, files, report_cache, full_report_cache)
            _log.info("[DirectoryScanWorker.run] emit scan_finished END")


# ── 后台元数据加载线程 ─────────────────────────────────────────────────────────

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



def _score_path_lookup_candidate(source_path: str, candidate_path: str, root_dir: str) -> tuple[int, int, int]:
    try:
        source_rel = os.path.relpath(source_path, root_dir)
    except Exception:
        source_rel = source_path
    try:
        cand_rel = os.path.relpath(candidate_path, root_dir)
    except Exception:
        cand_rel = candidate_path
    source_parts = [p.lower() for p in Path(os.path.dirname(source_rel)).parts if p not in ("", ".")]
    cand_parts = [p.lower() for p in Path(os.path.dirname(cand_rel)).parts if p not in ("", ".")]
    common_suffix = 0
    while common_suffix < min(len(source_parts), len(cand_parts)):
        if source_parts[-1 - common_suffix] != cand_parts[-1 - common_suffix]:
            break
        common_suffix += 1
    same_parent = 1 if source_parts and cand_parts and source_parts[-1] == cand_parts[-1] else 0
    return (common_suffix, same_parent, -len(cand_parts))


class PathLookupWorker(QThread):
    resolved = pyqtSignal(str, object)  # (source_path, actual_path_or_none)

    def __init__(self, source_path: str, root_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._source_path = os.path.normpath(source_path) if source_path else ""
        self._root_dir = os.path.normpath(root_dir) if root_dir else ""

    def run(self) -> None:
        source_path = self._source_path
        root_dir = self._root_dir
        actual_path = None
        _log.info("[PathLookupWorker.run] START source=%r root=%r", source_path, root_dir)
        if source_path and os.path.isfile(source_path):
            actual_path = source_path
        elif root_dir and os.path.isdir(root_dir) and source_path:
            target_name = Path(source_path).name.lower()
            best_score = None
            best_path = None
            scanned_dirs = 0
            candidates = 0
            try:
                for walk_root, dirs, names in os.walk(root_dir, topdown=True):
                    if self.isInterruptionRequested():
                        _log.info("[PathLookupWorker.run] interrupted source=%r", source_path)
                        return
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    scanned_dirs += 1
                    for name in names:
                        if name.lower() != target_name:
                            continue
                        candidate = os.path.normpath(os.path.join(walk_root, name))
                        score = _score_path_lookup_candidate(source_path, candidate, root_dir)
                        candidates += 1
                        if best_score is None or score > best_score:
                            best_score = score
                            best_path = candidate
                actual_path = best_path
                _log.info(
                    "[PathLookupWorker.run] END source=%r root=%r scanned_dirs=%s candidates=%s actual=%r",
                    source_path,
                    root_dir,
                    scanned_dirs,
                    candidates,
                    actual_path,
                )
            except Exception as e:
                _log.warning("[PathLookupWorker.run] failed source=%r root=%r: %s", source_path, root_dir, e)
        self.resolved.emit(source_path, actual_path)


class MetadataLoader(QThread):
    """
    批量读取图像文件的列表列元数据。
    若提供 report_cache 与 current_dir，则优先从 .superpicky/report.db 缓存取数；
    未命中再分块调用 read_batch_metadata（exiftool / XMP sidecar）。
    """

    all_metadata_ready = pyqtSignal(object)  # dict {norm_path: metadata_dict}
    progress_updated = pyqtSignal(int, int)  # (current_count, total_count)

    def __init__(
        self,
        paths: list,
        report_cache: dict | None = None,
        current_dir: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._report_cache = report_cache or {}
        self._current_dir = os.path.normpath(current_dir) if current_dir else None
        self._stop_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()

    @staticmethod
    def _report_row_needs_file_fallback(row: dict) -> bool:
        """
        某些 report.db 仅保存路径/评分，缺少标题、锐度、对焦等展示字段。
        这类行需要再走一次 read_batch_metadata（文件 + sidecar）补齐。
        """
        if not isinstance(row, dict):
            return True

        cp = _get_report_current_path_raw(row)
        if isinstance(cp, str) and cp.strip().lower().endswith(".xmp"):
            # report 里 current_path 指向 sidecar 的场景，通常依赖文件/XMP补全展示字段
            return True

        def _has_value(v) -> bool:
            if v is None:
                return False
            if isinstance(v, str):
                return v.strip() != ""
            return True

        rich_keys = (
            "title",
            "bird_species_cn",
            "caption",
            "city",
            "state_province",
            "country",
            "focus_status",
            "adj_sharpness",
            "adj_topiq",
            "head_sharp",
            "left_eye",
            "right_eye",
        )
        for k in rich_keys:
            if _has_value(row.get(k)):
                return False

        # rating>0 / is_flying==1 也视为已有有效展示信息
        try:
            if int(float(str(row.get("rating") or 0))) > 0:
                return False
        except Exception:
            pass
        try:
            if int(float(str(row.get("is_flying") or 0))) == 1:
                return False
        except Exception:
            pass
        return True

    def run(self) -> None:
        if not self._paths or self._stop_flag:
            _log.debug("[MetadataLoader.run] no paths or stopped")
            return
        _log.info("[MetadataLoader.run] START paths=%s", len(self._paths))
        try:
            paths = self._paths
            total = len(paths)
            result: dict = {}
            uncached: list = []
            fallback_paths: list = []
            # 先按 path 分流：命中 report 的立即填 result 并注入 writer 缓存，未命中进 uncached
            for path in paths:
                if self._stop_flag or self.isInterruptionRequested():
                    _log.info("[MetadataLoader.run] interrupted in report loop")
                    return
                norm = os.path.normpath(path)
                stem = Path(path).stem  # 只按文件名 stem 匹配，不包含路径
                if stem in self._report_cache:
                    row = self._report_cache[stem]
                    flat = report_row_to_exiftool_style(row, path)
                    needs_fallback = self._report_row_needs_file_fallback(row)
                    # 仅当不需要文件回退时才注入缓存，避免 read_batch 直接命中“空DB记录”
                    if not needs_fallback:
                        inject_metadata_cache(path, flat)
                    meta = self._parse_rec(flat)
                    species_cn = str(row.get("bird_species_cn") or "").strip()
                    if species_cn:
                        meta["bird_species_cn"] = species_cn
                    result[norm] = meta
                    if needs_fallback:
                        fallback_paths.append(path)
                    _log.info(
                        "[MetadataLoader.run] path=%r 来源=DB stem=%r 解析 title=%r rating=%s pick=%s",
                        path, stem, meta.get("title", ""), meta.get("rating"), meta.get("pick"),
                    )
                else:
                    uncached.append(path)
            processed = total - len(uncached)
            _log.info(
                "[MetadataLoader.run] report done result=%s uncached=%s fallback=%s",
                len(result), len(uncached), len(fallback_paths)
            )
            if processed > 0:
                self.progress_updated.emit(processed, total)
            # 命中 DB 但信息稀疏的路径，始终回退读文件/XMP；
            # 未命中 DB 的路径，仅在 EXIF_ONLY_FROM_REPORT_DB=False 时回退。
            # 注意：当目标源图不存在且 report.current_path 指向 .xmp 时，
            # 读取查询路径应改为 sidecar(.xmp)，但结果要映射回目标图片路径。
            read_query_by_key: dict = {}            # normcase(query_norm) -> query_norm
            read_targets_by_query_key: dict = {}    # normcase(query_norm) -> set(target_norm)
            fallback_target_norms: set = set()

            def _plan_read(query_path: str, target_norm: str) -> None:
                qn = os.path.normpath(query_path)
                qk = os.path.normcase(qn)
                read_query_by_key.setdefault(qk, qn)
                targets = read_targets_by_query_key.setdefault(qk, set())
                targets.add(target_norm)

            if fallback_paths:
                for target_path in fallback_paths:
                    target_norm = os.path.normpath(target_path)
                    fallback_target_norms.add(target_norm)
                    query_path = target_path

                    stem = Path(target_path).stem
                    row = self._report_cache.get(stem)
                    if isinstance(row, dict):
                        cp_text = _get_report_current_path_raw(row)
                        if cp_text and self._current_dir:
                            if os.path.isabs(cp_text):
                                cp_abs = os.path.normpath(cp_text)
                            else:
                                cp_abs = os.path.normpath(os.path.join(self._current_dir, cp_text))
                            if (
                                cp_abs.lower().endswith(".xmp")
                                and os.path.isfile(cp_abs)
                            ):
                                query_path = cp_abs
                                _log.debug(
                                    "[MetadataLoader.run] fallback prefer report current_path xmp: target=%r query=%r",
                                    target_path,
                                    query_path,
                                )

                    _plan_read(query_path, target_norm)

            if not EXIF_ONLY_FROM_REPORT_DB and uncached:
                for p in uncached:
                    norm = os.path.normpath(p)
                    _plan_read(p, norm)

            dedup_read = list(read_query_by_key.values())
            if dedup_read:
                _log.info(
                    "[MetadataLoader.run] read_batch planned total=%s fallback=%s uncached=%s exif_only=%s",
                    len(dedup_read), len(fallback_paths), len(uncached), EXIF_ONLY_FROM_REPORT_DB
                )
                chunk_size = max(1, _METADATA_CHUNK_SIZE)
                for i in range(0, len(dedup_read), chunk_size):
                    if self._stop_flag or self.isInterruptionRequested():
                        _log.info("[MetadataLoader.run] interrupted in read_batch loop")
                        return
                    chunk = dedup_read[i : i + chunk_size]
                    _log.debug("[MetadataLoader.run] read_batch chunk %s-%s", i, i + len(chunk))
                    chunk_raw = read_batch_metadata(chunk)
                    if self._stop_flag or self.isInterruptionRequested():
                        return
                    for norm, rec in chunk_raw.items():
                        if self._stop_flag or self.isInterruptionRequested():
                            return
                        norm_key = os.path.normpath(norm)
                        query_key = os.path.normcase(norm_key)
                        target_norms = read_targets_by_query_key.get(query_key)
                        if not target_norms:
                            target_norms = {norm_key}
                        meta = self._parse_rec(rec)
                        for target_norm in target_norms:
                            result[target_norm] = meta
                            src = "read_batch_fallback" if target_norm in fallback_target_norms else "read_batch"
                            _log.info(
                                "[MetadataLoader.run] query=%r target=%r 来源=%s 解析 title=%r rating=%s pick=%s",
                                norm_key, target_norm, src, meta.get("title", ""), meta.get("rating"), meta.get("pick"),
                            )
                    processed += len(chunk)
                    self.progress_updated.emit(min(processed, total), total)
        except Exception as e:
            _log.warning("[MetadataLoader.run] exception: %s", e)
            result = {}
        if not (self._stop_flag or self.isInterruptionRequested()):
            _log.info("[MetadataLoader.run] emit all_metadata_ready result=%s", len(result))
            self.all_metadata_ready.emit(result)
        _log.info("[MetadataLoader.run] END")

    def _parse_rec(self, rec: dict) -> dict:
        # 标题、对焦状态等支持 XMP sidecar（由 read_batch_metadata 合并），勿删以下键名
        # 标题：XMP dc:title（sidecar 多为小写 tag）、IFD0/XPTitle、IPTC
        title = (
            rec.get("XMP-dc:Title") or rec.get("XMP-dc:title")
            or rec.get("IFD0:XPTitle") or rec.get("IPTC:ObjectName") or ""
        )
        color = rec.get("XMP-xmp:Label") or ""
        rating_raw = rec.get("XMP-xmp:Rating")
        try:
            rating_num = int(float(str(rating_raw or 0)))
        except Exception:
            rating_num = 0
        rating = max(0, min(5, rating_num))
        # Pick/Reject 旗标（1=精选🏆, 0=无旗标, -1=排除🚫）
        # 实际 XMP 多为 <xmpDM:pick>1</xmpDM:pick>（Dynamic Media 命名空间），其次 xmp:Pick 等
        pick_raw = (
            rec.get("XMP-xmpDM:pick") or rec.get("XMP-xmpDM:Pick")
            or rec.get("XMP-xmp:Pick") or rec.get("XMP-xmp:PickLabel")
            or rec.get("XMP-1.0:Pick") or rec.get("XMP-1.0:PickLabel")
            or rec.get("XMP-lr:Pick") or rec.get("XMP-lr:PickLabel")
            or rec.get("XMP:Pick") or rec.get("XMP:PickLabel")
            or ""
        )
        try:
            s = str(pick_raw).strip().lower()
            if s in ("true", "1", "yes"):
                pick = 1
            elif s in ("false", "0", "no", ""):
                pick = 0
            elif s in ("-1", "reject"):
                pick = -1
            else:
                pick = max(-1, min(1, int(float(s))))
        except Exception:
            pick = 0
        if pick == 0 and rating_num < 0:
            pick = -1

        # 城市 = 锐度（XMP:City 数值），省/直辖市/自治区 = 美学评分（XMP:State 数值），国家/地区 = 对焦状态（XMP:Country）
        city_raw = (
            rec.get("XMP:City") or rec.get("XMP-photoshop:City")
            or rec.get("IPTC:City") or ""
        )
        state_raw = (
            rec.get("XMP:State") or rec.get("XMP-photoshop:State")
            or rec.get("IPTC:Province-State") or ""
        )
        country_raw = (
            rec.get("XMP:Country")
            or rec.get("XMP-photoshop:Country")
            or rec.get("XMP-photoshop:Country-PrimaryLocationName")
            or rec.get("IPTC:Country-PrimaryLocationName") or ""
        )

        city = _format_optional_number(city_raw, "%06.2f")    # 锐度
        state = _format_optional_number(state_raw, "%05.2f") # 美学
        country = _focus_status_to_display(country_raw)      # 对焦状态 → 精焦/合焦/偏移/失焦

        return {
            "title":   str(title).strip(),
            "color":   str(color).strip(),
            "rating":  rating,
            "pick":    pick,
            "city":    city,
            "state":   state,
            "country": country,
        }


# ── 图像文件列表面板 ───────────────────────────────────────────────────────────

class FileListPanel(QWidget):
    """
    图像文件列表面板。

    - 列表模式：含「文件名/标题/颜色/星级/城市/省区/国家」七列，可点击列头排序。
    - 缩略图模式：图标网格，缩略图左下显示颜色标签、右下显示星级，
      工具栏滑块可选 128/256/512/1024 px 四档大小。
    """

    # 子类可重载为 False 以不创建过滤栏（filter_bar）
    create_filter_bar = True

    file_selected = pyqtSignal(str)
    file_fast_preview_requested = pyqtSignal(str)
    _MODE_LIST  = 0
    _MODE_THUMB = 1

    def __init__(self, parent=None, *, create_filter_bar: bool | None = None) -> None:
        super().__init__(parent)
        self._all_files: list = []
        self._filtered_files: list = []
        self._current_dir = ""
        self._report_root_dir: str | None = None  # 当前使用的 report 根目录（含 .superpicky 的目录）
        self._report_full_root_dir: str | None = None
        self._report_full_cache: dict | None = None
        self._view_mode = self._MODE_LIST
        self._thumb_size = 128
        self._thumbnail_loader: ThumbnailLoader | None = None
        self._metadata_loader:  MetadataLoader  | None = None
        self._directory_scan_worker: DirectoryScanWorker | None = None
        self._file_table_model = FileTableModel(self)
        self._file_table_proxy = FileTableSortProxyModel(self)
        self._file_table_proxy.setSourceModel(self._file_table_model)
        self._thumb_list_model = ThumbnailListModel(self)
        self._meta_cache:    dict = {}   # norm_path → metadata dict
        self._report_cache:  dict = {}   # stem → report row (当前目录/子树筛出的 report 子集)
        self._report_row_by_path: dict = {}
        self._pending_loaders: list = []
        self._path_lookup_pending: set[str] = set()
        self._path_lookup_workers: list[PathLookupWorker] = []
        self._meta_apply_timer: QTimer | None = None
        self._meta_apply_items: list = []
        self._meta_apply_index: int = 0
        self._meta_apply_total: int = 0
        self._meta_apply_started_at: float = 0.0
        self._meta_apply_loop_started_at: float = 0.0
        self._meta_apply_tree_hits: int = 0
        self._meta_apply_list_hits: int = 0
        self._meta_apply_needs_filter: bool = False
        self._tree_header_fast_mode: bool = False
        self._tree_last_sort_column: int = _TREE_COL_NAME
        self._tree_last_sort_order = _AscendingOrder
        self._tree_view_dirty: bool = False
        self._copied_species_payload: dict | None = None
        self._pending_selection_paths: list | None = None  # 接收到的文件列表，目录加载完成后等同多选
        self._pending_selection_current_path: str = ""
        self._thumb_memory_cache = ThumbnailMemoryCache()
        self._pending_selection_current_path: str = ""
        self._selected_display_path: str = ""
        self._thumb_loader_workers = _thumbnail_loader_worker_count()
        self._thumb_viewport_timer: QTimer | None = None
        self._thumb_visible_signature: tuple | None = None
        self._thumb_visible_range: ThumbViewportRange | None = None
        self._thumb_model_dirty: bool = False
        self._thumb_model_populate_timer: QTimer | None = None
        self._thumb_model_pending_paths: list[str] = []
        self._thumb_model_pending_index: int = 0
        self._thumb_model_populate_started_at: float = 0.0
        self._thumb_request_token: int = 0
        self._thumb_pending_batch: dict[str, "QImage"] = {}
        self._thumb_apply_timer: QTimer | None = None
        self._deferred_file_selected_timer: QTimer | None = None
        self._deferred_file_selected_path: str = ""
        self._selection_key_nav_auto_repeat: bool = False
        self._key_navigation_fps: int = get_key_navigation_fps()
        self._key_navigation_last_step_at: float = 0.0
        self._combo_key_navigation_fps: QComboBox | None = None
        self._persistent_thumb_cache_worker: PersistentThumbCacheWorker | None = None
        self._persistent_thumb_cache_timer: QTimer | None = None
        self._persistent_thumb_cache_pending_paths: list[str] = []
        self._persistent_thumb_cache_base_dir: str = ""
        self._persistent_thumb_cache_generated: int = 0
        self._persistent_thumb_cache_skipped: int = 0
        self._persistent_thumb_cache_failed: int = 0
        self._persistent_thumb_cache_total: int = 0
        self._persistent_thumb_cache_done: int = 0
        self._persistent_thumb_cache_current_path: str = ""
        self._thumb_profile_enabled: bool = _THUMB_PROFILE_ENABLED
        self._thumb_profile_last_report_at: float = 0.0
        self._thumb_profile_window_started_at: float = _time.perf_counter()
        self._thumb_profile_ready_received_at: dict[str, float] = {}
        self._background_shutdown_started: bool = False
        self._thumb_profile_stats: dict[str, float] = {
            "schedule_calls": 0.0,
            "viewport_updates": 0.0,
            "visible_items_total": 0.0,
            "missing_visible_total": 0.0,
            "prefetch_total": 0.0,
            "cache_fill_total": 0.0,
            "evicted_total": 0.0,
            "loader_starts": 0.0,
            "loader_reprioritize": 0.0,
            "ready_signals": 0.0,
            "stale_ready": 0.0,
            "pending_peak": 0.0,
            "flush_calls": 0.0,
            "flush_pending_total": 0.0,
            "flush_applied": 0.0,
            "flush_skipped_offscreen": 0.0,
            "flush_skipped_invalid": 0.0,
            "ready_wait_total_s": 0.0,
            "ready_wait_count": 0.0,
            "ready_wait_max_s": 0.0,
            "flush_total_s": 0.0,
            "flush_max_s": 0.0,
            "last_visible_start": -1.0,
            "last_visible_end": -1.0,
            "last_visible_count": 0.0,
            "last_missing_count": 0.0,
            "last_prefetch_count": 0.0,
        }
        # 过滤状态
        self._filter_pick: bool = False   # 只显示精选(🏆)
        self._filter_min_rating: int = 0  # 最低星级(0=不限)
        self._filter_focus_status: str = ""
        self._star_btns: list = []
        self._focus_filter_btns: dict[str, QToolButton] = {}
        if create_filter_bar is None:
            create_filter_bar = getattr(type(self), "create_filter_bar", True)
        self._create_filter_bar = bool(create_filter_bar)
        self._init_ui()
        app = QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.connect(self._shutdown_background_work)
            except Exception:
                pass

    # ── UI 初始化 ──────────────────────────────────────────────────────────────
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        # ── 视图工具栏（视图切换 + 缩略图大小）──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(3)

        self._btn_list = QToolButton()
        self._btn_list.setText("≡")
        self._btn_list.setToolTip("列表视图")
        self._btn_list.setCheckable(True)
        self._btn_list.setChecked(True)
        self._btn_list.setFixedWidth(28)
        self._btn_list.clicked.connect(lambda: self._set_view_mode(self._MODE_LIST))

        self._btn_thumb = QToolButton()
        self._btn_thumb.setText("⊞")
        self._btn_thumb.setToolTip("缩略图视图")
        self._btn_thumb.setCheckable(True)
        self._btn_thumb.setFixedWidth(28)
        self._btn_thumb.clicked.connect(lambda: self._set_view_mode(self._MODE_THUMB))

        self._size_slider = QSlider(_Horizontal)
        self._size_slider.setRange(0, len(_THUMB_SIZE_STEPS) - 1)
        self._size_slider.setValue(0)
        self._size_slider.setFixedWidth(90)
        self._size_slider.setTickPosition(_TicksBelow)
        self._size_slider.setTickInterval(1)
        self._size_slider.setPageStep(1)
        self._size_slider.valueChanged.connect(self._on_size_slider_changed)

        self._size_label = QLabel(f"{_THUMB_SIZE_STEPS[0]}px")
        self._size_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self._size_label.setFixedWidth(42)

        toolbar.addWidget(self._btn_list)
        toolbar.addWidget(self._btn_thumb)
        toolbar.addSpacing(4)
        toolbar.addWidget(QLabel("大小:"))
        toolbar.addWidget(self._size_slider)
        toolbar.addWidget(self._size_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── 过滤栏（文件名 + 精选 + 星级）──
        if self._create_filter_bar:
            filter_bar = QHBoxLayout()
            filter_bar.setSpacing(3)

            self._filter_edit = QLineEdit()
            self._filter_edit.setPlaceholderText("过滤文件名…")
            self._filter_edit.setClearButtonEnabled(True)
            self._filter_edit.setStyleSheet(
                "QLineEdit { padding: 2px 4px; font-size: 12px; }"
            )
            self._filter_edit.textChanged.connect(lambda _: self._apply_filter())
            filter_bar.addWidget(self._filter_edit, stretch=1)

            # 精选按钮
            self._btn_filter_pick = QToolButton()
            self._btn_filter_pick.setText("🏆")
            self._btn_filter_pick.setToolTip("只显示精选（Pick=1）")
            self._btn_filter_pick.setCheckable(True)
            self._btn_filter_pick.setAutoRaise(False)
            self._btn_filter_pick.setStyleSheet(
                _filter_badge_stylesheet(
                    COLORS["star_gold"],
                    min_width=34,
                    checked_fg="#111111",
                )
            )
            self._btn_filter_pick.clicked.connect(self._on_pick_filter_toggled)
            filter_bar.addWidget(self._btn_filter_pick)

            # 星级按钮（1～5，单选，点击已激活按钮则取消）
            star_widths = [22, 28, 34, 40, 46]
            for n in range(1, 6):
                btn = QToolButton()
                btn.setText("★" * n)
                btn.setToolTip(f"只显示 ≥{n} 星")
                btn.setCheckable(True)
                btn.setAutoRaise(False)
                btn.setStyleSheet(
                    _filter_badge_stylesheet(
                        _STAR_SILVER_COLOR,
                        min_width=star_widths[n - 1],
                        checked_fg="#111111",
                    )
                )
                btn.clicked.connect(
                    lambda checked, rating=n: self._on_rating_filter_changed(rating)
                )
                self._star_btns.append(btn)
                filter_bar.addWidget(btn)

            for focus_status in _FOCUS_FILTER_OPTIONS:
                btn = QToolButton()
                btn.setText(focus_status)
                btn.setToolTip(f"只显示{focus_status}文件")
                btn.setCheckable(True)
                btn.setAutoRaise(False)
                btn.setStyleSheet(_focus_filter_button_stylesheet(focus_status))
                btn.clicked.connect(
                    lambda checked, status=focus_status: self._on_focus_filter_changed(status)
                )
                self._focus_filter_btns[focus_status] = btn
                filter_bar.addWidget(btn)

            filter_bar.addSpacing(8)
            filter_bar.addWidget(QLabel("方向键:"))
            self._combo_key_navigation_fps = QComboBox()
            self._combo_key_navigation_fps.setToolTip("按住方向键连续浏览时，按选定 FPS 控制移动速率。")
            for fps in KEY_NAVIGATION_FPS_OPTIONS:
                self._combo_key_navigation_fps.addItem(f"{fps} FPS", fps)
            self._sync_key_navigation_fps_combo()
            self._combo_key_navigation_fps.currentIndexChanged.connect(self._on_key_navigation_fps_changed)
            filter_bar.addWidget(self._combo_key_navigation_fps)

            layout.addLayout(filter_bar)
        else:
            self._filter_edit = None
            self._btn_filter_pick = None
            self._combo_key_navigation_fps = None

        # 视图堆叠
        self._stack = QStackedWidget()

        # ── 列表模式：多列 QTreeWidget ──
        self._tree_widget = FileTableView()
        self._tree_widget.setModel(self._file_table_proxy)
        
        # @Agents: 这个列名不要修改
        # 城市 = 锐度值（越高越清晰）
        # 省/直辖市/自治区 = 美学评分（越高越好看）
        # 国家/地区 = 对焦状态（精焦/合焦/偏移/失焦）
        # 🏳️ 白旗 = Pick 精选旗标（双维度都出色）
        # 🟢 绿色标签 = 飞鸟
        # 🔴 红色标签 = 精焦（对焦点在鸟头）
        self._tree_widget.setHeaderLabels([
            "#", "文件名", "标题", "颜色", "星级", "锐度值", "美学评分", "对焦状态"
        ])
        self._tree_widget.setSortingEnabled(True)
        self._tree_widget.setRootIsDecorated(False)
        self._tree_widget.setUniformRowHeights(True)
        self._tree_widget.setAlternatingRowColors(True)
        self._tree_widget.setSelectionMode(_ExtendedSelection)  # Shift/Command 多选
        self._tree_widget.setItemsExpandable(False)
        self._tree_widget.setSelectionBehavior(_SelectRows)
        self._tree_widget.setAllColumnsShowFocus(True)
        self._tree_widget.setStyleSheet("QTreeView { font-size: 12px; }")
        self._tree_widget.clicked.connect(self._on_tree_item_clicked)
        hdr = self._tree_widget.header()
        hdr.sortIndicatorChanged.connect(self._on_tree_sort_indicator_changed)
        self._tree_widget.selectionModel().currentChanged.connect(self._on_tree_current_item_changed)
        self._tree_widget.selectionModel().selectionChanged.connect(self._on_view_selection_changed)
        for col in range(8):
            hdr.setSectionResizeMode(col, _ResizeInteractive)
        self._tree_widget.setColumnWidth(_TREE_COL_SEQ, 44)
        self._tree_widget.setColumnWidth(_TREE_COL_NAME, 190)
        self._tree_widget.setColumnWidth(_TREE_COL_TITLE, 150)
        self._tree_widget.setColumnWidth(_TREE_COL_COLOR, 86)
        self._tree_widget.setColumnWidth(_TREE_COL_STAR, 72)
        self._tree_widget.setColumnWidth(_TREE_COL_SHARP, 96)
        self._tree_widget.setColumnWidth(_TREE_COL_AESTHETIC, 96)
        self._tree_widget.setColumnWidth(_TREE_COL_FOCUS, 108)
        self._tree_widget.sortByColumn(_TREE_COL_NAME, _AscendingOrder)
        self._tree_widget.setContextMenuPolicy(_CustomContextMenu)
        self._tree_widget.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree_widget.installEventFilter(self)
        self._tree_widget.viewport().installEventFilter(self)
        self._stack.addWidget(self._tree_widget)

        # ── 缩略图模式：QListWidget ──
        self._list_widget = QListView()
        self._list_widget.setViewMode(_ViewModeIcon)
        self._list_widget.setModel(self._thumb_list_model)
        self._list_widget.setItemDelegate(ThumbnailItemDelegate(self._list_widget))
        self._list_widget.setSelectionMode(_ExtendedSelection)  # Shift/Command 多选
        self._list_widget.setResizeMode(
            QListView.ResizeMode.Adjust if hasattr(QListView, "ResizeMode")
            else QListView.Adjust  # type: ignore[attr-defined]
        )
        try:
            self._list_widget.setLayoutMode(
                QListView.LayoutMode.Batched if hasattr(QListView, "LayoutMode")
                else QListView.Batched  # type: ignore[attr-defined]
            )
        except Exception:
            pass
        try:
            self._list_widget.setBatchSize(48)
        except Exception:
            pass
        try:
            self._list_widget.setMovement(
                QListView.Movement.Static if hasattr(QListView, "Movement")
                else QListView.Static  # type: ignore[attr-defined]
            )
        except Exception:
            pass
        self._list_widget.setUniformItemSizes(True)
        self._list_widget.setVerticalScrollMode(_ScrollPerPixel)
        self._list_widget.setHorizontalScrollMode(_ScrollPerPixel)
        self._list_widget.setWrapping(True)
        self._list_widget.setStyleSheet("QListView { font-size: 11px; }")
        self._list_widget.clicked.connect(self._on_list_item_clicked)
        self._list_widget.selectionModel().selectionChanged.connect(self._on_view_selection_changed)
        self._list_widget.setContextMenuPolicy(_CustomContextMenu)
        self._list_widget.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list_widget.installEventFilter(self)
        self._list_widget.viewport().installEventFilter(self)
        self._list_widget.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnail_update)
        self._list_widget.horizontalScrollBar().valueChanged.connect(self._schedule_visible_thumbnail_update)
        self._stack.addWidget(self._list_widget)

        layout.addWidget(self._stack, stretch=1)

        # EXIF 读取进度条（由 progress_updated 信号在主线程更新，多线程安全）
        self._meta_progress = QProgressBar()
        self._meta_progress.setMinimum(0)
        self._meta_progress.setMaximum(100)
        self._meta_progress.setValue(0)
        self._meta_progress.setFixedHeight(20)
        self._meta_progress.setTextVisible(True)
        self._meta_progress.setFormat("%v/%m")
        self._meta_progress.setStyleSheet(
            "QProgressBar { background: #333; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #3a7bd5; border-radius: 3px; }"
        )
        self._meta_progress.hide()

        self._persistent_thumb_progress = QProgressBar()
        self._persistent_thumb_progress.setMinimum(0)
        self._persistent_thumb_progress.setMaximum(100)
        self._persistent_thumb_progress.setValue(0)
        self._persistent_thumb_progress.setFixedHeight(20)
        self._persistent_thumb_progress.setMinimumWidth(200)
        self._persistent_thumb_progress.setTextVisible(True)
        self._persistent_thumb_progress.setFormat("小缩略图 %v/%m")
        self._persistent_thumb_progress.setStyleSheet(
            "QProgressBar { background: #333; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #16a085; border-radius: 3px; }"
        )
        self._persistent_thumb_progress.hide()

        self._selection_status_label = QLabel("共 0 张 | 当前未选中")
        self._selection_status_label.setStyleSheet("color: #aaa; font-size: 12px; padding: 0 4px;")
        self._selection_status_label.setMinimumWidth(220)

        status_bar = QHBoxLayout()
        status_bar.setSpacing(6)
        status_bar.addWidget(self._selection_status_label, 0)
        status_bar.addWidget(self._meta_progress, 1)
        status_bar.addWidget(self._persistent_thumb_progress, 1)
        layout.addLayout(status_bar)

        self._stack.setCurrentIndex(0)
        self._update_size_controls()
        self._update_selection_status()

        # Cmd+C / Ctrl+C 复制选中文件到剪贴板
        _copy_key = getattr(QKeySequence.StandardKey, "Copy", None) or getattr(QKeySequence, "Copy", QKeySequence("Ctrl+C"))
        copy_shortcut = QShortcut(_copy_key, self)
        try:
            copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        except Exception:
            pass
        copy_shortcut.activated.connect(self._copy_current_selection_to_clipboard)

    def _copy_current_selection_to_clipboard(self) -> None:
        """将当前视图（列表/缩略图）中选中的文件路径复制到剪贴板。"""
        w = self._stack.currentWidget()
        if w is self._tree_widget:
            paths = self._tree_selected_paths()
        elif w is self._list_widget:
            paths = self._thumb_selected_paths()
        else:
            paths = []
        self._copy_paths_to_clipboard(paths)

    def _on_view_selection_changed(self, *_args) -> None:
        self._update_selection_status()

    def _active_view_selected_paths(self) -> list[str]:
        if self._view_mode == self._MODE_THUMB:
            return self._thumb_selected_paths()
        return self._tree_selected_paths()

    def _active_view_current_path(self) -> str:
        if self._view_mode == self._MODE_THUMB:
            index = self._list_widget.currentIndex()
            path = self._thumb_path_from_index(index)
        else:
            index = self._tree_widget.currentIndex()
            path = self._tree_path_from_index(index)
        if path:
            return os.path.normpath(path)
        if self._selected_display_path:
            return os.path.normpath(self._selected_display_path)
        return ""

    def _update_selection_status(self) -> None:
        label = getattr(self, "_selection_status_label", None)
        if label is None:
            return
        total = len(self._filtered_files)
        if self._view_mode == self._MODE_THUMB:
            selected_count = len(self._thumb_selected_indexes())
            current_index = self._list_widget.currentIndex()
            current_row = current_index.row() + 1 if current_index.isValid() else None
            if current_row is None and self._selected_display_path:
                fallback_index = self._thumb_index_for_path(self._selected_display_path)
                if fallback_index.isValid():
                    current_row = fallback_index.row() + 1
        else:
            selected_count = len(self._tree_selected_indexes())
            current_index = self._tree_widget.currentIndex()
            current_row = current_index.row() + 1 if current_index.isValid() else None
            if current_row is None and self._selected_display_path:
                fallback_index = self._tree_index_for_path(self._selected_display_path)
                if fallback_index.isValid():
                    current_row = fallback_index.row() + 1
        if current_row is None and self._selected_display_path:
            try:
                current_row = self._filtered_files.index(os.path.normpath(self._selected_display_path)) + 1
            except ValueError:
                current_row = None
        parts = [f"共 {total} 张"]
        if selected_count > 1:
            parts.append(f"已选 {selected_count} 张")
        if current_row is not None and total > 0:
            parts.append(f"当前 {current_row}/{total}")
        else:
            parts.append("当前未选中")
        label.setText(" | ".join(parts))

    # ── 数据加载 ────────────────────────────────────────────────────────────────
    def _collect_image_files(self, dir_path: str, recursive: bool) -> list:
        """收集目录下支持的图像文件路径，委托给模块级函数（可被后台线程调用）。"""
        return _collect_image_files_impl(dir_path, recursive)

    def _has_any_filter(self) -> bool:
        """是否有任意过滤条件开启（文本 / 精选 / 星级）。"""
        if not self._create_filter_bar:
            return False
        return (
            bool(self._filter_edit.text().strip()) or
            self._filter_pick or
            self._filter_min_rating > 0 or
            bool(self._filter_focus_status)
        )

    def load_directory(self, path: str, force_reload: bool = False) -> None:
        """
        扫描目录，加载支持的图像文件。扫描与 report 加载在后台线程执行，避免阻塞 UI。
        当任意过滤条件开启（文本 / 🏆精选 / 星级）时，递归遍历该目录及所有子目录（不进入 . 开头目录）；
        否则仅当前目录。force_reload=True 时忽略「当前目录未变」的短路，用于切换过滤后刷新列表。
        """
        _log.info("[load_directory] 选中目录，将扫描并列出图像文件、随后查询 EXIF path=%r force_reload=%s", path, force_reload)
        _log.info("[load_directory] START path=%r force_reload=%s", path, force_reload)
        if not force_reload and path == self._current_dir:
            _log.info("[load_directory] SKIP same dir")
            return
        self._current_dir = path
        # 选择目录后，向上最多查找 4 层最近的 report 根目录；子目录共用同一个 report.db
        new_report_root_dir = find_report_root(path, max_levels=4)
        if new_report_root_dir != self._report_root_dir:
            _log.info(
                "[load_directory] report_root_dir changed old=%r new=%r",
                self._report_root_dir,
                new_report_root_dir,
            )
        self._report_root_dir = new_report_root_dir
        if self._report_root_dir:
            if self._report_full_root_dir != self._report_root_dir:
                _log.info(
                    "[load_directory] reset in-memory full report cache old_root=%r new_root=%r",
                    self._report_full_root_dir,
                    self._report_root_dir,
                )
                self._report_full_root_dir = self._report_root_dir
                self._report_full_cache = None
        elif self._report_full_root_dir is not None or self._report_full_cache is not None:
            _log.info(
                "[load_directory] clear in-memory full report cache old_root=%r",
                self._report_full_root_dir,
            )
            self._report_full_root_dir = None
            self._report_full_cache = None
        _log.info(
            "[load_directory] report_root_dir=%r has_cached_full_report=%s cached_entries=%s",
            self._report_root_dir,
            self._report_full_cache is not None,
            len(self._report_full_cache or {}),
        )
        _log.info("[load_directory] _stop_all_loaders")
        self._stop_all_loaders()
        _log.info("[load_directory] _stop_directory_scan_worker")
        self._stop_directory_scan_worker()
        # Folder-level FIFO eviction: release QImages cached for any directory
        # other than the one we are about to enter.  This ensures that browsing
        # through many large folders cannot accumulate an unbounded number of
        # cached thumbnails in RAM — only the current folder's images are kept.
        # Old-folder thumbnails remain on the disk cache and reload quickly if
        # the user navigates back.
        _evicted = self._thumb_memory_cache.evict_other_dirs(_path_key(path))
        if _evicted:
            _log.info("[load_directory] evicted %.1f MB from other dirs", _evicted / (1024 * 1024))
        self._meta_cache.clear()
        self._report_cache = {}
        self._report_row_by_path = {}
        self._selected_display_path = ""
        self._all_files = []
        _log.info("[load_directory] _rebuild_views (empty)")
        self._rebuild_views()
        recursive = self._has_any_filter()
        _log.info(
            "[load_directory] starting DirectoryScanWorker recursive=%s report_root_dir=%r has_cached_full_report=%s",
            recursive,
            self._report_root_dir,
            self._report_full_cache is not None,
        )
        self._directory_scan_worker = DirectoryScanWorker(
            path,
            recursive,
            self._report_root_dir,
            self._report_full_cache if self._report_root_dir and self._report_full_root_dir == self._report_root_dir else None,
            self,
        )
        self._directory_scan_worker.scan_finished.connect(self._on_directory_scan_finished)
        self._directory_scan_worker.start()
        _log.info("[load_directory] END worker.started")

    def _stop_directory_scan_worker(self) -> None:
        if self._directory_scan_worker is None:
            _log.debug("[_stop_directory_scan_worker] no worker")
            return
        _log.info("[_stop_directory_scan_worker] disconnecting and interrupting")
        try:
            self._directory_scan_worker.scan_finished.disconnect(self._on_directory_scan_finished)
        except Exception:
            pass
        self._directory_scan_worker.requestInterruption()
        self._directory_scan_worker = None

    def _on_directory_scan_finished(self, path: str, files: list, report_cache: dict, full_report_cache) -> None:
        _log.info("[_on_directory_scan_finished] 收到目录扫描结果 path=%r files=%s report_entries=%s，开始列出文件并查询 EXIF", path, len(files), len(report_cache))
        _log.info("[_on_directory_scan_finished] path=%r _current_dir=%r files=%s report_entries=%s", path, self._current_dir, len(files), len(report_cache))
        if path != self._current_dir:
            _log.info("[_on_directory_scan_finished] IGNORE stale path")
            return
        if self._report_root_dir:
            self._report_full_root_dir = self._report_root_dir
            if full_report_cache is not None:
                self._report_full_cache = full_report_cache
            _log.info(
                "[_on_directory_scan_finished] full report cache root=%r entries=%s",
                self._report_full_root_dir,
                len(self._report_full_cache or {}),
            )
        if _DEBUG_FILE_LIST_LIMIT > 0 and len(files) > _DEBUG_FILE_LIST_LIMIT:
            selected_files = files
            if _DEBUG_FILE_LIST_MATCH:
                matched = [p for p in files if _DEBUG_FILE_LIST_MATCH in str(p).lower()]
                if matched:
                    matched_set = set(matched)
                    selected_files = matched + [p for p in files if p not in matched_set]
                    _log.warning(
                        "[DEBUG] SuperViewer_DEBUG_FILE_LIST_MATCH=%r matched=%s (prioritized)",
                        _DEBUG_FILE_LIST_MATCH,
                        len(matched),
                    )
                else:
                    _log.warning(
                        "[DEBUG] SuperViewer_DEBUG_FILE_LIST_MATCH=%r no match in current files",
                        _DEBUG_FILE_LIST_MATCH,
                    )
            limited_files = selected_files[:_DEBUG_FILE_LIST_LIMIT]
            keep_stems = {Path(p).stem for p in limited_files}
            report_cache = {k: v for k, v in report_cache.items() if k in keep_stems}
            _log.warning(
                "[DEBUG] SuperViewer_DEBUG_FILE_LIST_LIMIT=%s active: files %s -> %s, report_entries -> %s",
                _DEBUG_FILE_LIST_LIMIT,
                len(files),
                len(limited_files),
                len(report_cache),
            )
            files = limited_files
        self._report_cache = report_cache
        self._report_row_by_path = {}
        row_cache_for_path_map = self._report_full_cache or self._report_cache or {}
        for p in files:
            norm_p = os.path.normpath(p) if p else ""
            if not norm_p:
                continue
            row = row_cache_for_path_map.get(Path(norm_p).stem)
            if isinstance(row, dict):
                self._report_row_by_path[norm_p] = row
        _log.info("[_on_directory_scan_finished] report row path map entries=%s", len(self._report_row_by_path))
        self._all_files = files
        _log.info("[_on_directory_scan_finished] 已列出 %s 个文件，重建列表/缩略图视图", len(files))
        _log.info("[_on_directory_scan_finished] _rebuild_views START")
        self._rebuild_views()
        _log.info("[_on_directory_scan_finished] _rebuild_views END")
        if self._pending_selection_paths:
            self._apply_pending_selection()
            if self._view_mode != self._MODE_THUMB or not self._thumb_model_dirty:
                self._pending_selection_paths = None
                self._pending_selection_current_path = ""
        if files:
            _log.info("[_on_directory_scan_finished] 为当前目录下列出的 %s 个文件启动 EXIF 查询（report_cache=%s 条，未命中走 exiftool/XMP）", len(files), len(report_cache))
            self._start_metadata_loader(files)
        else:
            _log.info("[_on_directory_scan_finished] 当前目录无图像文件，跳过 EXIF 查询")
        self._schedule_persistent_thumb_cache_build(files)
        self._directory_scan_worker = None
        _log.info("[_on_directory_scan_finished] END")

    def get_current_dir(self) -> str:
        """返回当前选中的目录路径（与 load_directory 的 path 一致）。"""
        return self._current_dir or ""

    def get_report_cache(self) -> dict:
        """返回当前目录的 report 缓存：stem（不含扩展名）→ report 行 dict。无缓存时返回空 dict。"""
        return self._report_cache

    def get_report_row_for_path(self, path: str) -> dict | None:
        row = self._get_report_row_for_path(path)
        return dict(row) if isinstance(row, dict) else None

    def set_pending_selection(self, paths: list, current_path: str | None = None) -> None:
        """设置「待选路径」：下次目录加载完成后将列表中匹配的项多选并视为当前选中（与目录内多选同等）。若当前已打开该目录且列表已加载，则立即应用。"""
        if not paths:
            self._pending_selection_paths = None
            self._pending_selection_current_path = ""
            return
        normalized = [os.path.normpath(os.path.abspath(str(p))) for p in paths if p]
        if not normalized:
            self._pending_selection_paths = None
            self._pending_selection_current_path = ""
            return
        normalized_keys = {os.path.normcase(p) for p in normalized}
        preferred_current = os.path.normpath(os.path.abspath(str(current_path))) if current_path else normalized[0]
        if os.path.normcase(preferred_current) not in normalized_keys:
            preferred_current = normalized[0]
        self._pending_selection_current_path = preferred_current
        parent = os.path.dirname(normalized[0])
        if (
            self._current_dir
            and os.path.normpath(self._current_dir) == os.path.normpath(parent)
            and (self._tree_row_count() > 0 or self._thumb_row_count() > 0)
        ):
            self._pending_selection_paths = normalized
            self._apply_pending_selection()
            if not self._thumb_model_dirty:
                self._pending_selection_paths = None
                self._pending_selection_current_path = ""
            return
        self._pending_selection_paths = normalized

    def _apply_pending_selection(self) -> None:
        """在目录加载完成后，将 _pending_selection_paths 中出现在当前列表的路径多选并刷新预览。"""
        paths = self._pending_selection_paths or []
        if not paths:
            self._pending_selection_current_path = ""
            return
        path_set = {os.path.normcase(os.path.normpath(p)) for p in paths if p}
        if not path_set:
            self._pending_selection_current_path = ""
            return
        preferred_current_key = (
            os.path.normcase(os.path.normpath(self._pending_selection_current_path))
            if self._pending_selection_current_path
            else ""
        )
        first_matched = None
        preferred_current_matched = False
        self._tree_widget.clearSelection()
        tree_sm = self._tree_widget.selectionModel()
        if tree_sm is not None:
            for path in self._file_table_model.all_paths():
                norm = os.path.normpath(path)
                if os.path.normcase(norm) not in path_set:
                    continue
                idx = self._tree_index_for_path(norm)
                if not idx.isValid():
                    continue
                tree_sm.select(idx, _Select)
                if preferred_current_key and os.path.normcase(norm) == preferred_current_key:
                    preferred_current_matched = True
                if first_matched is None:
                    first_matched = norm
            current_target = self._pending_selection_current_path if preferred_current_matched else first_matched
            if current_target is not None:
                idx_first = self._tree_index_for_path(current_target)
                if idx_first.isValid():
                    self._tree_widget.setCurrentIndex(idx_first)
                    self._tree_widget.scrollTo(idx_first)
        self._list_widget.clearSelection()
        sm = self._list_widget.selectionModel()
        if sm is not None:
            for path in self._thumb_list_model.all_paths():
                norm = os.path.normpath(path)
                if os.path.normcase(norm) not in path_set:
                    continue
                idx = self._thumb_index_for_path(norm)
                if not idx.isValid():
                    continue
                sm.select(idx, _Select)
                if preferred_current_key and os.path.normcase(norm) == preferred_current_key:
                    preferred_current_matched = True
                if first_matched is None:
                    first_matched = norm
        current_target = self._pending_selection_current_path if preferred_current_matched else first_matched
        if current_target is not None:
            idx_first = self._thumb_index_for_path(current_target)
            if idx_first.isValid():
                self._list_widget.setCurrentIndex(idx_first)
                self._list_widget.scrollTo(idx_first)
            self._emit_file_selected_for_path(current_target)
        self._update_selection_status()

    def _get_species_cn_from_metadata(self, path: str) -> str:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return ""

        meta = self._meta_cache.get(norm_path, {})
        if isinstance(meta, dict):
            cached_title = str(meta.get("bird_species_cn") or meta.get("title") or "").strip()
            if cached_title:
                return cached_title

        actual_path = self._get_actual_path_for_display(norm_path) or norm_path
        if not actual_path or not os.path.isfile(actual_path):
            return ""

        title = ""
        try:
            raw_map = read_batch_metadata([actual_path])
        except Exception as exc:
            _log.warning("[_get_species_cn_from_metadata] source=%r read_exif_failed: %s", path, exc)
            return ""

        actual_norm = os.path.normpath(actual_path)
        rec = raw_map.get(actual_norm) or raw_map.get(actual_path)
        if not isinstance(rec, dict):
            for candidate in raw_map.values():
                if isinstance(candidate, dict):
                    rec = candidate
                    break
        if isinstance(rec, dict):
            title = str(
                rec.get("XMP-dc:Title")
                or rec.get("XMP-dc:title")
                or rec.get("IFD0:XPTitle")
                or rec.get("IPTC:ObjectName")
                or ""
            ).strip()

        if title:
            cached_meta = self._meta_cache.setdefault(norm_path, {})
            if isinstance(cached_meta, dict):
                cached_meta["title"] = title
                cached_meta.setdefault("bird_species_cn", title)
        return title

    def _get_species_payload_for_path(self, path: str) -> dict | None:
        row = self._get_report_row_for_path(path)
        filename = str((row or {}).get("filename") or Path(path).stem or "").strip()
        if not filename:
            return None
        bird_species_cn = str((row or {}).get("bird_species_cn") or "").strip()
        if not bird_species_cn:
            bird_species_cn = self._get_species_cn_from_metadata(path)
        return {
            "filename": filename,
            "source_path": os.path.normpath(path) if path else "",
            "bird_species_cn": bird_species_cn,
            "bird_species_en": str((row or {}).get("bird_species_en") or "").strip(),
        }

    def _copy_text_to_clipboard(self, text: str) -> None:
        """通过 Qt 剪贴板复制纯文本，兼容 macOS / Windows。"""
        QApplication.clipboard().setText(text)
        _log.info("[_copy_text_to_clipboard] platform=%r text=%r", sys.platform, text)

    def _copy_species_from_path(self, path: str) -> None:
        payload = self._get_species_payload_for_path(path)
        if not payload:
            _log.info("[_copy_species_from_path] skip source=%r reason=no_report_row", path)
            return
        self._copied_species_payload = payload
        species_cn = str(payload.get("bird_species_cn") or "").strip()
        if species_cn:
            self._copy_text_to_clipboard(species_cn)
        _log.info(
            "[_copy_species_from_path] source=%r filename=%r bird_species_cn=%r bird_species_en=%r copied_to_clipboard=%s",
            path,
            payload.get("filename"),
            payload.get("bird_species_cn"),
            payload.get("bird_species_en"),
            bool(species_cn),
        )

    def _get_paste_species_action_text(self) -> str:
        payload = getattr(self, "_copied_species_payload", None) or {}
        label = str(payload.get("bird_species_cn") or payload.get("filename") or "").strip()
        if label:
            return f"粘贴鸟种名称（{label}）"
        return "粘贴鸟种名称"

    def _paste_species_to_paths(self, paths: list[str]) -> None:
        payload = getattr(self, "_copied_species_payload", None)
        if not payload:
            _log.info("[_paste_species_to_paths] skip reason=no_copied_species")
            return
        db_dir = self._report_root_dir or self._current_dir
        db = ReportDB.open_if_exists(db_dir) if db_dir else None
        if db is None:
            _log.info("[_paste_species_to_paths] skip db_dir=%r reason=no_report_db", db_dir)
            return

        cn = str(payload.get("bird_species_cn") or "").strip()
        en = str(payload.get("bird_species_en") or "").strip()
        data = {
            "bird_species_cn": cn,
            "bird_species_en": en,
        }
        updated = 0
        attempted = 0
        updated_stems: set[str] = set()
        try:
            for path in paths:
                row = self._get_report_row_for_path(path)
                filename = str((row or {}).get("filename") or Path(path).stem or "").strip()
                if not filename or filename in updated_stems:
                    continue
                attempted += 1
                if not db.update_photo(filename, data):
                    continue
                updated_stems.add(filename)
                updated += 1
                if isinstance(row, dict):
                    row["bird_species_cn"] = cn
                    row["bird_species_en"] = en
                if self._report_full_cache and filename in self._report_full_cache:
                    self._report_full_cache[filename]["bird_species_cn"] = cn
                    self._report_full_cache[filename]["bird_species_en"] = en
                if filename in self._report_cache:
                    self._report_cache[filename]["bird_species_cn"] = cn
                    self._report_cache[filename]["bird_species_en"] = en
                norm_path = os.path.normpath(path) if path else ""
                if norm_path:
                    meta = self._meta_cache.setdefault(norm_path, {})
                    if isinstance(meta, dict):
                        meta["bird_species_cn"] = cn
                        meta["bird_species_en"] = en
                        fallback_title = str((row or {}).get("title") or meta.get("title") or "").strip()
                        meta["title"] = cn or fallback_title
                        self._file_table_model.set_meta_for_path(norm_path, meta)
        finally:
            db.close()

        self._tree_widget.viewport().update()

        _log.info(
            "[_paste_species_to_paths] source_filename=%r bird_species_cn=%r bird_species_en=%r attempted=%s updated=%s",
            payload.get("filename"),
            cn,
            en,
            attempted,
            updated,
        )
        if updated > 0 and self._selected_display_path:
            selected_norm = os.path.normpath(self._selected_display_path)
            path_keys = {os.path.normcase(os.path.normpath(p)) for p in paths if p}
            if os.path.normcase(selected_norm) in path_keys:
                refreshed_path = self._resolve_source_path_for_action(selected_norm)
                _log.info(
                    "[_paste_species_to_paths] refresh_selected source=%r refreshed=%r",
                    selected_norm,
                    refreshed_path,
                )
                self.file_selected.emit(refreshed_path or selected_norm)

    def _unique_norm_paths(self, paths: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for path in paths:
            norm_path = os.path.normpath(path) if path else ""
            if not norm_path:
                continue
            norm_key = os.path.normcase(norm_path)
            if norm_key in seen:
                continue
            seen.add(norm_key)
            unique.append(norm_path)
        return unique

    def _rating_state_for_path(self, path: str) -> tuple[int, int]:
        norm_path = os.path.normpath(path) if path else ""
        meta = self._meta_cache.get(norm_path, {})
        if isinstance(meta, dict):
            try:
                rating = max(0, min(5, int(float(str(meta.get("rating", 0) or 0)))))
            except Exception:
                rating = 0
            try:
                pick = max(-1, min(1, int(float(str(meta.get("pick", 0) or 0)))))
            except Exception:
                pick = 0
            return rating, pick
        row = self._get_report_row_for_path(path)
        try:
            rating = max(0, min(5, int(float(str((row or {}).get("rating", 0) or 0)))))
        except Exception:
            rating = 0
        try:
            pick = max(-1, min(1, int(float(str((row or {}).get("pick", 0) or 0)))))
        except Exception:
            pick = 0
        return rating, pick

    def _pick_target_for_paths(self, paths: list[str]) -> int:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return 1
        all_picked = True
        for path in unique_paths:
            _rating, pick = self._rating_state_for_path(path)
            if pick != 1:
                all_picked = False
                break
        return 0 if all_picked else 1

    def _reject_target_for_paths(self, paths: list[str]) -> int:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return -1
        all_rejected = True
        for path in unique_paths:
            _rating, pick = self._rating_state_for_path(path)
            if pick != -1:
                all_rejected = False
                break
        return 0 if all_rejected else -1

    def _resolve_rating_write_source(
        self,
        path: str,
        *,
        report_db_available: bool,
    ) -> str:
        if report_db_available:
            row = self._get_report_row_for_path(path)
            if isinstance(row, dict):
                filename = str(row.get("filename") or Path(path).stem or "").strip()
                if filename:
                    return "report_db"
        sidecar_path = self._resolve_sidecar_path(path)
        if sidecar_path and os.path.isfile(sidecar_path):
            return "xmp_sidecar"
        return "source_exif"

    def _resolve_metadata_write_target(self, path: str) -> str:
        sidecar_path = self._resolve_sidecar_path(path)
        if sidecar_path and os.path.isfile(sidecar_path):
            return sidecar_path
        source_path = self._resolve_source_path_for_action(path)
        if source_path and os.path.isfile(source_path):
            return source_path
        return source_path or sidecar_path or os.path.normpath(path)

    def _build_exif_rating_assignments(
        self,
        *,
        rating: int | None = None,
        pick: int | None = None,
    ) -> list[str]:
        assignments: list[str] = []
        if rating is not None:
            rating_value = max(0, min(5, int(rating)))
            assignments.append(f"-XMP-xmp:Rating={rating_value}")
        if pick is not None:
            pick_value = max(-1, min(1, int(pick)))
            if pick_value == 0:
                assignments.extend([
                    "-XMP-xmpDM:pick=",
                    "-XMP-xmpDM:Pick=",
                    "-XMP-xmp:Pick=",
                    "-XMP-xmp:PickLabel=",
                    "-XMP:Pick=",
                    "-XMP:PickLabel=",
                ])
            else:
                assignments.extend([
                    f"-XMP-xmpDM:pick={pick_value}",
                    f"-XMP-xmpDM:Pick={pick_value}",
                    f"-XMP-xmp:Pick={pick_value}",
                    f"-XMP:Pick={pick_value}",
                ])
        return assignments

    def _ensure_report_cache_row(self, path: str, filename: str) -> dict:
        norm_path = os.path.normpath(path) if path else ""
        row = self._get_report_row_for_path(norm_path)
        if not isinstance(row, dict):
            row = {"filename": filename}
        else:
            row.setdefault("filename", filename)

        if isinstance(self._report_full_cache, dict):
            cached = self._report_full_cache.get(filename)
            if isinstance(cached, dict):
                row = cached
            else:
                self._report_full_cache[filename] = row
        if isinstance(self._report_cache, dict):
            cached = self._report_cache.get(filename)
            if isinstance(cached, dict):
                row = cached
            else:
                self._report_cache[filename] = row
        if norm_path:
            self._report_row_by_path[norm_path] = row
        return row

    def _apply_rating_state_to_meta_cache(
        self,
        path: str,
        *,
        rating: int | None = None,
        pick: int | None = None,
    ) -> None:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        meta = self._meta_cache.setdefault(norm_path, {})
        if not isinstance(meta, dict):
            meta = {}
            self._meta_cache[norm_path] = meta
        if rating is not None:
            meta["rating"] = max(0, min(5, int(rating)))
        if pick is not None:
            meta["pick"] = max(-1, min(1, int(pick)))

    def _refresh_metadata_state_for_paths(self, paths: list[str]) -> None:
        unique_paths = self._unique_norm_paths(paths)
        for norm_path in unique_paths:
            meta = self._meta_cache.get(norm_path, {})
            if not isinstance(meta, dict):
                continue
            self._file_table_model.set_meta_for_path(norm_path, meta)
            self._apply_thumb_meta_to_path(norm_path, meta)

        if self._tree_widget.isSortingEnabled():
            self._tree_widget.sortByColumn(self._tree_last_sort_column, self._tree_last_sort_order)
            self._refresh_tree_row_numbers()

        self._tree_widget.viewport().update()
        if self._view_mode == self._MODE_THUMB:
            self._list_widget.viewport().update()

        if self._filter_pick or self._filter_min_rating > 0 or self._filter_focus_status:
            self._apply_filter()

        if self._selected_display_path:
            selected_norm = os.path.normpath(self._selected_display_path)
            path_keys = {os.path.normcase(os.path.normpath(p)) for p in unique_paths}
            if os.path.normcase(selected_norm) in path_keys:
                refreshed_path = self._resolve_source_path_for_action(selected_norm)
                self.file_selected.emit(refreshed_path or selected_norm)

    def _apply_rating_state_via_report_db(
        self,
        paths: list[str],
        *,
        rating: int | None = None,
        pick: int | None = None,
    ) -> list[str]:
        db_dir = self._report_root_dir or self._current_dir
        db = ReportDB.open_if_exists(db_dir) if db_dir else None
        if db is None:
            return []

        updated_paths: list[str] = []
        try:
            for path in self._unique_norm_paths(paths):
                row = self._get_report_row_for_path(path)
                if not isinstance(row, dict):
                    continue
                filename = str(row.get("filename") or Path(path).stem or "").strip()
                if not filename:
                    continue
                data: dict[str, int] = {}
                if rating is not None:
                    data["rating"] = max(0, min(5, int(rating)))
                if pick is not None:
                    data["pick"] = max(-1, min(1, int(pick)))
                if not data:
                    continue
                try:
                    db.insert_photo({"filename": filename, **data})
                except Exception as exc:
                    _log.warning("[_apply_rating_state_via_report_db] source=%r filename=%r failed: %s", path, filename, exc)
                    continue
                cache_row = self._ensure_report_cache_row(path, filename)
                for key, value in data.items():
                    cache_row[key] = value
                self._apply_rating_state_to_meta_cache(path, rating=rating, pick=pick)
                updated_paths.append(path)
        finally:
            db.close()
        return updated_paths

    def _apply_rating_state_via_exif(
        self,
        paths: list[str],
        *,
        rating: int | None = None,
        pick: int | None = None,
    ) -> list[str]:
        assignments = self._build_exif_rating_assignments(rating=rating, pick=pick)
        if not assignments:
            return []
        updated_paths: list[str] = []
        target_groups: dict[str, dict[str, object]] = {}
        for path in self._unique_norm_paths(paths):
            target_path = self._resolve_metadata_write_target(path)
            if not target_path:
                continue
            target_key = os.path.normcase(os.path.normpath(target_path))
            group = target_groups.get(target_key)
            if not isinstance(group, dict):
                target_groups[target_key] = {
                    "target_path": target_path,
                    "paths": [path],
                }
                continue
            group_paths = group.get("paths")
            if isinstance(group_paths, list):
                group_paths.append(path)
        for group in target_groups.values():
            target_path = str(group.get("target_path") or "").strip()
            source_paths = [os.path.normpath(p) for p in group.get("paths", []) if p]
            if not target_path or not source_paths:
                continue
            try:
                run_exiftool_assignments(target_path, assignments)
            except Exception as exc:
                _log.warning(
                    "[_apply_rating_state_via_exif] source=%r target=%r failed: %s",
                    source_paths[0],
                    target_path,
                    exc,
                )
                continue
            for source_path in source_paths:
                self._apply_rating_state_to_meta_cache(source_path, rating=rating, pick=pick)
                updated_paths.append(source_path)
        return updated_paths

    def _set_rating_state_for_paths(
        self,
        paths: list[str],
        *,
        rating: int | None = None,
        pick: int | None = None,
    ) -> None:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return
        db_dir = self._report_root_dir or self._current_dir
        db_exists = False
        if db_dir:
            db_probe = ReportDB.open_if_exists(db_dir)
            db_exists = db_probe is not None
            if db_probe is not None:
                db_probe.close()
        report_paths: list[str] = []
        file_paths: list[str] = []
        source_counts = {
            "report_db": 0,
            "xmp_sidecar": 0,
            "source_exif": 0,
        }
        for path in unique_paths:
            source_name = self._resolve_rating_write_source(path, report_db_available=db_exists)
            source_counts[source_name] = source_counts.get(source_name, 0) + 1
            if source_name == "report_db":
                report_paths.append(path)
            else:
                file_paths.append(path)
        updated_paths: list[str] = []
        if report_paths:
            updated_paths.extend(self._apply_rating_state_via_report_db(report_paths, rating=rating, pick=pick))
        if file_paths:
            updated_paths.extend(self._apply_rating_state_via_exif(file_paths, rating=rating, pick=pick))
        source_summary = ", ".join(
            f"{name}={count}" for name, count in source_counts.items() if count > 0
        ) or "none"
        if not updated_paths:
            _log.info(
                "[_set_rating_state_for_paths] skip sources=%s rating=%r pick=%r selected=%s",
                source_summary,
                rating,
                pick,
                len(unique_paths),
            )
            return
        self._refresh_metadata_state_for_paths(updated_paths)
        _log.info(
            "[_set_rating_state_for_paths] sources=%s rating=%r pick=%r selected=%s updated=%s",
            source_summary,
            rating,
            pick,
            len(unique_paths),
            len(updated_paths),
        )

    def _add_rating_menu_actions(self, menu: QMenu, paths: list[str]) -> None:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return
        rating_menu = menu.addMenu("修改星级")
        clear_rating_action = rating_menu.addAction("取消星级")
        clear_rating_action.triggered.connect(
            lambda checked=False: self._set_rating_state_for_paths(unique_paths, rating=0)
        )
        rating_menu.addSeparator()
        for stars in range(1, 6):
            action = rating_menu.addAction("★" * stars)
            action.triggered.connect(
                lambda checked=False, value=stars: self._set_rating_state_for_paths(unique_paths, rating=value)
            )
        rating_menu.addSeparator()
        pick_target = self._pick_target_for_paths(unique_paths)
        pick_label = "取消🏆 Pick" if pick_target == 0 else "🏆 Pick"
        pick_action = rating_menu.addAction(pick_label)
        pick_action.triggered.connect(
            lambda checked=False, value=pick_target: self._set_rating_state_for_paths(unique_paths, pick=value)
        )
        reject_target = self._reject_target_for_paths(unique_paths)
        reject_label = "取消🚫 排除" if reject_target == 0 else "🚫 标记为排除"
        reject_action = rating_menu.addAction(reject_label)
        reject_action.triggered.connect(
            lambda checked=False, value=reject_target: self._set_rating_state_for_paths(unique_paths, pick=value)
        )

    def _get_actual_path_for_display(self, path: str) -> str | None:
        actual = _get_cached_actual_path(path)
        if actual and os.path.isfile(actual):
            return actual
        return None

    def _build_path_tooltip(self, path: str) -> str:
        # Normalise Windows-style backslashes before any path operation so that
        # report.db paths built on Windows display and resolve correctly on macOS.
        if path and sys.platform != "win32":
            path = path.replace("\\", "/")
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return ""
        actual_path = self._get_actual_path_for_display(norm_path)
        if actual_path and _path_key(actual_path) != _path_key(norm_path):
            return (
                "<html><body>"
                f"<div><span style='color:#c0392b'>{html.escape(norm_path)}</span></div>"
                f"<div><span style='color:#2980b9'>{html.escape(actual_path)}</span></div>"
                "</body></html>"
            )
        if os.path.isfile(norm_path):
            return (
                "<html><body>"
                f"<div><span style='color:#2980b9'>{html.escape(norm_path)}</span></div>"
                "</body></html>"
            )
        return (
            "<html><body>"
            f"<div><span style='color:#c0392b'>{html.escape(norm_path)} (选中查找实际路径...)</span></div>"
            "</body></html>"
        )

    def _resolve_preview_path_for_tooltip(self, path: str) -> str:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return ""
        preview_base_dir = self._report_root_dir or self._current_dir
        report_cache = self._report_full_cache or self._report_cache or {}
        if preview_base_dir:
            preview_target = get_preview_path_for_file(norm_path, preview_base_dir, report_cache)
            if preview_target and os.path.isfile(preview_target):
                return preview_target
        actual_path = self._get_actual_path_for_display(norm_path)
        return actual_path or norm_path

    def _resolve_existing_preview_image_path(self, path: str) -> str:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return ""
        preview_base_dir = self._report_root_dir or self._current_dir
        if not preview_base_dir:
            return ""
        report_cache = self._report_full_cache or self._report_cache or {}
        preview_target = get_preview_path_for_file(norm_path, preview_base_dir, report_cache)
        if preview_target and os.path.isfile(preview_target):
            return preview_target
        return ""

    def _build_list_path_tooltip(self, path: str) -> str:
        base_tooltip = self._build_path_tooltip(path)
        norm_path = os.path.normpath(path) if path else ""
        if not base_tooltip or not norm_path:
            return base_tooltip
        preview_path = self._resolve_preview_path_for_tooltip(norm_path)
        if not preview_path:
            return base_tooltip
        preview_line = (
            "<div><span style='color:#7f8c8d'>Preview:</span> "
            f"<span style='color:#16a085'>{html.escape(preview_path)}</span></div>"
        )
        end_tag = "</body></html>"
        if base_tooltip.endswith(end_tag):
            return base_tooltip[:-len(end_tag)] + preview_line + end_tag
        return "<html><body>" + base_tooltip + preview_line + "</body></html>"

    def _tree_row_count(self) -> int:
        return self._file_table_proxy.rowCount()

    def _tree_source_row_count(self) -> int:
        return self._file_table_model.rowCount()

    def _tree_index_for_path(self, path: str, column: int = 0) -> QModelIndex:
        source_index = self._file_table_model.index_for_path(path, column)
        if not source_index.isValid():
            return QModelIndex()
        return self._file_table_proxy.mapFromSource(source_index)

    def _tree_path_from_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        source_index = self._file_table_proxy.mapToSource(index)
        return self._file_table_model.path_for_index(source_index) or ""

    def _tree_selected_indexes(self) -> list[QModelIndex]:
        sm = self._tree_widget.selectionModel()
        if sm is None:
            return []
        indexes = [idx for idx in sm.selectedRows(0) if idx.isValid()]
        indexes.sort(key=lambda idx: idx.row())
        return indexes

    def _tree_selected_paths(self) -> list[str]:
        return [self._tree_path_from_index(idx) for idx in self._tree_selected_indexes() if self._tree_path_from_index(idx)]

    def _thumb_row_count(self) -> int:
        return self._thumb_list_model.rowCount()

    def _thumb_index_for_row(self, row: int) -> QModelIndex:
        if row < 0 or row >= self._thumb_row_count():
            return QModelIndex()
        return self._thumb_list_model.index(row, 0)

    def _thumb_index_for_path(self, path: str) -> QModelIndex:
        return self._thumb_list_model.index_for_path(path)

    def _thumb_path_from_index(self, index: QModelIndex) -> str:
        return self._thumb_list_model.path_for_index(index) or ""

    def _thumb_selected_indexes(self) -> list[QModelIndex]:
        sm = self._list_widget.selectionModel()
        if sm is None:
            return []
        indexes = [idx for idx in sm.selectedIndexes() if idx.isValid()]
        indexes.sort(key=lambda idx: idx.row())
        return indexes

    def _thumb_selected_paths(self) -> list[str]:
        return [self._thumb_path_from_index(idx) for idx in self._thumb_selected_indexes() if self._thumb_path_from_index(idx)]

    def _thumb_profile_add(self, key: str, value: float = 1.0) -> None:
        if not self._thumb_profile_enabled:
            return
        self._thumb_profile_stats[key] = float(self._thumb_profile_stats.get(key, 0.0)) + float(value)

    def _thumb_profile_set_max(self, key: str, value: float) -> None:
        if not self._thumb_profile_enabled:
            return
        self._thumb_profile_stats[key] = max(float(self._thumb_profile_stats.get(key, 0.0)), float(value))

    def _reset_thumb_profile_window(self) -> None:
        self._thumb_profile_window_started_at = _time.perf_counter()
        for key in list(self._thumb_profile_stats.keys()):
            if key.startswith("last_"):
                continue
            self._thumb_profile_stats[key] = 0.0

    def _report_thumb_profile(self, reason: str, *, force: bool = False, extra: str = "") -> None:
        if not self._thumb_profile_enabled:
            return
        now = _time.perf_counter()
        if not force and (now - self._thumb_profile_last_report_at) < _THUMB_PROFILE_REPORT_INTERVAL_S:
            return
        stats = self._thumb_profile_stats
        loader = self._thumbnail_loader
        snap = loader.profile_snapshot() if loader is not None else {}
        cache_stats = self._thumb_memory_cache.stats()
        model_pending = max(0, len(self._thumb_model_pending_paths) - int(self._thumb_model_pending_index))
        ready_wait_count = max(1.0, float(stats.get("ready_wait_count", 0.0)))
        flush_calls = max(1.0, float(stats.get("flush_calls", 0.0)))
        window_s = max(0.001, now - self._thumb_profile_window_started_at)
        extra_suffix = f" {extra}" if extra else ""
        _log.info(
            "[THUMB_PROFILE][ui] reason=%s window=%.2fs schedule=%s viewport=%s rows=%s-%s visible=%s missing=%s prefetch=%s cache_fill=%s evicted=%s loader_start=%s reprio=%s ready=%s stale=%s pending_peak=%s flush=%s pending=%s applied=%s offscreen=%s invalid=%s wait_avg=%.1fms wait_max=%.1fms flush_avg=%.1fms flush_max=%.1fms model_pending=%s loader_queue=%s loader_inflight=%s loader_done=%s mem_hit=%s disk_hit=%s progressive=%s frames=%s cache_mb=%.1f%s",
            reason,
            window_s,
            int(stats.get("schedule_calls", 0.0)),
            int(stats.get("viewport_updates", 0.0)),
            int(stats.get("last_visible_start", -1.0)),
            int(stats.get("last_visible_end", -1.0)),
            int(stats.get("last_visible_count", 0.0)),
            int(stats.get("last_missing_count", 0.0)),
            int(stats.get("last_prefetch_count", 0.0)),
            int(stats.get("cache_fill_total", 0.0)),
            int(stats.get("evicted_total", 0.0)),
            int(stats.get("loader_starts", 0.0)),
            int(stats.get("loader_reprioritize", 0.0)),
            int(stats.get("ready_signals", 0.0)),
            int(stats.get("stale_ready", 0.0)),
            int(stats.get("pending_peak", 0.0)),
            int(stats.get("flush_calls", 0.0)),
            int(stats.get("flush_pending_total", 0.0)),
            int(stats.get("flush_applied", 0.0)),
            int(stats.get("flush_skipped_offscreen", 0.0)),
            int(stats.get("flush_skipped_invalid", 0.0)),
            1000.0 * float(stats.get("ready_wait_total_s", 0.0)) / ready_wait_count,
            1000.0 * float(stats.get("ready_wait_max_s", 0.0)),
            1000.0 * float(stats.get("flush_total_s", 0.0)) / flush_calls,
            1000.0 * float(stats.get("flush_max_s", 0.0)),
            model_pending,
            int(snap.get("queue_size", 0)),
            max(0, int(snap.get("submitted", 0)) - int(snap.get("completed", 0))),
            int(snap.get("completed", 0)),
            int(snap.get("memory_hits", 0)),
            int(snap.get("disk_hits", 0)),
            int(snap.get("progressive_paths", 0)),
            int(snap.get("frames_emitted", 0)),
            float(cache_stats.get("bytes", 0)) / (1024.0 * 1024.0),
            extra_suffix,
        )
        self._thumb_profile_last_report_at = now
        self._reset_thumb_profile_window()

    def _find_thumb_index_for_tooltip(self, pos: QPoint) -> QModelIndex:
        idx = self._list_widget.indexAt(pos)
        if idx.isValid():
            return idx
        visible_range = self._thumb_visible_range or self._build_visible_thumbnail_data_source(overscan_rows=0)
        for entry in (visible_range.entries if visible_range is not None else ()):
            idx = self._thumb_index_for_row(entry.row)
            if not idx.isValid():
                continue
            rect = self._list_widget.visualRect(idx)
            if rect.isValid() and rect.contains(pos):
                return idx
        return QModelIndex()

    def _has_path_mismatch(self, path: str) -> bool:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return False
        actual_path = self._get_actual_path_for_display(norm_path)
        if actual_path and _path_key(actual_path) != _path_key(norm_path):
            return True
        return not os.path.isfile(norm_path)

    def _apply_path_status_to_items(self, path: str) -> None:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        mismatch = self._has_path_mismatch(norm_path)
        self._file_table_model.set_path_mismatch_for_path(norm_path, mismatch)
        self._thumb_list_model.set_path_mismatch(norm_path, mismatch)

    def _update_item_tooltips_for_path(self, path: str) -> None:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        tree_tooltip = self._build_list_path_tooltip(norm_path)
        list_tooltip = self._build_list_path_tooltip(norm_path)
        self._file_table_model.set_tooltip_for_path(norm_path, tree_tooltip)
        self._thumb_list_model.set_tooltip_for_path(norm_path, list_tooltip)
        self._apply_path_status_to_items(norm_path)

    def has_path_mismatch(self, path: str) -> bool:
        return self._has_path_mismatch(path)

    def _request_actual_path_lookup(self, path: str) -> None:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path or os.path.isfile(norm_path):
            return
        cached = self._get_actual_path_for_display(norm_path)
        if cached:
            self._update_item_tooltips_for_path(norm_path)
            return
        root_dir = self._report_root_dir or self._current_dir
        if not root_dir or not os.path.isdir(root_dir):
            return
        cache_key = _path_key(norm_path)
        if cache_key in self._path_lookup_pending:
            return
        worker = PathLookupWorker(norm_path, root_dir, self)
        worker.resolved.connect(self._on_actual_path_lookup_resolved)
        self._path_lookup_pending.add(cache_key)
        self._path_lookup_workers.append(worker)
        _log.info("[_request_actual_path_lookup] queued source=%r root=%r", norm_path, root_dir)
        worker.start()

    def _on_actual_path_lookup_resolved(self, source_path: str, actual_path) -> None:
        norm_source = os.path.normpath(source_path) if source_path else ""
        cache_key = _path_key(norm_source) if norm_source else ""
        if cache_key:
            self._path_lookup_pending.discard(cache_key)
        worker = self.sender()
        if isinstance(worker, PathLookupWorker):
            try:
                worker.resolved.disconnect(self._on_actual_path_lookup_resolved)
            except Exception:
                pass
            self._path_lookup_workers = [w for w in self._path_lookup_workers if w is not worker]
        resolved_path = os.path.normpath(actual_path) if actual_path else None
        if norm_source and resolved_path and os.path.isfile(resolved_path):
            _set_cached_actual_path(norm_source, resolved_path)
            row = self._report_row_by_path.get(norm_source)
            if isinstance(row, dict):
                self._sync_report_current_path_from_actual(norm_source, resolved_path, row)
                self._report_row_by_path[resolved_path] = row
            _log.info("[_on_actual_path_lookup_resolved] source=%r actual=%r cached=True", norm_source, resolved_path)
        else:
            _log.info("[_on_actual_path_lookup_resolved] source=%r actual=%r cached=False", norm_source, actual_path)
        if norm_source:
            self._update_item_tooltips_for_path(norm_source)
            if self._selected_display_path and _path_key(self._selected_display_path) == _path_key(norm_source):
                resolved = self._resolve_source_path_for_action(norm_source)
                if resolved and os.path.isfile(resolved):
                    _log.info("[_on_actual_path_lookup_resolved] re-emit selected source=%r resolved=%r", norm_source, resolved)
                    self.file_selected.emit(resolved)

    def _sync_report_current_path_from_actual(self, source_path: str, actual_path: str, row: dict | None) -> None:
        if not isinstance(row, dict):
            return
        root_dir = self._report_root_dir or self._current_dir
        if not root_dir or not os.path.isdir(root_dir):
            _log.info("[_sync_report_current_path_from_actual] skip source=%r actual=%r reason=no_root", source_path, actual_path)
            return
        if not _is_same_or_child_path(root_dir, actual_path):
            _log.info(
                "[_sync_report_current_path_from_actual] skip source=%r actual=%r root=%r reason=outside_root",
                source_path,
                actual_path,
                root_dir,
            )
            return
        filename = str(row.get("filename") or Path(source_path).stem or "").strip()
        if not filename:
            _log.info("[_sync_report_current_path_from_actual] skip source=%r actual=%r reason=no_filename", source_path, actual_path)
            return
        try:
            rel_current_path = os.path.normpath(os.path.relpath(actual_path, root_dir))
        except Exception as e:
            _log.warning(
                "[_sync_report_current_path_from_actual] skip source=%r actual=%r root=%r relpath_failed=%s",
                source_path,
                actual_path,
                root_dir,
                e,
            )
            return
        current_path_old = str(row.get("current_path") or "").strip()
        if current_path_old and os.path.normcase(os.path.normpath(current_path_old)) == os.path.normcase(rel_current_path):
            _log.info(
                "[_sync_report_current_path_from_actual] skip source=%r filename=%r current_path already=%r",
                source_path,
                filename,
                rel_current_path,
            )
            return
        db = ReportDB.open_if_exists(root_dir)
        if db is None:
            _log.info(
                "[_sync_report_current_path_from_actual] skip source=%r filename=%r root=%r reason=no_report_db",
                source_path,
                filename,
                root_dir,
            )
            return
        updated = False
        try:
            updated = db.update_photo(filename, {"current_path": rel_current_path})
        finally:
            db.close()
        if not updated:
            _log.info(
                "[_sync_report_current_path_from_actual] update_failed source=%r filename=%r current_path=%r",
                source_path,
                filename,
                rel_current_path,
            )
            return
        row["current_path"] = rel_current_path
        row["_current_path_report_raw"] = rel_current_path
        if self._report_full_cache and filename in self._report_full_cache:
            self._report_full_cache[filename]["current_path"] = rel_current_path
            self._report_full_cache[filename]["_current_path_report_raw"] = rel_current_path
        if filename in self._report_cache:
            self._report_cache[filename]["current_path"] = rel_current_path
            self._report_cache[filename]["_current_path_report_raw"] = rel_current_path
        _log.info(
            "[_sync_report_current_path_from_actual] updated source=%r actual=%r filename=%r old_current_path=%r new_current_path=%r",
            source_path,
            actual_path,
            filename,
            current_path_old,
            rel_current_path,
        )

    def resolve_preview_path(self, path: str, prefer_fast_preview: bool = False) -> str:
        """Resolve display preview path, preferring an existing cached preview file."""
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return path
        actual_path = self._get_actual_path_for_display(norm_path)
        preview_base_dir = self._report_root_dir or self._current_dir
        report_cache = self._report_full_cache or self._report_cache or {}
        source_path = actual_path or norm_path
        if prefer_fast_preview:
            thumb_source = _resolve_thumb_source_path(source_path, report_cache, preview_base_dir)
            source_stamp = _thumb_source_stamp(source_path, thumb_source)
            persistent_thumb_path = _existing_persistent_thumb_cache_path_for_file(
                source_path,
                preview_base_dir,
                requested_size=_persistent_thumb_cache_max_size(),
                source_stamp=source_stamp,
            )
            if persistent_thumb_path:
                _log.info(
                    "[resolve_preview_path] fast source=%r persistent_thumb=%r actual=%r preview_base_dir=%r",
                    norm_path,
                    persistent_thumb_path,
                    actual_path,
                    preview_base_dir,
                )
                return persistent_thumb_path
            if thumb_source and os.path.isfile(thumb_source):
                try:
                    thumb_mtime = float(os.path.getmtime(thumb_source))
                except Exception:
                    thumb_mtime = 0.0
                thumb_disk_path = _thumb_disk_cache_path(thumb_source, thumb_mtime, self._thumb_size)
                if thumb_disk_path and os.path.isfile(thumb_disk_path):
                    _log.info(
                        "[resolve_preview_path] fast source=%r thumb_disk=%r actual=%r preview_base_dir=%r size=%s",
                        norm_path,
                        thumb_disk_path,
                        actual_path,
                        preview_base_dir,
                        self._thumb_size,
                    )
                    return thumb_disk_path
        preview_target = get_preview_path_for_file(norm_path, preview_base_dir, report_cache)
        preview_path = preview_target if (preview_target and os.path.isfile(preview_target)) else ""
        _log.info(
            "[resolve_preview_path] source=%r preview=%r preview_target=%r actual=%r preview_base_dir=%r report_entries=%s fast=%s",
            norm_path,
            preview_path,
            preview_target,
            actual_path,
            preview_base_dir,
            len(report_cache),
            int(bool(prefer_fast_preview)),
        )
        return preview_path or actual_path or norm_path

    def _get_report_row_for_path(self, path: str) -> dict | None:
        norm_path = os.path.normpath(path) if path else ""
        if norm_path:
            row = self._report_row_by_path.get(norm_path)
            if isinstance(row, dict):
                _log.info("[_get_report_row_for_path] source=%r matched=path_map", path)
                return row
        stem = Path(path).stem if path else ""
        if not stem:
            return None
        cache = self._report_full_cache or self._report_cache or {}
        row = cache.get(stem)
        if isinstance(row, dict):
            _log.info("[_get_report_row_for_path] source=%r matched=stem_cache stem=%r", path, stem)
        return row if isinstance(row, dict) else None

    def _resolve_report_current_abs_path(self, path: str) -> str | None:
        row = self._get_report_row_for_path(path)
        if not row:
            return None
        cp_text = str(row.get("current_path") or "").strip()
        if not cp_text:
            return None
        base_dir = self._report_root_dir or self._current_dir
        if os.path.isabs(cp_text):
            return os.path.normpath(cp_text)
        if not base_dir:
            return None
        return os.path.normpath(os.path.join(base_dir, cp_text))

    def _resolve_sidecar_path(self, path: str) -> str | None:
        row = self._get_report_row_for_path(path)
        cp_abs = None
        cp_text_raw = _get_report_current_path_raw(row) if isinstance(row, dict) else ""
        if cp_text_raw:
            base_dir = self._report_root_dir or self._current_dir
            if os.path.isabs(cp_text_raw):
                cp_abs = os.path.normpath(cp_text_raw)
            elif base_dir:
                cp_abs = os.path.normpath(os.path.join(base_dir, cp_text_raw))
        if cp_abs and cp_abs.lower().endswith(".xmp") and os.path.isfile(cp_abs):
            _log.info("[_resolve_sidecar_path] source=%r sidecar(report_current)=%r", path, cp_abs)
            return cp_abs
        actual_source = self._get_actual_path_for_display(path)
        if actual_source:
            try:
                xmp_path = find_xmp_sidecar(actual_source)
            except Exception:
                xmp_path = None
            if xmp_path and os.path.isfile(xmp_path):
                resolved = os.path.normpath(os.path.abspath(xmp_path))
                _log.info("[_resolve_sidecar_path] source=%r sidecar(actual_sibling)=%r", path, resolved)
                return resolved
        try:
            xmp_path = find_xmp_sidecar(path)
        except Exception:
            xmp_path = None
        if xmp_path and os.path.isfile(xmp_path):
            resolved = os.path.normpath(os.path.abspath(xmp_path))
            _log.info("[_resolve_sidecar_path] source=%r sidecar(sibling)=%r", path, resolved)
            return resolved
        _log.info("[_resolve_sidecar_path] source=%r sidecar=None", path)
        return None

    def _resolve_source_path_for_action(self, path: str) -> str:
        norm_path = os.path.normpath(path) if path else ""
        actual_path = self._get_actual_path_for_display(norm_path)
        if actual_path:
            _log.info("[_resolve_source_path_for_action] source=%r resolved=actual_cache=%r", path, actual_path)
            return actual_path
        if norm_path and os.path.isfile(norm_path):
            _log.info("[_resolve_source_path_for_action] source=%r resolved=self=%r", path, norm_path)
            return norm_path

        row = self._get_report_row_for_path(norm_path)
        cp_abs = self._resolve_report_current_abs_path(norm_path)
        if row and cp_abs:
            if os.path.isfile(cp_abs) and Path(cp_abs).suffix.lower() in IMAGE_EXTENSIONS:
                _log.info("[_resolve_source_path_for_action] source=%r resolved=current_path=%r", path, cp_abs)
                return cp_abs
            op = str(row.get("original_path") or "").strip()
            ext_orig = Path(op).suffix.lower() if op else ""
            if ext_orig:
                sibling_source = str(Path(cp_abs).with_suffix(ext_orig))
                if os.path.isfile(sibling_source):
                    resolved = os.path.normpath(sibling_source)
                    _log.info("[_resolve_source_path_for_action] source=%r resolved=sibling_source=%r", path, resolved)
                    return resolved

        _log.info("[_resolve_source_path_for_action] source=%r unresolved return_original=%r", path, norm_path)
        return norm_path

    def _resolve_reveal_path(self, path: str) -> str:
        source_path = self._resolve_source_path_for_action(path)
        xmp_path = self._resolve_sidecar_path(path)
        source_exists = bool(source_path and os.path.exists(source_path))
        xmp_exists = bool(xmp_path and os.path.exists(xmp_path))
        if source_exists:
            final_path = source_path
        elif xmp_exists:
            final_path = xmp_path
        else:
            final_path = source_path or path
        _log.info(
            "[_resolve_reveal_path] source=%r source_path=%r source_exists=%s xmp_path=%r xmp_exists=%s final=%r",
            path,
            source_path,
            source_exists,
            xmp_path,
            xmp_exists,
            final_path,
        )
        return final_path

    def _apply_thumb_meta_to_path(self, path: str, meta: dict | None) -> None:
        self._thumb_list_model.set_meta_for_path(path, meta)

    def _clear_thumb_pixmap_for_path(self, path: str) -> None:
        self._thumb_list_model.clear_pixmap_for_path(path)

    def _apply_cached_thumbs_to_items(
        self,
        visible_range: "ThumbViewportRange | None" = None,
    ) -> int:
        """尽可能直接从内存缩略图缓存填充当前列表项，避免已加载目录间切换时重新排队后台加载。
        Only applies to currently-visible items to avoid creating pixmaps for thousands of
        off-screen items (which would cause unbounded memory growth)."""
        if self._thumb_row_count() <= 0:
            return 0
        # Determine which items are (or will soon be) visible so we only materialise
        # QPixmaps for those.  Fall back to all items when the viewport is not yet
        # ready (e.g. immediately after a directory switch before layout settles).
        range_data = visible_range if visible_range is not None else self._build_visible_thumbnail_data_source()
        if range_data is not None and range_data.entries:
            visible_norms = {e.path for e in range_data.entries}
        else:
            visible_norms = None  # layout not ready – apply to all (small directories)
        applied = 0
        for norm in self._thumb_list_model.all_paths():
            if visible_norms is not None and norm not in visible_norms:
                continue
            if self._thumb_list_model.has_current_pixmap(norm, self._thumb_size):
                continue
            cached = self._thumb_memory_cache.get(norm, self._thumb_size)
            if cached is None or cached.isNull():
                continue
            pixmap = QPixmap.fromImage(cached)
            self._thumb_list_model.set_pixmap_for_path(norm, pixmap, self._thumb_size)
            meta = self._meta_cache.get(norm, {})
            self._apply_thumb_meta_to_path(norm, meta)
            applied += 1
        return applied

    def _evict_offscreen_item_pixmaps(self, visible_range: "ThumbViewportRange") -> int:
        """Release QPixmap objects stored on items that are well outside the current
        viewport.  This is the primary guard against unbounded RAM growth: the
        ThumbnailMemoryCache (QImage, bounded at 512 MB) survives, so re-entering
        the viewport reloads from memory cache without any disk I/O.

        Keeps a buffer of 4 extra rows on each side of the visible range so that
        smooth scrolling doesn't cause visible flicker.
        """
        total = self._thumb_row_count()
        if total == 0:
            return 0
        vp_w = self._list_widget.viewport().rect().width()
        cols = max(1, vp_w // max(1, visible_range.grid_width))
        buffer = cols * 4  # 4 extra rows on each side
        keep_start = max(0, visible_range.start_row - buffer)
        keep_end = min(total - 1, visible_range.end_row + buffer)
        evicted = 0
        for i in range(total):
            if keep_start <= i <= keep_end:
                continue
            if self._thumb_list_model.clear_pixmap_for_row(i):
                evicted += 1
        return evicted

    def _compute_filtered_files(self) -> list[str]:
        ft = (self._filter_edit.text().strip().lower()) if self._filter_edit else ""
        fp = self._filter_pick
        fr = self._filter_min_rating
        ff = self._filter_focus_status
        filtered: list[str] = []
        for path in self._all_files:
            norm = os.path.normpath(path)
            name = Path(path).name
            meta = self._meta_cache.get(norm, {})
            pick = meta.get("pick", 0)
            rating = meta.get("rating", 0)
            if ft and ft not in name.lower():
                continue
            if fp and pick != 1:
                continue
            if rating < fr:
                continue
            if ff and _focus_status_to_display(meta.get("country", "")) != ff:
                continue
            filtered.append(path)
        return filtered

    def _unused_removed_clear_thumb_cache_button_tooltip(self) -> None:
        return
        if not self._create_filter_bar or self._btn_clear_thumb_cache is None:
            return
        stats = self._thumb_memory_cache.stats()
        mb = stats["bytes"] / (1024.0 * 1024.0)
        tooltip = (
            "清除当前会话的缩略图内存缓存。\n"
            f"- JPEG/JPG: 按 128/256/512/1024 级别缓存 MIP\n"
            f"- 其它格式: 直接缓存 {_THUMB_CACHE_BASE_SIZE}px 基础图，再按当前视图缩放\n"
            f"- 后台加载线程数: {self._thumb_loader_workers}\n"
            f"- 当前缓存: {stats['entries']} 项 ({mb:.1f} MB)\n"
            "- 点击后会清空缓存并释放当前列表项上的缩略图，视口中的图片会按当前尺寸重新加载"
        )
        self._btn_clear_thumb_cache.setToolTip(tooltip)

    def _unused_removed_clear_thumb_cache_clicked(self) -> None:
        return
        self._stop_thumbnail_loader()
        stats = self._thumb_memory_cache.clear()
        cleared_items = self._thumb_list_model.clear_all_pixmaps()
        self._invalidate_visible_thumbnail_signature()
        if self._view_mode == self._MODE_THUMB:
            self._list_widget.viewport().update()
            self._schedule_visible_thumbnail_update()
        self._update_clear_thumb_cache_button_tooltip()
        _log.info(
            "[_on_clear_thumb_cache_clicked] cleared entries=%s bytes=%.1fMB list_items=%s",
            stats.get("entries", 0),
            float(stats.get("bytes", 0)) / (1024.0 * 1024.0),
            cleared_items,
        )

    def _clear_tree_view_state(self) -> None:
        self._file_table_model.clear()

    def _rebuild_tree_items(self) -> None:
        self._tree_widget.setUpdatesEnabled(False)
        try:
            self._tree_widget.setSortingEnabled(False)
            self._set_tree_header_fast_mode(True)
            self._clear_tree_view_state()
            ft = (self._filter_edit.text().strip().lower()) if self._filter_edit else ""
            _log.info("[_rebuild_tree_items] filter_text=%r adding items", ft or "(none)")
            self._file_table_model.rebuild(
                self._filtered_files,
                meta_cache=self._meta_cache,
                tooltip_fn=self._build_list_path_tooltip,
                mismatch_fn=self._has_path_mismatch,
            )
        finally:
            self._tree_widget.setSortingEnabled(True)
            self._set_tree_header_fast_mode(False)
            if self._tree_row_count() > 0:
                hdr = self._tree_widget.header()
                try:
                    hdr.blockSignals(True)
                    hdr.setSortIndicator(self._tree_last_sort_column, self._tree_last_sort_order)
                finally:
                    hdr.blockSignals(False)
                self._tree_widget.sortByColumn(self._tree_last_sort_column, self._tree_last_sort_order)
            self._tree_widget.setUpdatesEnabled(True)
            self._refresh_tree_row_numbers()
        self._tree_view_dirty = False

    def _mark_tree_view_dirty(self) -> None:
        self._tree_view_dirty = True
        self._clear_tree_view_state()

    def _ensure_thumb_model_populate_timer(self) -> None:
        if self._thumb_model_populate_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._populate_thumb_model_batch)
        self._thumb_model_populate_timer = timer

    def _pause_thumb_model_population(self) -> None:
        if self._thumb_model_populate_timer is not None and self._thumb_model_populate_timer.isActive():
            self._thumb_model_populate_timer.stop()

    def _cancel_thumb_model_population(self) -> None:
        self._pause_thumb_model_population()
        self._thumb_model_pending_paths = []
        self._thumb_model_pending_index = 0
        self._thumb_model_populate_started_at = 0.0

    def _mark_thumb_model_dirty(self) -> None:
        self._pause_thumb_model_population()
        self._thumb_model_dirty = True
        self._thumb_model_pending_paths = list(self._filtered_files)
        self._thumb_model_pending_index = 0
        self._thumb_model_populate_started_at = 0.0
        self._thumb_list_model.clear()
        self._invalidate_visible_thumbnail_signature()

    def _start_thumb_model_population(self, *, resume: bool = False) -> None:
        if not resume:
            self._thumb_model_pending_paths = list(self._filtered_files)
            self._thumb_model_pending_index = 0
            self._thumb_model_populate_started_at = _time.perf_counter()
            self._thumb_list_model.clear()
            self._invalidate_visible_thumbnail_signature()
        elif not self._thumb_model_pending_paths:
            self._thumb_model_pending_paths = list(self._filtered_files)
        if not self._thumb_model_pending_paths:
            self._thumb_model_dirty = False
            return
        if self._thumb_model_populate_started_at <= 0:
            self._thumb_model_populate_started_at = _time.perf_counter()
        self._thumb_model_dirty = True
        self._ensure_thumb_model_populate_timer()
        self._populate_thumb_model_batch()

    def _populate_thumb_model_batch(self) -> None:
        total = len(self._thumb_model_pending_paths)
        start = self._thumb_model_pending_index
        if total <= 0 or start >= total:
            self._thumb_model_dirty = False
            self._thumb_model_pending_paths = []
            self._thumb_model_pending_index = 0
            return
        tick_t0 = _time.perf_counter()
        end = start
        min_batch = 24
        max_batch = max(1, _THUMB_MODEL_APPEND_BATCH_SIZE)
        while end < total:
            end += 1
            processed = end - start
            if processed >= max_batch:
                break
            if processed >= min_batch and (_time.perf_counter() - tick_t0) >= _THUMB_MODEL_APPEND_BUDGET_S:
                break
        appended = self._thumb_list_model.append_paths(
            self._thumb_model_pending_paths[start:end],
            meta_cache=self._meta_cache,
            tooltip_fn=self._build_list_path_tooltip,
            mismatch_fn=self._has_path_mismatch,
        )
        self._thumb_model_pending_index = end
        if appended:
            self._invalidate_visible_thumbnail_signature()
            if self._pending_selection_paths:
                self._apply_pending_selection()
            if self._view_mode == self._MODE_THUMB:
                self._schedule_visible_thumbnail_update()
        if end < total:
            self._thumb_model_dirty = True
            if self._view_mode == self._MODE_THUMB and self._thumb_model_populate_timer is not None:
                self._thumb_model_populate_timer.start(0)
            return
        self._thumb_model_dirty = False
        self._thumb_model_pending_paths = []
        self._thumb_model_pending_index = 0
        if self._pending_selection_paths:
            self._apply_pending_selection()
            self._pending_selection_paths = None
            self._pending_selection_current_path = ""
        _log.info(
            "[_populate_thumb_model_batch] completed total=%s elapsed=%.3fs",
            total,
            _time.perf_counter() - self._thumb_model_populate_started_at if self._thumb_model_populate_started_at > 0 else 0.0,
        )
        self._thumb_model_populate_started_at = 0.0

    def _rebuild_views(self, stop_loaders: bool = True) -> None:
        """根据当前过滤结果重建列表/树视图与缩略图项。"""
        if stop_loaders:
            self._stop_all_loaders()
        else:
            self._stop_thumbnail_loader()
        self._cancel_thumb_model_population()
        self._filtered_files = self._compute_filtered_files()
        _log.info(
            "[_rebuild_views] START all_files=%s filtered_files=%s stop_loaders=%s",
            len(self._all_files),
            len(self._filtered_files),
            stop_loaders,
        )
        _log.info("[_rebuild_views] added %s items", len(self._filtered_files))
        if self._view_mode == self._MODE_LIST:
            self._rebuild_tree_items()
            self._mark_thumb_model_dirty()
        else:
            self._mark_tree_view_dirty()
            self._update_thumb_display()
            self._start_thumb_model_population()
        if self._view_mode == self._MODE_THUMB:
            _log.info("[_rebuild_views] thumb mode: update thumb display + schedule visible loader")
            self._schedule_visible_thumbnail_update()
        self._update_selection_status()
        _log.info("[_rebuild_views] END")
        return
        self._tree_widget.setUpdatesEnabled(False)
        self._list_widget.setUpdatesEnabled(False)
        try:
            self._tree_widget.setSortingEnabled(False)
            self._tree_widget.clear()
            self._tree_item_map = {}
            ft = (self._filter_edit.text().strip().lower()) if self._filter_edit else ""
            _log.info("[_rebuild_views] filter_text=%r adding items", ft or "(none)")

            for seq, path in enumerate(self._filtered_files, start=1):
                name = Path(path).name
                norm = os.path.normpath(path)
                meta = self._meta_cache.get(norm, {})

                ti = SortableTreeItem([str(seq), name, "", "", "", "", "", ""])
                ti.setTextAlignment(_TREE_COL_SEQ, _AlignCenter)
                ti.setData(0, _UserRole, path)
                ti.setData(_TREE_COL_SEQ, _SortRole, 0)
                ti.setData(_TREE_COL_NAME, _SortRole, name.lower())
                self._set_tree_item_tooltip_all_columns(ti, self._build_list_path_tooltip(path))
                if meta:
                    self._apply_meta_to_tree_item(ti, meta)
                self._tree_widget.addTopLevelItem(ti)
                self._tree_item_map[norm] = ti
                self._apply_path_status_to_items(norm)
            self._thumb_list_model.rebuild(
                self._filtered_files,
                meta_cache=self._meta_cache,
                tooltip_fn=self._build_list_path_tooltip,
                mismatch_fn=self._has_path_mismatch,
            )
        finally:
            self._tree_widget.setSortingEnabled(True)
            # 重建后显式按当前记录的排序列排序，避免列头状态未同步时按序号列（全 0）产生不稳定顺序
            if self._tree_widget.topLevelItemCount() > 0:
                hdr = self._tree_widget.header()
                try:
                    hdr.blockSignals(True)
                    hdr.setSortIndicator(self._tree_last_sort_column, self._tree_last_sort_order)
                finally:
                    hdr.blockSignals(False)
                self._tree_widget.sortItems(self._tree_last_sort_column, self._tree_last_sort_order)
            self._tree_widget.setUpdatesEnabled(True)
            self._list_widget.setUpdatesEnabled(True)
            self._refresh_tree_row_numbers()

        _log.info("[_rebuild_views] added %s items", len(self._filtered_files))
        if self._view_mode == self._MODE_THUMB:
            _log.info("[_rebuild_views] thumb mode: update thumb display + schedule visible loader")
            self._invalidate_visible_thumbnail_signature()
            self._update_thumb_display()
            # 目录间切换时，优先尝试直接用内存缓存的缩略图填充，已有缓存的不再排队后台加载。
            self._apply_cached_thumbs_to_items()
            self._schedule_visible_thumbnail_update()
        _log.info("[_rebuild_views] END")

    def _apply_filter(self) -> None:
        """根据当前过滤条件（文件名、精选、星级、对焦）重算过滤结果并刷新视图。"""
        ft = (self._filter_edit.text().strip().lower()) if self._filter_edit else ""
        fp = self._filter_pick
        fr = self._filter_min_rating
        t0 = _time.perf_counter()
        filtered = self._compute_filtered_files()
        old_filtered = list(self._filtered_files)
        self._filtered_files = filtered
        _log.info(
            "[_apply_filter] START files=%s filtered=%s pick=%s min_rating=%s text=%r",
            len(self._all_files),
            len(filtered),
            fp,
            fr,
            ft or "(none)",
        )
        tree_ready = self._view_mode != self._MODE_LIST or (not self._tree_view_dirty and self._tree_source_row_count() == len(filtered))
        thumb_ready = self._view_mode != self._MODE_THUMB or (not self._thumb_model_dirty and self._thumb_row_count() == len(filtered))
        if old_filtered == filtered and tree_ready and thumb_ready:
            _log.info("[_apply_filter] SKIP unchanged elapsed=%.3fs", _time.perf_counter() - t0)
            return
        self._rebuild_views(stop_loaders=False)
        _log.info(
            "[_apply_filter] END visible=%s hidden=%s elapsed=%.3fs",
            len(filtered),
            max(0, len(self._all_files) - len(filtered)),
            _time.perf_counter() - t0,
        )

    def _on_pick_filter_toggled(self) -> None:
        """切换精选过滤：只显示 Pick=1 的文件。有任意过滤时递归子目录，无过滤时仅当前目录。"""
        self._filter_pick = self._btn_filter_pick.isChecked()
        if self._filter_min_rating != 0:
            self._filter_min_rating = 0
            for btn in self._star_btns:
                btn.setChecked(False)
        if self._current_dir and os.path.isdir(self._current_dir):
            self.load_directory(self._current_dir, force_reload=True)
        else:
            self._apply_filter()

    def _on_rating_filter_changed(self, n: int) -> None:
        """切换星级过滤：只显示 ≥n 星的文件。有任意过滤时递归子目录，无过滤时仅当前目录。"""
        if self._filter_min_rating == n:
            self._filter_min_rating = 0
        else:
            self._filter_min_rating = n
            if self._filter_pick:
                self._filter_pick = False
                self._btn_filter_pick.setChecked(False)
        for i, btn in enumerate(self._star_btns):
            btn.setChecked(i + 1 == self._filter_min_rating)
        if self._current_dir and os.path.isdir(self._current_dir):
            self.load_directory(self._current_dir, force_reload=True)
        else:
            self._apply_filter()

    def _on_focus_filter_changed(self, status: str) -> None:
        if self._filter_focus_status == status:
            self._filter_focus_status = ""
        else:
            self._filter_focus_status = status
        for key, btn in self._focus_filter_btns.items():
            btn.setChecked(key == self._filter_focus_status)
        if self._current_dir and os.path.isdir(self._current_dir):
            self.load_directory(self._current_dir, force_reload=True)
        else:
            self._apply_filter()

    def _apply_meta_to_tree_item(self, item: SortableTreeItem, meta: dict) -> None:
        title   = meta.get("title", "")
        color   = meta.get("color", "")
        rating  = meta.get("rating", 0)
        pick    = meta.get("pick", 0)
        city    = meta.get("city", "")
        state   = meta.get("state", "")
        country = meta.get("country", "")

        item.setText(_TREE_COL_TITLE, title);  item.setData(_TREE_COL_TITLE, _SortRole, title.lower())
        color_display = (_COLOR_LABEL_COLORS.get(color, ("", ""))[1] or color)
        item.setText(_TREE_COL_COLOR, color_display);  item.setData(_TREE_COL_COLOR, _SortRole, _COLOR_SORT_ORDER.get(color, 99))
        if color in _COLOR_LABEL_COLORS:
            hex_c, _ = _COLOR_LABEL_COLORS[color]
            item.setBackground(_TREE_COL_COLOR, QBrush(QColor(hex_c)))
            item.setForeground(_TREE_COL_COLOR, QBrush(QColor(
                "#333" if color in ("Yellow", "White") else "#fff"
            )))

        # 星级列：pick 旗标优先于星级显示
        # 排序键：精选=10 > 5星=5 > ... > 未标=0 > 排除=-1
        if pick == 1:
            star_text = "🏆"
            sort_val  = 10
        elif pick == -1:
            star_text = "🚫"
            sort_val  = -1
        else:
            star_text = "★" * rating if rating > 0 else ""
            sort_val  = rating
        item.setText(_TREE_COL_STAR, star_text); item.setData(_TREE_COL_STAR, _SortRole, sort_val)

        item.setText(_TREE_COL_SHARP, city);    item.setData(_TREE_COL_SHARP, _SortRole, city.lower())
        item.setText(_TREE_COL_AESTHETIC, state);   item.setData(_TREE_COL_AESTHETIC, _SortRole, state.lower())
        item.setText(_TREE_COL_FOCUS, country); item.setData(_TREE_COL_FOCUS, _SortRole, country.lower())
        focus_color = _FOCUS_STATUS_TEXT_COLORS.get(country, "")
        if focus_color:
            item.setForeground(_TREE_COL_FOCUS, QBrush(QColor(focus_color)))
        else:
            item.setForeground(_TREE_COL_FOCUS, QBrush())

    # ── 视图模式切换 ────────────────────────────────────────────────────────────
    def eventFilter(self, obj, event):
        tree_widget = getattr(self, "_tree_widget", None)
        tree_viewport = tree_widget.viewport() if tree_widget is not None else None
        list_widget = getattr(self, "_list_widget", None)
        list_viewport = list_widget.viewport() if list_widget is not None else None
        if event is not None and event.type() == _EventToolTip:
            if obj is tree_viewport and tree_widget is not None:
                idx = tree_widget.indexAt(event.pos())
                path = self._tree_path_from_index(idx) if idx.isValid() else ""
                if path:
                    tooltip = self._build_list_path_tooltip(path)
                    if tooltip:
                        QToolTip.showText(event.globalPos(), tooltip, tree_viewport)
                        return True
                QToolTip.hideText()
                try:
                    event.ignore()
                except Exception:
                    pass
                return True
            if obj is list_viewport and list_widget is not None:
                idx = self._find_thumb_index_for_tooltip(event.pos())
                path = self._thumb_path_from_index(idx) if idx.isValid() else ""
                if path:
                    tooltip = self._build_list_path_tooltip(path)
                    if tooltip:
                        QToolTip.showText(event.globalPos(), tooltip, list_viewport)
                        return True
                QToolTip.hideText()
                try:
                    event.ignore()
                except Exception:
                    pass
                return True
        if obj is list_viewport and event is not None:
            et = event.type()
            if et in (_EventResize, _EventShow):
                self._invalidate_visible_thumbnail_signature()
                self._schedule_visible_thumbnail_update()
        if (
            obj is tree_widget
            and event is not None
            and event.type() == _EventKeyPress
            and self._view_mode == self._MODE_LIST
            and tree_widget is not None
        ):
            key = event.key()
            if key in (_KeyUp, _KeyDown, _KeyLeft, _KeyRight):
                if not self._accept_key_navigation_step(event):
                    return True
                try:
                    self._selection_key_nav_auto_repeat = bool(event.isAutoRepeat())
                except Exception:
                    self._selection_key_nav_auto_repeat = False
                QTimer.singleShot(0, lambda: setattr(self, "_selection_key_nav_auto_repeat", False))
        if (
            obj is list_widget
            and event is not None
            and event.type() == _EventKeyPress
            and self._view_mode == self._MODE_THUMB
            and list_widget is not None
            and self._thumb_row_count() > 0
        ):
            key = event.key()
            if key not in (_KeyUp, _KeyDown, _KeyLeft, _KeyRight):
                return super().eventFilter(obj, event)
            if not self._accept_key_navigation_step(event):
                return True
            viewport = list_widget.viewport()
            grid = list_widget.gridSize()
            gw = max(1, grid.width())
            cols = max(1, viewport.rect().width() // gw)
            count = self._thumb_row_count()
            current_index = list_widget.currentIndex()
            idx = current_index.row() if current_index.isValid() else 0
            row, col = idx // cols, idx % cols
            new_idx = -1
            if key == _KeyUp and row > 0:
                new_idx = (row - 1) * cols + col
            elif key == _KeyDown:
                new_idx = (row + 1) * cols + col
                if new_idx >= count:
                    new_idx = -1
            elif key == _KeyLeft and idx > 0:
                new_idx = idx - 1
            elif key == _KeyRight and idx < count - 1:
                new_idx = idx + 1
            if new_idx >= 0 and new_idx < count:
                try:
                    fast_preview = bool(event.isAutoRepeat())
                except Exception:
                    fast_preview = False
                shift = _ShiftModifier and (event.modifiers() & _ShiftModifier)
                new_index = self._thumb_index_for_row(new_idx)
                if not new_index.isValid():
                    return True
                if shift:
                    sm = list_widget.selectionModel()
                    anchor = sm.anchorIndex().row() if sm and sm.anchorIndex().isValid() else idx
                    lo, hi = min(anchor, new_idx), max(anchor, new_idx)
                    list_widget.clearSelection()
                    for i in range(lo, hi + 1):
                        it = self._thumb_index_for_row(i)
                        if it.isValid() and sm is not None:
                            sm.select(it, _Select)
                    list_widget.setCurrentIndex(new_index)
                else:
                    list_widget.clearSelection()
                    list_widget.setCurrentIndex(new_index)
                    sm = list_widget.selectionModel()
                    if sm is not None:
                        sm.select(new_index, _SelectCurrent)
                path = self._thumb_path_from_index(list_widget.currentIndex())
                if path:
                    self._handle_selection_preview_request(
                        path,
                        fast_preview=fast_preview,
                        defer_full=fast_preview,
                    )
                return True
        return super().eventFilter(obj, event)

    def _ensure_thumb_viewport_timer(self) -> None:
        if self._thumb_viewport_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._update_visible_thumbnail_range)
        self._thumb_viewport_timer = timer

    def _invalidate_visible_thumbnail_signature(self) -> None:
        self._thumb_visible_signature = None
        self._thumb_visible_range = None

    def _build_visible_thumbnail_data_source(
        self,
        overscan_rows: int = 2,
    ) -> ThumbViewportRange | None:
        total_items = self._thumb_row_count()
        if self._view_mode != self._MODE_THUMB or total_items <= 0:
            self._thumb_visible_range = None
            return None
        viewport = self._list_widget.viewport()
        rect = viewport.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            self._thumb_visible_range = None
            return None

        # 首次切换到缩略图模式时，Qt 可能尚未完成 layout，此时滚动条最大值仍为 0。
        # 大目录如果在这个瞬间被误判成“全部可见”，会把整个目录都丢进缩略图队列。
        # 因此这里按当前 viewport 容量做一次保守估算；只有确实装得下时才视为全部可见。
        grid = self._list_widget.gridSize()
        # 首次切到缩略图模式时，Qt layout 可能尚未完成，滚动条最大值仍为 0。
        # 大目录如果在这一瞬间被误判成“全部可见”，会把整批文件直接丢进缩略图队列。
        # 这里按当前 viewport 容量做保守估算，只覆盖首屏附近。
        grid = self._list_widget.gridSize()
        grid = self._list_widget.gridSize()
        grid_w = max(1, grid.width())
        grid_h = max(1, grid.height())
        cols = max(1, rect.width() // grid_w)
        if self._list_widget.verticalScrollBar().maximum() <= 0:
            estimated_rows = max(1, (rect.height() + grid_h - 1) // grid_h)
            estimated_visible = cols * max(1, estimated_rows + max(0, overscan_rows) * 2)
            end_index = min(total_items - 1, max(0, estimated_visible - 1))
            entries: list[ThumbViewportEntry] = []
            for i in range(0, end_index + 1):
                path = self._thumb_list_model.path_for_row(i)
                if not path:
                    continue
                entries.append(ThumbViewportEntry(os.path.normpath(path), i))
            visible_range = ThumbViewportRange(
                thumb_size=self._thumb_size,
                start_row=0,
                end_row=end_index,
                grid_width=grid_w,
                grid_height=grid_h,
                total_items=total_items,
                entries=tuple(entries),
            )
            self._thumb_visible_range = visible_range
            return visible_range

        margin = 8
        sample_points = [
            QPoint(rect.left() + margin, rect.top() + margin),
            QPoint(rect.center().x(), rect.top() + margin),
            QPoint(max(rect.left() + margin, rect.right() - margin), rect.top() + margin),
            QPoint(rect.left() + margin, max(rect.top() + margin, rect.bottom() - margin)),
            QPoint(rect.center().x(), max(rect.top() + margin, rect.bottom() - margin)),
            QPoint(max(rect.left() + margin, rect.right() - margin), max(rect.top() + margin, rect.bottom() - margin)),
        ]
        rows: list[int] = []
        for pt in sample_points:
            idx = self._list_widget.indexAt(pt)
            if idx.isValid():
                rows.append(idx.row())
        if not rows:
            self._thumb_visible_range = None
            return None

        overscan = max(0, overscan_rows) * cols
        start = max(0, min(rows) - overscan)
        end = min(total_items - 1, max(rows) + overscan)

        entries: list[ThumbViewportEntry] = []
        for i in range(start, end + 1):
            path = self._thumb_list_model.path_for_row(i)
            if not path:
                continue
            entries.append(ThumbViewportEntry(os.path.normpath(path), i))

        visible_range = ThumbViewportRange(
            thumb_size=self._thumb_size,
            start_row=start,
            end_row=end,
            grid_width=grid_w,
            grid_height=grid_h,
            total_items=total_items,
            entries=tuple(entries),
        )
        self._thumb_visible_range = visible_range
        return visible_range

    def _collect_missing_visible_thumbnail_paths(
        self,
        visible_range: ThumbViewportRange | None = None,
    ) -> list[str]:
        requested_paths: list[str] = []
        seen: set[str] = set()
        range_data = visible_range if visible_range is not None else self._thumb_visible_range
        for entry in (range_data.entries if range_data is not None else ()):
            norm = entry.path
            if norm in seen:
                continue
            seen.add(norm)
            if self._thumb_list_model.has_current_pixmap(norm, self._thumb_size):
                continue
            requested_paths.append(norm)
        return requested_paths

    def _schedule_visible_thumbnail_update(self, *_args) -> None:
        if self._view_mode != self._MODE_THUMB:
            return
        self._thumb_profile_add("schedule_calls", 1)
        self._ensure_thumb_viewport_timer()
        if self._thumb_viewport_timer is not None:
            self._thumb_viewport_timer.start(25)

    def _collect_prefetch_paths(
        self,
        visible_range: "ThumbViewportRange",
        prefetch_rows: int = 6,
    ) -> list[str]:
        """Return paths just outside *visible_range* for background prefetching.

        The result is ordered so items closest to the visible area come first
        (top-adjacent rows before bottom-adjacent rows, alternating), giving
        the best chance of being ready before the user scrolls to them.
        """
        total = self._thumb_row_count()
        if total == 0:
            return []
        cols = max(1, self._list_widget.viewport().rect().width() // max(1, visible_range.grid_width))
        buffer = cols * prefetch_rows
        pre_start = max(0, visible_range.start_row - buffer)
        pre_end   = min(total - 1, visible_range.end_row + buffer)

        visible_set = {e.path for e in visible_range.entries}
        result: list[str] = []
        seen: set[str]    = set()

        # Alternate between rows above and below visible area so nearest items
        # are submitted first regardless of scroll direction.
        above = list(range(visible_range.start_row - 1, pre_start - 1, -1))
        below = list(range(visible_range.end_row   + 1, pre_end   + 1))
        for row in (r for pair in zip(above, below) for r in pair):
            path = self._thumb_list_model.path_for_row(row)
            if not path:
                continue
            norm = os.path.normpath(path)
            if norm in visible_set or norm in seen:
                continue
            seen.add(norm)
            result.append(norm)
        # tail: whichever sequence was longer
        for row in (above[len(below):] + below[len(above):]):
            path = self._thumb_list_model.path_for_row(row)
            if not path:
                continue
            norm = os.path.normpath(path)
            if norm in visible_set or norm in seen:
                continue
            seen.add(norm)
            result.append(norm)
        return result

    @staticmethod
    def _limit_prefetch_paths(
        prefetch_paths: list[str],
        *,
        visible_count: int,
        missing_count: int,
    ) -> list[str]:
        if not prefetch_paths:
            return []
        if missing_count > 0:
            limit = max(8, min(24, max(8, visible_count // 2)))
        else:
            limit = max(24, min(72, max(visible_count, 24)))
        return prefetch_paths[:limit]

    def _collect_materialized_thumbnail_paths(
        self,
        visible_range: "ThumbViewportRange",
        extra_rows: int = 2,
    ) -> set[str]:
        paths = {entry.path for entry in visible_range.entries}
        for norm in self._collect_prefetch_paths(visible_range, prefetch_rows=extra_rows):
            paths.add(norm)
        return paths

    def _update_visible_thumbnail_range(self) -> None:
        if self._view_mode != self._MODE_THUMB:
            return
        profile_started_at = _time.perf_counter()
        visible_range = self._build_visible_thumbnail_data_source()
        if visible_range is None or not visible_range.entries:
            return

        # Always evict off-screen QPixmaps first.  Without explicit eviction,
        # materialized pixmaps for scrolled-away rows would accumulate in RAM.
        # The ThumbnailMemoryCache (QImage, LRU-bounded) is unaffected and
        # provides fast re-population on scroll-back.
        evicted_count = self._evict_offscreen_item_pixmaps(visible_range)
        cached_fill_count = self._apply_cached_thumbs_to_items(visible_range)

        missing_visible = self._collect_missing_visible_thumbnail_paths(visible_range)
        same_signature  = visible_range.signature == self._thumb_visible_signature
        self._thumb_visible_signature = visible_range.signature

        loader = self._thumbnail_loader
        loader_running = loader is not None and loader.isRunning()

        if same_signature:
            if not missing_visible:
                self._thumb_profile_add("viewport_updates", 1)
                self._thumb_profile_stats["last_visible_start"] = float(visible_range.start_row)
                self._thumb_profile_stats["last_visible_end"] = float(visible_range.end_row)
                self._thumb_profile_stats["last_visible_count"] = float(len(visible_range.entries))
                self._thumb_profile_stats["last_missing_count"] = 0.0
                self._thumb_profile_stats["last_prefetch_count"] = 0.0
                return
            if loader_running:
                self._thumb_profile_add("viewport_updates", 1)
                self._thumb_profile_add("visible_items_total", len(visible_range.entries))
                self._thumb_profile_add("missing_visible_total", len(missing_visible))
                self._thumb_profile_stats["last_visible_start"] = float(visible_range.start_row)
                self._thumb_profile_stats["last_visible_end"] = float(visible_range.end_row)
                self._thumb_profile_stats["last_visible_count"] = float(len(visible_range.entries))
                self._thumb_profile_stats["last_missing_count"] = float(len(missing_visible))
                self._thumb_profile_stats["last_prefetch_count"] = 0.0
                self._report_thumb_profile(
                    "viewport_wait_loader",
                    force=len(missing_visible) >= max(12, len(visible_range.entries)),
                    extra=f"same=1 running=1 update_ms={(_time.perf_counter() - profile_started_at) * 1000.0:.1f}",
                )
                # Same viewport, loader still running — it is already handling
                # the missing items; nothing to do.
                return

        if not missing_visible and not loader_running:
            # All visible items are cached; still worth (re-)enqueueing prefetch
            # so background loading continues after a fast scroll.
            pass

        _log.debug(
            "[_update_visible_thumbnail_range] visible rows=%s-%s items=%s missing=%s size=%s",
            visible_range.start_row,
            visible_range.end_row,
            len(visible_range.entries),
            len(missing_visible),
            self._thumb_size,
        )

        raw_prefetch_paths = self._collect_prefetch_paths(visible_range)
        prefetch_paths = self._limit_prefetch_paths(
            raw_prefetch_paths,
            visible_count=len(visible_range.entries),
            missing_count=len(missing_visible),
        )
        self._thumb_profile_add("viewport_updates", 1)
        self._thumb_profile_add("visible_items_total", len(visible_range.entries))
        self._thumb_profile_add("missing_visible_total", len(missing_visible))
        self._thumb_profile_add("prefetch_total", len(prefetch_paths))
        self._thumb_profile_add("cache_fill_total", cached_fill_count)
        self._thumb_profile_add("evicted_total", evicted_count)
        self._thumb_profile_stats["last_visible_start"] = float(visible_range.start_row)
        self._thumb_profile_stats["last_visible_end"] = float(visible_range.end_row)
        self._thumb_profile_stats["last_visible_count"] = float(len(visible_range.entries))
        self._thumb_profile_stats["last_missing_count"] = float(len(missing_visible))
        self._thumb_profile_stats["last_prefetch_count"] = float(len(prefetch_paths))

        if loader_running:
            # ── Loader already running: reprioritize without stop/restart ────
            # Promote newly-visible items to the front of the queue so they
            # are processed before any pending prefetch.
            self._thumb_profile_add("loader_reprioritize", 1)
            loader.replace_pending(missing_visible, prefetch_paths)
        else:
            # ── No loader running: start fresh ───────────────────────────────
            if missing_visible or prefetch_paths:
                self._start_thumbnail_loader(missing_visible, prefetch_paths)
        update_elapsed_s = _time.perf_counter() - profile_started_at
        _record_thumb_bottleneck_sample("viewport_ms", update_elapsed_s * 1000.0)
        if (
            len(missing_visible) >= max(12, len(visible_range.entries))
            or update_elapsed_s >= 0.020
            or (_THUMB_PROFILE_VERBOSE and (cached_fill_count > 0 or evicted_count > 0))
        ):
            self._report_thumb_profile(
                "viewport",
                force=True,
                extra=f"same={int(same_signature)} running={int(loader_running)} update_ms={update_elapsed_s * 1000.0:.1f}",
            )
        else:
            self._report_thumb_profile("viewport")

    def _set_view_mode(self, mode: int) -> None:
        if self._view_mode == mode and self._stack.currentIndex() == (0 if mode == self._MODE_LIST else 1):
            self._update_selection_status()
            return
        selected_paths = self._active_view_selected_paths()
        current_path = self._active_view_current_path()
        if not selected_paths and current_path:
            selected_paths = [current_path]
        self._view_mode = mode
        self._btn_list.setChecked(mode == self._MODE_LIST)
        self._btn_thumb.setChecked(mode == self._MODE_THUMB)
        self._stack.setCurrentIndex(0 if mode == self._MODE_LIST else 1)
        self._update_size_controls()
        self._invalidate_visible_thumbnail_signature()
        if mode == self._MODE_THUMB:
            self._update_thumb_display()
            if self._thumb_model_dirty:
                self._start_thumb_model_population(
                    resume=bool(self._thumb_model_pending_paths) and self._thumb_model_pending_index > 0
                )
            self._schedule_visible_thumbnail_update()
        else:
            self._pause_thumb_model_population()
            self._stop_thumbnail_loader()
            if self._tree_view_dirty:
                self._rebuild_tree_items()
            # 切换到列表视图时显式恢复排序状态，避免因隐藏时列头状态丢失导致按序号列
            # （所有项 _SortRole 均为 0）排序产生不稳定顺序、列表项跳变
            if self._tree_widget.isSortingEnabled() and self._tree_row_count() > 0:
                hdr = self._tree_widget.header()
                try:
                    hdr.blockSignals(True)
                    hdr.setSortIndicator(self._tree_last_sort_column, self._tree_last_sort_order)
                finally:
                    hdr.blockSignals(False)
                self._tree_widget.sortByColumn(self._tree_last_sort_column, self._tree_last_sort_order)
                self._refresh_tree_row_numbers()
        if selected_paths:
            self.set_pending_selection(selected_paths, current_path=current_path)
        else:
            self._update_selection_status()

    def _update_size_controls(self) -> None:
        enabled = self._view_mode == self._MODE_THUMB
        self._size_slider.setEnabled(enabled)
        self._size_label.setEnabled(enabled)

    def _sync_key_navigation_fps_combo(self) -> None:
        combo = self._combo_key_navigation_fps
        if combo is None:
            return
        index = combo.findData(self._key_navigation_fps)
        if index < 0:
            index = combo.findData(24)
        if index < 0 and combo.count() > 0:
            index = 0
        if index < 0:
            return
        try:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _set_key_navigation_fps(self, fps: int, *, persist: bool) -> None:
        try:
            value = int(fps)
        except Exception:
            value = 24
        if value not in KEY_NAVIGATION_FPS_OPTIONS:
            value = 24
        self._key_navigation_fps = value
        self._key_navigation_last_step_at = 0.0
        self._sync_key_navigation_fps_combo()
        if not persist:
            return
        options = get_runtime_user_options()
        if int(options.get("key_navigation_fps", 24)) == value:
            return
        options["key_navigation_fps"] = value
        normalized = save_user_options(options)
        apply_runtime_user_options(normalized)

    def _on_key_navigation_fps_changed(self, index: int) -> None:
        combo = self._combo_key_navigation_fps
        if combo is None:
            return
        value = combo.itemData(index)
        if value is None:
            value = combo.currentData()
        if value is None:
            return
        self._set_key_navigation_fps(value, persist=True)

    def _accept_key_navigation_step(self, event) -> bool:
        try:
            auto_repeat = bool(event.isAutoRepeat())
        except Exception:
            auto_repeat = False
        now = _time.perf_counter()
        if not auto_repeat:
            self._key_navigation_last_step_at = now
            return True
        fps = max(1, int(self._key_navigation_fps))
        interval_s = 1.0 / float(fps)
        if self._key_navigation_last_step_at > 0.0 and (now - self._key_navigation_last_step_at) < interval_s:
            return False
        self._key_navigation_last_step_at = now
        return True

    def apply_user_options(self) -> None:
        self._thumb_loader_workers = _thumbnail_loader_worker_count()
        self._set_key_navigation_fps(get_key_navigation_fps(), persist=False)
        self._invalidate_visible_thumbnail_signature()
        self._stop_thumbnail_loader()
        self._stop_persistent_thumb_cache_worker()
        if self._view_mode == self._MODE_THUMB:
            self._thumb_list_model.clear_all_pixmaps()
            self._update_thumb_display()
            self._schedule_visible_thumbnail_update()
        if self._all_files:
            self._schedule_persistent_thumb_cache_build(self._all_files)
        else:
            self._update_persistent_thumb_progress_widget()

    def _on_size_slider_changed(self, value: int) -> None:
        size = _THUMB_SIZE_STEPS[max(0, min(len(_THUMB_SIZE_STEPS) - 1, value))]
        self._size_label.setText(f"{size}px")
        if self._thumb_size != size:
            self._thumb_size = size
            self._invalidate_visible_thumbnail_signature()
            if self._view_mode == self._MODE_THUMB:
                self._thumb_list_model.clear_all_pixmaps()
                self._update_thumb_display()
                self._schedule_visible_thumbnail_update()

    def _update_thumb_display(self) -> None:
        s = self._thumb_size
        self._list_widget.setIconSize(QSize(s, s))
        cell_w = s + 32
        cell_h = s + 46
        self._list_widget.setGridSize(QSize(cell_w, cell_h))
        self._list_widget.setSpacing(8)
        self._list_widget.doItemsLayout()

    def _start_thumbnail_loader(
        self,
        visible_paths: list[str] | None = None,
        prefetch_paths: list[str] | None = None,
    ) -> None:
        """Stop any running loader, create a fresh one, enqueue *visible_paths*
        at PRIORITY_VISIBLE and *prefetch_paths* at PRIORITY_PREFETCH, then start it.

        If *visible_paths* is None the current visible range is used.
        If there is nothing to load the call is a no-op.
        """
        _log.debug("[_start_thumbnail_loader] START")
        if self._view_mode != self._MODE_THUMB:
            _log.debug("[_start_thumbnail_loader] skip: not in thumb mode")
            return

        # Build visible list if not supplied
        if visible_paths is None:
            if self._thumb_visible_range is None:
                self._build_visible_thumbnail_data_source()
            visible_paths = [
                e.path
                for e in (self._thumb_visible_range.entries if self._thumb_visible_range else ())
            ]

        # Filter to items that actually need loading
        requested_visible: list[str] = []
        seen: set[str] = set()
        for path in visible_paths or []:
            norm = os.path.normpath(path)
            if norm in seen:
                continue
            seen.add(norm)
            if not self._thumb_index_for_path(norm).isValid():
                continue
            if self._thumb_list_model.has_current_pixmap(norm, self._thumb_size):
                continue
            requested_visible.append(norm)

        if not requested_visible and not prefetch_paths:
            _log.debug("[_start_thumbnail_loader] nothing to load")
            return

        self._thumb_profile_add("loader_starts", 1)
        self._stop_thumbnail_loader()
        self._thumb_loader_workers = _thumbnail_loader_worker_count()

        cache_stats = self._thumb_memory_cache.stats()
        _log.debug(
            "[_start_thumbnail_loader] visible=%s prefetch=%s workers=%s cache_mb=%.1f",
            len(requested_visible),
            len(prefetch_paths or []),
            self._thumb_loader_workers,
            float(cache_stats.get("bytes", 0)) / (1024.0 * 1024.0),
        )
        if self._thumb_profile_enabled:
            _log.info(
                "[THUMB_PROFILE][loader.request] token=%s visible=%s prefetch=%s rows=%s-%s size=%s cache_mb=%.1f",
                self._thumb_request_token + 1,
                len(requested_visible),
                len(prefetch_paths or []),
                self._thumb_visible_range.start_row if self._thumb_visible_range is not None else -1,
                self._thumb_visible_range.end_row if self._thumb_visible_range is not None else -1,
                self._thumb_size,
                float(cache_stats.get("bytes", 0)) / (1024.0 * 1024.0),
            )

        preview_base_dir = self._report_root_dir or self._current_dir
        self._thumb_request_token += 1
        loader = ThumbnailLoader(
            self._thumb_size,
            self._thumb_request_token,
            report_cache=self._report_cache,
            current_dir=preview_base_dir,
            thumb_cache=self._thumb_memory_cache,
        )
        if requested_visible:
            loader.enqueue(requested_visible, priority=ThumbnailLoader.PRIORITY_VISIBLE)
        if prefetch_paths:
            loader.enqueue(prefetch_paths, priority=ThumbnailLoader.PRIORITY_PREFETCH)
        loader.set_desired_paths(requested_visible, prefetch_paths)

        loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        loader.finished.connect(self._schedule_visible_thumbnail_update)
        self._thumbnail_loader = loader
        loader.start()
        _log.debug("[_start_thumbnail_loader] END loader.started")

    def _stop_thumbnail_loader(self) -> None:
        if self._thumb_profile_enabled and self._thumbnail_loader is not None:
            snap = self._thumbnail_loader.profile_snapshot()
            self._report_thumb_profile(
                "loader_stop",
                force=True,
                extra=f"queue={int(snap.get('queue_size', 0))} done={int(snap.get('completed', 0))}",
            )
        self._thumb_request_token += 1
        if self._thumbnail_loader:
            self._detach_loader(
                self._thumbnail_loader,
                self._thumbnail_loader.thumbnail_ready,
                self._on_thumbnail_ready,
            )
            self._thumbnail_loader = None
        if self._thumb_apply_timer is not None and self._thumb_apply_timer.isActive():
            self._thumb_apply_timer.stop()
        self._thumb_pending_batch.clear()
        self._thumb_profile_ready_received_at.clear()
        self._pending_loaders = [l for l in self._pending_loaders if l.isRunning()]

    def _start_metadata_loader(self, paths: list) -> None:
        report_cache_for_meta = self._report_full_cache or self._report_cache
        _log.info(
            "[_start_metadata_loader] 启动元数据加载 EXIF paths=%s report_cache=%s full_report_cache=%s",
            len(paths),
            len(self._report_cache),
            len(self._report_full_cache or {}),
        )
        _log.info("[_start_metadata_loader] START paths=%s", len(paths))
        self._stop_metadata_loader()
        total = len(paths)
        if total <= 0:
            _log.info("[_start_metadata_loader] no paths, return")
            return
        self._meta_progress.setMaximum(total)
        self._meta_progress.setValue(0)
        self._meta_progress.show()
        metadata_base_dir = self._report_root_dir or self._current_dir
        loader = MetadataLoader(
            paths,
            report_cache=report_cache_for_meta,
            current_dir=metadata_base_dir,
        )
        loader.progress_updated.connect(self._on_metadata_progress)
        loader.all_metadata_ready.connect(self._on_metadata_ready)
        self._metadata_loader = loader
        loader.start()
        _log.info("[_start_metadata_loader] MetadataLoader 已启动，EXIF 将来自 DB(report_cache) 或 read_batch(exiftool/XMP)")

    def _stop_metadata_loader(self) -> None:
        if self._metadata_loader:
            self._detach_loader(
                self._metadata_loader,
                self._metadata_loader.all_metadata_ready,
                self._on_metadata_ready,
            )
            try:
                self._metadata_loader.progress_updated.disconnect(
                    self._on_metadata_progress
                )
            except Exception:
                pass
            self._metadata_loader = None
        self._meta_progress.hide()

    def _ensure_deferred_file_selected_timer(self) -> None:
        if self._deferred_file_selected_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._commit_deferred_file_selected)
        self._deferred_file_selected_timer = timer

    def _cancel_deferred_file_selected(self) -> None:
        if self._deferred_file_selected_timer is not None and self._deferred_file_selected_timer.isActive():
            self._deferred_file_selected_timer.stop()
        self._deferred_file_selected_path = ""
        self._selection_key_nav_auto_repeat = False

    def _schedule_deferred_file_selected(self, path: str) -> None:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        self._deferred_file_selected_path = norm_path
        self._ensure_deferred_file_selected_timer()
        self._deferred_file_selected_timer.start(_FAST_PREVIEW_COMMIT_DELAY_MS)

    def _commit_deferred_file_selected(self) -> None:
        path = self._deferred_file_selected_path
        self._deferred_file_selected_path = ""
        self._selection_key_nav_auto_repeat = False
        if path:
            self._emit_file_selected_for_path(path)

    def _emit_fast_preview_for_path(self, path: str) -> None:
        if not path:
            return
        self._selected_display_path = os.path.normpath(path)
        resolved_path = self._resolve_source_path_for_action(path)
        if not resolved_path or not os.path.isfile(resolved_path):
            self._request_actual_path_lookup(path)
        self.file_fast_preview_requested.emit(resolved_path or path)

    def _handle_selection_preview_request(
        self,
        path: str,
        *,
        fast_preview: bool = False,
        defer_full: bool = False,
    ) -> None:
        if not path:
            return
        if fast_preview:
            self._emit_fast_preview_for_path(path)
            if defer_full:
                self._schedule_deferred_file_selected(path)
            else:
                self._cancel_deferred_file_selected()
                self._emit_file_selected_for_path(path)
            return
        self._cancel_deferred_file_selected()
        self._emit_file_selected_for_path(path)

    def _ensure_persistent_thumb_cache_timer(self) -> None:
        if self._persistent_thumb_cache_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._start_persistent_thumb_cache_worker)
        self._persistent_thumb_cache_timer = timer

    def _hide_persistent_thumb_progress_if_idle(self) -> None:
        if self._persistent_thumb_cache_worker is not None:
            return
        if self._persistent_thumb_cache_total > 0 and self._persistent_thumb_cache_done < self._persistent_thumb_cache_total:
            return
        self._persistent_thumb_progress.hide()

    def _update_persistent_thumb_progress_widget(self) -> None:
        total = max(0, int(self._persistent_thumb_cache_total))
        if total <= 0:
            self._persistent_thumb_progress.hide()
            self._persistent_thumb_progress.setToolTip("")
            return
        done = min(max(0, int(self._persistent_thumb_cache_done)), total)
        self._persistent_thumb_progress.setMaximum(max(1, total))
        self._persistent_thumb_progress.setValue(done)
        self._persistent_thumb_progress.setFormat(f"小缩略图 {done}/{total}")
        sizes = _persistent_thumb_cache_sizes()
        cache_dirs = [
            _persistent_thumb_cache_dir(self._persistent_thumb_cache_base_dir, size)
            for size in sizes
        ]
        current_name = os.path.basename(self._persistent_thumb_cache_current_path) if self._persistent_thumb_cache_current_path else "(waiting)"
        tooltip = (
            f"后台持久化小缩略图缓存\n"
            f"- 目录: {self._persistent_thumb_cache_base_dir or '(none)'}\n"
            f"- 缓存目录: {'; '.join(cache_dirs) if cache_dirs else '(none)'}\n"
            f"- 尺寸层级: {', '.join(str(size) for size in sizes) or '(none)'}\n"
            f"- 生成线程: {_persistent_thumb_cache_worker_count()}\n"
            f"- 进度: {done}/{total}\n"
            f"- 新生成: {self._persistent_thumb_cache_generated}\n"
            f"- 已跳过: {self._persistent_thumb_cache_skipped}\n"
            f"- 失败: {self._persistent_thumb_cache_failed}\n"
            f"- 当前: {current_name}"
        )
        self._persistent_thumb_progress.setToolTip(tooltip)
        self._persistent_thumb_progress.show()

    def _schedule_persistent_thumb_cache_build(self, paths: list[str] | None) -> None:
        base_dir = self._report_root_dir or self._current_dir
        pending_paths = ThumbnailLoader._normalize_unique_paths(paths or [])
        self._persistent_thumb_cache_pending_paths = pending_paths
        self._persistent_thumb_cache_base_dir = base_dir or ""
        self._persistent_thumb_cache_generated = 0
        self._persistent_thumb_cache_skipped = 0
        self._persistent_thumb_cache_failed = 0
        self._persistent_thumb_cache_total = len(pending_paths)
        self._persistent_thumb_cache_done = 0
        self._persistent_thumb_cache_current_path = ""
        if not pending_paths or not self._persistent_thumb_cache_base_dir:
            self._update_persistent_thumb_progress_widget()
            return
        self._update_persistent_thumb_progress_widget()
        self._ensure_persistent_thumb_cache_timer()
        self._persistent_thumb_cache_timer.start(_PERSISTENT_THUMB_CACHE_START_DELAY_MS)

    def _start_persistent_thumb_cache_worker(self) -> None:
        if self._background_shutdown_started:
            return
        if self._persistent_thumb_cache_worker is not None:
            return
        if not self._persistent_thumb_cache_pending_paths or not self._persistent_thumb_cache_base_dir:
            self._update_persistent_thumb_progress_widget()
            return
        loader = self._thumbnail_loader
        if loader is not None and loader.isRunning():
            snap = loader.profile_snapshot()
            queue_size = int(snap.get("queue_size", 0))
            inflight = max(
                0,
                int(snap.get("submitted", 0)) - int(snap.get("completed", 0)),
            )
            if queue_size > 0 or inflight > 0:
                self._persistent_thumb_cache_current_path = "(waiting for visible thumbs)"
                self._update_persistent_thumb_progress_widget()
                self._ensure_persistent_thumb_cache_timer()
                self._persistent_thumb_cache_timer.start(_PERSISTENT_THUMB_CACHE_START_DELAY_MS)
                return
        worker = PersistentThumbCacheWorker(
            self._persistent_thumb_cache_pending_paths,
            self._persistent_thumb_cache_base_dir,
            report_cache=self._report_full_cache or self._report_cache or {},
            sizes=_persistent_thumb_cache_sizes(),
            worker_count=_persistent_thumb_cache_worker_count(),
            parent=self,
        )
        worker.progress_updated.connect(self._on_persistent_thumb_cache_progress)
        worker.finished_summary.connect(self._on_persistent_thumb_cache_finished)
        self._persistent_thumb_cache_worker = worker
        worker.start()
        _log.info(
            "[_start_persistent_thumb_cache_worker] dir=%r total=%s sizes=%s workers=%s",
            self._persistent_thumb_cache_base_dir,
            len(self._persistent_thumb_cache_pending_paths),
            _persistent_thumb_cache_sizes(),
            _persistent_thumb_cache_worker_count(),
        )

    def _stop_persistent_thumb_cache_worker(self) -> None:
        if self._persistent_thumb_cache_timer is not None and self._persistent_thumb_cache_timer.isActive():
            self._persistent_thumb_cache_timer.stop()
        worker = self._persistent_thumb_cache_worker
        if worker is not None:
            try:
                worker.progress_updated.disconnect(self._on_persistent_thumb_cache_progress)
            except Exception:
                pass
            self._detach_loader(
                worker,
                worker.finished_summary,
                self._on_persistent_thumb_cache_finished,
            )
            self._persistent_thumb_cache_worker = None
        self._persistent_thumb_cache_pending_paths = []
        self._persistent_thumb_cache_base_dir = ""
        self._persistent_thumb_cache_generated = 0
        self._persistent_thumb_cache_skipped = 0
        self._persistent_thumb_cache_failed = 0
        self._persistent_thumb_cache_total = 0
        self._persistent_thumb_cache_done = 0
        self._persistent_thumb_cache_current_path = ""
        self._update_persistent_thumb_progress_widget()

    def _on_persistent_thumb_cache_progress(
        self,
        done: int,
        total: int,
        generated: int,
        skipped: int,
        failed: int,
        current_path: str,
    ) -> None:
        self._persistent_thumb_cache_done = max(0, int(done))
        self._persistent_thumb_cache_total = max(0, int(total))
        self._persistent_thumb_cache_generated = max(0, int(generated))
        self._persistent_thumb_cache_skipped = max(0, int(skipped))
        self._persistent_thumb_cache_failed = max(0, int(failed))
        self._persistent_thumb_cache_current_path = os.path.normpath(current_path) if current_path else ""
        self._update_persistent_thumb_progress_widget()

    def _on_persistent_thumb_cache_finished(
        self,
        done: int,
        total: int,
        generated: int,
        skipped: int,
        failed: int,
    ) -> None:
        self._persistent_thumb_cache_worker = None
        self._persistent_thumb_cache_done = max(0, int(done))
        self._persistent_thumb_cache_total = max(0, int(total))
        self._persistent_thumb_cache_generated = max(0, int(generated))
        self._persistent_thumb_cache_skipped = max(0, int(skipped))
        self._persistent_thumb_cache_failed = max(0, int(failed))
        self._persistent_thumb_cache_pending_paths = []
        self._update_persistent_thumb_progress_widget()
        QTimer.singleShot(1500, self._hide_persistent_thumb_progress_if_idle)

    def _stop_actual_path_lookup_workers(self) -> None:
        if not self._path_lookup_workers and not self._path_lookup_pending:
            return
        workers = self._path_lookup_workers
        self._path_lookup_workers = []
        self._path_lookup_pending.clear()
        for worker in workers:
            try:
                worker.resolved.disconnect(self._on_actual_path_lookup_resolved)
            except Exception:
                pass
            worker.requestInterruption()

    def _detach_loader(self, loader, signal, slot) -> None:
        loader.stop()
        try:
            signal.disconnect(slot)
        except Exception:
            pass
        self._pending_loaders.append(loader)
        try:
            loader.finished.connect(
                lambda ldr=loader: (
                    self._pending_loaders.remove(ldr)
                    if ldr in self._pending_loaders else None
                )
            )
        except Exception:
            pass

    def _stop_all_loaders(self) -> None:
        self._cancel_deferred_file_selected()
        self._stop_pending_meta_apply()
        self._stop_thumbnail_loader()
        self._stop_metadata_loader()
        self._stop_persistent_thumb_cache_worker()
        self._stop_actual_path_lookup_workers()

    def _shutdown_background_work(self) -> None:
        if self._background_shutdown_started:
            return
        self._background_shutdown_started = True

        directory_worker = self._directory_scan_worker
        lookup_workers = list(self._path_lookup_workers)
        active_threads = [
            worker
            for worker in (
                self._thumbnail_loader,
                self._metadata_loader,
                self._persistent_thumb_cache_worker,
                directory_worker,
            )
            if worker is not None
        ]
        active_threads.extend(lookup_workers)
        active_threads.extend(self._pending_loaders)

        self._pause_thumb_model_population()
        self._stop_all_loaders()
        self._stop_directory_scan_worker()

        wait_threads = []
        seen: set[int] = set()
        for worker in active_threads + self._pending_loaders:
            if worker is None:
                continue
            key = id(worker)
            if key in seen:
                continue
            seen.add(key)
            wait_threads.append(worker)

        for worker in wait_threads:
            try:
                if worker.isRunning():
                    worker.wait(2500)
            except Exception:
                pass
        _log_thumb_bottleneck_summary()
        _shutdown_thumb_disk_writer(wait=True)

    def closeEvent(self, event) -> None:
        self._shutdown_background_work()
        super().closeEvent(event)

    def _ensure_meta_apply_timer(self) -> None:
        if self._meta_apply_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(False)
        timer.timeout.connect(self._apply_meta_batch_tick)
        self._meta_apply_timer = timer

    def _stop_pending_meta_apply(self) -> None:
        if self._meta_apply_timer is not None and self._meta_apply_timer.isActive():
            self._meta_apply_timer.stop()
        self._meta_apply_items = []
        self._meta_apply_index = 0
        self._meta_apply_total = 0
        self._meta_apply_started_at = 0.0
        self._meta_apply_loop_started_at = 0.0
        self._meta_apply_tree_hits = 0
        self._meta_apply_list_hits = 0
        self._meta_apply_needs_filter = False
        self._set_tree_header_fast_mode(False)

    def _set_tree_header_fast_mode(self, enabled: bool) -> None:
        """批量更新期间临时关闭 ResizeToContents，避免 O(N^2) 级重算。"""
        if enabled == self._tree_header_fast_mode:
            return
        hdr = self._tree_widget.header()
        try:
            if enabled:
                for col in (_TREE_COL_TITLE, _TREE_COL_COLOR, _TREE_COL_STAR, _TREE_COL_SHARP, _TREE_COL_AESTHETIC, _TREE_COL_FOCUS):
                    hdr.setSectionResizeMode(col, _ResizeInteractive)
                self._tree_header_fast_mode = True
            else:
                hdr.setSectionResizeMode(_TREE_COL_SEQ, _ResizeInteractive)
                hdr.setSectionResizeMode(_TREE_COL_NAME, _ResizeInteractive)
                hdr.setSectionResizeMode(_TREE_COL_TITLE, _ResizeToContents)
                hdr.setSectionResizeMode(_TREE_COL_COLOR, _ResizeToContents)
                hdr.setSectionResizeMode(_TREE_COL_STAR, _ResizeToContents)
                hdr.setSectionResizeMode(_TREE_COL_SHARP, _ResizeToContents)
                hdr.setSectionResizeMode(_TREE_COL_AESTHETIC, _ResizeToContents)
                hdr.setSectionResizeMode(_TREE_COL_FOCUS, _ResizeToContents)
                self._tree_header_fast_mode = False
        except Exception:
            pass

    def _refresh_tree_row_numbers(self) -> None:
        self._tree_widget.viewport().update()

    def refresh_row_numbers(self) -> None:
        """公开的列表编号刷新入口，供通用/业务列表在增删行后统一调用。"""
        self._refresh_tree_row_numbers()

    def _on_tree_sort_indicator_changed(self, column: int, order) -> None:
        if column == _TREE_COL_SEQ:
            hdr = self._tree_widget.header()
            try:
                hdr.blockSignals(True)
                hdr.setSortIndicator(self._tree_last_sort_column, self._tree_last_sort_order)
            finally:
                hdr.blockSignals(False)
            self._tree_widget.sortByColumn(self._tree_last_sort_column, self._tree_last_sort_order)
            QTimer.singleShot(0, self._refresh_tree_row_numbers)
            return
        self._tree_last_sort_column = column
        self._tree_last_sort_order = order
        QTimer.singleShot(0, self._refresh_tree_row_numbers)

    def _order_meta_items_by_file_list(self, meta_dict: dict) -> list:
        ordered: list = []
        seen: set = set()
        preferred = self._filtered_files or self._all_files
        for p in preferred:
            norm = os.path.normpath(p)
            if norm in meta_dict:
                ordered.append((norm, meta_dict[norm]))
                seen.add(norm)
        for norm, meta in meta_dict.items():
            if norm in seen:
                continue
            ordered.append((norm, meta))
        return ordered

    def _start_meta_apply(self, meta_dict: dict) -> None:
        self._stop_pending_meta_apply()
        self._ensure_meta_apply_timer()
        self._meta_apply_items = self._order_meta_items_by_file_list(meta_dict)
        self._meta_apply_total = len(self._meta_apply_items)
        self._meta_apply_index = 0
        self._meta_apply_tree_hits = 0
        self._meta_apply_list_hits = 0
        self._meta_apply_started_at = _time.perf_counter()
        self._meta_apply_loop_started_at = self._meta_apply_started_at
        self._meta_apply_needs_filter = bool(self._filter_pick or self._filter_min_rating > 0 or self._filter_focus_status)

        self._set_tree_header_fast_mode(True)
        self._tree_widget.setSortingEnabled(False)
        self._meta_progress.setMaximum(max(1, self._meta_apply_total))
        self._meta_progress.setValue(0)
        self._meta_progress.show()
        _log.info(
            "[STAT][_on_metadata_ready] apply_meta begin tree_items=%s list_items=%s batch=%s",
            self._tree_source_row_count(),
            self._thumb_row_count(),
            _META_APPLY_BATCH_SIZE,
        )
        if self._meta_apply_timer is not None:
            self._meta_apply_timer.start(1)

    def _finish_meta_apply(self) -> None:
        sort_t0 = _time.perf_counter()
        _log.info("[STAT][_on_metadata_ready] enabling tree sorting")
        self._set_tree_header_fast_mode(False)
        self._tree_widget.setSortingEnabled(True)
        self._tree_widget.sortByColumn(self._tree_last_sort_column, self._tree_last_sort_order)
        self._refresh_tree_row_numbers()
        _log.info("[STAT][_on_metadata_ready] tree sorting enabled elapsed=%.3fs", _time.perf_counter() - sort_t0)

        if self._view_mode == self._MODE_THUMB:
            paint_t0 = _time.perf_counter()
            self._list_widget.viewport().update()
            self._invalidate_visible_thumbnail_signature()
            self._schedule_visible_thumbnail_update()
            _log.info("[STAT][_on_metadata_ready] list viewport updated elapsed=%.3fs", _time.perf_counter() - paint_t0)

        if self._meta_apply_needs_filter:
            _log.info("[_on_metadata_ready] _apply_filter")
            filter_t0 = _time.perf_counter()
            self._apply_filter()
            _log.info("[STAT][_on_metadata_ready] _apply_filter elapsed=%.3fs", _time.perf_counter() - filter_t0)

        self._meta_progress.setValue(self._meta_progress.maximum())
        QTimer.singleShot(400, self._meta_progress.hide)
        _log.info(
            "[STAT][_on_metadata_ready] total elapsed=%.3fs",
            _time.perf_counter() - self._meta_apply_started_at,
        )
        _log.info("[_on_metadata_ready] 目录文件列表 EXIF 已全部就绪 END")
        self._stop_pending_meta_apply()

    def _apply_meta_batch_tick(self) -> None:
        total = self._meta_apply_total
        if total <= 0:
            self._finish_meta_apply()
            return

        start = self._meta_apply_index
        i = start
        tick_t0 = _time.perf_counter()
        max_batch = max(1, _META_APPLY_BATCH_SIZE)
        budget_s = max(1.0, _META_APPLY_TIME_BUDGET_MS) / 1000.0
        while i < total:
            if (i - start) >= max_batch:
                break
            if (i - start) >= 8 and (_time.perf_counter() - tick_t0) >= budget_s:
                break
            norm_path, meta = self._meta_apply_items[i]
            if self._file_table_model.set_meta_for_path(norm_path, meta):
                self._meta_apply_tree_hits += 1
                if _DEBUG_FILE_LIST_LIMIT == 1:
                    _log.info("[DEBUG][_apply_meta] norm=%r meta=%r", norm_path, meta)
            if self._view_mode == self._MODE_THUMB:
                if self._thumb_index_for_path(norm_path).isValid():
                    self._meta_apply_list_hits += 1
                    self._apply_thumb_meta_to_path(norm_path, meta)
            i += 1

        end = i
        self._meta_apply_index = end
        self._meta_progress.setValue(end)
        if end % 1000 == 0 or end >= total:
            _log.info(
                "[STAT][_on_metadata_ready] apply_meta progress=%s/%s tree_hits=%s list_hits=%s elapsed=%.3fs",
                end,
                total,
                self._meta_apply_tree_hits,
                self._meta_apply_list_hits,
                _time.perf_counter() - self._meta_apply_loop_started_at,
            )

        if end >= total:
            _log.info(
                "[STAT][_on_metadata_ready] apply_meta end tree_hits=%s list_hits=%s elapsed=%.3fs",
                self._meta_apply_tree_hits,
                self._meta_apply_list_hits,
                _time.perf_counter() - self._meta_apply_loop_started_at,
            )
            if self._meta_apply_timer is not None:
                self._meta_apply_timer.stop()
            self._finish_meta_apply()

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _on_thumbnail_ready(self, request_token: int, path: str, qimg) -> None:
        if self._view_mode != self._MODE_THUMB:
            return
        if int(request_token) != int(self._thumb_request_token):
            self._thumb_profile_add("stale_ready", 1)
            return
        norm = os.path.normpath(path)
        self._thumb_profile_add("ready_signals", 1)
        self._thumb_profile_ready_received_at[norm] = _time.perf_counter()
        self._thumb_pending_batch[norm] = qimg
        self._thumb_profile_set_max("pending_peak", float(len(self._thumb_pending_batch)))
        if self._thumb_apply_timer is None:
            self._thumb_apply_timer = QTimer(self)
            self._thumb_apply_timer.setSingleShot(True)
            self._thumb_apply_timer.timeout.connect(self._flush_thumb_pending_batch)
        # Only start the timer if it is not already counting down.
        # Restarting on every signal (old behaviour) deferred the entire batch
        # until 60 ms after the *last* thumbnail arrived, defeating two-phase loading.
        if not self._thumb_apply_timer.isActive():
            self._thumb_apply_timer.start(30)

    def _flush_thumb_pending_batch(self) -> None:
        if not self._thumb_pending_batch:
            return
        flush_started_at = _time.perf_counter()
        pending = self._thumb_pending_batch
        self._thumb_pending_batch = {}
        visible_range = self._thumb_visible_range or self._build_visible_thumbnail_data_source()
        materialize_paths = (
            self._collect_materialized_thumbnail_paths(visible_range)
            if visible_range is not None and visible_range.entries
            else None
        )
        update_rect = QRect()
        applied_count = 0
        skipped_invalid = 0
        skipped_offscreen = 0
        ready_wait_total_s = 0.0
        ready_wait_max_s = 0.0
        pixmap_updates: list[tuple[str, QPixmap | None, int]] = []
        update_rows: list[int] = []
        for norm, qimg in pending.items():
            idx = self._thumb_index_for_path(norm)
            if not idx.isValid():
                skipped_invalid += 1
                self._thumb_profile_ready_received_at.pop(norm, None)
                continue
            if materialize_paths is not None and norm not in materialize_paths:
                skipped_offscreen += 1
                self._thumb_profile_ready_received_at.pop(norm, None)
                continue
            pixmap = QPixmap.fromImage(qimg)
            pixmap_updates.append((norm, pixmap, self._thumb_size))
            meta = self._meta_cache.get(norm, {})
            self._apply_thumb_meta_to_path(norm, meta)
            applied_count += 1
            update_rows.append(idx.row())
            ready_at = self._thumb_profile_ready_received_at.pop(norm, 0.0)
            if ready_at > 0:
                wait_s = max(0.0, _time.perf_counter() - ready_at)
                ready_wait_total_s += wait_s
                _record_thumb_bottleneck_sample("ready_wait_ms", wait_s * 1000.0)
                if wait_s > ready_wait_max_s:
                    ready_wait_max_s = wait_s
        changed_rows = self._thumb_list_model.set_pixmaps_for_paths(pixmap_updates)
        for row in changed_rows or sorted(set(update_rows)):
            idx = self._thumb_list_model.index(row, 0)
            rect = self._list_widget.visualRect(idx)
            if rect.isValid():
                update_rect = update_rect.united(rect) if update_rect.isValid() else rect
        flush_elapsed_s = _time.perf_counter() - flush_started_at
        _record_thumb_bottleneck_sample("flush_ms", flush_elapsed_s * 1000.0)
        self._thumb_profile_add("flush_calls", 1)
        self._thumb_profile_add("flush_pending_total", len(pending))
        self._thumb_profile_add("flush_applied", applied_count)
        self._thumb_profile_add("flush_skipped_offscreen", skipped_offscreen)
        self._thumb_profile_add("flush_skipped_invalid", skipped_invalid)
        self._thumb_profile_add("ready_wait_total_s", ready_wait_total_s)
        self._thumb_profile_add("ready_wait_count", applied_count)
        self._thumb_profile_set_max("ready_wait_max_s", ready_wait_max_s)
        self._thumb_profile_add("flush_total_s", flush_elapsed_s)
        self._thumb_profile_set_max("flush_max_s", flush_elapsed_s)
        if update_rect.isValid():
            self._list_widget.viewport().update(update_rect)
        if (
            len(pending) >= 24
            or skipped_offscreen > applied_count
            or ready_wait_max_s >= 0.250
            or flush_elapsed_s >= 0.020
        ):
            self._report_thumb_profile(
                "flush",
                force=True,
                extra=(
                    f"pending={len(pending)} applied={applied_count} "
                    f"offscreen={skipped_offscreen} invalid={skipped_invalid} "
                    f"flush_ms={flush_elapsed_s * 1000.0:.1f}"
                ),
            )

    def _on_metadata_progress(self, current: int, total: int) -> None:
        """主线程槽：由 progress_updated 信号触发，安全更新进度条。"""
        if total <= 0:
            return
        self._meta_progress.setMaximum(total)
        self._meta_progress.setValue(min(current, total))

    def _on_metadata_ready(self, meta_dict: dict) -> None:
        _log.info("[_on_metadata_ready] 当前目录 EXIF 查询完成，共 %s 条，更新列表与缩略图", len(meta_dict))
        _log.info("[_on_metadata_ready] START entries=%s", len(meta_dict))
        t0 = _time.perf_counter()
        total = len(meta_dict)
        self._meta_cache.update(meta_dict)
        title_cnt = 0
        color_cnt = 0
        rating_pos_cnt = 0
        city_cnt = 0
        state_cnt = 0
        country_cnt = 0
        for m in meta_dict.values():
            try:
                if str(m.get("title", "")).strip():
                    title_cnt += 1
                if str(m.get("color", "")).strip():
                    color_cnt += 1
                if int(float(str(m.get("rating", 0) or 0))) > 0:
                    rating_pos_cnt += 1
                if str(m.get("city", "")).strip():
                    city_cnt += 1
                if str(m.get("state", "")).strip():
                    state_cnt += 1
                if str(m.get("country", "")).strip():
                    country_cnt += 1
            except Exception:
                pass
        _log.info(
            "[STAT][_on_metadata_ready] meta_cache updated entries=%s cache_size=%s elapsed=%.3fs",
            total,
            len(self._meta_cache),
            _time.perf_counter() - t0,
        )
        _log.info(
            "[STAT][_on_metadata_ready] richness title=%s color=%s rating>0=%s city=%s state=%s country=%s",
            title_cnt,
            color_cnt,
            rating_pos_cnt,
            city_cnt,
            state_cnt,
            country_cnt,
        )
        self._start_meta_apply(meta_dict)

    def _emit_file_selected_for_path(self, path: str) -> None:
        """更新当前显示路径并发出 file_selected，供点击与键盘选择共用。"""
        if not path:
            return
        self._selected_display_path = os.path.normpath(path)
        self._update_selection_status()
        resolved_path = self._resolve_source_path_for_action(path)
        if not resolved_path or not os.path.isfile(resolved_path):
            self._request_actual_path_lookup(path)
        _log.info(
            "[_emit_file_selected_for_path] source=%r resolved=%r exists=%s",
            path,
            resolved_path,
            os.path.isfile(resolved_path) if resolved_path else False,
        )
        self.file_selected.emit(resolved_path or path)

    def _on_tree_item_clicked(self, index) -> None:
        path = self._tree_path_from_index(index)
        if path:
            self._handle_selection_preview_request(path)

    def _on_tree_current_item_changed(self, current, previous) -> None:
        """列表模式下键盘上下/Shift 改变当前项时触发刷新。"""
        if current is None or not current.isValid():
            return
        path = self._tree_path_from_index(current)
        if path:
            fast_preview = bool(self._selection_key_nav_auto_repeat)
            self._selection_key_nav_auto_repeat = False
            self._handle_selection_preview_request(
                path,
                fast_preview=fast_preview,
                defer_full=fast_preview,
            )

    def _on_list_item_clicked(self, index) -> None:
        path = self._thumb_path_from_index(index)
        if path:
            self._handle_selection_preview_request(path)

    def _copy_paths_to_clipboard(self, paths: list) -> None:
        """将本地文件路径写入剪贴板；若存在同名 XMP sidecar 也一并复制。"""
        expanded_paths: list[str] = []
        seen: set[str] = set()

        for p in paths:
            if not p:
                continue
            abs_path = self._resolve_source_path_for_action(p)
            source_exists = bool(abs_path and os.path.isfile(abs_path))
            if source_exists:
                abs_path = os.path.abspath(abs_path)
                norm_key = os.path.normcase(os.path.normpath(abs_path))
                if norm_key not in seen:
                    expanded_paths.append(abs_path)
                    seen.add(norm_key)

            # 同步带上 sidecar（如 IMG_0001.CR3 -> IMG_0001.xmp）
            xmp_path = self._resolve_sidecar_path(p)
            xmp_exists = bool(xmp_path and os.path.isfile(xmp_path))
            if xmp_exists:
                abs_xmp = os.path.abspath(xmp_path)
                xmp_key = os.path.normcase(os.path.normpath(abs_xmp))
                if xmp_key not in seen:
                    expanded_paths.append(abs_xmp)
                    seen.add(xmp_key)
            _log.info(
                "[_copy_paths_to_clipboard] source=%r resolved_source=%r source_exists=%s xmp_path=%r xmp_exists=%s",
                p,
                abs_path,
                source_exists,
                xmp_path,
                xmp_exists,
            )

        if not expanded_paths:
            _log.info("[_copy_paths_to_clipboard] nothing_to_copy input=%s", len(paths))
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in expanded_paths])
        mime.setText("\n".join(expanded_paths))
        QApplication.clipboard().setMimeData(mime)
        _log.info("[_copy_paths_to_clipboard] platform=%r copied=%s", sys.platform, expanded_paths)

    def _copy_filenames_to_clipboard(self, paths: list[str]) -> None:
        """Copy file full paths as plain text, one per line, without sidecars."""
        copied_paths: list[str] = []
        seen: set[str] = set()

        for p in paths:
            if not p:
                continue
            resolved_path = self._resolve_source_path_for_action(p)
            full_path = os.path.abspath(resolved_path or p)
            key = os.path.normcase(os.path.normpath(full_path))
            if key in seen:
                continue
            seen.add(key)
            copied_paths.append(full_path)
            _log.info(
                "[_copy_filenames_to_clipboard] source=%r resolved=%r full_path=%r",
                p,
                resolved_path,
                full_path,
            )

        if not copied_paths:
            _log.info("[_copy_filenames_to_clipboard] nothing_to_copy input=%s", len(paths))
            return

        QApplication.clipboard().setText("\n".join(copied_paths))
        _log.info("[_copy_filenames_to_clipboard] platform=%r copied=%s", sys.platform, copied_paths)

    def _add_send_to_external_app_actions(self, menu: QMenu, paths: list[str]) -> None:
        """在右键菜单中平铺加入「发送到外部应用」各项，使用当前选中的文件列表。无配置时显示灰色提示项。"""
        apps = get_external_apps()
        if not apps:
            hint = menu.addAction("请在「文件 → 外部应用设置」中添加应用")
            hint.setEnabled(False)
            return
        base_dir = self.get_current_dir() or ""
        for app in apps:
            name = f"发送到：{(app.get("name") or app.get("path") or "未命名").strip()}"
            act = menu.addAction(name)
            act.triggered.connect(lambda checked=False, a=app, p=paths: send_files_to_app(p, a, base_directory=base_dir))

    def _add_species_menu_actions(self, menu: QMenu, primary_path: str | None, paths: list[str]) -> None:
        source_path = primary_path or (paths[0] if paths else "")
        copy_payload = self._get_species_payload_for_path(source_path) if source_path else None
        act_copy_species = menu.addAction("复制鸟种名称")
        act_copy_species.setEnabled(copy_payload is not None)
        if copy_payload is not None:
            act_copy_species.triggered.connect(lambda: self._copy_species_from_path(source_path))

        act_paste_species = menu.addAction(self._get_paste_species_action_text())
        can_paste = getattr(self, "_copied_species_payload", None) is not None and bool(paths) and bool(self._report_root_dir or self._current_dir)
        act_paste_species.setEnabled(can_paste)
        if can_paste:
            act_paste_species.triggered.connect(lambda: self._paste_species_to_paths(paths))

    def _add_browse_preview_menu_action(self, menu: QMenu, source_path: str | None) -> None:
        preview_path = self._resolve_existing_preview_image_path(source_path or "")
        act_preview = menu.addAction("浏览预览图像")
        act_preview.setEnabled(bool(preview_path))
        if preview_path:
            _log.info("[_add_browse_preview_menu_action] source=%r preview=%r", source_path, preview_path)
            act_preview.triggered.connect(lambda checked=False, p=preview_path: reveal_in_file_manager(p))

    def _on_tree_context_menu(self, pos) -> None:
        index = self._tree_widget.indexAt(pos)
        if index.isValid() and not self._tree_widget.selectionModel().isSelected(index):
            self._tree_widget.clearSelection()
            self._tree_widget.setCurrentIndex(index)
            sm = self._tree_widget.selectionModel()
            if sm is not None:
                sm.select(index, _SelectCurrent)
        paths = self._tree_selected_paths()
        if not paths and index.isValid():
            p = self._tree_path_from_index(index)
            if p:
                paths = [p]
        if not paths:
            return
        menu = QMenu(self)
        act_copy = menu.addAction("复制")
        act_copy.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        act_copy_filename = menu.addAction("复制文件名")
        act_copy_filename.triggered.connect(lambda: self._copy_filenames_to_clipboard(paths))
        self._add_rating_menu_actions(menu, paths)
        menu.addSeparator()
        self._add_species_menu_actions(menu, self._tree_path_from_index(index) if index.isValid() else (paths[0] if paths else ""), paths)
        menu.addSeparator()
        self._add_send_to_external_app_actions(menu, paths)
        menu.addSeparator()
        label = "在Finder中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        primary_path = self._tree_path_from_index(index) if index.isValid() else (paths[0] if paths else None)
        reveal_path = self._resolve_reveal_path(primary_path)
        if reveal_path:
            _log.info("[_on_tree_context_menu] reveal_path=%r paths=%s", reveal_path, len(paths))
            act_reveal = menu.addAction(label)
            act_reveal.triggered.connect(lambda: reveal_in_file_manager(reveal_path))
        self._add_browse_preview_menu_action(menu, primary_path)
        menu.addSeparator()
        act_delete = menu.addAction("删除")
        act_delete.triggered.connect(lambda: self._move_paths_to_trash(paths))
        _exec_menu(menu, self._tree_widget.viewport().mapToGlobal(pos))

    def _move_paths_to_trash(self, paths: list) -> None:
        """将选中路径移动到垃圾桶并刷新当前目录列表。"""
        if not paths:
            return
        ok_count = 0
        for p in paths:
            if p and os.path.exists(p):
                if move_to_trash(p):
                    ok_count += 1
        if ok_count and self._current_dir:
            self.load_directory(self._current_dir, force_reload=True)

    def _on_list_context_menu(self, pos) -> None:
        index = self._list_widget.indexAt(pos)
        sm = self._list_widget.selectionModel()
        if index.isValid() and sm is not None and not sm.isSelected(index):
            self._list_widget.clearSelection()
            self._list_widget.setCurrentIndex(index)
            sm.select(index, _SelectCurrent)
        paths = self._thumb_selected_paths()
        if not paths and index.isValid():
            p = self._thumb_path_from_index(index)
            if p:
                paths = [p]
        if not paths:
            return
        menu = QMenu(self)
        act_copy = menu.addAction("复制")
        act_copy.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        act_copy_filename = menu.addAction("复制文件名")
        act_copy_filename.triggered.connect(lambda: self._copy_filenames_to_clipboard(paths))
        self._add_rating_menu_actions(menu, paths)
        menu.addSeparator()
        self._add_species_menu_actions(menu, self._thumb_path_from_index(index) if index.isValid() else (paths[0] if paths else ""), paths)
        menu.addSeparator()
        self._add_send_to_external_app_actions(menu, paths)
        menu.addSeparator()
        label = "在Finder中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        primary_path = self._thumb_path_from_index(index) if index.isValid() else (paths[0] if paths else None)
        reveal_path = self._resolve_reveal_path(primary_path)
        if reveal_path:
            _log.info("[_on_list_context_menu] reveal_path=%r paths=%s", reveal_path, len(paths))
            act_reveal = menu.addAction(label)
            act_reveal.triggered.connect(lambda: reveal_in_file_manager(reveal_path))
        self._add_browse_preview_menu_action(menu, primary_path)
        menu.addSeparator()
        act_delete = menu.addAction("删除")
        act_delete.triggered.connect(lambda: self._move_paths_to_trash(paths))
        _exec_menu(menu, self._list_widget.viewport().mapToGlobal(pos))


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

class DirectoryBrowserWidget(QWidget):
    """
    本机目录树浏览器（QTreeWidget + 懒加载）。
    macOS：将 /Volumes 下检测到的外接卷作为独立 root 节点显示。
    Windows：显示各盘符。
    """

    directory_selected = pyqtSignal(str)
    _PLACEHOLDER = "__ph__"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lbl = QLabel("  目录")
        lbl.setStyleSheet(
            "color: #aaa; font-size: 11px; padding: 4px 6px 2px 6px; background: #252525;"
        )
        layout.addWidget(lbl)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setAnimated(True)
        self._tree.setIndentation(14)
        self._tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; border: none; background: #2a2a2a; }"
            "QTreeWidget::item:selected { background: #3a5a8a; color: #fff; }"
            "QTreeWidget::item:hover { background: #333; }"
        )
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.itemClicked.connect(self._on_clicked)
        self._tree.setContextMenuPolicy(_CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_dir_context_menu)
        self._tree.installEventFilter(self)
        layout.addWidget(self._tree)

        self._populate_roots()

    def _populate_roots(self) -> None:
        """添加根节点：主目录 + macOS 外接卷 / Windows 盘符。"""
        home = os.path.expanduser("~")
        home_item = self._make_item(home, "🏠 " + os.path.basename(home))
        self._tree.addTopLevelItem(home_item)

        if sys.platform == "darwin":
            try:
                root_dev = os.stat("/").st_dev
            except OSError:
                root_dev = -1
            try:
                for entry in sorted(
                    os.scandir("/Volumes"), key=lambda e: e.name.lower()
                ):
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    try:
                        is_external = os.stat(entry.path).st_dev != root_dev
                    except OSError:
                        is_external = True
                    if is_external:
                        vol_item = self._make_item(entry.path, "💾 " + entry.name)
                        self._tree.addTopLevelItem(vol_item)
            except (PermissionError, OSError):
                pass
        elif os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    self._tree.addTopLevelItem(
                        self._make_item(drive, f"💾 {letter}:")
                    )

        self._tree.expandItem(home_item)

    def _make_item(self, path: str, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        item.setData(0, _UserRole, path)
        if os.path.isdir(path):
            item.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
        return item

    @staticmethod
    def _path_key(path: str) -> str:
        """路径归一化键（兼容 Windows 大小写不敏感文件系统）。"""
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _is_same_or_parent_path(self, parent: str, child: str) -> bool:
        """判断 parent 是否为 child 本身或祖先目录。"""
        try:
            parent_abs = os.path.normpath(os.path.abspath(parent))
            child_abs = os.path.normpath(os.path.abspath(child))
            if self._path_key(parent_abs) == self._path_key(child_abs):
                return True
            common = os.path.commonpath([parent_abs, child_abs])
            return self._path_key(common) == self._path_key(parent_abs)
        except Exception:
            return False

    def _find_best_root_item(self, target_path: str) -> QTreeWidgetItem | None:
        """从顶层 root 中找到最匹配 target_path 的节点（最长前缀）。"""
        best_item = None
        best_len = -1
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            root_path = item.data(0, _UserRole)
            if not root_path or not self._is_same_or_parent_path(root_path, target_path):
                continue
            n = len(os.path.normpath(os.path.abspath(root_path)))
            if n > best_len:
                best_item = item
                best_len = n
        return best_item

    def _ensure_children_loaded(self, item: QTreeWidgetItem) -> None:
        """若节点仍是占位符状态，则同步加载其子目录。"""
        if item.childCount() == 1 and item.child(0).text(0) == self._PLACEHOLDER:
            self._on_expanded(item)

    def _find_child_item_by_path(self, parent: QTreeWidgetItem, target_path: str) -> QTreeWidgetItem | None:
        """在 parent 的直接子节点中按真实路径匹配目标目录。"""
        target_key = self._path_key(target_path)
        for i in range(parent.childCount()):
            child = parent.child(i)
            path = child.data(0, _UserRole)
            if path and self._path_key(path) == target_key:
                return child
        return None

    def select_directory(self, path: str, emit_signal: bool = True) -> bool:
        """
        按路径展开目录树并选中目标目录。
        返回是否成功定位到目标目录节点。
        """
        if not path:
            return False
        try:
            target_path = os.path.normpath(os.path.abspath(path))
        except Exception:
            return False
        if not os.path.isdir(target_path):
            return False

        root_item = self._find_best_root_item(target_path)
        if root_item is None:
            return False

        root_path = root_item.data(0, _UserRole)
        if not root_path:
            return False
        root_path = os.path.normpath(os.path.abspath(root_path))

        chain: list[str] = [target_path]
        cur = target_path
        while self._path_key(cur) != self._path_key(root_path):
            parent = os.path.dirname(cur)
            if not parent or self._path_key(parent) == self._path_key(cur):
                return False
            chain.append(parent)
            cur = parent
        chain.reverse()  # root -> ... -> target

        current = root_item
        self._tree.expandItem(current)
        for sub_path in chain[1:]:
            self._ensure_children_loaded(current)
            self._tree.expandItem(current)
            nxt = self._find_child_item_by_path(current, sub_path)
            if nxt is None:
                return False
            current = nxt

        self._tree.expandItem(current)
        self._tree.setCurrentItem(current)
        self._tree.clearSelection()
        current.setSelected(True)
        try:
            self._tree.scrollToItem(current)
        except Exception:
            pass
        if emit_signal:
            self.directory_selected.emit(target_path)
        return True

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        """懒加载：展开时填充子目录。"""
        if item.childCount() > 0 and item.child(0).text(0) != self._PLACEHOLDER:
            return
        item.takeChildren()
        path = item.data(0, _UserRole)
        if not path:
            return
        try:
            for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                child = QTreeWidgetItem([entry.name])
                child.setData(0, _UserRole, entry.path)
                child.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
                item.addChild(child)
        except (PermissionError, OSError):
            pass

    def _on_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        path = item.data(0, _UserRole)
        if path and os.path.isdir(path):
            self.directory_selected.emit(path)

    def _refresh_dir_item_children(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, _UserRole)
        if not path or not os.path.isdir(path):
            return
        was_expanded = item.isExpanded()
        item.takeChildren()
        item.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
        if was_expanded:
            self._on_expanded(item)
            self._tree.expandItem(item)

    def _trash_empty_subdirectories(self, path: str, item: QTreeWidgetItem) -> None:
        moved_paths, failed_paths = move_empty_dirs_to_trash(path, include_root=False)
        self._refresh_dir_item_children(item)

        trash_name = "废纸篓" if sys.platform == "darwin" else "回收站"
        if failed_paths:
            QMessageBox.warning(
                self,
                "删除空目录",
                f"已移入{trash_name} {len(moved_paths)} 个空目录，另有 {len(failed_paths)} 个目录处理失败。",
            )
            return
        if moved_paths:
            QMessageBox.information(
                self,
                "删除空目录",
                f"已移入{trash_name} {len(moved_paths)} 个空目录。",
            )
            return
        QMessageBox.information(
            self,
            "删除空目录",
            "没有找到可删除的空目录。",
        )

    def eventFilter(self, obj, event) -> bool:
        if obj is not self._tree or event is None or event.type() != _EventKeyPress:
            return super().eventFilter(obj, event)
        key = event.key()
        item = self._tree.currentItem()
        if item is None:
            return super().eventFilter(obj, event)
        path = item.data(0, _UserRole)
        if not path or not os.path.isdir(path):
            return super().eventFilter(obj, event)
        target = None
        if key == _KeyUp:
            target = self._tree.itemAbove(item)
        elif key == _KeyDown:
            target = self._tree.itemBelow(item)
        elif key == _KeyLeft:
            target = item.parent()
        elif key == _KeyRight:
            if item.childCount() > 0:
                self._ensure_children_loaded(item)
                self._tree.expandItem(item)
                if item.childCount() > 0:
                    child = item.child(0)
                    if child.text(0) != self._PLACEHOLDER:
                        target = child
        if target is None:
            return super().eventFilter(obj, event)
        target_path = target.data(0, _UserRole)
        if not target_path or not os.path.isdir(target_path):
            return super().eventFilter(obj, event)
        self._tree.setCurrentItem(target)
        self._tree.clearSelection()
        target.setSelected(True)
        try:
            self._tree.scrollToItem(target)
        except Exception:
            pass
        self.directory_selected.emit(target_path)
        return True

    def _on_dir_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, _UserRole)
        if not path:
            return
        menu = QMenu(self)
        label = "在Finder中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        act = menu.addAction(label)
        act.triggered.connect(lambda: reveal_in_file_manager(path))
        menu.addSeparator()
        act_remove_empty = menu.addAction("删除所有空目录")
        act_remove_empty.triggered.connect(
            lambda checked=False, p=path, it=item: self._trash_empty_subdirectories(p, it)
        )
        _exec_menu(menu, self._tree.viewport().mapToGlobal(pos))
