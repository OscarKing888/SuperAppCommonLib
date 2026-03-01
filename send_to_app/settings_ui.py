# -*- coding: utf-8 -*-
"""
发送到外部应用 - 设置 UI：编辑 extern_app.json 中的应用列表。
由主程序在需要时打开（如菜单「外部应用设置」），config_dir 由主程序传入。
跨平台：Windows / macOS，选择应用时按平台使用不同文件过滤器。
"""
from __future__ import annotations

import os
import sys
from typing import Callable

from . import config as _config


def _get_app_file_filter():
    """按平台返回「选择应用」文件过滤器：macOS 用 .app，Windows 用 .exe。"""
    if sys.platform == "darwin":
        return "Applications (*.app);;All Files (*)"
    if sys.platform == "win32":
        return "Executables (*.exe);;All Files (*)"
    return "All Files (*)"


def _get_app_browse_start_dir():
    """按平台返回「选择应用」对话框的初始目录。"""
    if sys.platform == "darwin":
        return "/Applications"
    if sys.platform == "win32":
        return os.environ.get("ProgramFiles", "C:\\Program Files")
    return os.path.expanduser("~")


def _qt():
    try:
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QLabel,
            QLineEdit,
            QFileDialog,
            QMessageBox,
            QDialogButtonBox,
            QFormLayout,
            QGroupBox,
        )
        from PyQt6.QtCore import Qt
        return (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
            QPushButton, QLabel, QLineEdit, QFileDialog, QMessageBox,
            QDialogButtonBox, QFormLayout, QGroupBox, Qt,
        )
    except ImportError:
        from PyQt5.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QLabel,
            QLineEdit,
            QFileDialog,
            QMessageBox,
            QDialogButtonBox,
            QFormLayout,
            QGroupBox,
        )
        from PyQt5.QtCore import Qt
        return (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
            QPushButton, QLabel, QLineEdit, QFileDialog, QMessageBox,
            QDialogButtonBox, QFormLayout, QGroupBox, Qt,
        )


def show_external_apps_settings_dialog(
    parent,
    config_dir: str | None = None,
    on_saved: Callable[[], None] | None = None,
) -> None:
    """
    显示「外部应用」设置对话框，从 extern_app.json 读写。
    config_dir: 与主程序同目录，若为 None 则使用 send_to_app 默认目录。
    on_saved: 保存后回调（可用来刷新菜单等）。
    """
    (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
        QPushButton, QLabel, QLineEdit, QFileDialog, QMessageBox,
        QDialogButtonBox, QFormLayout, QGroupBox, Qt,
    ) = _qt()

    data = _config.load_config(config_dir=config_dir)
    apps = list(data.get("apps") or [])

    dlg = QDialog(parent)
    dlg.setWindowTitle("发送到外部应用 - 设置")
    layout = QVBoxLayout(dlg)

    group = QGroupBox("已配置的应用")
    group_layout = QVBoxLayout(group)
    list_widget = QListWidget()
    list_widget.setMinimumHeight(120)
    for a in apps:
        list_widget.addItem(QListWidgetItem(f"{a.get('name', '')}  |  {a.get('path', '')}"))
    group_layout.addWidget(list_widget)

    btn_layout = QHBoxLayout()
    add_btn = QPushButton("添加")
    edit_btn = QPushButton("编辑")
    remove_btn = QPushButton("删除")
    btn_layout.addWidget(add_btn)
    btn_layout.addWidget(edit_btn)
    btn_layout.addWidget(remove_btn)
    btn_layout.addStretch()
    group_layout.addLayout(btn_layout)
    layout.addWidget(group)

    def current_index():
        return list_widget.currentRow()

    def apply_changes():
        list_widget.clear()
        for a in apps:
            list_widget.addItem(QListWidgetItem(f"{a.get('name', '')}  |  {a.get('path', '')}"))

    def on_add():
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("显示名称")
        path_edit = QLineEdit()
        path_edit.setPlaceholderText("应用路径（.app 或可执行文件）")
        path_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览…")
        form = QFormLayout()
        form.addRow("名称:", name_edit)
        row = QHBoxLayout()
        row.addWidget(path_edit)
        row.addWidget(browse_btn)
        form.addRow("路径:", row)
        sub = QDialog(parent)
        sub.setWindowTitle("添加外部应用")
        sub_layout = QVBoxLayout(sub)
        sub_layout.addLayout(form)
        _OkCancel = (
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bb = QDialogButtonBox(_OkCancel)
        sub_layout.addWidget(bb)

        def choose_path():
            start = _get_app_browse_start_dir()
            p = QFileDialog.getOpenFileName(sub, "选择应用", start, _get_app_file_filter())[0]
            if p:
                path_edit.setText(p)
        browse_btn.clicked.connect(choose_path)

        def accept():
            name = name_edit.text().strip()
            path = path_edit.text().strip()
            if not path:
                QMessageBox.warning(sub, "提示", "请选择应用路径。")
                return
            apps.append({"name": name or os.path.basename(path), "path": path})
            apply_changes()
            sub.accept()
        bb.accepted.connect(accept)
        bb.rejected.connect(sub.reject)
        sub.exec()

    def on_edit():
        i = current_index()
        if i < 0 or i >= len(apps):
            QMessageBox.information(dlg, "提示", "请先选中一项。")
            return
        a = apps[i]
        name_edit = QLineEdit(a.get("name", ""))
        path_edit = QLineEdit(a.get("path", ""))
        path_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览…")
        form = QFormLayout()
        form.addRow("名称:", name_edit)
        row = QHBoxLayout()
        row.addWidget(path_edit)
        row.addWidget(browse_btn)
        form.addRow("路径:", row)
        sub = QDialog(parent)
        sub.setWindowTitle("编辑外部应用")
        sub_layout = QVBoxLayout(sub)
        sub_layout.addLayout(form)
        _OkCancel = (
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bb = QDialogButtonBox(_OkCancel)
        sub_layout.addWidget(bb)

        def choose_path():
            start = path_edit.text() or _get_app_browse_start_dir()
            p = QFileDialog.getOpenFileName(sub, "选择应用", start, _get_app_file_filter())[0]
            if p:
                path_edit.setText(p)
        browse_btn.clicked.connect(choose_path)

        def accept():
            name = name_edit.text().strip()
            path = path_edit.text().strip()
            if not path:
                QMessageBox.warning(sub, "提示", "请选择应用路径。")
                return
            apps[i] = {"name": name or os.path.basename(path), "path": path}
            apply_changes()
            sub.accept()
        bb.accepted.connect(accept)
        bb.rejected.connect(sub.reject)
        sub.exec()

    def on_remove():
        i = current_index()
        if i < 0 or i >= len(apps):
            QMessageBox.information(dlg, "提示", "请先选中一项。")
            return
        apps.pop(i)
        apply_changes()

    add_btn.clicked.connect(on_add)
    edit_btn.clicked.connect(on_edit)
    remove_btn.clicked.connect(on_remove)

    _SaveCancel = (
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        if hasattr(QDialogButtonBox.StandardButton, "Save")
        else QDialogButtonBox.Save | QDialogButtonBox.Cancel
    )
    bb = QDialogButtonBox(_SaveCancel)
    layout.addWidget(bb)

    def save():
        try:
            _config.save_config(apps, config_dir=config_dir)
            if on_saved:
                on_saved()
            dlg.accept()
        except Exception as e:
            QMessageBox.critical(dlg, "错误", f"保存失败：{e}")

    bb.accepted.connect(save)
    bb.rejected.connect(dlg.reject)

    dlg.exec()
