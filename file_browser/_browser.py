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
import sys
from pathlib import Path

# ── Qt 导入 ───────────────────────────────────────────────────────────────────
try:
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QListView,
        QToolButton, QHeaderView, QAbstractItemView,
        QTreeWidget, QTreeWidgetItem,
        QStyledItemDelegate, QStackedWidget, QSlider,
    )
    from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QRect
    from PyQt6.QtGui import (
        QPixmap, QImage, QFont, QColor, QIcon, QPainter, QBrush,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QListView,
        QToolButton, QHeaderView, QAbstractItemView,
        QTreeWidget, QTreeWidgetItem,
        QStyledItemDelegate, QStackedWidget, QSlider,
    )
    from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal, QRect
    from PyQt5.QtGui import (
        QPixmap, QImage, QFont, QColor, QIcon, QPainter, QBrush,
    )

from app_common.exif_io import read_batch_metadata

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

# 缩略图尺寸档位（像素）
_THUMB_SIZE_STEPS = [128, 256, 512, 1024]

# Lightroom 颜色标签 → (十六进制色, 中文简称)
_COLOR_LABEL_COLORS: dict[str, tuple[str, str]] = {
    "Red":    ("#c0392b", "红"),
    "Yellow": ("#d4ac0d", "黄"),
    "Green":  ("#27ae60", "绿"),
    "Blue":   ("#2980b9", "蓝"),
    "Purple": ("#8e44ad", "紫"),
    "White":  ("#bdc3c7", "白"),
    "Orange": ("#e67e22", "橙"),
}
_COLOR_SORT_ORDER: dict[str, int] = {
    k: i for i, k in enumerate(
        ["Red", "Orange", "Yellow", "Green", "Blue", "Purple", "White", ""]
    )
}

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
        has_color = bool(color_label and color_label in _COLOR_LABEL_COLORS)
        has_rating = isinstance(rating, int) and rating > 0
        if not has_color and not has_rating:
            return
        painter.save()
        try:
            painter.setRenderHint(_PainterAntialiasing)
            cell = option.rect
            icon_rect = QRect(
                cell.left() + 3, cell.top() + 3,
                cell.width() - 6, cell.height() - 25,
            )
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
            if has_rating:
                n = min(5, rating)
                stars = "★" * n
                f2 = QFont()
                f2.setPixelSize(10)
                painter.setFont(f2)
                fm = painter.fontMetrics()
                try:
                    sw = fm.horizontalAdvance(stars)
                except AttributeError:
                    sw = fm.width(stars)
                bw2, bh2 = sw + 6, 15
                badge2 = QRect(
                    icon_rect.right() - bw2 - 2, icon_rect.bottom() - bh2 - 1, bw2, bh2,
                )
                painter.setBrush(QBrush(QColor(0, 0, 0, 140)))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge2, 4, 4)
                painter.setPen(QColor("#ffd700"))
                painter.drawText(badge2, _AlignCenter, stars)
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

