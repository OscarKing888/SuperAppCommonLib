# -*- coding: utf-8 -*-
"""File list panel implementation for app_common.file_browser."""
from __future__ import annotations

from app_common.file_browser._browser_core import *
from app_common.file_browser._models import *
from app_common.file_browser._thumbnail import *
from app_common.file_browser._workers import *

class FileListPanel(QWidget):
    """
    图像文件列表面板。

    - 列表模式：含「文件名/标题/颜色/星级/城市/省区/国家」七列，可点击列头排序。
    - 缩略图模式：图标网格，缩略图显示文件名与星级/Pick 标记，
      工具栏滑块可选 128/256/512/1024 px 四档大小。
    """

    # 子类可重载为 False 以不创建过滤栏（filter_bar）
    create_filter_bar = True
    # 子类可重载为 False，以纯文件系统 + sidecar 模式工作，不读取/写入 report.db。
    use_report_db = True
    # 子类可重载为 False，避免使用 .superpicky/cache 下的派生预览图与持久缩略图。
    use_preview_cache = True

    file_selected = pyqtSignal(str)
    file_fast_preview_requested = pyqtSignal(str)
    files_loaded = pyqtSignal(object)
    focus_cache_batch_ready = pyqtSignal(object)
    _MODE_LIST  = 0
    _MODE_THUMB = 1
    _WHEEL_SCROLL_ROWS = 3
    _WHEEL_ANGLE_STEP = 120

    def __init__(self, parent=None, *, create_filter_bar: bool | None = None) -> None:
        super().__init__(parent)
        self._all_files: list = []
        self._filtered_files: list = []
        self._current_dir = ""
        self._report_root_dir: str | None = None  # 当前使用的 report 根目录（含 .superpicky 的目录）
        self._report_full_root_dir: str | None = None
        self._report_full_cache: dict | None = None
        self._meta_proxy = PhotoMetaDataProxy(report_db=PhotoMetaDataReportDB())
        self._view_mode = self._MODE_THUMB
        self._thumb_size = 128
        self._thumbnail_loader: ThumbnailLoader | None = None
        self._metadata_loader:  MetadataLoader  | None = None
        self._directory_scan_worker: DirectoryScanWorker | None = None
        self._file_table_model = FileTableModel(self)
        self._file_table_proxy = FileTableSortProxyModel(self)
        self._file_table_proxy.setSourceModel(self._file_table_model)
        self._thumb_list_model = ThumbnailListModel(self)
        self._meta_cache:    dict = {}   # norm_path → metadata dict
        self._directory_scope_cache: dict[bool, dict] = {}  # 当前目录 shallow/recursive 两个 scope 的文件列表缓存
        self._report_cache:  dict = {}   # stem → report row (当前目录/子树筛出的 report 子集)
        self._report_row_by_path: dict = {}
        self._loaded_directory_recursive: bool = False
        self._requested_directory_recursive: bool = False
        self._pending_loaders: list = []
        self._path_lookup_pending: set[str] = set()
        self._path_lookup_workers: list[PathLookupWorker] = []
        self._meta_apply_timer: QTimer | None = None
        self._meta_apply_items: list = []
        self._meta_apply_index: int = 0
        self._meta_apply_total: int = 0
        self._meta_apply_expected_total: int = 0
        self._meta_apply_started_at: float = 0.0
        self._meta_apply_loop_started_at: float = 0.0
        self._meta_apply_tree_hits: int = 0
        self._meta_apply_list_hits: int = 0
        self._meta_apply_needs_filter: bool = False
        self._meta_apply_loader_finished: bool = True
        self._meta_filter_refresh_timer: QTimer | None = None
        self._use_report_db = bool(getattr(type(self), "use_report_db", True))
        self._use_preview_cache = bool(getattr(type(self), "use_preview_cache", True))
        if not self._use_report_db:
            self._meta_proxy.report_db.update_cache({})
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
        self._selection_key_nav_hold_active: bool = False
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
        self._selection_scroll_debug_events: deque[str] = deque(maxlen=120)
        self._selection_scroll_debug_total: int = 0
        self._selection_scroll_debug_flushed: bool = False
        self._selection_visibility_restore_path: str = ""
        self._selection_visibility_restore_budget: int = 0
        self._wheel_angle_remainder_by_view: dict[str, int] = {"tree": 0, "thumb": 0}
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

        self._btn_thumb = QToolButton()
        self._btn_thumb.setText("⊞")
        self._btn_thumb.setToolTip("缩略图视图")
        self._btn_thumb.setCheckable(True)
        self._btn_thumb.setChecked(self._view_mode == self._MODE_THUMB)
        self._btn_thumb.setFixedWidth(28)
        self._btn_thumb.clicked.connect(lambda: self._set_view_mode(self._MODE_THUMB))

        self._btn_list = QToolButton()
        self._btn_list.setText("≡")
        self._btn_list.setToolTip("列表视图")
        self._btn_list.setCheckable(True)
        self._btn_list.setChecked(self._view_mode == self._MODE_LIST)
        self._btn_list.setFixedWidth(28)
        self._btn_list.clicked.connect(lambda: self._set_view_mode(self._MODE_LIST))

        self._size_slider = QSlider(_Horizontal)
        self._size_slider.setRange(0, len(_THUMB_SIZE_STEPS) - 1)
        self._size_slider.setValue(0)
        self._size_slider.setFixedWidth(90)
        self._size_slider.setTickPosition(_TicksBelow)
        self._size_slider.setTickInterval(1)
        self._size_slider.setPageStep(1)
        self._size_slider.setToolTip("调整缩略图尺寸；列表模式下也会影响快速预览使用的小图尺寸")
        self._size_slider.valueChanged.connect(self._on_size_slider_changed)

        self._size_label = QLabel(f"{_THUMB_SIZE_STEPS[0]}px")
        self._size_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self._size_label.setFixedWidth(42)
        self._size_label.setToolTip("当前缩略图/快速预览尺寸")

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
            self._filter_edit.textChanged.connect(self._on_filter_text_changed)
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
            # filter_bar.addWidget(self._btn_filter_pick)

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
                # filter_bar.addWidget(btn)

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
                # filter_bar.addWidget(btn)

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
        self._tree_widget.setHeader(FileTableHeaderView(self._tree_widget))

        # @Agents: 这个列名不要修改
        # 城市 = 锐度值（越高越清晰）
        # 省/直辖市/自治区 = 美学评分（越高越好看）
        # 国家/地区 = 对焦状态（精焦/合焦/偏移/失焦）
        # 🏆 奖杯 = Pick 精选旗标（双维度都出色）
        # 🟢 绿色标签 = 飞鸟
        # 🔴 红色标签 = 精焦（对焦点在鸟头）
        self._tree_widget.setHeaderLabels(_FILE_TABLE_HEADERS)
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
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(True)
        hdr.setSectionsMovable(False)
        hdr.setMouseTracking(True)
        hdr.setSortIndicatorShown(True)
        hdr.sortIndicatorChanged.connect(self._on_tree_sort_indicator_changed)
        self._tree_widget.selectionModel().currentChanged.connect(self._on_tree_current_item_changed)
        self._tree_widget.selectionModel().selectionChanged.connect(self._on_view_selection_changed)
        for col in range(len(_FILE_TABLE_HEADERS)):
            hdr.setSectionResizeMode(col, _ResizeInteractive)
        self._tree_widget.setColumnWidth(_TREE_COL_SEQ, 44)
        self._tree_widget.setColumnWidth(_TREE_COL_NAME, 190)
        self._tree_widget.setColumnWidth(_TREE_COL_TITLE, 150)
        self._tree_widget.setColumnWidth(_TREE_COL_COLOR, 86)
        self._tree_widget.setColumnWidth(_TREE_COL_STAR, 72)
        self._tree_widget.setColumnWidth(_TREE_COL_SHARP, 96)
        self._tree_widget.setColumnWidth(_TREE_COL_AESTHETIC, 96)
        self._tree_widget.setColumnWidth(_TREE_COL_FOCUS, 108)
        self._tree_widget.setColumnWidth(_TREE_COL_SHUTTER, 84)
        self._tree_widget.setColumnWidth(_TREE_COL_ISO, 72)
        self._tree_widget.setColumnWidth(_TREE_COL_APERTURE, 72)
        self._apply_tree_sort(_TREE_COL_NAME, _AscendingOrder, sync_indicator=True)
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
        self._sync_wheel_scroll_steps()

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

        self._stack.setCurrentIndex(0 if self._view_mode == self._MODE_LIST else 1)
        if self._view_mode == self._MODE_THUMB:
            self._update_thumb_display()
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
        self._file_action_shortcuts: list[QShortcut] = []
        self._install_file_action_shortcut("Q", "reject")
        self._install_file_action_shortcut("`", "pick")
        self._install_file_action_shortcut("~", "pick")

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

    def _install_file_action_shortcut(self, sequence: str, action_kind: str) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self)
        try:
            shortcut.setContext(_WindowShortcut)
        except Exception:
            pass
        try:
            shortcut.setAutoRepeat(False)
        except Exception:
            pass
        shortcut.activated.connect(lambda kind=action_kind: self._trigger_active_shortcut_action(kind))
        self._file_action_shortcuts.append(shortcut)

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

    def _capture_selection_restore_state(self) -> tuple[list[str], str]:
        """按路径保存当前选中状态，供过滤重建/视图切换后恢复。"""
        selected_paths = self._active_view_selected_paths()
        current_path = self._active_view_current_path()
        if not selected_paths and current_path:
            selected_paths = [current_path]
        if not selected_paths and self._selected_display_path:
            selected_paths = [self._selected_display_path]
        if not current_path and selected_paths:
            current_path = selected_paths[0]
        return self._unique_norm_paths(selected_paths), (os.path.normpath(current_path) if current_path else "")

    def _selection_restore_target(self, selected_paths: list[str], current_path: str = "") -> str:
        """Return the path that filter/view rebuilds should keep visible."""
        for path in selected_paths or []:
            norm_path = os.path.normpath(path) if path else ""
            if norm_path:
                return norm_path
        return os.path.normpath(current_path) if current_path else ""

    def _restore_selection_after_view_change(
        self,
        selected_paths: list[str],
        current_path: str = "",
        *,
        reason: str,
        apply_immediately: bool = True,
    ) -> None:
        target_path = self._selection_restore_target(selected_paths, current_path)
        if not target_path:
            self._update_selection_status()
            return
        restore_paths = selected_paths or [target_path]
        self._request_selection_visibility_restore(target_path, budget=5, reason=reason)
        self.set_pending_selection(
            restore_paths,
            current_path=target_path,
            apply_immediately=apply_immediately,
        )
        self._schedule_selection_visibility_restore(
            target_path,
            reason=reason,
            delays_ms=(0, 40, 120, 300, 600),
        )

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

    def _store_directory_scope_cache(
        self,
        *,
        recursive: bool,
        files: list[str],
        report_cache: dict,
        report_row_by_path: dict,
    ) -> None:
        self._directory_scope_cache[bool(recursive)] = {
            "files": list(files),
            "report_cache": dict(report_cache or {}),
            "report_row_by_path": dict(report_row_by_path or {}),
        }

    def _get_cached_directory_scope(self, recursive: bool) -> dict | None:
        cached = self._directory_scope_cache.get(bool(recursive))
        if not isinstance(cached, dict):
            return None
        files = cached.get("files")
        report_cache = cached.get("report_cache")
        report_row_by_path = cached.get("report_row_by_path")
        if not isinstance(files, list) or not isinstance(report_cache, dict) or not isinstance(report_row_by_path, dict):
            return None
        return {
            "files": list(files),
            "report_cache": dict(report_cache),
            "report_row_by_path": dict(report_row_by_path),
        }

    def _collect_uncached_metadata_paths(self, paths: list[str]) -> list[str]:
        uncached: list[str] = []
        for path in paths:
            norm_path = os.path.normpath(path) if path else ""
            if not norm_path:
                continue
            if norm_path not in self._meta_cache:
                uncached.append(path)
        return uncached

    def _apply_directory_listing_result(
        self,
        path: str,
        files: list[str],
        report_cache: dict,
        full_report_cache,
        *,
        recursive: bool,
        report_row_by_path: dict | None = None,
        from_cache: bool = False,
    ) -> None:
        if path != self._current_dir:
            _log.info("[_apply_directory_listing_result] IGNORE stale path=%r current=%r", path, self._current_dir)
            return
        if not self._use_report_db:
            report_cache = {}
            full_report_cache = None
            report_row_by_path = {}
            self._report_full_root_dir = None
            self._report_full_cache = None
        if self._report_root_dir:
            self._report_full_root_dir = self._report_root_dir
            if full_report_cache is not None:
                self._report_full_cache = full_report_cache
            _log.info(
                "[_apply_directory_listing_result] full report cache root=%r entries=%s",
                self._report_full_root_dir,
                len(self._report_full_cache or {}),
            )
        if not from_cache and _DEBUG_FILE_LIST_LIMIT > 0 and len(files) > _DEBUG_FILE_LIST_LIMIT:
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
        self._report_cache = dict(report_cache or {})
        self._meta_proxy.report_db.update_cache(self._report_full_cache or self._report_cache)
        if report_row_by_path is None:
            report_row_by_path = {}
            row_cache_for_path_map = self._report_full_cache or self._report_cache or {}
            for p in files:
                norm_p = os.path.normpath(p) if p else ""
                if not norm_p:
                    continue
                row = row_cache_for_path_map.get(Path(norm_p).stem)
                if isinstance(row, dict):
                    report_row_by_path[norm_p] = row
        self._report_row_by_path = dict(report_row_by_path or {})
        self._all_files = list(files)
        self._loaded_directory_recursive = bool(recursive)
        self._store_directory_scope_cache(
            recursive=recursive,
            files=self._all_files,
            report_cache=self._report_cache,
            report_row_by_path=self._report_row_by_path,
        )
        _log.info(
            "[_apply_directory_listing_result] apply files=%s report_entries=%s recursive=%s from_cache=%s",
            len(self._all_files),
            len(self._report_cache),
            recursive,
            from_cache,
        )
        self._rebuild_views()
        self.files_loaded.emit(self.get_display_file_paths())
        if self._pending_selection_paths:
            self._apply_pending_selection()
            if self._view_mode != self._MODE_THUMB or not self._thumb_model_dirty:
                self._pending_selection_paths = None
                self._pending_selection_current_path = ""
        uncached_meta_paths = self._collect_uncached_metadata_paths(self._all_files)
        if uncached_meta_paths:
            _log.info(
                "[_apply_directory_listing_result] start metadata loader missing=%s cached=%s total=%s",
                len(uncached_meta_paths),
                max(0, len(self._all_files) - len(uncached_meta_paths)),
                len(self._all_files),
            )
            self._start_metadata_loader(uncached_meta_paths)
        else:
            _log.info(
                "[_apply_directory_listing_result] metadata already cached for all files total=%s",
                len(self._all_files),
            )
        self._schedule_persistent_thumb_cache_build(self._all_files)

    def _refresh_filter_scope(self) -> None:
        if not self._current_dir or not os.path.isdir(self._current_dir):
            self._apply_filter()
            return
        target_recursive = self._has_any_filter()
        if target_recursive == self._loaded_directory_recursive:
            self._apply_filter()
            return
        # 从当前目录切换到递归范围时，先在现有数据集上即时过滤一版，随后异步补齐子目录结果。
        if target_recursive and not self._loaded_directory_recursive:
            self._apply_filter()
        selected_paths, current_path = self._capture_selection_restore_state()
        if selected_paths:
            self._restore_selection_after_view_change(
                selected_paths,
                current_path,
                reason="refresh_filter_scope",
                apply_immediately=False,
            )
        self.load_directory(
            self._current_dir,
            force_reload=True,
            preserve_meta_cache=True,
            reuse_cached_listing=True,
        )

    def load_directory(
        self,
        path: str,
        force_reload: bool = False,
        *,
        preserve_meta_cache: bool = False,
        reuse_cached_listing: bool = False,
    ) -> None:
        """
        扫描目录，加载支持的图像文件。扫描与 report 加载在后台线程执行，避免阻塞 UI。
        当任意过滤条件开启（文本 / 🏆精选 / 星级 / 对焦）时，递归遍历该目录及所有子目录（不进入 . 开头目录）；
        否则仅当前目录。过滤切换同目录 scope 时可复用当前内存中的文件列表和 metadata 缓存，避免重复全量读取。
        """
        recursive = self._has_any_filter()
        same_dir = path == self._current_dir
        _log.info(
            "[load_directory] 选中目录，将扫描并列出图像文件、随后查询 EXIF path=%r force_reload=%s recursive=%s preserve_meta_cache=%s reuse_cached_listing=%s",
            path,
            force_reload,
            recursive,
            preserve_meta_cache,
            reuse_cached_listing,
        )
        _log.info(
            "[load_directory] START path=%r force_reload=%s recursive=%s preserve_meta_cache=%s reuse_cached_listing=%s",
            path,
            force_reload,
            recursive,
            preserve_meta_cache,
            reuse_cached_listing,
        )
        if not force_reload and same_dir and recursive == self._loaded_directory_recursive:
            _log.info("[load_directory] SKIP same dir")
            return
        self._current_dir = path
        if not same_dir:
            self._directory_scope_cache.clear()
            self._loaded_directory_recursive = False
        # 选择目录后，向上最多查找 4 层最近的 report 根目录；子目录共用同一个 report.db。
        # 纯 sidecar 模式子类会关闭该行为，直接扫描原始目录。
        new_report_root_dir = find_report_root(path, max_levels=4) if self._use_report_db else None
        if new_report_root_dir != self._report_root_dir:
            _log.info(
                "[load_directory] report_root_dir changed old=%r new=%r",
                self._report_root_dir,
                new_report_root_dir,
            )
        self._report_root_dir = new_report_root_dir
        self._meta_proxy.report_db.update_report_root(self._report_root_dir)
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
        if same_dir and reuse_cached_listing:
            cached_scope = self._get_cached_directory_scope(recursive)
            if cached_scope is not None:
                _log.info(
                    "[load_directory] reuse cached directory scope recursive=%s files=%s report_entries=%s",
                    recursive,
                    len(cached_scope["files"]),
                    len(cached_scope["report_cache"]),
                )
                self._selected_display_path = ""
                self._apply_directory_listing_result(
                    path,
                    cached_scope["files"],
                    cached_scope["report_cache"],
                    self._report_full_cache,
                    recursive=recursive,
                    report_row_by_path=cached_scope["report_row_by_path"],
                    from_cache=True,
                )
                _log.info("[load_directory] END reused cached scope")
                return
        # Folder-level FIFO eviction: release QImages cached for any directory
        # other than the one we are about to enter.  This ensures that browsing
        # through many large folders cannot accumulate an unbounded number of
        # cached thumbnails in RAM — only the current folder's images are kept.
        # Old-folder thumbnails remain on the disk cache and reload quickly if
        # the user navigates back.
        _evicted = self._thumb_memory_cache.evict_other_dirs(_path_key(path))
        if _evicted:
            _log.info("[load_directory] evicted %.1f MB from other dirs", _evicted / (1024 * 1024))
        if not same_dir or not preserve_meta_cache:
            self._directory_scope_cache.clear()
            self._meta_cache.clear()
            self._report_cache = {}
            self._report_row_by_path = {}
            self._selected_display_path = ""
            self._all_files = []
            _log.info("[load_directory] _rebuild_views (empty)")
            self._rebuild_views()
        else:
            _log.info(
                "[load_directory] preserve same-dir meta cache cache_size=%s loaded_recursive=%s target_recursive=%s",
                len(self._meta_cache),
                self._loaded_directory_recursive,
                recursive,
            )
        self._requested_directory_recursive = recursive
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
            use_report_db=self._use_report_db,
            parent=self,
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
        recursive = self._requested_directory_recursive
        _log.info(
            "[_on_directory_scan_finished] apply scan result recursive=%s files=%s report_entries=%s",
            recursive,
            len(files),
            len(report_cache),
        )
        self._apply_directory_listing_result(
            path,
            files,
            report_cache,
            full_report_cache,
            recursive=recursive,
        )
        self._directory_scan_worker = None
        _log.info("[_on_directory_scan_finished] END")

    def get_current_dir(self) -> str:
        """返回当前选中的目录路径（与 load_directory 的 path 一致）。"""
        return self._current_dir or ""

    def get_display_file_paths(self) -> list[str]:
        """返回当前文件列表的稳定快照，供后台预热类任务复用。"""
        preferred = self._filtered_files or self._all_files
        return [os.path.normpath(path) for path in preferred if path]

    def get_report_cache(self) -> dict:
        """返回当前目录的 report 缓存：stem（不含扩展名）→ report 行 dict。无缓存时返回空 dict。"""
        return self._report_cache

    def get_report_row_for_path(self, path: str) -> dict | None:
        row = self._get_report_row_for_path(path)
        return dict(row) if isinstance(row, dict) else None

    def get_photo_metadata_for_path(self, path: str, *, allow_slow_read: bool = False) -> dict:
        """
        Return metadata for one photo through the panel's PhotoMetaDataProxy.

        Fast path uses already-loaded browser metadata or report.db cache.  Set
        ``allow_slow_read=True`` only for committed selections where a direct
        EXIF/XMP read is acceptable.
        """
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return {}
        candidates = [norm_path]
        selected_display = os.path.normpath(self._selected_display_path) if self._selected_display_path else ""
        if selected_display and selected_display not in candidates:
            candidates.append(selected_display)
        for candidate in candidates:
            cached = self._meta_cache.get(candidate)
            if isinstance(cached, dict) and cached:
                return dict(cached)
        for candidate in candidates:
            try:
                report_data = self._meta_proxy.report_db.read(candidate)
            except Exception:
                report_data = {}
            if isinstance(report_data, dict) and report_data:
                return dict(report_data)
        if not allow_slow_read:
            return {}
        try:
            data = self._meta_proxy.read(norm_path)
        except Exception:
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def get_photo_exposure_settings_for_path(
        self,
        path: str,
        *,
        allow_slow_read: bool = False,
    ) -> tuple[str, str, str]:
        """
        Return display-ready ``(shutter, aperture, iso)`` using PhotoMetaDataProxy.

        Cached browser metadata and report.db rows are used first; committed
        selections may fall back to direct proxy reads.
        """
        metadata = self.get_photo_metadata_for_path(path, allow_slow_read=False)
        exposure = extract_exposure_settings(metadata)
        if any(exposure) or not allow_slow_read:
            return exposure
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return exposure
        try:
            return self._meta_proxy.read_exposure_settings(norm_path)
        except Exception:
            return exposure

    def sync_metadata_edit_for_path(
        self,
        path: str,
        *,
        report_fields: dict | None = None,
        meta_updates: dict | None = None,
    ) -> bool:
        norm_path = os.path.normpath(path) if path else ""
        report_fields = {
            str(k): v
            for k, v in (report_fields or {}).items()
            if str(k) in {"title", "caption"} and v is not None
        }
        meta_updates = {
            str(k): v
            for k, v in (meta_updates or {}).items()
            if str(k) and v is not None
        }
        if not norm_path or (not report_fields and not meta_updates):
            return False

        filename = ""
        row = self._get_report_row_for_path(norm_path)
        if isinstance(row, dict):
            filename = str(row.get("filename") or "").strip()
        if not filename:
            filename = str(Path(norm_path).stem or "").strip()

        db_updated = False
        if self._use_report_db and report_fields and filename:
            db_dir = self._report_root_dir or self._current_dir
            db = ReportDB.open_if_exists(db_dir) if db_dir else None
            if db is not None:
                try:
                    if isinstance(row, dict) or db.get_photo(filename):
                        db_updated = bool(db.update_photo(filename, report_fields))
                finally:
                    db.close()

        refresh_paths: list[str] = []
        if filename:
            for cache in (self._report_full_cache, self._report_cache):
                cache_row = cache.get(filename) if isinstance(cache, dict) else None
                if isinstance(cache_row, dict):
                    cache_row.update(report_fields)
            for mapped_path, mapped_row in list((self._report_row_by_path or {}).items()):
                mapped_filename = str((mapped_row or {}).get("filename") or Path(mapped_path).stem or "").strip()
                if mapped_filename != filename:
                    continue
                if isinstance(mapped_row, dict):
                    mapped_row.update(report_fields)
                refresh_paths.append(os.path.normpath(mapped_path))

        if norm_path not in refresh_paths:
            refresh_paths.append(norm_path)

        if meta_updates:
            for refresh_path in refresh_paths:
                meta = self._meta_cache.get(refresh_path)
                if not isinstance(meta, dict):
                    meta = {}
                    self._meta_cache[refresh_path] = meta
                meta.update(meta_updates)

        if meta_updates:
            self._refresh_metadata_state_for_paths(refresh_paths)

        return db_updated or bool(meta_updates)

    def set_pending_selection(
        self,
        paths: list,
        current_path: str | None = None,
        *,
        apply_immediately: bool = True,
    ) -> None:
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
        if (
            apply_immediately
            and self._current_dir
            and (
                self._tree_row_count() > 0
                or self._thumb_row_count() > 0
                or self._thumb_model_dirty
            )
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
                    self._record_selection_scroll_debug(
                        "apply_pending.tree",
                        current_target,
                        row=idx_first.row(),
                        view_mode=self._view_mode,
                    )
                    self._scroll_path_into_view(current_target, prefer_active=False)
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
                self._record_selection_scroll_debug(
                    "apply_pending.thumb",
                    current_target,
                    row=idx_first.row(),
                    view_mode=self._view_mode,
                )
                self._scroll_path_into_view(current_target, prefer_active=False)
            self._schedule_selection_visibility_restore(current_target, reason="apply_pending_selection")
            self._emit_file_selected_for_path(current_target)
        self._update_selection_status()

    def _record_selection_scroll_debug(self, event: str, path: str = "", **fields) -> None:
        """缓存选中/滚动诊断信息，退出时统一汇总输出，避免运行中刷屏。"""
        self._selection_scroll_debug_total += 1
        line_parts = [f"{self._selection_scroll_debug_total:03d}", event]
        if path:
            line_parts.append(f"path={os.path.normpath(path)!r}")
        for key, value in fields.items():
            line_parts.append(f"{key}={value!r}")
        self._selection_scroll_debug_events.append(" ".join(line_parts))

    def _request_selection_visibility_restore(self, path: str, *, budget: int = 3, reason: str = "") -> None:
        """记录一次短期的“选中项需要保持可见”请求，供后续刷新/排序后补定位。"""
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        self._selection_visibility_restore_path = norm_path
        self._selection_visibility_restore_budget = max(self._selection_visibility_restore_budget, max(1, int(budget)))
        self._record_selection_scroll_debug(
            "restore.request",
            norm_path,
            budget=self._selection_visibility_restore_budget,
            reason=reason,
        )

    def _schedule_selection_visibility_restore(
        self,
        path: str,
        *,
        reason: str = "",
        delays_ms: tuple[int, ...] = (0, 30, 120, 250),
    ) -> None:
        """
        对当前激活视图做几次延迟补定位，覆盖异步 layout / 缩略图补模后的可见性恢复。
        """
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        normalized_delays: list[int] = []
        seen: set[int] = set()
        for delay in delays_ms:
            try:
                delay_ms = max(0, int(delay))
            except Exception:
                delay_ms = 0
            if delay_ms in seen:
                continue
            seen.add(delay_ms)
            normalized_delays.append(delay_ms)
        self._record_selection_scroll_debug(
            "restore.schedule",
            norm_path,
            reason=reason,
            delays=tuple(normalized_delays),
            view_mode=self._view_mode,
        )
        for delay_ms in normalized_delays:
            QTimer.singleShot(delay_ms, lambda p=norm_path: self._scroll_path_into_view(p))

    def _replay_selection_visibility_restore(self, reason: str) -> None:
        """在后续 tree refresh / sort 完成后，按需再次确保选中项可见。"""
        norm_path = self._selection_visibility_restore_path
        if not norm_path or self._selection_visibility_restore_budget <= 0:
            return
        self._selection_visibility_restore_budget = max(0, self._selection_visibility_restore_budget - 1)
        self._record_selection_scroll_debug(
            "restore.replay",
            norm_path,
            budget=self._selection_visibility_restore_budget,
            reason=reason,
            view_mode=self._view_mode,
        )
        self._schedule_selection_visibility_restore(norm_path, reason=f"replay:{reason}")
        if self._selection_visibility_restore_budget <= 0:
            self._selection_visibility_restore_path = ""

    def _flush_selection_scroll_debug_summary(self) -> None:
        if self._selection_scroll_debug_flushed:
            return
        self._selection_scroll_debug_flushed = True
        kept = len(self._selection_scroll_debug_events)
        if kept <= 0:
            return
        dropped = max(0, self._selection_scroll_debug_total - kept)
        lines = "\n".join(self._selection_scroll_debug_events)
        _log.info(
            "[selection_scroll_debug_summary] total=%s kept=%s dropped=%s\n%s",
            self._selection_scroll_debug_total,
            kept,
            dropped,
            lines,
        )

    def _scroll_path_into_view(self, path: str, *, prefer_active: bool = True) -> None:
        """按路径滚动到目标项，必要时补设当前项。"""
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        target_mode = self._view_mode if prefer_active else None

        def _scroll_tree() -> bool:
            idx = self._tree_index_for_path(norm_path)
            if not idx.isValid():
                self._record_selection_scroll_debug("scroll.tree.miss", norm_path)
                return False
            if target_mode in (None, self._MODE_LIST):
                self._tree_widget.setCurrentIndex(idx)
            bar = self._tree_widget.verticalScrollBar()
            before = bar.value() if bar is not None else None
            rect_before = self._tree_widget.visualRect(idx)
            self._tree_widget.scrollTo(idx, _PositionAtCenter)
            rect_after = self._tree_widget.visualRect(idx)
            after = bar.value() if bar is not None else None
            self._record_selection_scroll_debug(
                "scroll.tree",
                norm_path,
                row=idx.row(),
                target_mode=target_mode,
                scroll_before=before,
                scroll_after=after,
                rect_before=(rect_before.x(), rect_before.y(), rect_before.width(), rect_before.height()),
                rect_after=(rect_after.x(), rect_after.y(), rect_after.width(), rect_after.height()),
            )
            return True

        def _scroll_thumb() -> bool:
            idx = self._thumb_index_for_path(norm_path)
            if not idx.isValid():
                self._record_selection_scroll_debug("scroll.thumb.miss", norm_path)
                return False
            if target_mode in (None, self._MODE_THUMB):
                self._list_widget.setCurrentIndex(idx)
            bar = self._list_widget.verticalScrollBar()
            before = bar.value() if bar is not None else None
            rect_before = self._list_widget.visualRect(idx)
            self._list_widget.scrollTo(idx, _PositionAtCenter)
            rect_after = self._list_widget.visualRect(idx)
            after = bar.value() if bar is not None else None
            self._record_selection_scroll_debug(
                "scroll.thumb",
                norm_path,
                row=idx.row(),
                target_mode=target_mode,
                scroll_before=before,
                scroll_after=after,
                rect_before=(rect_before.x(), rect_before.y(), rect_before.width(), rect_before.height()),
                rect_after=(rect_after.x(), rect_after.y(), rect_after.width(), rect_after.height()),
            )
            return True

        if target_mode == self._MODE_LIST:
            _scroll_tree()
            return
        if target_mode == self._MODE_THUMB:
            _scroll_thumb()
            return
        tree_ok = _scroll_tree()
        thumb_ok = _scroll_thumb()
        if not tree_ok and not thumb_ok:
            return

    def _scroll_to_selected_display_path(self) -> None:
        """Scroll the active view so that _selected_display_path is visible."""
        path = self._selected_display_path
        if not path:
            return
        self._scroll_path_into_view(path)

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
        if not self._use_report_db:
            _log.info("[_paste_species_to_paths] skip reason=report_db_disabled")
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

    def _rating_toggle_target_for_paths(self, paths: list[str], target_rating: int) -> int:
        unique_paths = self._unique_norm_paths(paths)
        rating_value = max(0, min(5, int(target_rating)))
        if rating_value <= 0 or not unique_paths:
            return rating_value
        all_target_rating = True
        for path in unique_paths:
            rating, _pick = self._rating_state_for_path(path)
            if rating != rating_value:
                all_target_rating = False
                break
        return 0 if all_target_rating else rating_value

    def _paths_for_active_shortcut_action(self) -> list[str]:
        paths = self._active_view_selected_paths()
        if paths:
            return self._unique_norm_paths(paths)
        current_path = self._active_view_current_path()
        if current_path:
            return [os.path.normpath(current_path)]
        return []

    def _event_has_blocked_shortcut_modifier(self, event, *, include_shift: bool = False) -> bool:
        if event is None:
            return False
        blocked_modifiers = [_ControlModifier, _AltModifier, _MetaModifier]
        if include_shift:
            blocked_modifiers.append(_ShiftModifier)
        modifiers = event.modifiers()
        for blocked_modifier in blocked_modifiers:
            if blocked_modifier is None:
                continue
            try:
                if modifiers & blocked_modifier:
                    return True
            except Exception:
                continue
        return False

    def _toggle_rating_for_paths(self, paths: list[str], target_rating: int) -> None:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return
        rating_target = self._rating_toggle_target_for_paths(unique_paths, target_rating)
        if rating_target <= 0:
            self._set_rating_state_for_paths(unique_paths, rating=0)
            return

        rejected_paths: list[str] = []
        other_paths: list[str] = []
        for path in unique_paths:
            _rating, pick = self._rating_state_for_path(path)
            if pick == -1:
                rejected_paths.append(path)
            else:
                other_paths.append(path)

        if other_paths:
            self._set_rating_state_for_paths(other_paths, rating=rating_target)
        if rejected_paths:
            # 给星级时只清除排除标记，不影响已有 Pick。
            self._set_rating_state_for_paths(rejected_paths, rating=rating_target, pick=0)

    def _toggle_pick_for_paths(self, paths: list[str]) -> None:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return
        self._set_rating_state_for_paths(unique_paths, pick=self._pick_target_for_paths(unique_paths))

    def _toggle_reject_for_paths(self, paths: list[str]) -> None:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return
        self._set_rating_state_for_paths(unique_paths, pick=self._reject_target_for_paths(unique_paths))

    def _shortcut_text_from_event(self, event) -> str:
        text = str(event.text() or "")
        if text:
            return text
        if _key_matches(event.key(), _KeyPeriod):
            return "."
        if _key_matches(event.key(), _KeyQ):
            return "q"
        if _key_matches(event.key(), _KeyQuoteLeft):
            return "`"
        if _key_matches(event.key(), _KeyAsciiTilde):
            return "~"
        return ""

    def _shortcut_rating_value_from_event(self, event, text: str) -> int:
        if len(text) == 1:
            try:
                digit = int(unicodedata.digit(text))
            except Exception:
                digit = 0
            if 1 <= digit <= 5:
                return digit
        if text:
            return 0
        try:
            key = event.key()
            value = _KeyRatingDigits.get(key)
            if value is None:
                value = _KeyRatingDigits.get(int(key))
            return int(value or 0)
        except Exception:
            return 0

    def _shortcut_is_pick_keypress(self, event, text: str) -> bool:
        if text in {".", "\u00b7", "\uff0e", "\u3002", "`", "\uff40", "~", "\uff5e"}:
            return True
        if text:
            return False
        return (
            _key_matches(event.key(), _KeyPeriod)
            or _key_matches(event.key(), _KeyQuoteLeft)
            or _key_matches(event.key(), _KeyAsciiTilde)
        )

    def _shortcut_is_reject_keypress(self, event, text: str) -> bool:
        return text.lower() == "q" or _key_matches(event.key(), _KeyQ)

    def _keyboard_shortcut_focus_allows_file_action(self) -> bool:
        focus = QApplication.focusWidget()
        if focus is None:
            return True
        try:
            if focus.window() is not self.window():
                return False
        except Exception:
            pass
        if isinstance(focus, (QLineEdit, QComboBox)):
            return False
        try:
            class_name = str(focus.metaObject().className())
        except Exception:
            class_name = focus.__class__.__name__
        blocked_name_parts = ("LineEdit", "TextEdit", "PlainTextEdit", "SpinBox", "ComboBox")
        return not any(part in class_name for part in blocked_name_parts)

    def _trigger_active_shortcut_action(self, action_kind: str, action_value: int = 0) -> None:
        if not self._keyboard_shortcut_focus_allows_file_action():
            return
        paths = self._paths_for_active_shortcut_action()
        if not paths:
            return
        if action_kind == "rating":
            self._toggle_rating_for_paths(paths, action_value)
        elif action_kind == "pick":
            self._toggle_pick_for_paths(paths)
        elif action_kind == "reject":
            self._toggle_reject_for_paths(paths)

    def _handle_rating_shortcut_keypress(self, event) -> bool:
        if event is None:
            return False
        if self._event_has_blocked_shortcut_modifier(event):
            return False

        text = self._shortcut_text_from_event(event)
        action_kind = ""
        action_value = 0
        rating_value = self._shortcut_rating_value_from_event(event, text)
        if rating_value:
            action_kind = "rating"
            action_value = rating_value
        elif self._shortcut_is_reject_keypress(event, text):
            action_kind = "reject"
        elif self._shortcut_is_pick_keypress(event, text):
            action_kind = "pick"
        else:
            return False

        try:
            if event.isAutoRepeat():
                return True
        except Exception:
            pass

        paths = self._paths_for_active_shortcut_action()
        if not paths:
            return True
        if action_kind == "rating":
            self._toggle_rating_for_paths(paths, action_value)
        elif action_kind == "pick":
            self._toggle_pick_for_paths(paths)
        else:
            self._toggle_reject_for_paths(paths)
        return True

    def _handle_delete_shortcut_keypress(self, event) -> bool:
        if event is None or event.key() != _KeyDelete:
            return False
        if self._event_has_blocked_shortcut_modifier(event, include_shift=True):
            return False
        try:
            if event.isAutoRepeat():
                return True
        except Exception:
            pass
        paths = self._paths_for_active_shortcut_action()
        if paths:
            self._move_paths_to_trash(paths)
        return True

    def _resolve_rating_write_source(
        self,
        path: str,
        *,
        report_db_available: bool,
    ) -> str:
        if self._use_report_db and report_db_available:
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
            self._apply_tree_sort(
                self._tree_last_sort_column,
                self._tree_last_sort_order,
                sync_indicator=True,
            )
            self._refresh_tree_row_numbers()
            if self._view_mode == self._MODE_LIST:
                self._replay_selection_visibility_restore("refresh_metadata_state.sort")

        self._tree_widget.viewport().update()
        if self._view_mode == self._MODE_THUMB:
            self._list_widget.viewport().update()

        if self._filters_require_rebuild_after_metadata_refresh(unique_paths):
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
        if not self._use_report_db:
            _log.info("[_apply_rating_state_via_report_db] skip reason=report_db_disabled")
            return []
        db_dir = self._report_root_dir or self._current_dir
        db = ReportDB.open_if_exists(db_dir) if db_dir else None
        if db is None:
            _log.warning("[_apply_rating_state_via_report_db] db_dir=%r open failed", db_dir)
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
                    ok = db.update_photo(filename, data)
                    if not ok:
                        db.insert_photo({"filename": filename, **data})
                        ok = True
                except Exception as exc:
                    _log.warning(
                        "[_apply_rating_state_via_report_db] source=%r filename=%r write failed: %s",
                        path,
                        filename,
                        exc,
                    )
                    continue
                if not ok:
                    _log.warning("[_apply_rating_state_via_report_db] source=%r filename=%r write failed", path, filename)
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
                fields = {}
                for a in assignments:
                    if a.startswith("-") and "=" in a:
                        tag, _, val = a[1:].partition("=")
                        fields[tag] = val
                if not self._meta_proxy.exif.write(target_path, fields):
                    raise RuntimeError("exif write returned False")
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
        db_exists = False
        db_dir = self._report_root_dir or self._current_dir
        if self._use_report_db and db_dir:
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
            action = rating_menu.addAction(f"{'★' * stars}\t{stars}")
            action.triggered.connect(
                lambda checked=False, value=stars: self._toggle_rating_for_paths(unique_paths, value)
            )
        rating_menu.addSeparator()
        pick_target = self._pick_target_for_paths(unique_paths)
        pick_label = "取消🏆 Pick" if pick_target == 0 else "🏆 Pick"
        pick_action = rating_menu.addAction(pick_label)
        pick_action.triggered.connect(
            lambda checked=False: self._toggle_pick_for_paths(unique_paths)
        )
        pick_action.setText(f"{pick_label}\t`")
        reject_target = self._reject_target_for_paths(unique_paths)
        reject_label = "取消🚫 排除" if reject_target == 0 else "🚫 标记为排除"
        reject_action = rating_menu.addAction(reject_label)
        reject_action.triggered.connect(
            lambda checked=False: self._toggle_reject_for_paths(unique_paths)
        )
        reject_action.setText(f"{reject_label}\tQ")

    def _add_delete_menu_action(self, menu: QMenu, paths: list[str]) -> None:
        unique_paths = self._unique_norm_paths(paths)
        if not unique_paths:
            return
        act_delete = menu.addAction("删除\tDel")
        act_delete.triggered.connect(lambda: self._move_paths_to_trash(unique_paths))

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
        if not norm_path or not self._use_preview_cache:
            return ""
        preview_base_dir = self._report_root_dir or self._current_dir
        report_cache = self._report_full_cache or self._report_cache or {}
        if preview_base_dir:
            preview_target = get_preview_path_for_file(norm_path, preview_base_dir, report_cache)
            if preview_target and os.path.isfile(preview_target):
                return preview_target
        actual_path = self._get_actual_path_for_display(norm_path)
        return actual_path or norm_path

    def _resolve_existing_sized_preview_image_path(self, path: str) -> str:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path or not self._use_preview_cache:
            return ""
        preview_base_dir = self._report_root_dir or self._current_dir
        if not preview_base_dir:
            return ""
        actual_path = self._get_actual_path_for_display(norm_path)
        report_cache = self._report_full_cache or self._report_cache or {}
        source_path = actual_path or norm_path
        thumb_source = _resolve_thumb_source_path(source_path, report_cache, preview_base_dir)
        source_stamp = _thumb_source_stamp(source_path, thumb_source)
        persistent_thumb_path = _existing_persistent_thumb_cache_path_for_file(
            source_path,
            preview_base_dir,
            requested_size=self._thumb_size,
            source_stamp=source_stamp,
            candidate_sizes=_effective_persistent_thumb_cache_sizes(self._thumb_size),
        )
        if persistent_thumb_path:
            return persistent_thumb_path
        if thumb_source and os.path.isfile(thumb_source):
            try:
                thumb_mtime = float(os.path.getmtime(thumb_source))
            except Exception:
                thumb_mtime = 0.0
            thumb_disk_path = _thumb_disk_cache_path(thumb_source, thumb_mtime, self._thumb_size)
            if thumb_disk_path and os.path.isfile(thumb_disk_path):
                return thumb_disk_path
        return ""

    def _resolve_existing_selected_preview_image_path(self, path: str) -> str:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path or not self._use_preview_cache:
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
        if not self._use_preview_cache:
            return actual_path or norm_path
        preview_base_dir = self._report_root_dir or self._current_dir
        report_cache = self._report_full_cache or self._report_cache or {}
        source_path = actual_path or norm_path
        if prefer_fast_preview:
            thumb_source = _resolve_thumb_source_path(source_path, report_cache, preview_base_dir)
            source_stamp = _thumb_source_stamp(source_path, thumb_source)
            persistent_thumb_path = _existing_persistent_thumb_cache_path_for_file(
                source_path,
                preview_base_dir,
                requested_size=self._thumb_size,
                source_stamp=source_stamp,
                candidate_sizes=_effective_persistent_thumb_cache_sizes(self._thumb_size),
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
        filtered: list[str] = []
        for path in self._all_files:
            if self._path_matches_active_filters(path):
                filtered.append(path)
        return filtered

    def _path_matches_filters(
        self,
        path: str,
        *,
        filter_text: str = "",
        filter_pick: bool = False,
        filter_min_rating: int = 0,
        filter_focus_status: str = "",
    ) -> bool:
        norm = os.path.normpath(path) if path else ""
        if not norm:
            return False
        name = Path(norm).name
        meta = self._meta_cache.get(norm, {})
        try:
            pick = int(meta.get("pick", 0) or 0)
        except Exception:
            pick = 0
        try:
            rating = int(meta.get("rating", 0) or 0)
        except Exception:
            rating = 0
        if filter_text and filter_text not in name.lower():
            return False
        if filter_pick and pick != 1:
            return False
        if rating < filter_min_rating:
            return False
        if filter_focus_status and _focus_status_to_display(meta.get("country", "")) != filter_focus_status:
            return False
        return True

    def _path_matches_active_filters(self, path: str) -> bool:
        return self._path_matches_filters(
            path,
            filter_text=(self._filter_edit.text().strip().lower()) if self._filter_edit else "",
            filter_pick=self._filter_pick,
            filter_min_rating=self._filter_min_rating,
            filter_focus_status=self._filter_focus_status,
        )

    def _filters_require_rebuild_after_metadata_refresh(self, paths: list[str]) -> bool:
        """
        仅当 metadata 更新真的改变了过滤成员资格时，才重建过滤结果。

        缩略图模式下改星级/精选时，大多数情况只是 badge 变化，不应该因为过滤器
        正处于激活状态就整表 `_apply_filter()`，否则当前视图和选中项会跳动。
        """
        if not (self._filter_pick or self._filter_min_rating > 0 or self._filter_focus_status):
            return False
        if not paths:
            return False
        visible_keys = {
            os.path.normcase(os.path.normpath(path))
            for path in self._filtered_files
            if path
        }
        for norm_path in self._unique_norm_paths(paths):
            key = os.path.normcase(norm_path)
            was_visible = key in visible_keys
            is_visible = self._path_matches_active_filters(norm_path)
            if was_visible != is_visible:
                return True
        return False

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

    def _apply_tree_sort(self, column: int, order, *, sync_indicator: bool = False) -> None:
        """显式驱动 proxy 排序，避免依赖不同 Qt 版本对表头点击排序的隐式行为。"""
        hdr = self._tree_widget.header()
        if sync_indicator:
            try:
                hdr.blockSignals(True)
                hdr.setSortIndicator(column, order)
            finally:
                hdr.blockSignals(False)
        model = self._tree_widget.model()
        if model is not None:
            try:
                model.sort(column, order)
                return
            except Exception:
                pass
        self._tree_widget.sortByColumn(column, order)

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
                self._apply_tree_sort(
                    self._tree_last_sort_column,
                    self._tree_last_sort_order,
                    sync_indicator=True,
                )
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
        if self._view_mode == self._MODE_THUMB and self._selection_visibility_restore_path:
            self._schedule_selection_visibility_restore(
                self._selection_visibility_restore_path,
                reason="thumb_model_population_done",
                delays_ms=(0, 40, 120),
            )
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

                ti = SortableTreeItem([str(seq), name, *([""] * (len(_FILE_TABLE_HEADERS) - 2))])
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
        ff = self._filter_focus_status
        t0 = _time.perf_counter()
        selected_paths, current_path = self._capture_selection_restore_state()
        filtered = self._compute_filtered_files()
        old_filtered = list(self._filtered_files)
        self._filtered_files = filtered
        _log.info(
            "[_apply_filter] START files=%s filtered=%s pick=%s min_rating=%s focus=%r text=%r",
            len(self._all_files),
            len(filtered),
            fp,
            fr,
            ff or "(none)",
            ft or "(none)",
        )
        tree_ready = self._view_mode != self._MODE_LIST or (not self._tree_view_dirty and self._tree_source_row_count() == len(filtered))
        thumb_ready = self._view_mode != self._MODE_THUMB or (not self._thumb_model_dirty and self._thumb_row_count() == len(filtered))
        if old_filtered == filtered and tree_ready and thumb_ready:
            self._restore_selection_after_view_change(
                selected_paths,
                current_path,
                reason="apply_filter_unchanged",
            )
            _log.info("[_apply_filter] SKIP unchanged elapsed=%.3fs", _time.perf_counter() - t0)
            return
        self._rebuild_views(stop_loaders=False)
        self._restore_selection_after_view_change(
            selected_paths,
            current_path,
            reason="apply_filter",
        )
        _log.info(
            "[_apply_filter] END visible=%s hidden=%s elapsed=%.3fs",
            len(filtered),
            max(0, len(self._all_files) - len(filtered)),
            _time.perf_counter() - t0,
        )

    def _on_filter_text_changed(self, _text: str) -> None:
        self._refresh_filter_scope()

    def _on_pick_filter_toggled(self) -> None:
        """切换精选过滤：只显示 Pick=1 的文件；仅在目录 scope 发生变化时才重扫。"""
        self._filter_pick = self._btn_filter_pick.isChecked()
        if self._filter_min_rating != 0:
            self._filter_min_rating = 0
            for btn in self._star_btns:
                btn.setChecked(False)
        self._refresh_filter_scope()

    def _on_rating_filter_changed(self, n: int) -> None:
        """切换星级过滤：只显示 ≥n 星的文件；仅在目录 scope 发生变化时才重扫。"""
        if self._filter_min_rating == n:
            self._filter_min_rating = 0
        else:
            self._filter_min_rating = n
            if self._filter_pick:
                self._filter_pick = False
                self._btn_filter_pick.setChecked(False)
        for i, btn in enumerate(self._star_btns):
            btn.setChecked(i + 1 == self._filter_min_rating)
        self._refresh_filter_scope()

    def _on_focus_filter_changed(self, status: str) -> None:
        if self._filter_focus_status == status:
            self._filter_focus_status = ""
        else:
            self._filter_focus_status = status
        for key, btn in self._focus_filter_btns.items():
            btn.setChecked(key == self._filter_focus_status)
        self._refresh_filter_scope()

    def _apply_meta_to_tree_item(self, item: SortableTreeItem, meta: dict) -> None:
        title   = meta.get("title", "")
        color   = meta.get("color", "")
        rating  = meta.get("rating", 0)
        pick    = meta.get("pick", 0)
        city    = meta.get("city", "")
        state   = meta.get("state", "")
        country = meta.get("country", "")
        shutter = str(meta.get("shutter", "") or "")
        iso = str(meta.get("iso", "") or "")
        aperture = str(meta.get("aperture", "") or "")

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
        shutter_seconds = _parse_positive_fraction_or_float(shutter)
        item.setText(_TREE_COL_SHUTTER, shutter)
        item.setData(_TREE_COL_SHUTTER, _SortRole, (0, shutter_seconds) if shutter_seconds is not None else (1, shutter.lower()))
        iso_value = _parse_optional_int(iso)
        item.setText(_TREE_COL_ISO, iso)
        item.setData(_TREE_COL_ISO, _SortRole, (0, iso_value) if iso_value is not None else (1, iso.lower()))
        aperture_value = _parse_positive_fraction_or_float(aperture)
        item.setText(_TREE_COL_APERTURE, aperture)
        item.setData(_TREE_COL_APERTURE, _SortRole, (0, aperture_value) if aperture_value is not None else (1, aperture.lower()))
        focus_color = _FOCUS_STATUS_TEXT_COLORS.get(country, "")
        if focus_color:
            item.setForeground(_TREE_COL_FOCUS, QBrush(QColor(focus_color)))
        else:
            item.setForeground(_TREE_COL_FOCUS, QBrush())

    # ── 视图模式切换 ────────────────────────────────────────────────────────────
    def _view_uses_pixel_scroll(self, view) -> bool:
        try:
            return view.verticalScrollMode() == _ScrollPerPixel
        except Exception:
            return False

    def _tree_wheel_row_unit(self) -> int:
        if self._view_uses_pixel_scroll(self._tree_widget):
            row_height = 0
            try:
                row_height = int(self._tree_widget.sizeHintForRow(0))
            except Exception:
                row_height = 0
            if row_height <= 0:
                try:
                    row_height = int(self._tree_widget.fontMetrics().height()) + 6
                except Exception:
                    row_height = 22
            return max(1, row_height)
        return 1

    def _thumb_wheel_row_unit(self) -> int:
        if self._view_uses_pixel_scroll(self._list_widget):
            grid_height = 0
            try:
                grid_height = int(self._list_widget.gridSize().height())
            except Exception:
                grid_height = 0
            if grid_height <= 0:
                grid_height = int(self._thumb_size) + 46
            return max(1, grid_height)
        return 1

    def _sync_wheel_scroll_steps(self) -> None:
        try:
            self._tree_widget.verticalScrollBar().setSingleStep(
                max(1, self._WHEEL_SCROLL_ROWS * self._tree_wheel_row_unit())
            )
        except Exception:
            pass
        try:
            self._list_widget.verticalScrollBar().setSingleStep(
                max(1, self._WHEEL_SCROLL_ROWS * self._thumb_wheel_row_unit())
            )
        except Exception:
            pass

    def _handle_bounded_view_wheel(self, view_key: str, view, row_unit: int, event) -> bool:
        bar = view.verticalScrollBar() if view is not None else None
        if bar is None:
            return False
        angle_y = 0
        pixel_y = 0
        try:
            angle_y = int(event.angleDelta().y())
        except Exception:
            angle_y = 0
        try:
            pixel_y = int(event.pixelDelta().y())
        except Exception:
            pixel_y = 0
        if angle_y == 0 and pixel_y == 0:
            return False

        row_unit = max(1, int(row_unit))
        max_delta = max(1, self._WHEEL_SCROLL_ROWS * row_unit)
        if self._view_uses_pixel_scroll(view):
            try:
                viewport_limit = int(view.viewport().height() * 0.75)
            except Exception:
                viewport_limit = 0
            if viewport_limit > 0:
                max_delta = min(max_delta, max(row_unit, viewport_limit))
        if pixel_y:
            delta = max(-max_delta, min(max_delta, pixel_y))
        else:
            total = self._wheel_angle_remainder_by_view.get(view_key, 0) + angle_y
            if total >= 0:
                notches = total // self._WHEEL_ANGLE_STEP
            else:
                notches = -((-total) // self._WHEEL_ANGLE_STEP)
            self._wheel_angle_remainder_by_view[view_key] = total - notches * self._WHEEL_ANGLE_STEP
            if notches == 0:
                try:
                    event.accept()
                except Exception:
                    pass
                return True
            row_delta = int(notches) * self._WHEEL_SCROLL_ROWS
            row_delta = max(-self._WHEEL_SCROLL_ROWS, min(self._WHEEL_SCROLL_ROWS, row_delta))
            delta = max(-max_delta, min(max_delta, row_delta * row_unit))

        value = bar.value()
        target = value - delta
        target = max(bar.minimum(), min(bar.maximum(), target))
        if target != value:
            bar.setValue(target)
        try:
            event.accept()
        except Exception:
            pass
        return True

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
        if event is not None and event.type() == _EventWheel:
            if (
                self._view_mode == self._MODE_LIST
                and tree_widget is not None
                and obj in (tree_widget, tree_viewport)
            ):
                return self._handle_bounded_view_wheel(
                    "tree",
                    tree_widget,
                    self._tree_wheel_row_unit(),
                    event,
                )
            if (
                self._view_mode == self._MODE_THUMB
                and list_widget is not None
                and obj in (list_widget, list_viewport)
            ):
                return self._handle_bounded_view_wheel(
                    "thumb",
                    list_widget,
                    self._thumb_wheel_row_unit(),
                    event,
                )
        if (
            event is not None
            and event.type() == _EventKeyPress
            and (
                (
                    self._view_mode == self._MODE_LIST
                    and obj in (tree_widget, tree_viewport)
                )
                or (
                    self._view_mode == self._MODE_THUMB
                    and obj in (list_widget, list_viewport)
                )
            )
            and self._handle_rating_shortcut_keypress(event)
        ):
            return True
        if (
            event is not None
            and event.type() == _EventKeyPress
            and (
                (
                    self._view_mode == self._MODE_LIST
                    and obj in (tree_widget, tree_viewport)
                )
                or (
                    self._view_mode == self._MODE_THUMB
                    and obj in (list_widget, list_viewport)
                )
            )
            and self._handle_delete_shortcut_keypress(event)
        ):
            return True
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
                self._selection_key_nav_auto_repeat = True
                self._selection_key_nav_hold_active = True
                QTimer.singleShot(0, lambda: setattr(self, "_selection_key_nav_auto_repeat", False))
        if (
            obj is tree_widget
            and event is not None
            and event.type() == _EventKeyRelease
            and self._view_mode == self._MODE_LIST
            and tree_widget is not None
        ):
            key = event.key()
            if key in (_KeyUp, _KeyDown, _KeyLeft, _KeyRight):
                self._commit_deferred_file_selected()
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
                self._selection_key_nav_hold_active = True
                fast_preview = True
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
        if (
            obj is list_widget
            and event is not None
            and event.type() == _EventKeyRelease
            and self._view_mode == self._MODE_THUMB
            and list_widget is not None
        ):
            key = event.key()
            if key in (_KeyUp, _KeyDown, _KeyLeft, _KeyRight):
                self._commit_deferred_file_selected()
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
        # 大目录如果在这个瞬间被误判成"全部可见"，会把整个目录都丢进缩略图队列。
        # 因此这里按当前 viewport 容量做一次保守估算；只有确实装得下时才视为全部可见。
        grid = self._list_widget.gridSize()
        # 首次切到缩略图模式时，Qt layout 可能尚未完成，滚动条最大值仍为 0。
        # 大目录如果在这一瞬间被误判成"全部可见"，会把整批文件直接丢进缩略图队列。
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
        selected_paths, current_path = self._capture_selection_restore_state()
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
                self._apply_tree_sort(
                    self._tree_last_sort_column,
                    self._tree_last_sort_order,
                    sync_indicator=True,
                )
                self._refresh_tree_row_numbers()
        if selected_paths:
            self._request_selection_visibility_restore(current_path or selected_paths[0], reason="set_view_mode")
            self.set_pending_selection(selected_paths, current_path=current_path)
            self._schedule_selection_visibility_restore(
                current_path or selected_paths[0],
                reason="set_view_mode",
            )
        else:
            self._update_selection_status()

    def _update_size_controls(self) -> None:
        self._size_slider.setEnabled(True)
        self._size_label.setEnabled(True)

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
        if self._all_files and self._use_preview_cache:
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
            if self._all_files and self._use_preview_cache:
                self._schedule_persistent_thumb_cache_build(self._all_files)
            else:
                self._update_persistent_thumb_progress_widget()

    def _update_thumb_display(self) -> None:
        s = self._thumb_size
        self._list_widget.setIconSize(QSize(s, s))
        cell_w = s + 32
        cell_h = s + 46
        self._list_widget.setGridSize(QSize(cell_w, cell_h))
        self._list_widget.setSpacing(8)
        self._list_widget.doItemsLayout()
        self._sync_wheel_scroll_steps()

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

        preview_base_dir = (self._report_root_dir or self._current_dir) if self._use_preview_cache else ""
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
        _log.info(
            "[_start_metadata_loader] START paths=%s report_cache=%s full_report_cache=%s",
            len(paths),
            len(self._report_cache),
            len(self._report_full_cache or {}),
        )
        self._stop_metadata_loader()
        total = len(paths)
        if total <= 0:
            _log.info("[_start_metadata_loader] no paths, return")
            return
        self._stop_pending_meta_apply()
        self._begin_meta_apply_session(total)
        loader = MetadataLoader(
            paths,
            meta_proxy=self._meta_proxy,
            focus_source_paths=self._build_metadata_focus_source_paths(paths),
            metadata_tags=_SUPERBIRDSTAMP_BROWSER_METADATA_TAGS,
        )
        loader.progress_updated.connect(self._on_metadata_progress)
        loader.metadata_batch_ready.connect(self._on_metadata_batch_ready)
        loader.focus_cache_batch_ready.connect(self._on_metadata_focus_cache_batch_ready)
        loader.finished.connect(self._on_metadata_loader_finished)
        self._metadata_loader = loader
        loader.start()
        _log.info("[_start_metadata_loader] MetadataLoader started via PhotoMetaDataProxy")

    def _stop_metadata_loader(self) -> None:
        if self._metadata_loader:
            self._detach_loader(
                self._metadata_loader,
                self._metadata_loader.metadata_batch_ready,
                self._on_metadata_batch_ready,
            )
            try:
                self._metadata_loader.focus_cache_batch_ready.disconnect(
                    self._on_metadata_focus_cache_batch_ready
                )
            except Exception:
                pass
            try:
                self._metadata_loader.progress_updated.disconnect(
                    self._on_metadata_progress
                )
            except Exception:
                pass
            try:
                self._metadata_loader.finished.disconnect(
                    self._on_metadata_loader_finished
                )
            except Exception:
                pass
            self._metadata_loader = None
        self._meta_progress.hide()

    def _build_metadata_focus_source_paths(self, paths: list[str]) -> dict[str, str]:
        """
        为 metadata loader 构建“显示路径 -> 焦点源文件路径”的静态快照。

        这里故意不直接复用 _resolve_source_path_for_action() 的逐条日志版本，
        否则大目录批量加载时日志会被每个文件刷满。若将来改动解析规则，请同步两边逻辑。
        """
        mapping: dict[str, str] = {}
        for raw_path in paths or []:
            norm_path = os.path.normpath(raw_path) if raw_path else ""
            if not norm_path:
                continue
            actual_path = self._get_actual_path_for_display(norm_path)
            if actual_path and os.path.isfile(actual_path):
                mapping[norm_path] = os.path.normpath(actual_path)
                continue
            if os.path.isfile(norm_path):
                mapping[norm_path] = norm_path
                continue
            row = self._get_report_row_for_path(norm_path)
            cp_abs = self._resolve_report_current_abs_path(norm_path)
            if row and cp_abs:
                op = str(row.get("original_path") or "").strip()
                ext_orig = Path(op).suffix.lower() if op else ""
                if ext_orig:
                    sibling_source = os.path.normpath(str(Path(cp_abs).with_suffix(ext_orig)))
                    if os.path.isfile(sibling_source):
                        mapping[norm_path] = sibling_source
                        continue
                if os.path.isfile(cp_abs):
                    mapping[norm_path] = os.path.normpath(cp_abs)
                    continue
            mapping[norm_path] = norm_path
        return mapping

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
        self._selection_key_nav_hold_active = False

    def _schedule_deferred_file_selected(self, path: str) -> None:
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return
        self._deferred_file_selected_path = norm_path
        self._ensure_deferred_file_selected_timer()
        if self._deferred_file_selected_timer is not None and self._deferred_file_selected_timer.isActive():
            self._deferred_file_selected_timer.stop()
        if self._selection_key_nav_hold_active:
            return
        self._deferred_file_selected_timer.start(_FAST_PREVIEW_COMMIT_DELAY_MS)

    def _commit_deferred_file_selected(self) -> None:
        if self._deferred_file_selected_timer is not None and self._deferred_file_selected_timer.isActive():
            self._deferred_file_selected_timer.stop()
        path = self._deferred_file_selected_path
        self._deferred_file_selected_path = ""
        self._selection_key_nav_auto_repeat = False
        self._selection_key_nav_hold_active = False
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
        sizes = _effective_persistent_thumb_cache_sizes(self._thumb_size)
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
        if not self._use_preview_cache:
            self._persistent_thumb_cache_pending_paths = []
            self._persistent_thumb_cache_base_dir = ""
            self._persistent_thumb_cache_total = 0
            self._persistent_thumb_cache_done = 0
            self._update_persistent_thumb_progress_widget()
            return
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
            sizes=_effective_persistent_thumb_cache_sizes(self._thumb_size),
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
            _effective_persistent_thumb_cache_sizes(self._thumb_size),
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
        self._flush_selection_scroll_debug_summary()
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

    def _ensure_meta_filter_refresh_timer(self) -> None:
        if self._meta_filter_refresh_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._flush_meta_filter_refresh)
        self._meta_filter_refresh_timer = timer

    def _begin_meta_apply_session(self, expected_total: int) -> None:
        self._ensure_meta_apply_timer()
        self._ensure_meta_filter_refresh_timer()
        self._meta_apply_items = []
        self._meta_apply_index = 0
        self._meta_apply_total = 0
        self._meta_apply_expected_total = max(0, int(expected_total or 0))
        self._meta_apply_started_at = _time.perf_counter()
        self._meta_apply_loop_started_at = self._meta_apply_started_at
        self._meta_apply_tree_hits = 0
        self._meta_apply_list_hits = 0
        self._meta_apply_needs_filter = bool(self._filter_pick or self._filter_min_rating > 0 or self._filter_focus_status)
        self._meta_apply_loader_finished = False
        self._set_tree_header_fast_mode(True)
        self._tree_widget.setSortingEnabled(False)
        self._meta_progress.setMaximum(max(1, self._meta_apply_expected_total))
        self._meta_progress.setValue(0)
        self._meta_progress.show()
        _log.info(
            "[STAT][_meta_apply] begin tree_items=%s list_items=%s expected_total=%s batch=%s",
            self._tree_source_row_count(),
            self._thumb_row_count(),
            self._meta_apply_expected_total,
            _META_APPLY_BATCH_SIZE,
        )

    def _stop_pending_meta_apply(self) -> None:
        sorting_was_disabled = not self._tree_widget.isSortingEnabled()
        if self._meta_apply_timer is not None and self._meta_apply_timer.isActive():
            self._meta_apply_timer.stop()
        if self._meta_filter_refresh_timer is not None and self._meta_filter_refresh_timer.isActive():
            self._meta_filter_refresh_timer.stop()
        self._meta_apply_items = []
        self._meta_apply_index = 0
        self._meta_apply_total = 0
        self._meta_apply_expected_total = 0
        self._meta_apply_started_at = 0.0
        self._meta_apply_loop_started_at = 0.0
        self._meta_apply_tree_hits = 0
        self._meta_apply_list_hits = 0
        self._meta_apply_needs_filter = False
        self._meta_apply_loader_finished = True
        self._set_tree_header_fast_mode(False)
        if sorting_was_disabled:
            self._tree_widget.setSortingEnabled(True)
            if self._tree_row_count() > 0:
                self._apply_tree_sort(
                    self._tree_last_sort_column,
                    self._tree_last_sort_order,
                    sync_indicator=True,
                )
                self._refresh_tree_row_numbers()

    def _set_tree_header_fast_mode(self, enabled: bool) -> None:
        """批量更新期间切到轻量模式；恢复后保持列宽可手动拖拽。"""
        if enabled == self._tree_header_fast_mode:
            return
        hdr = self._tree_widget.header()
        try:
            if enabled:
                for col in (
                    _TREE_COL_TITLE,
                    _TREE_COL_COLOR,
                    _TREE_COL_STAR,
                    _TREE_COL_SHARP,
                    _TREE_COL_AESTHETIC,
                    _TREE_COL_FOCUS,
                    _TREE_COL_SHUTTER,
                    _TREE_COL_ISO,
                    _TREE_COL_APERTURE,
                ):
                    hdr.setSectionResizeMode(col, _ResizeInteractive)
                self._tree_header_fast_mode = True
            else:
                for col in range(len(_FILE_TABLE_HEADERS)):
                    hdr.setSectionResizeMode(col, _ResizeInteractive)
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
            self._apply_tree_sort(
                self._tree_last_sort_column,
                self._tree_last_sort_order,
                sync_indicator=True,
            )
            QTimer.singleShot(0, self._refresh_tree_row_numbers)
            return
        self._tree_last_sort_column = column
        self._tree_last_sort_order = order
        self._apply_tree_sort(column, order)
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

    def _schedule_meta_filter_refresh(self) -> None:
        if not self._meta_apply_needs_filter:
            return
        self._ensure_meta_filter_refresh_timer()
        if self._meta_filter_refresh_timer is None or self._meta_filter_refresh_timer.isActive():
            return
        # 过滤重建比较重，这里只做节流刷新，避免每个 chunk 都重建一次全视图。
        self._meta_filter_refresh_timer.start(120)

    def _flush_meta_filter_refresh(self) -> None:
        if not self._meta_apply_needs_filter:
            return
        _log.info(
            "[STAT][_meta_apply] incremental filter refresh applied=%s queued=%s expected=%s",
            self._meta_apply_index,
            self._meta_apply_total,
            self._meta_apply_expected_total,
        )
        self._apply_filter()
        if not self._meta_apply_loader_finished or self._meta_apply_index < self._meta_apply_total:
            self._set_tree_header_fast_mode(True)
            self._tree_widget.setSortingEnabled(False)

    def _enqueue_meta_apply(self, meta_dict: dict) -> None:
        if not meta_dict:
            return
        ordered_batch = self._order_meta_items_by_file_list(meta_dict)
        if not ordered_batch:
            return
        self._meta_apply_items.extend(ordered_batch)
        self._meta_apply_total = len(self._meta_apply_items)
        self._meta_progress.setMaximum(max(1, self._meta_apply_expected_total or self._meta_apply_total))
        self._meta_progress.setValue(min(self._meta_apply_index, self._meta_progress.maximum()))
        self._meta_progress.show()
        _log.info(
            "[STAT][_meta_apply] enqueue batch=%s queued_total=%s applied=%s expected=%s",
            len(ordered_batch),
            self._meta_apply_total,
            self._meta_apply_index,
            self._meta_apply_expected_total,
        )
        self._schedule_meta_filter_refresh()
        if self._meta_apply_timer is not None and not self._meta_apply_timer.isActive():
            self._meta_apply_timer.start(1)

    def _finish_meta_apply(self) -> None:
        if self._meta_filter_refresh_timer is not None and self._meta_filter_refresh_timer.isActive():
            self._meta_filter_refresh_timer.stop()
        sort_t0 = _time.perf_counter()
        _log.info("[STAT][_meta_apply] enabling tree sorting")
        self._set_tree_header_fast_mode(False)
        self._tree_widget.setSortingEnabled(True)
        self._apply_tree_sort(
            self._tree_last_sort_column,
            self._tree_last_sort_order,
            sync_indicator=True,
        )
        self._refresh_tree_row_numbers()
        self._replay_selection_visibility_restore("finish_meta_apply.sort")
        _log.info("[STAT][_meta_apply] tree sorting enabled elapsed=%.3fs", _time.perf_counter() - sort_t0)

        if self._view_mode == self._MODE_THUMB:
            paint_t0 = _time.perf_counter()
            self._list_widget.viewport().update()
            self._invalidate_visible_thumbnail_signature()
            self._schedule_visible_thumbnail_update()
            _log.info("[STAT][_meta_apply] list viewport updated elapsed=%.3fs", _time.perf_counter() - paint_t0)

        if self._meta_apply_needs_filter:
            _log.info("[_meta_apply] final _apply_filter")
            filter_t0 = _time.perf_counter()
            self._apply_filter()
            _log.info("[STAT][_meta_apply] _apply_filter elapsed=%.3fs", _time.perf_counter() - filter_t0)

        self._meta_progress.setValue(self._meta_progress.maximum())
        QTimer.singleShot(400, self._meta_progress.hide)
        elapsed = (_time.perf_counter() - self._meta_apply_started_at) if self._meta_apply_started_at > 0 else 0.0
        _log.info(
            "[STAT][_meta_apply] total elapsed=%.3fs applied=%s queued_total=%s expected=%s",
            elapsed,
            self._meta_apply_index,
            self._meta_apply_total,
            self._meta_apply_expected_total,
        )
        _log.info("[_meta_apply] 目录文件列表 EXIF 已全部应用 END")
        self._stop_pending_meta_apply()

    def _apply_meta_batch_tick(self) -> None:
        total = self._meta_apply_total
        if total <= 0 or self._meta_apply_index >= total:
            if self._meta_apply_timer is not None and self._meta_apply_timer.isActive():
                self._meta_apply_timer.stop()
            if self._meta_apply_loader_finished:
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
        self._meta_progress.setValue(min(end, self._meta_progress.maximum()))
        if end % 1000 == 0 or end >= total:
            _log.info(
                "[STAT][_meta_apply] apply_meta progress=%s/%s tree_hits=%s list_hits=%s expected=%s elapsed=%.3fs",
                end,
                total,
                self._meta_apply_tree_hits,
                self._meta_apply_list_hits,
                self._meta_apply_expected_total,
                _time.perf_counter() - self._meta_apply_loop_started_at,
            )

        if end >= total:
            _log.info(
                "[STAT][_meta_apply] apply_meta drained tree_hits=%s list_hits=%s loader_finished=%s elapsed=%.3fs",
                self._meta_apply_tree_hits,
                self._meta_apply_list_hits,
                self._meta_apply_loader_finished,
                _time.perf_counter() - self._meta_apply_loop_started_at,
            )
            if self._meta_apply_timer is not None:
                self._meta_apply_timer.stop()
            if self._meta_apply_loader_finished:
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
        """主线程槽：由 progress_updated 信号触发，更新 metadata 总量基线。"""
        if total <= 0:
            return
        self._meta_apply_expected_total = max(self._meta_apply_expected_total, int(total))
        self._meta_progress.setMaximum(max(1, self._meta_apply_expected_total))
        self._meta_progress.setValue(min(self._meta_apply_index, self._meta_progress.maximum()))
        _log.debug(
            "[_on_metadata_progress] loaded=%s/%s applied=%s queued=%s",
            current,
            total,
            self._meta_apply_index,
            self._meta_apply_total,
        )

    def _on_metadata_batch_ready(self, meta_dict: dict) -> None:
        _log.info("[_on_metadata_batch_ready] 收到 metadata 批次 %s 条，增量更新列表与缩略图", len(meta_dict))
        _log.info("[_on_metadata_batch_ready] START entries=%s", len(meta_dict))
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
            "[STAT][_on_metadata_batch_ready] meta_cache updated entries=%s cache_size=%s elapsed=%.3fs",
            total,
            len(self._meta_cache),
            _time.perf_counter() - t0,
        )
        _log.info(
            "[STAT][_on_metadata_batch_ready] richness title=%s color=%s rating>0=%s city=%s state=%s country=%s",
            title_cnt,
            color_cnt,
            rating_pos_cnt,
            city_cnt,
            state_cnt,
            country_cnt,
        )
        self._enqueue_meta_apply(meta_dict)

    def _on_metadata_focus_cache_batch_ready(self, focus_dict: dict) -> None:
        loader = self.sender()
        if loader is not self._metadata_loader:
            return
        if not focus_dict:
            return
        _log.info("[_on_metadata_focus_cache_batch_ready] 收到 focus 批次 %s 条", len(focus_dict))
        self.focus_cache_batch_ready.emit(focus_dict)

    def _on_metadata_loader_finished(self) -> None:
        loader = self.sender()
        if loader is not self._metadata_loader:
            return
        self._metadata_loader = None
        self._meta_apply_loader_finished = True
        _log.info(
            "[_on_metadata_loader_finished] loader finished applied=%s queued_total=%s expected=%s",
            self._meta_apply_index,
            self._meta_apply_total,
            self._meta_apply_expected_total,
        )
        if self._meta_apply_index >= self._meta_apply_total:
            self._finish_meta_apply()

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
        """在右键菜单中加入「发送到其它应用」子菜单，使用当前选中的文件列表。"""
        send_menu = menu.addMenu("发送到其它应用")
        apps = get_external_apps()
        if not apps:
            hint = send_menu.addAction("请在「文件 → 外部应用设置」中添加应用")
            hint.setEnabled(False)
            return
        base_dir = self.get_current_dir() or ""
        selected_paths = list(paths or [])
        for app in apps:
            name = (app.get("name") or app.get("path") or "未命名").strip()
            act = send_menu.addAction(name)
            act.triggered.connect(
                lambda checked=False, a=app, p=selected_paths: send_files_to_app(
                    p,
                    a,
                    base_directory=base_dir,
                )
            )

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
        sized_preview_path = self._resolve_existing_sized_preview_image_path(source_path or "")
        act_preview = menu.addAction("浏览预览图像")
        act_preview.setEnabled(bool(sized_preview_path))
        if sized_preview_path:
            _log.info(
                "[_add_browse_preview_menu_action] source=%r sized_preview=%r",
                source_path,
                sized_preview_path,
            )
            act_preview.triggered.connect(lambda checked=False, p=sized_preview_path: reveal_in_file_manager(p))

        selected_preview_path = self._resolve_existing_selected_preview_image_path(source_path or "")
        act_selected_preview = menu.addAction("浏览预览图像")
        act_selected_preview.setEnabled(bool(selected_preview_path))
        if selected_preview_path:
            _log.info(
                "[_add_browse_preview_menu_action] source=%r selected_preview=%r",
                source_path,
                selected_preview_path,
            )
            act_selected_preview.triggered.connect(
                lambda checked=False, p=selected_preview_path: reveal_in_file_manager(p)
            )

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
        #self._add_species_menu_actions(menu, self._tree_path_from_index(index) if index.isValid() else (paths[0] if paths else ""), paths)
        
        self._add_photo_tag_menu_actions(menu, paths)
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
        # self._add_browse_preview_menu_action(menu, primary_path)
        # menu.addSeparator()
        # self._add_delete_menu_action(menu, paths)
        _exec_menu(menu, self._tree_widget.viewport().mapToGlobal(pos))

    def _collect_report_filenames_for_paths(self, paths: list[str]) -> list[str]:
        filenames: list[str] = []
        seen: set[str] = set()
        for path in paths or []:
            norm_path = os.path.normpath(path) if path else ""
            row = self._get_report_row_for_path(norm_path)
            filename = str((row or {}).get("filename") or Path(norm_path).stem or "").strip()
            if not filename or filename in seen:
                continue
            seen.add(filename)
            filenames.append(filename)
        return filenames

    def _remove_report_cache_entries_for_filenames(self, filenames: list[str]) -> None:
        filename_set = {str(name or "").strip() for name in filenames if str(name or "").strip()}
        if not filename_set:
            return
        if isinstance(self._report_full_cache, dict):
            for filename in filename_set:
                self._report_full_cache.pop(filename, None)
        if isinstance(self._report_cache, dict):
            for filename in filename_set:
                self._report_cache.pop(filename, None)
        if self._report_row_by_path:
            self._report_row_by_path = {
                path: row
                for path, row in self._report_row_by_path.items()
                if str((row or {}).get("filename") or Path(path).stem or "").strip() not in filename_set
            }
        self._meta_proxy.report_db.update_cache(self._report_full_cache or self._report_cache)
        _log.info(
            "[_remove_report_cache_entries_for_filenames] removed=%s full_cache=%s selected_cache=%s path_map=%s",
            len(filename_set),
            len(self._report_full_cache or {}),
            len(self._report_cache or {}),
            len(self._report_row_by_path or {}),
        )

    def _delete_report_rows_for_paths(self, paths: list[str]) -> int:
        if not self._use_report_db:
            _log.info("[_delete_report_rows_for_paths] skip reason=report_db_disabled")
            return 0
        filenames = self._collect_report_filenames_for_paths(paths)
        if not filenames:
            _log.info("[_delete_report_rows_for_paths] skip reason=no_filenames")
            return 0
        db_dir = self._report_root_dir or self._current_dir
        db = ReportDB.open_if_exists(db_dir) if db_dir else None
        if db is None:
            _log.info("[_delete_report_rows_for_paths] skip db_dir=%r reason=no_report_db", db_dir)
            return 0
        try:
            deleted = db.delete_photos_by_filenames(filenames)
        except Exception as exc:
            _log.warning(
                "[_delete_report_rows_for_paths] db_dir=%r filenames=%s delete_failed: %s",
                db_dir,
                len(filenames),
                exc,
            )
            return 0
        finally:
            db.close()
        self._remove_report_cache_entries_for_filenames(filenames)
        _log.info(
            "[_delete_report_rows_for_paths] db_dir=%r filenames=%s deleted=%s",
            db_dir,
            len(filenames),
            deleted,
        )
        return deleted

    def _move_paths_to_trash(self, paths: list) -> None:
        """将选中路径移动到垃圾桶，并同步删除 report.db 中对应记录。"""
        if not paths:
            return
        ok_count = 0
        moved_display_paths: list[str] = []
        for p in paths:
            norm_path = os.path.normpath(p) if p else ""
            if not norm_path:
                continue
            target_path = self._resolve_source_path_for_action(norm_path)
            target_path = os.path.normpath(target_path) if target_path else norm_path
            if target_path and os.path.exists(target_path):
                if move_to_trash(target_path):
                    ok_count += 1
                    moved_display_paths.append(norm_path)
                    _log.info(
                        "[_move_paths_to_trash] moved source=%r target=%r",
                        norm_path,
                        target_path,
                    )
            else:
                _log.info(
                    "[_move_paths_to_trash] skip source=%r target=%r reason=missing",
                    norm_path,
                    target_path,
                )
        if moved_display_paths:
            self._delete_report_rows_for_paths(moved_display_paths)
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
        #self._add_species_menu_actions(menu, self._thumb_path_from_index(index) if index.isValid() else (paths[0] if paths else ""), paths)
        
        self._add_photo_tag_menu_actions(menu, paths)
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
        # self._add_browse_preview_menu_action(menu, primary_path)
        # menu.addSeparator()
        # self._add_delete_menu_action(menu, paths)
        _exec_menu(menu, self._list_widget.viewport().mapToGlobal(pos))
