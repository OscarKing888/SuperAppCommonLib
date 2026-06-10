# -*- coding: utf-8 -*-
"""Qt item models and delegates for app_common.file_browser."""
from __future__ import annotations

from app_common.file_browser._browser_core import *


def _metadata_rating_value(meta: dict | None) -> int:
    """从规范字段或原始 XMP 字段解析 0-5 星级。"""
    if not isinstance(meta, dict):
        return 0
    raw = _first_non_empty(
        meta.get("rating"),
        meta.get("XMP-xmp:Rating"),
        meta.get("XMP:Rating"),
        meta.get("XMP-xmp:rating"),
    )
    try:
        return max(0, min(5, int(float(str(raw or 0)))))
    except Exception:
        return 0


def _metadata_pick_value(meta: dict | None) -> int:
    """从规范字段或原始 XMP 字段解析 Pick/Reject：1/0/-1。"""
    if not isinstance(meta, dict):
        return 0
    raw = _first_non_empty(
        meta.get("pick"),
        meta.get("XMP-xmpDM:pick"),
        meta.get("XMP-xmpDM:Pick"),
        meta.get("XMP-xmp:Pick"),
        meta.get("XMP-xmp:PickLabel"),
        meta.get("XMP:Pick"),
        meta.get("XMP:PickLabel"),
    )
    if not str(raw or "").strip():
        rating_raw = _first_non_empty(
            meta.get("XMP-xmp:Rating"),
            meta.get("XMP:Rating"),
            meta.get("XMP-xmp:rating"),
            meta.get("rating"),
        )
        try:
            if int(float(str(rating_raw or 0))) < 0:
                return -1
        except Exception:
            pass
    try:
        text = str(raw or "").strip().lower()
        if text in ("true", "yes"):
            return 1
        if text in ("false", "no", ""):
            return 0
        return max(-1, min(1, int(float(text))))
    except Exception:
        return 0


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
    comment: str = ""
    tags: list[str] = field(default_factory=list)
    tags_display: str = ""
    title: str = ""
    color: str = ""
    color_display: str = ""
    rating: int = 0
    pick: int = 0
    city: str = ""
    state: str = ""
    country: str = ""
    shutter: str = ""
    iso: str = ""
    aperture: str = ""


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
        entry.comment = _metadata_comment_from_meta(meta)
        entry.tags = _metadata_tags_from_meta(meta)
        entry.tags_display = _FILE_TAG_DISPLAY_SEPARATOR.join(entry.tags)
        entry.title = str(meta.get("title", "") or "")
        entry.color = str(meta.get("color", "") or "")
        entry.color_display = _COLOR_LABEL_COLORS.get(entry.color, ("", ""))[1] or entry.color
        entry.rating = _metadata_rating_value(meta)
        entry.pick = _metadata_pick_value(meta)
        entry.city = str(meta.get("city", "") or "")
        entry.state = str(meta.get("state", "") or "")
        entry.country = str(meta.get("country", "") or "")
        entry.shutter = str(meta.get("shutter", "") or "")
        entry.iso = str(meta.get("iso", "") or "")
        entry.aperture = str(meta.get("aperture", "") or "")

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
        if column == _TREE_COL_NAME:
            return entry.name.lower()
        if column == _TREE_COL_COMMENT:
            return entry.comment.lower()
        if column == _TREE_COL_STAR:
            if entry.pick == 1:
                return 10
            if entry.pick == -1:
                return -1
            return entry.rating
        if column == _TREE_COL_TAGS:
            return entry.tags_display.lower()
        return ""

    def _display_value(self, entry: FileTableEntry, row: int, column: int) -> str:
        if column == _TREE_COL_NAME:
            return entry.name
        if column == _TREE_COL_COMMENT:
            return entry.comment
        if column == _TREE_COL_STAR:
            if entry.pick == 1:
                return "🏆"
            if entry.pick == -1:
                return "🚫"
            return "★" * max(0, entry.rating)
        if column == _TREE_COL_TAGS:
            return entry.tags_display
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
        if role == _ForegroundRole:
            if column == _TREE_COL_NAME and entry.mismatch:
                return QBrush(QColor("#c0392b"))
            return None
        return None

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
        left = self.index(row, _TREE_COL_COMMENT)
        right = self.index(row, self.columnCount() - 1)
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


