"""可编辑或只选列表共用的过滤下拉框；搜索与原编辑框分别持有输入焦点。"""
from __future__ import annotations

from typing import Any
try:
    from PyQt6.QtCore import QEvent, QModelIndex, QSortFilterProxyModel, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QComboBox, QFrame, QLabel, QLineEdit, QListView, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent, QModelIndex, QSortFilterProxyModel, Qt, QTimer, pyqtSignal
    from PyQt5.QtWidgets import QApplication, QComboBox, QFrame, QLabel, QLineEdit, QListView, QVBoxLayout, QWidget


class _FilterModel(QSortFilterProxyModel):
    def __init__(self, combo, parent):
        super().__init__(parent)
        self.words = ()
        self.column = combo.modelColumn()
        self.root = combo.rootModelIndex()
        self.setSourceModel(combo.model())

    def set_query(self, query: str) -> None:
        self.words = tuple(query.casefold().split())
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        if not self.words or parent != self.root:
            return True
        index = self.sourceModel().index(row, self.column, parent)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        value = index.data(Qt.ItemDataRole.UserRole)
        haystack = f"{text} {value}".casefold()
        return all(word in haystack for word in self.words)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        value = super().data(index, role)
        if role == Qt.ItemDataRole.ToolTipRole and not value:
            return super().data(index, Qt.ItemDataRole.DisplayRole)
        return value


