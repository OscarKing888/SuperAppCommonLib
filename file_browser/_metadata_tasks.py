"""GUI ownership, bounded result delivery and progress for metadata actions."""
from __future__ import annotations

import html
import os
import queue
import threading
import time

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._metadata_actions import MetadataBatchWorker, sidecar_fingerprint
from app_common.file_browser._work_policy import WorkKind
try:
    from PyQt6.QtCore import QObject, QTimer, QSignalBlocker, pyqtSignal, QCoreApplication
    from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QTextEdit, QPushButton, QVBoxLayout
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer, QSignalBlocker, pyqtSignal, QCoreApplication
    from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QTextEdit, QPushButton, QVBoxLayout


class MetadataProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumSize(600, 380)
        self.running = True
        self.label, self.summary = QLabel(self), QLabel(self)
        self.label.setWordWrap(True)
        self.summary.setWordWrap(True)
        self.bar = QProgressBar(self)
        self.details = QTextEdit(self)
        self.details.setReadOnly(True)
        self.details.document().setMaximumBlockCount(1000)
        self.button = QPushButton("停止", self)
        self.button.clicked.connect(self._clicked)
        layout = QVBoxLayout(self)
        for widget in (self.label, self.bar, self.summary, self.details, self.button):
            layout.addWidget(widget, 1 if widget is self.details else 0)

    def _clicked(self):
        if self.running:
            self.cancel_requested.emit()
            self.button.setText("正在停止…")
            self.button.setEnabled(False)
        else:
            self.close()

    def reject(self):
        if self.running:
            self.cancel_requested.emit()
        super().reject()

    def closeEvent(self, event):
        if self.running:
            self.cancel_requested.emit()
        super().closeEvent(event)

    def finish(self, text):
        self.running = False
        self.label.setText(text)
        self.button.setText("关闭")
        self.button.setEnabled(True)


class MetadataCopyController(QObject):
    """Only the newest request may replace an unchanged system clipboard."""
    def __init__(self, files, parent=None):
        super().__init__(parent or files)
        self.files, self._reads = files, []
        self._generation = self._clipboard_generation = 0
        self._shutdown = False
        QApplication.clipboard().dataChanged.connect(self._clipboard_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._drain)

    def _clipboard_changed(self):
        self._clipboard_generation += 1

    @property
    def busy(self):
        return bool(self._reads)

    @property
    def pending(self):
        return any(generation == self._generation and not stop.is_set()
                   for _, stop, generation, *_ in self._reads)

    def start(self, factory, apply, failed):
        if self._shutdown or self.files._background_shutdown_requested:
            return False
        self._generation += 1
        for future, stop, *_ in self._reads:
            stop.set()
            self.files.metadata_work_pool().cancel(future)
        stop = threading.Event()
        future = self.files.metadata_work_pool().submit_action(
            factory(stop.is_set), kind=WorkKind.METADATA, priority=-20,
        )
        self._reads.append((future, stop, self._generation, self._clipboard_generation, apply, failed))
        self._timer.start()
        return True

    def _drain(self):
        for job in self._reads[:]:
            future, stop, generation, clipboard_generation, apply, failed = job
            if not future.done():
                continue
            self._reads.remove(job)
            if (self._shutdown or stop.is_set() or generation != self._generation
                    or clipboard_generation != self._clipboard_generation or future.cancelled()):
                continue
            try:
                result = future.result()
            except Exception as exc:
                failed(exc)
            else:
                if result is not None:
                    apply(result)
        if not self._reads:
            self._timer.stop()

    def request_shutdown(self):
        self._shutdown = True
        for future, stop, *_ in self._reads:
            stop.set()
            future.cancel()


