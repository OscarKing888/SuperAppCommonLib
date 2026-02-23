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

from app_common.exif_io import read_batch_metadata, find_xmp_sidecar

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
        w, h = img.size
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
            icon_rect = QRect(
                cell.left() + 3, cell.top() + 3,
                cell.width() - 6, cell.height() - 25,
            )
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
    """后台缩略图加载线程，逐个生成缩略图并通过信号通知主线程。"""

    thumbnail_ready = pyqtSignal(str, object)  # (文件路径, QImage)

    def __init__(self, paths: list, size: int, parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._size = size
        self._stop_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()

    def run(self) -> None:
        for path in self._paths:
            if self._stop_flag or self.isInterruptionRequested():
                break
            qimg = _load_thumbnail_image(path, self._size)
            if qimg is not None and not (self._stop_flag or self.isInterruptionRequested()):
                self.thumbnail_ready.emit(path, qimg)


# ── 后台元数据加载线程 ─────────────────────────────────────────────────────────

# 后台元数据读取：每块最大文件数（分块顺序读取，提升取消响应性）
_METADATA_CHUNK_SIZE = 150


class MetadataLoader(QThread):
    """
    批量读取图像文件的列表列元数据。
    内部将路径分块，在单个后台线程中顺序调用 read_batch_metadata（exiftool / XMP sidecar）。
    说明：read_batch_metadata 本身已做批量读取与缓存；这里不再额外并行拆块，
    避免递归过滤/快速切目录时堆积多路 exiftool 子进程，导致界面卡死。
    """

    all_metadata_ready = pyqtSignal(object)  # dict {norm_path: metadata_dict}
    # 进度更新（主线程槽更新 UI，Qt 跨线程信号自动排队，线程安全）
    progress_updated = pyqtSignal(int, int)  # (current_count, total_count)

    def __init__(self, paths: list, parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._stop_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()

    def run(self) -> None:
        if not self._paths or self._stop_flag:
            return
        try:
            # 分块顺序读取：兼顾进度更新与取消响应（切目录时最多等待当前分块完成）
            paths = self._paths
            chunk_size = max(1, _METADATA_CHUNK_SIZE)
            chunks = [
                paths[i : i + chunk_size]
                for i in range(0, len(paths), chunk_size)
            ]
            total = len(paths)
            result: dict = {}
            processed = 0
            for chunk in chunks:
                if self._stop_flag or self.isInterruptionRequested():
                    return
                chunk_raw = read_batch_metadata(chunk)
                if self._stop_flag or self.isInterruptionRequested():
                    return
                for norm, rec in chunk_raw.items():
                    if self._stop_flag or self.isInterruptionRequested():
                        return
                    result[norm] = self._parse_rec(rec)
                processed += len(chunk)
                self.progress_updated.emit(min(processed, total), total)
        except Exception:
            result = {}
        if not (self._stop_flag or self.isInterruptionRequested()):
            self.all_metadata_ready.emit(result)

    def _parse_rec(self, rec: dict) -> dict:
        # 标题、对焦状态等支持 XMP sidecar（由 read_batch_metadata 合并），勿删以下键名
        # 标题：XMP dc:title（sidecar 多为小写 tag）、IFD0/XPTitle、IPTC
        title = (
            rec.get("XMP-dc:Title") or rec.get("XMP-dc:title")
            or rec.get("IFD0:XPTitle") or rec.get("IPTC:ObjectName") or ""
        )
        color = rec.get("XMP-xmp:Label") or ""
        try:
            rating = max(0, min(5, int(float(str(rec.get("XMP-xmp:Rating") or 0)))))
        except Exception:
            rating = 0
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
        self._view_mode = self._MODE_LIST
        self._thumb_size = 128
        self._thumbnail_loader: ThumbnailLoader | None = None
        self._metadata_loader:  MetadataLoader  | None = None
        self._item_map:      dict = {}   # norm_path → QListWidgetItem  (缩略图)
        self._tree_item_map: dict = {}   # norm_path → SortableTreeItem (列表)
        self._meta_cache:    dict = {}   # norm_path → metadata dict
        self._pending_loaders: list = []
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
        """
        收集目录下支持的图像文件路径。
        recursive=True 时递归遍历所有子目录；否则仅当前目录。
        """
        files: list = []
        try:
            if recursive:
                for root, _dirs, names in os.walk(dir_path, topdown=True):
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

    def _has_any_filter(self) -> bool:
        """是否有任意过滤条件开启（文本 / 精选 / 星级）。"""
        return (
            bool(self._filter_edit.text().strip()) or
            self._filter_pick or
            self._filter_min_rating > 0
        )

    def load_directory(self, path: str, force_reload: bool = False) -> None:
        """
        扫描目录，加载支持的图像文件。
        当任意过滤条件开启（文本 / 🏆精选 / 星级）时，递归遍历该目录及所有子目录，
        收集图像后按当前过滤条件显示；否则仅当前目录。
        force_reload=True 时忽略「当前目录未变」的短路，用于切换过滤后刷新列表。
        """
        if not force_reload and path == self._current_dir:
            return
        self._current_dir = path
        self._stop_all_loaders()
        self._meta_cache.clear()
        recursive = self._has_any_filter()
        files = self._collect_image_files(path, recursive=recursive)
        self._all_files = files
        self._rebuild_views()
        if files:
            self._start_metadata_loader(files)

    def _rebuild_views(self) -> None:
        """从文件列表重建列表视图和缩略图视图。"""
        self._stop_all_loaders()
        self._tree_widget.setSortingEnabled(False)
        self._tree_widget.clear()
        self._tree_item_map = {}
        self._list_widget.clear()
        self._item_map = {}
        ft = self._filter_edit.text().strip().lower()

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

        self._tree_widget.setSortingEnabled(True)
        self._update_thumb_display()
        if self._view_mode == self._MODE_THUMB:
            self._start_thumbnail_loader()

    def _apply_filter(self) -> None:
        """统一过滤：文件名文字 + 精选旗标 + 最低星级，三者 AND 组合。"""
        ft = self._filter_edit.text().strip().lower()
        fp = self._filter_pick
        fr = self._filter_min_rating

        for path in self._all_files:
            norm = os.path.normpath(path)
            name = Path(path).name
            meta = self._meta_cache.get(norm, {})
            pick   = meta.get("pick", 0)
            rating = meta.get("rating", 0)

            name_ok   = not ft or ft in name.lower()
            pick_ok   = not fp or pick == 1
            rating_ok = rating >= fr

            hidden = not (name_ok and pick_ok and rating_ok)

            ti = self._tree_item_map.get(norm)
            if ti is not None:
                ti.setHidden(hidden)
            li = self._item_map.get(norm)
            if li is not None:
                li.setHidden(hidden)

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
            self._start_thumbnail_loader()

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
        self._list_widget.setGridSize(QSize(s + 20, s + 36))
        self._list_widget.setSpacing(4)

    # ── 加载器管理 ──────────────────────────────────────────────────────────────
    def _start_thumbnail_loader(self) -> None:
        self._stop_thumbnail_loader()
        paths = [
            self._list_widget.item(i).data(_UserRole)
            for i in range(self._list_widget.count())
            if self._list_widget.item(i) and not self._list_widget.item(i).isHidden()
        ]
        paths = [p for p in paths if p]
        if not paths:
            return
        loader = ThumbnailLoader(paths, self._thumb_size)
        loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumbnail_loader = loader
        loader.start()

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
        self._stop_metadata_loader()
        total = len(paths)
        if total <= 0:
            return
        self._meta_progress.setMaximum(total)
        self._meta_progress.setValue(0)
        self._meta_progress.show()
        loader = MetadataLoader(paths)
        loader.progress_updated.connect(self._on_metadata_progress)
        loader.all_metadata_ready.connect(self._on_metadata_ready)
        self._metadata_loader = loader
        loader.start()

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
        self._stop_thumbnail_loader()
        self._stop_metadata_loader()

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _on_thumbnail_ready(self, path: str, qimg) -> None:
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
        self._meta_cache.update(meta_dict)
        self._meta_progress.setValue(self._meta_progress.maximum())
        QTimer.singleShot(400, self._meta_progress.hide)
        self._tree_widget.setSortingEnabled(False)
        for norm_path, meta in meta_dict.items():
            ti = self._tree_item_map.get(norm_path)
            if ti:
                self._apply_meta_to_tree_item(ti, meta)
            li = self._item_map.get(norm_path)
            if li:
                li.setData(_MetaColorRole,  meta.get("color", ""))
                li.setData(_MetaRatingRole, meta.get("rating", 0))
                li.setData(_MetaPickRole,   meta.get("pick", 0))
        self._tree_widget.setSortingEnabled(True)
        self._list_widget.viewport().update()
        # 元数据加载完成后，根据最新 meta_cache 重新应用过滤
        if self._filter_pick or self._filter_min_rating > 0:
            self._apply_filter()

    def _on_tree_item_clicked(self, item, column) -> None:
        path = item.data(0, _UserRole)
        if path and os.path.isfile(path):
            self.file_selected.emit(path)

    def _on_list_item_clicked(self, item) -> None:
        path = item.data(_UserRole)
        if path and os.path.isfile(path):
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
