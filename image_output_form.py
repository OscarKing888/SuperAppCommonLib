"""Reusable output-location form; all naming semantics live in image_output_paths."""
from __future__ import annotations

try:
    from PyQt6.QtWidgets import QWidget, QFormLayout, QHBoxLayout, QComboBox, QLineEdit, QPushButton, QFileDialog
except ImportError:
    from PyQt5.QtWidgets import QWidget, QFormLayout, QHBoxLayout, QComboBox, QLineEdit, QPushButton, QFileDialog

from .image_output_paths import OUTPUT_MODES, ImageOutputOptions, validate_output_options


class ImageOutputOptionsForm(QWidget):
    def __init__(self, options, parent=None, *, prefix="photo_edit", formats=("tiff","jpeg","png")):
        super().__init__(parent)
        self.prefix = prefix
        layout = QFormLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setRowWrapPolicy(getattr(QFormLayout,"RowWrapPolicy",QFormLayout).WrapLongRows)
        self.mode = QComboBox(self)
        for value,label in OUTPUT_MODES:
            self.mode.addItem(label,value)
        self.mode.setCurrentIndex(max(0,self.mode.findData(options.get(prefix+"_output_mode","source_subdir"))))
        layout.addRow("输出位置",self.mode)
        self.subdir = QLineEdit(str(options.get(prefix+"_subdir","edited")),self)
        self.subdir.setToolTip("子目录模式使用此名称；平级目录模式使用“原目录名_此名称”。")
        layout.addRow("目录名称",self.subdir)
        self.directory = QLineEdit(str(options.get(prefix+"_output_directory","")),self)
        self.browse = QPushButton("浏览…",self)
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory,1)
        directory_row.addWidget(self.browse)
        layout.addRow("固定目录",directory_row)
        self.format = QComboBox(self)
        labels = {"tiff":"TIFF（16 位，无损）","jpeg":"JPEG（质量 95）","png":"PNG（16 位，无损）"}
        for value in formats:
            self.format.addItem(labels[value],value)
        self.format.setCurrentIndex(max(0,self.format.findData(options.get(prefix+"_format","tiff"))))
        layout.addRow("输出格式",self.format)
        self.browse.clicked.connect(self._browse)
        self.mode.currentIndexChanged.connect(self._update_mode)
        self._update_mode()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self,"选择成片目录",self.directory.text())
        if path:
            self.directory.setText(path)

    def _update_mode(self,*_):
        mode = self.mode.currentData()
        self.subdir.setEnabled(mode in {"source_subdir","sibling_dir"})
        self.directory.setEnabled(mode=="fixed")
        self.browse.setEnabled(mode=="fixed")

    def selected_options(self):
        return {self.prefix+"_"+key:value for key,value in dict(output_mode=self.mode.currentData(),
            subdir=self.subdir.text().strip(),output_directory=self.directory.text().strip(),
            format=self.format.currentData()).items()}

    def validation_error(self):
        try:
            # "ask" is resolved at export time, not in the preferences dialog.
            mode = self.mode.currentData()
            validate_output_options(ImageOutputOptions(output_mode="source_subdir" if mode=="ask" else mode,
                subdir=self.subdir.text().strip(),output_directory=self.directory.text().strip(),
                format=self.format.currentData()))
            return ""
        except ValueError as exc:
            return str(exc)