class MetadataLoader(QThread):
    """
    批量读取图像文件的列表列元数据。
    内部调用 read_batch_metadata（自动 exiftool 优先 + XMP sidecar 回退）。
    """

    all_metadata_ready = pyqtSignal(object)  # dict {norm_path: metadata_dict}

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
            raw = read_batch_metadata(self._paths)
            result: dict = {}
            for norm, rec in raw.items():
                if self._stop_flag or self.isInterruptionRequested():
                    return
                result[norm] = self._parse_rec(rec)
        except Exception:
            result = {}
        if not (self._stop_flag or self.isInterruptionRequested()):
            self.all_metadata_ready.emit(result)

    def _parse_rec(self, rec: dict) -> dict:
        title = (rec.get("XMP-dc:Title") or rec.get("IFD0:XPTitle")
                 or rec.get("IPTC:ObjectName") or "")
        color = rec.get("XMP-xmp:Label") or ""
        try:
            rating = max(0, min(5, int(float(str(rec.get("XMP-xmp:Rating") or 0)))))
        except Exception:
            rating = 0
        city    = rec.get("XMP-photoshop:City")  or rec.get("IPTC:City") or ""
        state   = rec.get("XMP-photoshop:State") or rec.get("IPTC:Province-State") or ""
        country = (rec.get("XMP-photoshop:Country-PrimaryLocationName")
                   or rec.get("IPTC:Country-PrimaryLocationName") or "")
        return {
            "title":   str(title).strip(),
            "color":   str(color).strip(),
            "rating":  rating,
            "city":    str(city).strip(),
            "state":   str(state).strip(),
            "country": str(country).strip(),
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
        self._init_ui()

    # ── UI 初始化 ──────────────────────────────────────────────────────────────
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 过滤框
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("过滤文件名…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setStyleSheet("QLineEdit { padding: 4px; font-size: 12px; }")
        self._filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter_edit)

        # 工具栏
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
        toolbar.addSpacing(6)
        toolbar.addWidget(QLabel("大小:"))
        toolbar.addWidget(self._size_slider)
        toolbar.addWidget(self._size_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 视图堆叠
        self._stack = QStackedWidget()

        # ── 列表模式：多列 QTreeWidget ──
        self._tree_widget = QTreeWidget()
        self._tree_widget.setColumnCount(7)
        self._tree_widget.setHeaderLabels([
            "文件名", "标题", "颜色",
            "星级", "城市",
            "省/直辖市/自治区",
            "国家/地区",
        ])
        self._tree_widget.setSortingEnabled(True)
        self._tree_widget.setRootIsDecorated(False)
        self._tree_widget.setUniformRowHeights(True)
        self._tree_widget.setAlternatingRowColors(True)
        self._tree_widget.setSelectionMode(_SingleSelection)
        self._tree_widget.setStyleSheet("QTreeWidget { font-size: 12px; }")
        self._tree_widget.itemClicked.connect(self._on_tree_item_clicked)
        hdr = self._tree_widget.header()
        hdr.setSectionResizeMode(0, _ResizeInteractive)
        hdr.setSectionResizeMode(1, _ResizeStretch)
        hdr.setSectionResizeMode(2, _ResizeToContents)
        hdr.setSectionResizeMode(3, _ResizeToContents)
        hdr.setSectionResizeMode(4, _ResizeToContents)
        hdr.setSectionResizeMode(5, _ResizeToContents)
        hdr.setSectionResizeMode(6, _ResizeToContents)
        self._tree_widget.setColumnWidth(0, 180)
        self._stack.addWidget(self._tree_widget)

        # ── 缩略图模式：QListWidget ──
        self._list_widget = QListWidget()
        self._list_widget.setViewMode(_ViewModeIcon)
        self._list_widget.setItemDelegate(ThumbnailItemDelegate(self._list_widget))
        self._list_widget.setSelectionMode(_SingleSelection)
        self._list_widget.setResizeMode(
            QListView.ResizeMode.Adjust if hasattr(QListView, "ResizeMode")
            else QListView.Adjust  # type: ignore[attr-defined]
        )
        self._list_widget.setUniformItemSizes(True)
        self._list_widget.setStyleSheet("QListWidget { font-size: 11px; }")
        self._list_widget.itemClicked.connect(self._on_list_item_clicked)
        self._stack.addWidget(self._list_widget)

        layout.addWidget(self._stack, stretch=1)
        self._stack.setCurrentIndex(0)
        self._update_size_controls()

    # ── 数据加载 ────────────────────────────────────────────────────────────────
    def load_directory(self, path: str) -> None:
        """扫描目录，加载所有支持的图像文件。"""
        if path == self._current_dir:
            return
        self._current_dir = path
        self._stop_all_loaders()
        self._meta_cache.clear()
        files: list = []
        try:
            for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS:
                    files.append(entry.path)
        except (PermissionError, OSError):
            pass
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
                li.setData(_MetaColorRole, meta.get("color", ""))
                li.setData(_MetaRatingRole, meta.get("rating", 0))
            self._item_map[norm] = li
            self._list_widget.addItem(li)

        self._tree_widget.setSortingEnabled(True)
        self._update_thumb_display()
        if self._view_mode == self._MODE_THUMB:
            self._start_thumbnail_loader()

    def _apply_filter(self, text: str) -> None:
        ft = text.strip().lower()
        for i in range(self._tree_widget.topLevelItemCount()):
            it = self._tree_widget.topLevelItem(i)
            if it:
                it.setHidden(bool(ft) and ft not in it.text(0).lower())
        for i in range(self._list_widget.count()):
            it = self._list_widget.item(i)
            if it:
                it.setHidden(bool(ft) and ft not in (it.text() or "").lower())

    def _apply_meta_to_tree_item(self, item: SortableTreeItem, meta: dict) -> None:
        title   = meta.get("title", "")
        color   = meta.get("color", "")
        rating  = meta.get("rating", 0)
        city    = meta.get("city", "")
        state   = meta.get("state", "")
        country = meta.get("country", "")

        item.setText(1, title);  item.setData(1, _SortRole, title.lower())
        item.setText(2, color);  item.setData(2, _SortRole, _COLOR_SORT_ORDER.get(color, 99))
        if color in _COLOR_LABEL_COLORS:
            hex_c, _ = _COLOR_LABEL_COLORS[color]
            item.setBackground(2, QBrush(QColor(hex_c)))
            item.setForeground(2, QBrush(QColor(
                "#333" if color in ("Yellow", "White") else "#fff"
            )))
        stars = "★" * rating if rating > 0 else ""
        item.setText(3, stars);  item.setData(3, _SortRole, rating)
        item.setText(4, city);   item.setData(4, _SortRole, city.lower())
        item.setText(5, state);  item.setData(5, _SortRole, state.lower())
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
        loader = MetadataLoader(paths)
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
            self._metadata_loader = None

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
            item.setData(_MetaColorRole, meta.get("color", ""))
            item.setData(_MetaRatingRole, meta.get("rating", 0))

    def _on_metadata_ready(self, meta_dict: dict) -> None:
        self._meta_cache.update(meta_dict)
        self._tree_widget.setSortingEnabled(False)
        for norm_path, meta in meta_dict.items():
            ti = self._tree_item_map.get(norm_path)
            if ti:
                self._apply_meta_to_tree_item(ti, meta)
            li = self._item_map.get(norm_path)
            if li:
                li.setData(_MetaColorRole, meta.get("color", ""))
                li.setData(_MetaRatingRole, meta.get("rating", 0))
        self._tree_widget.setSortingEnabled(True)
        self._list_widget.viewport().update()

    def _on_tree_item_clicked(self, item, column) -> None:
        path = item.data(0, _UserRole)
        if path and os.path.isfile(path):
            self.file_selected.emit(path)

    def _on_list_item_clicked(self, item) -> None:
        path = item.data(_UserRole)
        if path and os.path.isfile(path):
            self.file_selected.emit(path)


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