class MetadataBatchController(QObject):
    """A batch keeps its source snapshot across selection/directory changes."""
    def __init__(self, files, parent=None):
        super().__init__(parent or files)
        self._files = files
        self._worker = self._dialog = None
        self._shutdown_requested = self._finished_received = False
        self._done = self._failed = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._drain)

    @property
    def busy(self):
        return self._worker is not None

    def start_batch(self, aliases, factory, title):
        if self._shutdown_requested or self.busy or self._files._background_shutdown_requested:
            return False
        from app_common.exif_io.metadata_store import resource_key
        unique = {}
        self._aliases = {}
        for display, source in aliases:
            source = os.path.normpath(os.path.abspath(source))
            key = resource_key(source)
            canonical = unique.setdefault(key, source)
            self._aliases.setdefault(canonical, []).append(display)
        if not unique:
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._directory = self._files._current_dir
        self._done = self._failed = 0
        self._finished_received = False
        self._dialog = MetadataProgressDialog(self.parent())
        self._dialog.setWindowTitle(title)
        self._dialog.label.setText(f"正在{title}…")
        self._dialog.cancel_requested.connect(self.stop)
        self._worker = worker = MetadataBatchWorker(list(unique.values()), factory, self._files.metadata_work_pool())
        worker.finished.connect(lambda w=worker: self._finished(w))
        self._dialog.bar.setRange(0, len(worker.paths))
        self._dialog.show()
        self._timer.start()
        worker.start()
        return True

    def _sync_results(self, results):
        files = self._files
        updates = {}
        for result in results:
            if not result.updates:
                continue
            if result.revision is not None:
                from app_common.exif_io.metadata_store import get_store
                if get_store().current_revision(result.source) != result.revision:
                    continue
            elif result.saved_fingerprint != sidecar_fingerprint(PhotoMetaDataXMP().sidecar_path_for(result.source)):
                continue
            paths = files.listed_sidecar_siblings(result.source)
            paths.extend(path for path in self._aliases.get(result.source, ())
                         if self._directory == files._current_dir or files.is_listed_path(path))
            for path in paths:
                updates.setdefault(path, {}).update(result.updates)
        if not updates:
            return
        # Refresh just metadata fields, never reselect/redecode the current photo.
        blocker = QSignalBlocker(files)
        try:
            files.sync_metadata_edits_for_paths(updates)
        finally:
            del blocker
        signal = getattr(files, "photo_metadata_cache_updated", None)
        if signal is not None:
            signal.emit(list(updates))

    def _finished(self, worker):
        if worker is self._worker:
            self._finished_received = True
            self._drain()

    def _drain(self):
        worker = self._worker
        if worker is None:
            return
        batch, started = [], time.monotonic()
        while len(batch) < 16 and time.monotonic() - started < .008:
            try:
                batch.append(worker.results.get_nowait())
            except queue.Empty:
                break
        self._done += len(batch)
        self._failed += sum(not result.updates for result in batch)
        if not self._shutdown_requested:
            self._sync_results(batch)
            for result in batch:
                if result.message:
                    self._dialog.details.append(html.escape(f"{result.source}：{result.message}"))
            self._dialog.bar.setValue(self._done)
            self._dialog.label.setText(f"已处理 {self._done}/{len(worker.paths)} · 活动线程 {worker.active_count}")
            self._dialog.summary.setText(f"已保存 {self._done - self._failed}，失败 {self._failed}")
        if self._finished_received and worker.results.empty():
            self._timer.stop()
            self._worker = None
            if worker.error:
                self._dialog.details.append(html.escape(worker.error))
            self._dialog.finish("已停止，已保存的元数据保留。" if worker.cancelled.is_set()
                                else "处理失败，请查看详情。" if worker.error else "元数据保存完成。")
            if self._shutdown_requested:
                self._dialog.close()
            worker.deleteLater()

    def stop(self):
        if self._worker is not None:
            self._worker.cancelled.set()

    def request_shutdown(self):
        self._shutdown_requested = True
        self.stop()
        if self._worker is not None:
            self._worker.discard_results.set()
        if self._dialog is not None:
            self._dialog.close()

    def is_shutdown_done(self):
        return self._worker is None

    def shutdown(self):
        self.request_shutdown()
        while self._worker is not None:
            self._worker.wait(10)
            QCoreApplication.processEvents()
