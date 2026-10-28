"""待剪切状态随剪贴板和视图更新，不能修改照片、选择或缩略图缓存。"""
from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import QMimeData, Qt, QUrl
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import _TREE_COL_NAME, _set_cached_actual_path
from app_common.file_browser._cut_state import (
    _FILE_CLIPBOARD_ACTION_MIME, _FILE_CLIPBOARD_ENTRIES_MIME,
)
from app_common.file_browser._models import _CutPendingRole
from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


class CutPanel(FileListPanel):
    show_cut_state = True


@pytest.fixture
def panel_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *a, **kw: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **kw: None)
    _APP.clipboard().clear()
    panels = []

    def create(cls=CutPanel, **kwargs):
        panel = cls(**kwargs)
        panels.append(panel)
        return panel

    yield create
    for panel in panels:
        panel.close()
        panel.deleteLater()
    _APP.processEvents()
    _APP.clipboard().clear()


def populate(panel, paths):
    for model in (panel._file_table_model, panel._thumb_list_model):
        model.clear()
        model.append_paths(paths, meta_cache={}, tooltip_fn=None, mismatch_fn=None)


def pending(panel):
    return [bool(panel._thumb_list_model.index(row, 0).data(_CutPendingRole))
            for row in range(panel._thumb_list_model.rowCount())]


def test_multiselection_cut_copy_replace_and_clear_preserve_models(panel_factory, tmp_path):
    panel = panel_factory()
    paths = [str(tmp_path / f"白鹭_{i}.jpg") for i in range(3)]
    for path in paths:
        with open(path, "wb") as photo:
            photo.write(b"photo")
    populate(panel, paths)
    model = panel._thumb_list_model
    pixmap = QPixmap(40, 30)
    pixmap.fill(QColor("#88cc55"))
    model.set_pixmap_for_path(paths[0], pixmap, 128)
    entry = model._entries[0]
    changes = []
    model.modelReset.connect(lambda: changes.append("reset"))
    panel.file_selected.connect(lambda *_: changes.append("selection"))
    panel._cut_paths_to_clipboard(paths[:2])
    assert pending(panel) == [True, True, False]
    assert "待剪切" in model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    # 新窗口也从当前剪贴板取得状态，普通共享浏览器不改变外观。
    second = panel_factory()
    basic = panel_factory(FileListPanel)
    populate(second, paths)
    populate(basic, paths)
    assert pending(second) == [True, True, False]
    assert pending(basic) == [False, False, False]
    # 再次剪切替换旧集合；普通文件复制、文本复制和清空均恢复正常。
    panel._cut_paths_to_clipboard(paths[2:])
    assert pending(panel) == pending(second) == [False, False, True]
    panel._copy_paths_to_clipboard(paths[:1])
    assert not any(pending(panel))
    panel._cut_paths_to_clipboard(paths[:1])
    _APP.clipboard().setText("普通文本")
    assert not any(pending(panel))
    panel._cut_paths_to_clipboard(paths[:1])
    _APP.clipboard().clear()
    assert not any(pending(panel))
    assert changes == []
    assert model._entries[0] is entry
    assert entry.pixmap.cacheKey() == pixmap.cacheKey()


def test_state_survives_rebuild_and_source_resolution_without_io(panel_factory, tmp_path, monkeypatch):
    panel = panel_factory()
    actual = tmp_path / "真实文件.jpg"
    actual.write_bytes(b"photo")
    stale = str(tmp_path / "旧目录" / actual.name)
    monkeypatch.setattr(panel, "_resolve_source_path_for_action", lambda _: str(actual))
    panel._cut_paths_to_clipboard([stale])
    # 切走再回来、过滤重建和迟到图片均不把状态当作条目上的临时标志。
    populate(panel, [str(tmp_path / "another.jpg")])
    assert pending(panel) == [False]
    populate(panel, [stale, str(actual)])
    cached_alias = str(tmp_path / "报告" / actual.name)
    _set_cached_actual_path(cached_alias, str(actual))
    panel._thumb_list_model.append_paths([cached_alias], meta_cache={}, tooltip_fn=None, mismatch_fn=None)
    with monkeypatch.context() as patch:
        patch.setattr(panel_module.os.path, "isfile", lambda *_: pytest.fail("cut state must not stat files"))
        assert pending(panel) == [True, True, True]
    table = panel._file_table_model
    option = QStyleOptionViewItem()
    index = table.index(0, _TREE_COL_NAME)
    panel._tree_widget.itemDelegateForColumn(_TREE_COL_NAME).initStyleOption(option, index)
    assert option.text.startswith("待剪切 · ")
    assert option.font.italic()
    assert index.data() == actual.name  # 真实文件名和排序值不变。


def test_failed_paste_retains_state_and_success_clears_all_panels(panel_factory, tmp_path, monkeypatch):
    import time
    from PyQt6.QtTest import QTest

    def wait_for_paste():
        deadline = time.monotonic() + 5
        while not panel._metadata_commit_bridge.is_idle and time.monotonic() < deadline:
            _APP.processEvents()
            QTest.qWait(1)
        assert panel._metadata_commit_bridge.is_idle

    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()
    photo = source / "中文.jpg"
    sidecar = source / "中文.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_text("中文元数据", encoding="utf-8")
    panel, second = panel_factory(), panel_factory()
    populate(panel, [str(photo)])
    populate(second, [str(photo)])
    panel._current_dir = str(dest)
    monkeypatch.setattr(panel, "load_directory", lambda *a, **kw: None)
    monkeypatch.setattr(panel, "set_pending_selection", lambda *a, **kw: None)
    monkeypatch.setattr(panel_module.QMessageBox, "warning", lambda *a, **kw: None)
    panel._cut_paths_to_clipboard([str(photo)])
    with monkeypatch.context() as patch:
        def fail(*a, **kw):
            raise OSError("injected move failure")
        patch.setattr(panel, "_paste_path_pairs_transaction", fail)
        panel._paste_clipboard_to_current_dir()
        wait_for_paste()
    assert pending(panel) == pending(second) == [True]
    assert photo.exists() and sidecar.exists()
    panel._paste_clipboard_to_current_dir()
    wait_for_paste()
    assert pending(panel) == pending(second) == [False]
    assert not photo.exists() and not sidecar.exists()
    assert (dest / photo.name).read_bytes() == b"photo"
    assert (dest / sidecar.name).read_text(encoding="utf-8") == "中文元数据"


def test_external_copy_and_malformed_payload_never_dim_files(panel_factory, tmp_path):
    panel = panel_factory()
    path = str(tmp_path / "bird.jpg")
    populate(panel, [path])
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path)])
    _APP.clipboard().setMimeData(mime)
    assert pending(panel) == [False]
    for payload in (b"broken", b"{}", b"[null, 1]", json.dumps([{"source": 42}]).encode()):
        mime = QMimeData()
        mime.setData(_FILE_CLIPBOARD_ACTION_MIME, b"cut")
        mime.setData(_FILE_CLIPBOARD_ENTRIES_MIME, payload)
        _APP.clipboard().setMimeData(mime)
        assert pending(panel) == [False]