class FileTableHeaderView(QHeaderView):
    """列表模式表头：显式提供分割线 hover 与拖拽改宽。"""

    def __init__(self, parent=None) -> None:
        super().__init__(_Horizontal, parent)
        self._resize_section: int = -1
        self._resize_origin_x: int = 0
        self._resize_origin_size: int = 0
        self._resize_margin_px: int = 6
        self.setSectionsClickable(True)
        self.setSectionsMovable(False)
        self.setStretchLastSection(False)
        self.setMinimumSectionSize(24)
        try:
            self.setCascadingSectionResizes(False)
        except Exception:
            pass
        self.setMouseTracking(True)

    def _event_pos_x(self, event) -> int:
        position = getattr(event, "position", None)
        if callable(position):
            try:
                return int(position().x())
            except Exception:
                pass
        pos = getattr(event, "pos", None)
        if callable(pos):
            try:
                return int(pos().x())
            except Exception:
                pass
        return 0

    def _resize_target_for_x(self, x: int) -> int:
        logical = self.logicalIndexAt(x)
        if logical < 0:
            return -1
        left = self.sectionViewportPosition(logical)
        size = self.sectionSize(logical)
        if size <= 0:
            return -1
        right = left + size
        margin = max(3, min(self._resize_margin_px, size // 3 if size > 0 else self._resize_margin_px))
        if abs(x - right) <= margin and self.sectionResizeMode(logical) == _ResizeInteractive:
            return logical
        if abs(x - left) <= margin:
            visual = self.visualIndex(logical)
            if visual > 0:
                prev_logical = self.logicalIndex(visual - 1)
                if prev_logical >= 0 and self.sectionResizeMode(prev_logical) == _ResizeInteractive:
                    return prev_logical
        return -1

    def _update_resize_cursor(self, x: int) -> None:
        if self._resize_section >= 0 or self._resize_target_for_x(x) >= 0:
            self.setCursor(_SplitHCursor)
        else:
            self.setCursor(_ArrowCursor)

    def mousePressEvent(self, event) -> None:
        button = getattr(event, "button", lambda: None)()
        x = self._event_pos_x(event)
        target = self._resize_target_for_x(x)
        if button == _LeftButton and target >= 0:
            self._resize_section = target
            self._resize_origin_x = x
            self._resize_origin_size = max(self.minimumSectionSize(), self.sectionSize(target))
            self.setCursor(_SplitHCursor)
            try:
                event.accept()
            except Exception:
                pass
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        x = self._event_pos_x(event)
        if self._resize_section >= 0:
            delta = x - self._resize_origin_x
            new_size = max(self.minimumSectionSize(), self._resize_origin_size + delta)
            self.resizeSection(self._resize_section, new_size)
            self.setCursor(_SplitHCursor)
            try:
                event.accept()
            except Exception:
                pass
            return
        self._update_resize_cursor(x)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_section >= 0:
            self._resize_section = -1
            self._update_resize_cursor(self._event_pos_x(event))
            try:
                event.accept()
            except Exception:
                pass
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._resize_section < 0:
            self.setCursor(_ArrowCursor)
        super().leaveEvent(event)


class FileTableView(QTreeView):
    """Compatibility wrapper while list mode migrates from QTreeWidget APIs."""

    def setHeaderLabels(self, labels) -> None:
        try:
            self._header_labels = list(labels)
        except Exception:
            self._header_labels = []


# ── 缩略图 delegate（缩略图 + 星级徽章）───────────────────────────────────────

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
        rating = _metadata_rating_value(meta)
        pick = _metadata_pick_value(meta)
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
        new_rating = _metadata_rating_value(meta)
        if entry.rating != new_rating:
            entry.rating = new_rating
            changed_roles.append(_MetaRatingRole)
        new_pick = _metadata_pick_value(meta)
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
        self.dataChanged.emit(idx, idx, list(dict.fromkeys(changed_roles + [_DisplayRole])))
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
        rating = index.data(_MetaRatingRole)
        pick = index.data(_MetaPickRole)
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

            def draw_badge(text: str, bg: QColor, fg: QColor, *, left: bool) -> None:
                f2 = QFont(opt.font)
                f2.setPixelSize(11)
                painter.setFont(f2)
                fm2 = painter.fontMetrics()
                try:
                    sw = fm2.horizontalAdvance(text)
                except AttributeError:
                    sw = fm2.width(text)
                bw2, bh2 = sw + 10, 16
                if left:
                    badge2 = QRect(draw_rect.left() + 2, draw_rect.top() + 2, bw2, bh2)
                else:
                    badge2 = QRect(draw_rect.right() - bw2 - 2, draw_rect.top() + 2, bw2, bh2)
                painter.setBrush(QBrush(bg))
                painter.setPen(_NoPen)
                painter.drawRoundedRect(badge2, 4, 4)
                painter.setPen(fg)
                painter.drawText(badge2, _AlignCenter, text)

            if pick == 1:
                draw_badge("🏆", QColor(0, 0, 0, 160), QColor(COLORS["star_gold"]), left=True)
            elif pick == -1:
                draw_badge("🚫", QColor(0, 0, 0, 160), QColor("#ffffff"), left=True)

            try:
                rating_value = int(rating or 0)
            except Exception:
                rating_value = 0
            if rating_value > 0:
                draw_badge(
                    "★" * min(5, rating_value),
                    QColor(0, 0, 0, 140),
                    QColor(_STAR_SILVER_COLOR),
                    left=False,
                )

            text_rect = QRect(cell.left(), thumb_rect.bottom() + 4, cell.width(), name_height)
            text_color = opt.palette.highlightedText().color() if selected else opt.palette.text().color()
            painter.setPen(text_color)
            painter.setFont(opt.font)
            elided = fm.elidedText(name, _ElideRight, text_rect.width())
            painter.drawText(text_rect, _AlignCenter, elided)
        finally:
            painter.restore()


__all__ = [name for name in globals() if not name.startswith('__')]