class _FilterPopup(QFrame):
    def __init__(self, combo: "FilterableComboBox") -> None:
        # macOS 的 Qt.Popup 不成为原生键盘窗口：focusWidget 虽变了，
        # QWindow.focusObject 仍可能指向原组合框，中文输入也会写回原字段。
        # Tool 窗口能真正接收键盘/输入法；关闭语义由组件统一维护。
        super().__init__(combo, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.combo = combo
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("filterableComboPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.search = QLineEdit(self)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(combo._filter_placeholder_text)
        self.search.setObjectName("filterableComboFilterEdit")
        layout.addWidget(self.search)
        self.setFocusProxy(self.search)

        # 直接查看组合框模型。字体后台就绪时，已打开的搜索结果也会随之更新。
        self.proxy = _FilterModel(combo, self)
        self.results = QListView(self)
        self.results.setUniformItemSizes(True)
        self.results.setObjectName("filterableComboList")
        self.results.setModel(self.proxy)
        self.results.setModelColumn(combo.modelColumn())
        layout.addWidget(self.results, 1)
        self.empty = QLabel(combo._filter_empty_text, self)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty.setEnabled(False)
        layout.addWidget(self.empty, 1)

        self.search.textChanged.connect(self.set_query)
        self.search.returnPressed.connect(self.choose_current)
        self.results.clicked.connect(self.choose)
        self.results.activated.connect(self.choose)
        source = combo.model()
        for signal in (source.modelReset, source.rowsInserted, source.rowsRemoved, source.dataChanged,
                       source.layoutChanged):
            signal.connect(self.select_current)
        combo.currentIndexChanged.connect(self.select_current)
        self.select_current()

    def set_query(self, text: str) -> None:
        self.proxy.set_query(text)
        self.select_current()

    def select_current(self) -> None:
        root = self.combo.rootModelIndex()
        view_root = self.proxy.mapFromSource(root)
        self.results.setRootIndex(view_root)
        has_rows = self.proxy.rowCount(view_root) > 0
        self.empty.setVisible(not has_rows)
        self.results.setVisible(has_rows)
        if has_rows:
            column = self.combo.modelColumn()
            current = self.combo.model().index(self.combo.currentIndex(), column, root)
            index = self.proxy.mapFromSource(current)
            if not index.isValid():
                index = self.proxy.index(0, column, view_root)
            self.results.setCurrentIndex(index)
            self.results.scrollTo(index)

    def choose_current(self) -> None:
        self.choose(self.results.currentIndex())

    def choose(self, index: QModelIndex) -> None:
        if not index.isValid() or self.combo._filter_popup is not self:
            return
        flags = index.flags()
        if not (flags & Qt.ItemFlag.ItemIsEnabled and flags & Qt.ItemFlag.ItemIsSelectable):
            return
        row = self.proxy.mapToSource(index).row()
        label = self.combo.itemText(row)
        self.combo.setCurrentIndex(row)
        self.combo.hidePopup()
        self.combo.window().activateWindow()
        self.combo.setFocus(Qt.FocusReason.PopupFocusReason)
        self.combo.activated.emit(row)
        self.combo.textActivated.emit(label)


    def hideEvent(self, event) -> None:
        self.combo._clear_filter_popup_refs(self)
        super().hideEvent(event)
        self.deleteLater()


class FilterableComboBox(QComboBox):
    """下拉列表顶部内置过滤框，适合长字段/字体列表。"""

    popupAboutToShow = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filter_placeholder_text = "过滤..."
        self._filter_empty_text = "无匹配结果"
        self._filter_popup: _FilterPopup | None = None
        self._filter_popup_filter: QLineEdit | None = None
        self._filter_popup_list: QListView | None = None

    def setFilterPlaceholderText(self, text: str) -> None:
        self._filter_placeholder_text = str(text or "").strip() or "过滤..."
        if self._filter_popup_filter is not None:
            self._filter_popup_filter.setPlaceholderText(self._filter_placeholder_text)

    def setFilterEmptyText(self, text: str) -> None:
        self._filter_empty_text = text
        if self._filter_popup is not None:
            self._filter_popup.empty.setText(text)

    def hidePopup(self) -> None:  # type: ignore[override]
        popup = self._filter_popup
        if popup is not None:
            self._clear_filter_popup_refs(popup)
            popup.hide()
            popup.deleteLater()
        super().hidePopup()

    def showPopup(self) -> None:  # type: ignore[override]
        self.popupAboutToShow.emit()
        self.hidePopup()

        popup = _FilterPopup(self)
        filter_edit, list_widget = popup.search, popup.results
        self._filter_popup = popup
        self._filter_popup_filter = filter_edit
        self._filter_popup_list = list_widget
        QApplication.instance().installEventFilter(self)

        # 从整个目录抽取最多一屏名称，兼顾不同文字的宽度，避免打开时测量每一行。
        visible_rows = max(6, min(max(self.maxVisibleItems(), 8), 24))
        metrics = list_widget.fontMetrics()
        step = max(1, (self.count() + visible_rows - 1) // visible_rows)
        text_width = max(
            (metrics.horizontalAdvance(self.itemText(row)) for row in range(0, self.count(), step)),
            default=0,
        )
        text_width = max(text_width, metrics.horizontalAdvance(self.currentText()))
        popup_width = max(self.width(), min(max(text_width + 72, 260), 760))
        row_height = max(list_widget.sizeHintForRow(0), list_widget.fontMetrics().height() + 8)
        popup_height = filter_edit.sizeHint().height() + row_height * visible_rows + 24
        popup.resize(popup_width, popup_height)
        position = self.mapToGlobal(self.rect().bottomLeft())
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            popup.resize(min(popup.width(), available.width()), min(popup.height(), available.height()))
            if position.y() + popup.height() > available.bottom() + 1:
                position.setY(self.mapToGlobal(self.rect().topLeft()).y() - popup.height())
            position.setX(max(available.left(), min(position.x(), available.right() + 1 - popup.width())))
            position.setY(max(available.top(), min(position.y(), available.bottom() + 1 - popup.height())))
        popup.move(position)
        popup.show()
        popup.activateWindow()
        filter_edit.setFocus(Qt.FocusReason.PopupFocusReason)
        # 等本次点击和窗口激活完成后再交接焦点，避免 macOS/Windows 将焦点留在组合框。
        focus_timer = QTimer(popup)
        focus_timer.setSingleShot(True)
        focus_timer.timeout.connect(lambda: self._focus_filter_popup(popup))
        focus_timer.start(0)

    def _focus_filter_popup(self, popup: QFrame) -> None:
        # 已关闭或被替换的弹窗不能抢走其他控件的焦点。
        if (
            self._filter_popup is popup
            and popup.isVisible()
            and popup.isActiveWindow()
            and self._filter_popup_filter is not None
        ):
            self._filter_popup_filter.setFocus(Qt.FocusReason.PopupFocusReason)

    def _clear_filter_popup_refs(self, popup: QFrame) -> None:
        if self._filter_popup is not popup:
            return
        self._filter_popup = None
        self._filter_popup_filter = None
        self._filter_popup_list = None
        QApplication.instance().removeEventFilter(self)

    def hideEvent(self, event) -> None:
        self.hidePopup()
        super().hideEvent(event)

    def eventFilter(self, watched: Any, event: Any) -> bool:  # type: ignore[override]
        popup = self._filter_popup
        if popup is None:
            return super().eventFilter(watched, event)
        if (
            # Qt 会把父窗口的失活事件向子控件传播；此时工具弹窗本身可能刚激活。
            (watched is popup and event.type() == QEvent.Type.WindowDeactivate
             and not popup.isActiveWindow())
            or event.type() == QEvent.Type.ApplicationDeactivate
        ):
            self.hidePopup()
        elif event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            if watched is not popup and not popup.isAncestorOf(watched):
                self.hidePopup()
                # 再次点击原下拉框仅关闭，不能在同一次点击中重新打开。
                if watched is self or self.isAncestorOf(watched):
                    return True
        elif event.type() == QEvent.Type.KeyPress and (
            watched is popup or (isinstance(watched, QWidget) and popup.isAncestorOf(watched))
        ):
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hidePopup()
                self.window().activateWindow()
                self.setFocus(Qt.FocusReason.PopupFocusReason)
                return True
            if watched is self._filter_popup_list and key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                popup.choose_current()
                return True
            if watched is self._filter_popup_filter and key in {
                Qt.Key.Key_Down,
                Qt.Key.Key_PageDown,
            }:
                if self._filter_popup_list is not None:
                    self._filter_popup_list.setFocus(Qt.FocusReason.TabFocusReason)
                return True
            if (
                watched is self._filter_popup_list
                and key == Qt.Key.Key_Up
                and self._filter_popup_list.currentIndex().row() <= 0
            ):
                if self._filter_popup_filter is not None:
                    self._filter_popup_filter.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().eventFilter(watched, event)
