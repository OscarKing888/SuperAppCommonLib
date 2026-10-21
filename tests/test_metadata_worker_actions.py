"""Real XMP writes, concurrent scheduling, clipboard generations and shutdown."""
from pathlib import Path
import threading
import time

from PIL import Image
import pytest
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser import FileListPanel
from app_common.file_browser._metadata_actions import MetadataWriteAction, SpeciesCopyAction
from app_common.file_browser._metadata_tasks import MetadataProgressDialog
from app_common.file_browser._work_pool import BrowserWorkPool

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    end = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < end
        _APP.processEvents()
        time.sleep(.001)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'cache'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    widget = FileListPanel()
    widget._current_dir = str(tmp_path)
    widget._metadata_edit_pool = BrowserWorkPool(4, 2)
    widget._metadata_edit_pool.set_thumbnail_mode(False)
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def photo(tmp_path, name):
    path = tmp_path / name
    Image.new('RGB', (12, 8)).save(path)
    return str(path)


def paste_done(panel):
    return panel._species_paste_controller is not None and not panel._species_paste_controller.busy


def test_writes_are_parallel_off_gui_and_deduplicate_sidecars(panel, tmp_path, monkeypatch):
    paths = [photo(tmp_path, name) for name in ('白鹭.jpg', '白鹭.png', '苍鹭.jpg')]
    panel._all_files = paths
    original = MetadataWriteAction.execute
    barrier = threading.Barrier(2)
    gate = threading.Event()
    entered = []

    def execute(action):
        entered.append((action.path, threading.get_ident()))
        barrier.wait(5)  # A sequential implementation cannot pass this test.
        assert gate.wait(5)
        return original(action)

    monkeypatch.setattr(MetadataWriteAction, 'execute', execute)
    panel._copied_species_payload = {'bird_species_cn': '白头鹎', 'shooting_location': '深圳湾·红树林'}
    selected = []
    panel.file_selected.connect(selected.append)
    panel._selected_display_path = paths[0]
    panel._paste_species_to_paths(paths + paths[:1])
    try:
        wait_for(lambda: len(entered) == 2)
        assert len({ident for _, ident in entered}) == 2
        assert all(ident != threading.get_ident() for _, ident in entered)
        # The GUI still dispatches events while both writes are blocked.
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        wait_for(lambda: ticks)
    finally:
        gate.set()
    wait_for(lambda: paste_done(panel))
    assert panel._species_paste_controller._done == 2
    for path in paths:
        saved = PhotoMetaDataXMP().read(path)
        assert saved['bird_species_cn'] == '白头鹎'
        assert saved['shooting_location'] == '深圳湾·红树林'
        assert panel._meta_cache[path]['bird_species_cn'] == '白头鹎'
    assert not selected  # A metadata paste must not reload the preview.


def test_newer_sidecar_edit_is_not_overwritten_by_late_ui_result(panel, tmp_path, monkeypatch):
    from app_common.file_browser import _metadata_actions as actions
    path = photo(tmp_path, '中文.jpg')
    ready, gate = threading.Event(), threading.Event()
    original = actions.MetadataWriteAction.execute

    def execute(action):
        result = original(action)
        ready.set()
        assert gate.wait(5)
        return result

    monkeypatch.setattr(actions.MetadataWriteAction, 'execute', execute)
    panel._copied_species_payload = {'bird_species_cn': '白头鹎'}
    panel._paste_species_to_paths([path])
    try:
        assert ready.wait(5)
        assert PhotoMetaDataXMP().write(path, {'XMP-dc:Title': '新编辑', 'XMP-superpicky:bird_species_cn': '新编辑'})
        panel.sync_metadata_edits_for_paths({path: {'bird_species_cn': '新编辑'}})
        worker = panel._species_paste_controller._worker
        panel._species_paste_controller._finished(object())
        assert panel._species_paste_controller._worker is worker
    finally:
        gate.set()
    wait_for(lambda: paste_done(panel))
    assert panel._meta_cache[path]['bird_species_cn'] == '新编辑'
    assert PhotoMetaDataXMP().read(path)['bird_species_cn'] == '新编辑'


