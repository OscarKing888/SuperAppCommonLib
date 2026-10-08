"""可编辑或只选列表共用的过滤下拉框；搜索与原编辑框分别持有输入焦点。"""
from __future__ import annotations

from typing import Any
try:
    from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
    from PyQt5.QtWidgets import QApplication, QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


class _FilterPopup(QFrame):
    def __init__(self, combo: "FilterableComboBox") -> None:
        # macOS 的 Qt.Popup 不成为原生键盘窗口：focusWidget 虽变了，
        # QWindow.focusObject 仍可能指向原组合框，中文输入也会写回原字段。
        # Tool 窗口能真正接收键盘/输入法；关闭语义由组件统一维护。
        super().__init__(combo, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.combo = combo

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
        self._filter_popup: QFrame | None = None
        self._filter_popup_filter: QLineEdit | None = None
        self._filter_popup_list: QListWidget | None = None

    def setFilterPlaceholderText(self, text: str) -> None:
        self._filter_placeholder_text = str(text or "").strip() or "过滤..."

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
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        popup.setObjectName("filterableComboPopup")

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        filter_edit = QLineEdit(popup)
        filter_edit.setClearButtonEnabled(True)
        filter_edit.setPlaceholderText(self._filter_placeholder_text)
        filter_edit.setObjectName("filterableComboFilterEdit")
        layout.addWidget(filter_edit)
        popup.setFocusProxy(filter_edit)

        list_widget = QListWidget(popup)
        list_widget.setUniformItemSizes(True)
        list_widget.setObjectName("filterableComboList")
        layout.addWidget(list_widget)

        source_items = [
            (idx, str(self.itemText(idx) or ""), self.itemData(idx))
            for idx in range(self.count())
        ]

        def _matches(text: str, data: Any, query: str) -> bool:
            query_parts = [
                part for part in str(query or "").strip().lower().split()
                if part
            ]
            if not query_parts:
                return True
            haystack = f"{text} {data}".lower()
            return all(part in haystack for part in query_parts)

        def _refresh_list(query: str = "") -> None:
            current_combo_index = self.currentIndex()
            list_widget.blockSignals(True)
            try:
                list_widget.clear()
                selected_row = 0
                for combo_index, text, data in source_items:
                    if not _matches(text, data, query):
                        continue
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, combo_index)
                    source_index = self.model().index(combo_index, self.modelColumn(), self.rootModelIndex())
                    item.setFlags(self.model().flags(source_index) & (
                        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable))
                    item.setIcon(self.itemIcon(combo_index))
                    if text:
                        item.setToolTip(text)
                    list_widget.addItem(item)
                    if combo_index == current_combo_index:
                        selected_row = list_widget.count() - 1

                if list_widget.count() == 0:
                    empty_item = QListWidgetItem("无匹配结果")
                    empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    list_widget.addItem(empty_item)
                    selected_row = -1

                if selected_row >= 0:
                    list_widget.setCurrentRow(selected_row)
                    current_item = list_widget.item(selected_row)
                    if current_item is not None:
                        list_widget.scrollToItem(current_item)
            finally:
                list_widget.blockSignals(False)

        def _choose_item(item: QListWidgetItem | None = None) -> None:
            chosen = item or list_widget.currentItem()
            if chosen is None or not (chosen.flags() & Qt.ItemFlag.ItemIsEnabled
                                      and chosen.flags() & Qt.ItemFlag.ItemIsSelectable):
                return
            combo_index = chosen.data(Qt.ItemDataRole.UserRole)
            if combo_index is None:
                return
            try:
                self.setCurrentIndex(int(combo_index))
            except Exception:
                return
            self.hidePopup()
            self.window().activateWindow()
            self.setFocus(Qt.FocusReason.PopupFocusReason)
            # 自定义 popup 与原生 QComboBox 一样发送用户选择信号。
            self.activated.emit(int(combo_index))
            self.textActivated.emit(self.itemText(int(combo_index)))

        filter_edit.textChanged.connect(_refresh_list)
        filter_edit.returnPressed.connect(lambda: _choose_item())
        list_widget.itemClicked.connect(_choose_item)
        list_widget.itemActivated.connect(_choose_item)

        _refresh_list("")
        self._filter_popup = popup
        self._filter_popup_filter = filter_edit
        self._filter_popup_list = list_widget
        QApplication.instance().installEventFilter(self)

        text_width = max(
            (list_widget.fontMetrics().horizontalAdvance(text) for _idx, text, _data in source_items),
            default=0,
        )
        popup_width = max(self.width(), min(max(text_width + 72, 260), 760))
        visible_rows = max(6, min(max(self.maxVisibleItems(), 8), 24))
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
                current = self._filter_popup_list.currentItem()
                if current is not None:
                    self._filter_popup_list.itemActivated.emit(current)
                return True
            if watched is self._filter_popup_filter and key in {
                Qt.Key.Key_Down,
                Qt.Key.Key_PageDown,
            }:
                if self._filter_popup_list is not None:
                    if self._filter_popup_list.currentRow() < 0 and self._filter_popup_list.count() > 0:
                        self._filter_popup_list.setCurrentRow(0)
                    self._filter_popup_list.setFocus(Qt.FocusReason.TabFocusReason)
                return True
            if (
                watched is self._filter_popup_list
                and key == Qt.Key.Key_Up
                and self._filter_popup_list.currentRow() <= 0
            ):
                if self._filter_popup_filter is not None:
                    self._filter_popup_filter.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().eventFilter(watched, event)
