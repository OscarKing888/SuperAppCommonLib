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

import io as _io
import os
import subprocess
import sys
import time as _time
from pathlib import Path

# ── Qt 导入 ───────────────────────────────────────────────────────────────────
try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QListView,
        QMenu, QProgressBar, QToolButton, QHeaderView, QAbstractItemView,
        QTreeWidget, QTreeWidgetItem,
        QStyledItemDelegate, QStackedWidget, QSlider,
        QApplication,
    )
    from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QRect, QTimer, QUrl, QMimeData
    from PyQt6.QtGui import (
        QPixmap, QImage, QFont, QColor, QIcon, QPainter, QBrush,
        QKeySequence, QShortcut,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QListView,
        QMenu, QProgressBar, QToolButton, QHeaderView, QAbstractItemView,
        QTreeWidget, QTreeWidgetItem,
        QStyledItemDelegate, QStackedWidget, QSlider,
        QApplication, QShortcut,
    )
    from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal, QRect, QTimer, QUrl, QMimeData
    from PyQt5.QtGui import (
        QPixmap, QImage, QFont, QColor, QIcon, QPainter, QBrush,
        QKeySequence,
    )

from app_common.exif_io import read_batch_metadata, find_xmp_sidecar, inject_metadata_cache
from app_common.log import get_logger
from app_common.report_db import (
    ReportDB,
    report_row_to_exiftool_style,
    EXIF_ONLY_FROM_REPORT_DB,
    get_preview_path_for_file,
    find_report_root,
)

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
    _UserRole = Qt.ItemDataRole.UserRole
except AttributeError:
    _UserRole = Qt.UserRole  # type: ignore[attr-defined]

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

# 缩略图尺寸档位（像素）
_THUMB_SIZE_STEPS = [128, 256, 512, 1024]