def test_copy_uses_latest_request_and_respects_external_clipboard(panel, tmp_path, monkeypatch):
    paths = [photo(tmp_path, f'{i}.jpg') for i in range(2)]
    for path, name in zip(paths, ('家燕', '白头鹎')):
        assert PhotoMetaDataXMP().write_title(path, name)
    original = SpeciesCopyAction.execute
    entered, gate = threading.Event(), threading.Event()
    seen_threads = []

    def execute(action):
        seen_threads.append(threading.get_ident())
        if action.path == paths[0]:
            entered.set()
            assert gate.wait(5)
        return original(action)

    monkeypatch.setattr(SpeciesCopyAction, 'execute', execute)
    QApplication.clipboard().setText('原剪贴板')
    panel._copy_species_from_path(paths[0])
    try:
        assert entered.wait(5)
        panel._copy_species_from_path(paths[1])
        panel._paste_species_to_paths(paths[:1])
        wait_for(lambda: QApplication.clipboard().text() == '白头鹎')
        # A cancelled older read must not hold up this newer copy/paste.
        wait_for(lambda: paste_done(panel))
        assert PhotoMetaDataXMP().read(paths[0])['bird_species_cn'] == '白头鹎'
    finally:
        gate.set()
    wait_for(lambda: not panel._metadata_copy_controller.busy)
    assert QApplication.clipboard().text() == '白头鹎'
    assert all(ident != threading.get_ident() for ident in seen_threads)
    gate.clear()
    entered.clear()
    panel._copy_species_from_path(paths[0])
    try:
        assert entered.wait(5)
        QApplication.clipboard().setText('用户复制的其他内容')
    finally:
        gate.set()
    wait_for(lambda: not panel._metadata_copy_controller.busy)
    assert QApplication.clipboard().text() == '用户复制的其他内容'


def test_immediate_paste_waits_for_new_copy_snapshot(panel, tmp_path, monkeypatch):
    source, target = [photo(tmp_path, f'{i}.jpg') for i in range(2)]
    assert PhotoMetaDataXMP().write_title(source, '白头鹎')
    original = SpeciesCopyAction.execute
    gate = threading.Event()

    def execute(action):
        assert gate.wait(5)
        return original(action)

    monkeypatch.setattr(SpeciesCopyAction, 'execute', execute)
    panel._copied_species_payload = {'bird_species_cn': '旧鸟名'}
    panel._copy_species_from_path(source)
    panel._paste_species_to_paths([target])
    assert not Path(target).with_suffix('.xmp').exists()
    gate.set()
    wait_for(lambda: paste_done(panel))
    assert PhotoMetaDataXMP().read(target)['bird_species_cn'] == '白头鹎'


def test_cancel_and_shutdown_keep_running_workers_owned(panel, tmp_path, monkeypatch):
    paths = [photo(tmp_path, f'{i}.jpg') for i in range(12)]
    original = MetadataWriteAction.execute
    gate = threading.Event()
    entered = []

    def execute(action):
        result = original(action)
        entered.append(action.path)
        assert gate.wait(5)
        return result

    monkeypatch.setattr(MetadataWriteAction, 'execute', execute)
    panel._copied_species_payload = {'bird_species_cn': '白头鹎'}
    panel._paste_species_to_paths(paths)
    controller = panel._species_paste_controller
    try:
        wait_for(lambda: len(entered) == 4)
        worker = controller._worker
        panel._request_background_shutdown()
        assert controller.busy and controller._worker is worker
        assert not controller.is_shutdown_done()
    finally:
        gate.set()
    wait_for(controller.is_shutdown_done)
    assert len(entered) == 4
    assert all(PhotoMetaDataXMP().read(path)['bird_species_cn'] == '白头鹎' for path in entered)
    assert all(not Path(path).with_suffix('.xmp').exists() for path in paths if path not in entered)
    assert panel._meta_cache == {}


def test_directory_switch_does_not_pollute_new_directory(panel, tmp_path, monkeypatch):
    path = photo(tmp_path, '旧目录.jpg')
    original = panel._meta_proxy.write
    gate, entered = threading.Event(), threading.Event()

    def write(path, fields):
        entered.set()
        assert gate.wait(5)
        return original(path, fields)

    monkeypatch.setattr(panel._meta_proxy, 'write', write)
    panel._copied_species_payload = {'bird_species_cn': '白头鹎'}
    panel._paste_species_to_paths([path])
    try:
        assert entered.wait(5)
        panel._current_dir = str(tmp_path / '另一目录')
        panel._all_files = []
        panel._meta_cache = {}
    finally:
        gate.set()
    wait_for(lambda: paste_done(panel))
    assert PhotoMetaDataXMP().read(path)['bird_species_cn'] == '白头鹎'
    assert panel._meta_cache == {}


def test_missing_source_fails_without_orphan_sidecar(panel, tmp_path):
    path = str(tmp_path / '已删除.jpg')
    panel._copied_species_payload = {'bird_species_cn': '白头鹎'}
    panel._paste_species_to_paths([path])
    wait_for(lambda: paste_done(panel))
    assert panel._species_paste_controller._failed == 1
    assert path in panel._species_paste_controller._dialog.details.toPlainText()
    assert not Path(path).with_suffix('.xmp').exists()
    assert panel._meta_cache == {}


def test_progress_escape_requests_stop_and_keeps_action_reachable(panel):
    dialog = MetadataProgressDialog(panel)
    stopped = []
    dialog.cancel_requested.connect(lambda: stopped.append(True))
    dialog.show()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert stopped
    dialog.finish('已完成')
    dialog.show()
    _APP.processEvents()
    assert dialog.button.isEnabled() and dialog.button.text() == '关闭'
    assert dialog.rect().contains(dialog.button.geometry())
    dialog.close()
    dialog.deleteLater()