# Lightroom 颜色标签 → (十六进制色, 列表/缩略图显示文本)
# 红=眼部对焦，绿=飞版；其余保持常规色名
_COLOR_LABEL_COLORS: dict[str, tuple[str, str]] = {
    "Red":    ("#c0392b", "眼部对焦"),
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


# 右键菜单策略兼容常量
try:
    _CustomContextMenu = Qt.ContextMenuPolicy.CustomContextMenu
except AttributeError:
    _CustomContextMenu = Qt.CustomContextMenu  # type: ignore[attr-defined]

# ── 系统文件管理器工具函数 ────────────────────────────────────────────────────────

def _path_key(path: str) -> str:
    """Normalize path for case-insensitive comparison on Windows."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


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
    """Resolve full file path from report row current_path/original_path."""
    cp = row.get("current_path")
    if not cp or not str(cp).strip():
        return None

    cp_text = str(cp).strip()
    if os.path.isabs(cp_text):
        full_path = os.path.normpath(cp_text)
    else:
        base_dir = report_root or fallback_dir
        full_path = os.path.normpath(os.path.join(base_dir, cp_text))

    op = row.get("original_path")
    if op and str(op).strip():
        ext_orig = Path(str(op).strip()).suffix
        if ext_orig:
            full_path = str(Path(full_path).with_suffix(ext_orig))
    return full_path


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


def _reveal_in_file_manager(path: str) -> None:
    """
    在系统文件管理器中定位并高亮显示指定文件或目录。
    - macOS  : open -R <path>（在 Finder 中显示）
    - Windows: explorer /select,<path>（在资源管理器中选中）
    - Linux  : xdg-open 打开父目录
    """
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            parent = os.path.dirname(path) if os.path.isfile(path) else path
            subprocess.Popen(["xdg-open", parent])
    except Exception:
        pass


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
    支持普通图像格式及各家 RAW 嵌入缩略图。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        ext = Path(path).suffix.lower()
        img = None
        if ext in RAW_EXTENSIONS:
            thumb_data = _get_raw_thumbnail(path)
            if thumb_data:
                try:
                    img = Image.open(_io.BytesIO(thumb_data))
                except Exception:
                    img = None
        if img is None:
            try:
                img = Image.open(path)
            except Exception:
                return None
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img.thumbnail((size, size), Image.LANCZOS)
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (45, 45, 45))
            try:
                alpha = img.split()[-1]
                bg.paste(img.convert("RGB"), mask=alpha)
            except Exception:
                bg.paste(img.convert("RGB"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        # 贴到 size×size 正方形画布上，预览图缩放居中，避免列表里被拉成条
        w, h = img.size
        if (w, h) != (size, size):
            canvas = Image.new("RGB", (size, size), (45, 45, 45))
            x = (size - w) // 2
            y = (size - h) // 2
            canvas.paste(img, (x, y))
            img = canvas
            w, h = size, size
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, w, h, w * 3, _QImageRGB888)
        return qimg.copy()
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


# ── 缩略图 delegate（颜色标签 + 星级徽章）─────────────────────────────────────

class ThumbnailItemDelegate(QStyledItemDelegate):
    """在缩略图左下角绘制颜色标签徽章，右下角绘制星级徽章。"""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        color_label = index.data(_MetaColorRole)
        rating = index.data(_MetaRatingRole)
        pick   = index.data(_MetaPickRole)
        has_color = bool(color_label and color_label in _COLOR_LABEL_COLORS)
        # 右下角内容：pick 旗标优先，其次星级
        if pick == 1:
            right_badge_text = "🏆"
            right_badge_bg   = QColor(0, 0, 0, 160)
            right_badge_fg   = QColor("#ffd700")
        elif pick == -1:
            right_badge_text = "🚫"
            right_badge_bg   = QColor(0, 0, 0, 160)
            right_badge_fg   = QColor("#ffffff")
        elif isinstance(rating, int) and rating > 0:
            right_badge_text = "★" * min(5, rating)
            right_badge_bg   = QColor(0, 0, 0, 140)
            right_badge_fg   = QColor("#ffd700")
        else:
            right_badge_text = ""
        has_right = bool(right_badge_text)
        if not has_color and not has_right:
            return
        painter.save()
        try:
            painter.setRenderHint(_PainterAntialiasing)
            cell = option.rect
            # 缩略图区域按正方形算，与 setIconSize(s,s) 一致，徽章贴正方形底部
            side = min(cell.width(), cell.height()) - 6
            if side <= 0:
                side = cell.width() - 6
            icon_rect = QRect(cell.left() + 3, cell.top() + 3, side, side)
            # 左下角：颜色标签
            if has_color:
                hex_c, cn = _COLOR_LABEL_COLORS[color_label]
                bw, bh = 28, 15
                badge = QRect(
                    icon_rect.left() + 2, icon_rect.bottom() - bh - 1, bw, bh,
                )
                painter.setBrush(QBrush(QColor(hex_c)))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge, 4, 4)
                painter.setPen(QColor("#333" if color_label in ("Yellow", "White") else "#fff"))
                f = QFont()
                f.setPixelSize(9)
                painter.setFont(f)
                painter.drawText(badge, _AlignCenter, cn)
            # 右下角：pick 旗标 / 星级
            if has_right:
                f2 = QFont()
                f2.setPixelSize(11)
                painter.setFont(f2)
                fm = painter.fontMetrics()
                try:
                    sw = fm.horizontalAdvance(right_badge_text)
                except AttributeError:
                    sw = fm.width(right_badge_text)
                bw2, bh2 = sw + 8, 16
                badge2 = QRect(
                    icon_rect.right() - bw2 - 2,
                    icon_rect.bottom() - bh2 - 1,
                    bw2, bh2,
                )
                painter.setBrush(QBrush(right_badge_bg))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge2, 4, 4)
                painter.setPen(right_badge_fg)
                painter.drawText(badge2, _AlignCenter, right_badge_text)
        finally:
            painter.restore()


# ── 后台缩略图加载线程 ─────────────────────────────────────────────────────────

class ThumbnailLoader(QThread):
    """后台缩略图加载线程，逐个生成缩略图并通过信号通知主线程。HIF/HEIC/HEIF 优先用 report 的 temp_jpeg_path。"""

    thumbnail_ready = pyqtSignal(str, object)  # (文件路径, QImage)

    def __init__(
        self,
        paths: list,
        size: int,
        report_cache: dict | None = None,
        current_dir: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._size = size
        self._report_cache = report_cache or {}
        self._current_dir = current_dir or ""
        self._stop_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()

    def run(self) -> None:
        for path in self._paths:
            if self._stop_flag or self.isInterruptionRequested():
                break
            path_to_load = get_preview_path_for_file(path, self._current_dir, self._report_cache)
            qimg = _load_thumbnail_image(path_to_load, self._size)
            if qimg is not None and not (self._stop_flag or self.isInterruptionRequested()):
                self.thumbnail_ready.emit(path, qimg)


# ── 后台目录扫描线程（避免选目录/过滤时阻塞 UI）────────────────────────────────
class DirectoryScanWorker(QThread):
    """在后台执行目录扫描与 report.db 加载，完成后通过信号回传结果。"""

    scan_finished = pyqtSignal(str, object, object)  # (path, files_list, report_cache_dict)

    def __init__(self, path: str, recursive: bool, report_root: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._path = path
        self._recursive = recursive
        self._report_root = report_root

    def run(self) -> None:
        _log.info(
            "[DirectoryScanWorker.run] START path=%r recursive=%s report_root=%r",
            self._path,
            self._recursive,
            self._report_root,
        )
        report_cache: dict = {}
        try:
            db_dir = self._report_root or self._path
            db = ReportDB.open_if_exists(db_dir)
            if db:
                try:
                    for row in db.get_all_photos():
                        r = dict(row)
                        stem = r.get("filename")
                        if stem is not None:
                            report_cache[stem] = r
                finally:
                    db.close()
            _log.info("[DirectoryScanWorker.run] report_cache loaded %s entries", len(report_cache))
        except Exception as e:
            _log.warning("[DirectoryScanWorker.run] report load failed: %s", e)
        if self.isInterruptionRequested():
            _log.info("[DirectoryScanWorker.run] interrupted after report")
            return
        files: list = []
        if report_cache:
            # 当 report.db 有记录时，用 DB 中 current_path（相对选中目录）拼出完整路径，扩展名用 original_path 的（如 .ARW）
            selected_dir = os.path.normpath(self._path)
            report_root = os.path.normpath(self._report_root or self._path)
            selected_rel = ""
            if _is_same_or_child_path(report_root, selected_dir):
                try:
                    selected_rel = os.path.relpath(selected_dir, report_root)
                except Exception:
                    selected_rel = ""
            selected_rel_norm = _norm_rel_path_for_match(selected_rel)
            selected_report_cache: dict = {}
            for stem, row in sorted(report_cache.items(), key=lambda kv: (kv[0].lower() if kv[0] else "")):
                cp_text = str(row.get("current_path") or "").strip()
                if selected_rel_norm and cp_text and not os.path.isabs(cp_text):
                    cp_norm = _norm_rel_path_for_match(cp_text)
                    if cp_norm != selected_rel_norm and not cp_norm.startswith(selected_rel_norm + os.sep):
                        continue
                full_path = _resolve_report_full_path(row, report_root, self._path)
                if not full_path:
                    continue
                if not _is_same_or_child_path(selected_dir, full_path):
                    continue
                files.append(full_path)
                selected_report_cache[stem] = row
            report_cache = selected_report_cache
            _log.info(
                "[DirectoryScanWorker.run] selected scope files=%s selected_report_cache=%s selected_dir=%r selected_rel=%r report_root=%r",
                len(files), len(report_cache), selected_dir, selected_rel_norm or ".", report_root,
            )
            _log.info(
                "[DirectoryScanWorker.run] 使用 DB current_path 拼出完整路径构建文件列表 files=%s（跳过文件系统扫描）",
                len(files),
            )
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
            self.scan_finished.emit(self._path, files, report_cache)
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


_DEBUG_FILE_LIST_LIMIT = max(0, _env_int("SUPEREXIF_DEBUG_FILE_LIST_LIMIT", 0))
_DEBUG_FILE_LIST_MATCH = (os.environ.get("SUPEREXIF_DEBUG_FILE_LIST_MATCH", "") or "").strip().lower()


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

        cp = row.get("current_path")
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
                        cp_text = str(row.get("current_path") or "").strip()
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

    file_selected = pyqtSignal(str)
    _MODE_LIST  = 0
    _MODE_THUMB = 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_files: list = []
        self._current_dir = ""
        self._report_root_dir: str | None = None  # 当前使用的 report 根目录（含 .superpicky 的目录）
        self._view_mode = self._MODE_LIST
        self._thumb_size = 128
        self._thumbnail_loader: ThumbnailLoader | None = None
        self._metadata_loader:  MetadataLoader  | None = None
        self._directory_scan_worker: DirectoryScanWorker | None = None
        self._item_map:      dict = {}   # norm_path → QListWidgetItem  (缩略图)
        self._tree_item_map: dict = {}   # norm_path → SortableTreeItem (列表)
        self._meta_cache:    dict = {}   # norm_path → metadata dict
        self._report_cache:  dict = {}   # stem → report row (当前目录 .superpicky/report.db 缓存)
        self._pending_loaders: list = []
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
        # 过滤状态
        self._filter_pick: bool = False   # 只显示精选(🏆)
        self._filter_min_rating: int = 0  # 最低星级(0=不限)
        self._star_btns: list = []
        self._init_ui()

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
        self._btn_filter_pick.setFixedWidth(30)
        self._btn_filter_pick.clicked.connect(self._on_pick_filter_toggled)
        filter_bar.addWidget(self._btn_filter_pick)

        # 星级按钮（1～5，单选，点击已激活按钮则取消）
        star_widths = [22, 28, 34, 40, 46]
        for n in range(1, 6):
            btn = QToolButton()
            btn.setText("★" * n)
            btn.setToolTip(f"只显示 ≥{n} 星")
            btn.setCheckable(True)
            btn.setFixedWidth(star_widths[n - 1])
            btn.setStyleSheet("QToolButton { font-size: 10px; padding: 1px; }")
            btn.clicked.connect(
                lambda checked, rating=n: self._on_rating_filter_changed(rating)
            )
            self._star_btns.append(btn)
            filter_bar.addWidget(btn)

        layout.addLayout(filter_bar)

        # 视图堆叠
        self._stack = QStackedWidget()

        # ── 列表模式：多列 QTreeWidget ──
        self._tree_widget = QTreeWidget()
        self._tree_widget.setColumnCount(7)
        
        # @Agents: 这个列名不要修改
        # 城市 = 锐度值（越高越清晰）
        # 省/直辖市/自治区 = 美学评分（越高越好看）
        # 国家/地区 = 对焦状态（精焦/合焦/偏移/失焦）
        # 🏳️ 白旗 = Pick 精选旗标（双维度都出色）
        # 🟢 绿色标签 = 飞鸟
        # 🔴 红色标签 = 精焦（对焦点在鸟头）
        self._tree_widget.setHeaderLabels([
            "文件名", "标题", "颜色", "星级", "锐度值", "美学评分", "对焦状态"
        ])
        self._tree_widget.setSortingEnabled(True)
        self._tree_widget.setRootIsDecorated(False)
        self._tree_widget.setUniformRowHeights(True)
        self._tree_widget.setAlternatingRowColors(True)
        self._tree_widget.setSelectionMode(_ExtendedSelection)  # Shift/Command 多选
        self._tree_widget.setStyleSheet("QTreeWidget { font-size: 12px; }")
        self._tree_widget.itemClicked.connect(self._on_tree_item_clicked)
        hdr = self._tree_widget.header()
        hdr.setSectionResizeMode(0, _ResizeInteractive)
        hdr.setSectionResizeMode(1, _ResizeInteractive)
        hdr.setSectionResizeMode(2, _ResizeToContents)
        hdr.setSectionResizeMode(3, _ResizeToContents)
        hdr.setSectionResizeMode(4, _ResizeToContents)
        hdr.setSectionResizeMode(5, _ResizeToContents)
        hdr.setSectionResizeMode(6, _ResizeToContents)
        fm = self._tree_widget.fontMetrics()
        text_width = getattr(fm, "horizontalAdvance", None) or getattr(fm, "width")
        self._tree_widget.setColumnWidth(0, text_width("DSC05250.ARW") + 28)
        self._tree_widget.setColumnWidth(1, text_width("汉" * 6) + 28)
        self._tree_widget.setContextMenuPolicy(_CustomContextMenu)
        self._tree_widget.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._stack.addWidget(self._tree_widget)

        # ── 缩略图模式：QListWidget ──
        self._list_widget = QListWidget()
        self._list_widget.setViewMode(_ViewModeIcon)
        self._list_widget.setItemDelegate(ThumbnailItemDelegate(self._list_widget))
        self._list_widget.setSelectionMode(_ExtendedSelection)  # Shift/Command 多选
        self._list_widget.setResizeMode(
            QListView.ResizeMode.Adjust if hasattr(QListView, "ResizeMode")
            else QListView.Adjust  # type: ignore[attr-defined]
        )
        self._list_widget.setUniformItemSizes(True)
        self._list_widget.setStyleSheet("QListWidget { font-size: 11px; }")
        self._list_widget.itemClicked.connect(self._on_list_item_clicked)
        self._list_widget.setContextMenuPolicy(_CustomContextMenu)
        self._list_widget.customContextMenuRequested.connect(self._on_list_context_menu)
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
        layout.addWidget(self._meta_progress)

        self._stack.setCurrentIndex(0)
        self._update_size_controls()

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
            paths = [it.data(0, _UserRole) for it in self._tree_widget.selectedItems() if it and it.data(0, _UserRole)]
        elif w is self._list_widget:
            paths = [it.data(_UserRole) for it in self._list_widget.selectedItems() if it and it.data(_UserRole)]
        else:
            paths = []
        self._copy_paths_to_clipboard(paths)

    # ── 数据加载 ────────────────────────────────────────────────────────────────
    def _collect_image_files(self, dir_path: str, recursive: bool) -> list:
        """收集目录下支持的图像文件路径，委托给模块级函数（可被后台线程调用）。"""
        return _collect_image_files_impl(dir_path, recursive)

    def _has_any_filter(self) -> bool:
        """是否有任意过滤条件开启（文本 / 精选 / 星级）。"""
        return (
            bool(self._filter_edit.text().strip()) or
            self._filter_pick or
            self._filter_min_rating > 0
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
        # 选择目录后，向上查找最近的 report 根目录；子目录共用同一个 report.db
        self._report_root_dir = find_report_root(path)
        _log.info("[load_directory] report_root_dir=%r", self._report_root_dir)
        _log.info("[load_directory] _stop_all_loaders")
        self._stop_all_loaders()
        _log.info("[load_directory] _stop_directory_scan_worker")
        self._stop_directory_scan_worker()
        self._meta_cache.clear()
        self._report_cache = {}
        self._all_files = []
        _log.info("[load_directory] _rebuild_views (empty)")
        self._rebuild_views()
        recursive = self._has_any_filter()
        _log.info(
            "[load_directory] starting DirectoryScanWorker recursive=%s report_root_dir=%r",
            recursive,
            self._report_root_dir,
        )
        self._directory_scan_worker = DirectoryScanWorker(path, recursive, self._report_root_dir, self)
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

    def _on_directory_scan_finished(self, path: str, files: list, report_cache: dict) -> None:
        _log.info("[_on_directory_scan_finished] 收到目录扫描结果 path=%r files=%s report_entries=%s，开始列出文件并查询 EXIF", path, len(files), len(report_cache))
        _log.info("[_on_directory_scan_finished] path=%r _current_dir=%r files=%s report_entries=%s", path, self._current_dir, len(files), len(report_cache))
        if path != self._current_dir:
            _log.info("[_on_directory_scan_finished] IGNORE stale path")
            return
        if _DEBUG_FILE_LIST_LIMIT > 0 and len(files) > _DEBUG_FILE_LIST_LIMIT:
            selected_files = files
            if _DEBUG_FILE_LIST_MATCH:
                matched = [p for p in files if _DEBUG_FILE_LIST_MATCH in str(p).lower()]
                if matched:
                    matched_set = set(matched)
                    selected_files = matched + [p for p in files if p not in matched_set]
                    _log.warning(
                        "[DEBUG] SUPEREXIF_DEBUG_FILE_LIST_MATCH=%r matched=%s (prioritized)",
                        _DEBUG_FILE_LIST_MATCH,
                        len(matched),
                    )
                else:
                    _log.warning(
                        "[DEBUG] SUPEREXIF_DEBUG_FILE_LIST_MATCH=%r no match in current files",
                        _DEBUG_FILE_LIST_MATCH,
                    )
            limited_files = selected_files[:_DEBUG_FILE_LIST_LIMIT]
            keep_stems = {Path(p).stem for p in limited_files}
            report_cache = {k: v for k, v in report_cache.items() if k in keep_stems}
            _log.warning(
                "[DEBUG] SUPEREXIF_DEBUG_FILE_LIST_LIMIT=%s active: files %s -> %s, report_entries -> %s",
                _DEBUG_FILE_LIST_LIMIT,
                len(files),
                len(limited_files),
                len(report_cache),
            )
            files = limited_files
        self._report_cache = report_cache
        self._all_files = files
        _log.info("[_on_directory_scan_finished] 已列出 %s 个文件，重建列表/缩略图视图", len(files))
        _log.info("[_on_directory_scan_finished] _rebuild_views START")
        self._rebuild_views()
        _log.info("[_on_directory_scan_finished] _rebuild_views END")
        if files:
            _log.info("[_on_directory_scan_finished] 为当前目录下列出的 %s 个文件启动 EXIF 查询（report_cache=%s 条，未命中走 exiftool/XMP）", len(files), len(report_cache))
            self._start_metadata_loader(files)
        else:
            _log.info("[_on_directory_scan_finished] 当前目录无图像文件，跳过 EXIF 查询")
        self._directory_scan_worker = None
        _log.info("[_on_directory_scan_finished] END")

    def get_current_dir(self) -> str:
        """返回当前选中的目录路径（与 load_directory 的 path 一致）。"""
        return self._current_dir or ""

    def get_report_cache(self) -> dict:
        """返回当前目录的 report 缓存：stem（不含扩展名）→ report 行 dict。无缓存时返回空 dict。"""
        return self._report_cache

    def _rebuild_views(self) -> None:
        """从文件列表重建列表视图和缩略图视图。"""
        _log.info("[_rebuild_views] START _all_files=%s", len(self._all_files))
        self._stop_all_loaders()
        self._tree_widget.setSortingEnabled(False)
        self._tree_widget.clear()
        self._tree_item_map = {}
        self._list_widget.clear()
        self._item_map = {}
        ft = self._filter_edit.text().strip().lower()
        _log.info("[_rebuild_views] filter_text=%r adding items", ft or "(none)")

        added = 0
        for path in self._all_files:
            name = Path(path).name
            if ft and ft not in name.lower():
                continue
            norm = os.path.normpath(path)
            meta = self._meta_cache.get(norm, {})

            # 列表节点
            ti = SortableTreeItem([name, "", "", "", "", "", ""])
            ti.setData(0, _UserRole, path)
            ti.setData(0, _SortRole, name.lower())
            if meta:
                self._apply_meta_to_tree_item(ti, meta)
            self._tree_widget.addTopLevelItem(ti)
            self._tree_item_map[norm] = ti

            # 缩略图节点
            li = QListWidgetItem(name)
            li.setData(_UserRole, path)
            li.setToolTip(path)
            if meta:
                li.setData(_MetaColorRole,  meta.get("color", ""))
                li.setData(_MetaRatingRole, meta.get("rating", 0))
                li.setData(_MetaPickRole,   meta.get("pick", 0))
            self._item_map[norm] = li
            self._list_widget.addItem(li)
            added += 1

        self._tree_widget.setSortingEnabled(True)
        _log.info("[_rebuild_views] added %s items", added)
        if self._view_mode == self._MODE_THUMB:
            _log.info("[_rebuild_views] thumb mode: update thumb display + start loader")
            self._update_thumb_display()
            self._start_thumbnail_loader()
        _log.info("[_rebuild_views] END")

    def _apply_filter(self) -> None:
        """统一过滤：文件名文字 + 精选旗标 + 最低星级，三者 AND 组合。"""
        ft = self._filter_edit.text().strip().lower()
        fp = self._filter_pick
        fr = self._filter_min_rating
        _log.info("[_apply_filter] START files=%s pick=%s min_rating=%s", len(self._all_files), fp, fr)
        t0 = _time.perf_counter()
        total = len(self._all_files)
        visible = 0

        for idx, path in enumerate(self._all_files, 1):
            norm = os.path.normpath(path)
            name = Path(path).name
            meta = self._meta_cache.get(norm, {})
            pick   = meta.get("pick", 0)
            rating = meta.get("rating", 0)

            name_ok   = not ft or ft in name.lower()
            pick_ok   = not fp or pick == 1
            rating_ok = rating >= fr

            hidden = not (name_ok and pick_ok and rating_ok)
            if not hidden:
                visible += 1

            ti = self._tree_item_map.get(norm)
            if ti is not None:
                ti.setHidden(hidden)
            li = self._item_map.get(norm)
            if li is not None:
                li.setHidden(hidden)
            if idx % 2000 == 0:
                _log.info(
                    "[STAT][_apply_filter] progress=%s/%s elapsed=%.3fs",
                    idx,
                    total,
                    _time.perf_counter() - t0,
                )
        _log.info(
            "[_apply_filter] END visible=%s hidden=%s elapsed=%.3fs",
            visible,
            max(0, total - visible),
            _time.perf_counter() - t0,
        )

    def _on_pick_filter_toggled(self) -> None:
        """切换精选过滤：只显示 Pick=1 的文件。有任意过滤时递归子目录，无过滤时仅当前目录。"""
        self._filter_pick = self._btn_filter_pick.isChecked()
        if self._current_dir and os.path.isdir(self._current_dir):
            self.load_directory(self._current_dir, force_reload=True)
        else:
            self._apply_filter()

    def _on_rating_filter_changed(self, n: int) -> None:
        """切换最低星级过滤：点击已激活的按钮则取消。有任意过滤时递归子目录，无过滤时仅当前目录。"""
        if self._filter_min_rating == n:
            self._filter_min_rating = 0
        else:
            self._filter_min_rating = n
        for i, btn in enumerate(self._star_btns):
            btn.setChecked(i + 1 == self._filter_min_rating)
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

        item.setText(1, title);  item.setData(1, _SortRole, title.lower())
        color_display = (_COLOR_LABEL_COLORS.get(color, ("", ""))[1] or color)
        item.setText(2, color_display);  item.setData(2, _SortRole, _COLOR_SORT_ORDER.get(color, 99))
        if color in _COLOR_LABEL_COLORS:
            hex_c, _ = _COLOR_LABEL_COLORS[color]
            item.setBackground(2, QBrush(QColor(hex_c)))
            item.setForeground(2, QBrush(QColor(
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
        item.setText(3, star_text); item.setData(3, _SortRole, sort_val)

        item.setText(4, city);    item.setData(4, _SortRole, city.lower())
        item.setText(5, state);   item.setData(5, _SortRole, state.lower())
        item.setText(6, country); item.setData(6, _SortRole, country.lower())

    # ── 视图模式切换 ────────────────────────────────────────────────────────────
    def _set_view_mode(self, mode: int) -> None:
        self._view_mode = mode
        self._btn_list.setChecked(mode == self._MODE_LIST)
        self._btn_thumb.setChecked(mode == self._MODE_THUMB)
        self._stack.setCurrentIndex(0 if mode == self._MODE_LIST else 1)
        self._update_size_controls()
        if mode == self._MODE_THUMB:
            self._update_thumb_display()
            self._start_thumbnail_loader()
        else:
            self._stop_thumbnail_loader()

    def _update_size_controls(self) -> None:
        enabled = self._view_mode == self._MODE_THUMB
        self._size_slider.setEnabled(enabled)
        self._size_label.setEnabled(enabled)

    def _on_size_slider_changed(self, value: int) -> None:
        size = _THUMB_SIZE_STEPS[max(0, min(len(_THUMB_SIZE_STEPS) - 1, value))]
        self._size_label.setText(f"{size}px")
        if self._thumb_size != size:
            self._thumb_size = size
            if self._view_mode == self._MODE_THUMB:
                for i in range(self._list_widget.count()):
                    it = self._list_widget.item(i)
                    if it:
                        it.setIcon(QIcon())
                self._update_thumb_display()
                self._start_thumbnail_loader()

    def _update_thumb_display(self) -> None:
        s = self._thumb_size
        self._list_widget.setIconSize(QSize(s, s))
        # 网格设为正方形，使每个缩略图格子为正方形，预览图已在加载时缩放到 s×s 内
        cell = s + 28
        self._list_widget.setGridSize(QSize(cell, cell))
        self._list_widget.setSpacing(4)

    # ── 加载器管理 ──────────────────────────────────────────────────────────────
    def _start_thumbnail_loader(self) -> None:
        _log.info("[_start_thumbnail_loader] START")
        if self._view_mode != self._MODE_THUMB:
            _log.info("[_start_thumbnail_loader] skip: not in thumb mode")
            return
        self._stop_thumbnail_loader()
        paths = [
            self._list_widget.item(i).data(_UserRole)
            for i in range(self._list_widget.count())
            if self._list_widget.item(i) and not self._list_widget.item(i).isHidden()
        ]
        paths = [p for p in paths if p]
        if not paths:
            _log.info("[_start_thumbnail_loader] no visible paths, return")
            return
        _log.info("[_start_thumbnail_loader] loading %s thumbnails", len(paths))
        preview_base_dir = self._report_root_dir or self._current_dir
        loader = ThumbnailLoader(
            paths,
            self._thumb_size,
            report_cache=self._report_cache,
            current_dir=preview_base_dir,
        )
        loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumbnail_loader = loader
        loader.start()
        _log.info("[_start_thumbnail_loader] END loader.started")

    def _stop_thumbnail_loader(self) -> None:
        if self._thumbnail_loader:
            self._detach_loader(
                self._thumbnail_loader,
                self._thumbnail_loader.thumbnail_ready,
                self._on_thumbnail_ready,
            )
            self._thumbnail_loader = None
        self._pending_loaders = [l for l in self._pending_loaders if l.isRunning()]

    def _start_metadata_loader(self, paths: list) -> None:
        _log.info("[_start_metadata_loader] 为目录文件列表查询 EXIF paths=%s report_cache=%s", len(paths), len(self._report_cache))
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
            report_cache=self._report_cache,
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
        self._stop_pending_meta_apply()
        self._stop_thumbnail_loader()
        self._stop_metadata_loader()

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
                for col in (2, 3, 4, 5, 6):
                    hdr.setSectionResizeMode(col, _ResizeInteractive)
                self._tree_header_fast_mode = True
            else:
                hdr.setSectionResizeMode(0, _ResizeInteractive)
                hdr.setSectionResizeMode(1, _ResizeInteractive)
                hdr.setSectionResizeMode(2, _ResizeToContents)
                hdr.setSectionResizeMode(3, _ResizeToContents)
                hdr.setSectionResizeMode(4, _ResizeToContents)
                hdr.setSectionResizeMode(5, _ResizeToContents)
                hdr.setSectionResizeMode(6, _ResizeToContents)
                self._tree_header_fast_mode = False
        except Exception:
            pass

    def _order_meta_items_by_file_list(self, meta_dict: dict) -> list:
        ordered: list = []
        seen: set = set()
        for p in self._all_files:
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
        self._meta_apply_needs_filter = bool(self._filter_pick or self._filter_min_rating > 0)

        self._set_tree_header_fast_mode(True)
        self._tree_widget.setSortingEnabled(False)
        self._meta_progress.setMaximum(max(1, self._meta_apply_total))
        self._meta_progress.setValue(0)
        self._meta_progress.show()
        _log.info(
            "[STAT][_on_metadata_ready] apply_meta begin tree_items=%s list_items=%s batch=%s",
            len(self._tree_item_map),
            len(self._item_map),
            _META_APPLY_BATCH_SIZE,
        )
        if self._meta_apply_timer is not None:
            self._meta_apply_timer.start(1)

    def _finish_meta_apply(self) -> None:
        sort_t0 = _time.perf_counter()
        _log.info("[STAT][_on_metadata_ready] enabling tree sorting")
        self._set_tree_header_fast_mode(False)
        self._tree_widget.setSortingEnabled(True)
        _log.info("[STAT][_on_metadata_ready] tree sorting enabled elapsed=%.3fs", _time.perf_counter() - sort_t0)

        if self._view_mode == self._MODE_THUMB:
            paint_t0 = _time.perf_counter()
            self._list_widget.viewport().update()
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
            ti = self._tree_item_map.get(norm_path)
            if ti:
                self._meta_apply_tree_hits += 1
                if _DEBUG_FILE_LIST_LIMIT == 1:
                    _log.info("[DEBUG][_apply_meta] norm=%r meta=%r", norm_path, meta)
                self._apply_meta_to_tree_item(ti, meta)
                if _DEBUG_FILE_LIST_LIMIT == 1:
                    _log.info(
                        "[DEBUG][_apply_meta] row_texts name=%r title=%r color=%r star=%r sharp=%r aesthetic=%r focus=%r",
                        ti.text(0), ti.text(1), ti.text(2), ti.text(3), ti.text(4), ti.text(5), ti.text(6),
                    )
            if self._view_mode == self._MODE_THUMB:
                li = self._item_map.get(norm_path)
                if li:
                    self._meta_apply_list_hits += 1
                    li.setData(_MetaColorRole,  meta.get("color", ""))
                    li.setData(_MetaRatingRole, meta.get("rating", 0))
                    li.setData(_MetaPickRole,   meta.get("pick", 0))
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
    def _on_thumbnail_ready(self, path: str, qimg) -> None:
        if self._view_mode != self._MODE_THUMB:
            return
        norm = os.path.normpath(path)
        item = self._item_map.get(norm)
        if item is None:
            return
        item.setIcon(QIcon(QPixmap.fromImage(qimg)))
        meta = self._meta_cache.get(norm, {})
        if meta:
            item.setData(_MetaColorRole,  meta.get("color", ""))
            item.setData(_MetaRatingRole, meta.get("rating", 0))
            item.setData(_MetaPickRole,   meta.get("pick", 0))

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

    def _on_tree_item_clicked(self, item, column) -> None:
        path = item.data(0, _UserRole)
        if path and os.path.isfile(path):
            _log.info("[_on_tree_item_clicked] 选中照片，触发 EXIF 查询 path=%r", path)
            self.file_selected.emit(path)

    def _on_list_item_clicked(self, item) -> None:
        path = item.data(_UserRole)
        if path and os.path.isfile(path):
            _log.info("[_on_list_item_clicked] 选中照片，触发 EXIF 查询 path=%r", path)
            self.file_selected.emit(path)

    def _copy_paths_to_clipboard(self, paths: list) -> None:
        """将本地文件路径写入剪贴板；若存在同名 XMP sidecar 也一并复制。"""
        expanded_paths: list[str] = []
        seen: set[str] = set()

        for p in paths:
            if not p or not os.path.isfile(p):
                continue
            abs_path = os.path.abspath(p)
            norm_key = os.path.normcase(os.path.normpath(abs_path))
            if norm_key not in seen:
                expanded_paths.append(abs_path)
                seen.add(norm_key)

            # 同步带上 sidecar（如 IMG_0001.CR3 -> IMG_0001.xmp）
            try:
                xmp_path = find_xmp_sidecar(abs_path)
            except Exception:
                xmp_path = None
            if xmp_path and os.path.isfile(xmp_path):
                abs_xmp = os.path.abspath(xmp_path)
                xmp_key = os.path.normcase(os.path.normpath(abs_xmp))
                if xmp_key not in seen:
                    expanded_paths.append(abs_xmp)
                    seen.add(xmp_key)

        if not expanded_paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in expanded_paths])
        QApplication.clipboard().setMimeData(mime)

    def _on_tree_context_menu(self, pos) -> None:
        item = self._tree_widget.itemAt(pos)
        if item is not None and not item.isSelected():
            self._tree_widget.clearSelection()
            item.setSelected(True)
            self._tree_widget.setCurrentItem(item)
        selected = self._tree_widget.selectedItems()
        paths = [it.data(0, _UserRole) for it in selected if it and it.data(0, _UserRole)]
        if not paths and item:
            p = item.data(0, _UserRole)
            if p:
                paths = [p]
        if not paths:
            return
        menu = QMenu(self)
        act_copy = menu.addAction("复制")
        act_copy.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()
        label = "在 Finder 中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        reveal_path = item.data(0, _UserRole) if item else (paths[0] if paths else None)
        if reveal_path:
            act_reveal = menu.addAction(label)
            act_reveal.triggered.connect(lambda: _reveal_in_file_manager(reveal_path))
        _exec_menu(menu, self._tree_widget.viewport().mapToGlobal(pos))


    def _on_list_context_menu(self, pos) -> None:
        item = self._list_widget.itemAt(pos)
        if item is not None and not item.isSelected():
            self._list_widget.clearSelection()
            item.setSelected(True)
            self._list_widget.setCurrentItem(item)
        selected = self._list_widget.selectedItems()
        paths = [it.data(_UserRole) for it in selected if it and it.data(_UserRole)]
        if not paths and item:
            p = item.data(_UserRole)
            if p:
                paths = [p]
        if not paths:
            return
        menu = QMenu(self)
        act_copy = menu.addAction("复制")
        act_copy.triggered.connect(lambda: self._copy_paths_to_clipboard(paths))
        menu.addSeparator()
        label = "在 Finder 中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        reveal_path = item.data(_UserRole) if item else (paths[0] if paths else None)
        if reveal_path:
            act_reveal = menu.addAction(label)
            act_reveal.triggered.connect(lambda: _reveal_in_file_manager(reveal_path))
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

    def _on_dir_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, _UserRole)
        if not path:
            return
        menu = QMenu(self)
        label = "在 Finder 中显示" if sys.platform == "darwin" else "在资源管理器中显示"
        act = menu.addAction(label)
        act.triggered.connect(lambda: _reveal_in_file_manager(path))
        _exec_menu(menu, self._tree.viewport().mapToGlobal(pos))
