import os
import importlib

from app_common import superviewer_user_options
from app_common.superviewer_user_options import normalize_user_options

_browser_core = importlib.import_module("app_common.file_browser._browser_core")
_workers = importlib.import_module("app_common.file_browser._workers")
_panel_module = importlib.import_module("app_common.file_browser._panel")


def _expected_metadata_workers() -> int:
    cpu_count = max(1, os.cpu_count() or 1)
    return max(1, min(8, cpu_count // 4 or 1))


def test_default_workers_split_metadata_and_persistent_thumbnail_generation() -> None:
    cpu_count = max(1, os.cpu_count() or 1)
    metadata_workers = _expected_metadata_workers()

    options = normalize_user_options({})

    assert options["metadata_loader_workers"] == metadata_workers
    assert options["persistent_thumb_workers"] == max(1, cpu_count - metadata_workers)


def test_legacy_options_keep_persistent_worker_count_and_add_metadata_default() -> None:
    custom_persistent_workers = max(1, os.cpu_count() or 1) + 7

    options = normalize_user_options({"persistent_thumb_workers": custom_persistent_workers})

    assert options["metadata_loader_workers"] == _expected_metadata_workers()
    assert options["persistent_thumb_workers"] == custom_persistent_workers


def test_legacy_default_persistent_workers_migrate_to_remaining_cpu_budget() -> None:
    cpu_count = max(1, os.cpu_count() or 1)
    metadata_workers = _expected_metadata_workers()

    options = normalize_user_options({"persistent_thumb_workers": cpu_count})

    assert options["metadata_loader_workers"] == metadata_workers
    assert options["persistent_thumb_workers"] == max(1, cpu_count - metadata_workers)


def test_metadata_workers_boost_when_thumbnail_work_is_idle(monkeypatch) -> None:
    previous = superviewer_user_options.get_runtime_user_options()
    monkeypatch.delenv("SuperViewer_METADATA_WORKERS", raising=False)
    monkeypatch.setattr(_browser_core.os, "cpu_count", lambda: 32)
    try:
        superviewer_user_options.apply_runtime_user_options(
            {
                "thumbnail_loader_workers": 32,
                "metadata_loader_workers": 8,
                "persistent_thumb_workers": 24,
                "persistent_thumb_max_size": 128,
                "key_navigation_fps": 24,
                "keep_view_on_switch": 1,
            }
        )

        assert _browser_core._metadata_loader_worker_count_for_thumbnail_state(False) == 12
        assert _browser_core._metadata_loader_worker_count_for_thumbnail_state(True) == 8
    finally:
        superviewer_user_options.apply_runtime_user_options(previous)


def test_metadata_worker_env_override_disables_idle_boost(monkeypatch) -> None:
    monkeypatch.setenv("SuperViewer_METADATA_WORKERS", "6")
    monkeypatch.setattr(_browser_core.os, "cpu_count", lambda: 32)

    assert _browser_core._metadata_loader_worker_count_for_thumbnail_state(False) == 6


def test_persistent_thumbnail_worker_env_override_is_independent(monkeypatch) -> None:
    monkeypatch.setenv("SuperViewer_METADATA_WORKERS", "5")
    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "17")

    assert _browser_core._metadata_loader_worker_count() == 5
    assert _browser_core._persistent_thumb_cache_worker_count() == 17


def test_metadata_chunk_size_allows_requested_parallelism(monkeypatch) -> None:
    monkeypatch.setattr(_workers, "_METADATA_CHUNK_SIZE", 150)

    chunk_size = _workers._metadata_chunk_size_for_worker_count(1000, 24)
    chunk_count = (1000 + chunk_size - 1) // chunk_size

    assert chunk_count >= 24
    assert chunk_size < 150


def test_persistent_thumbnail_worker_starts_while_metadata_is_running(monkeypatch, tmp_path) -> None:
    class _Signal:
        def connect(self, _slot) -> None:
            pass

    class _FakePersistentWorker:
        instances = []

        def __init__(self, paths, current_dir, **kwargs) -> None:
            self.paths = list(paths)
            self.current_dir = current_dir
            self.kwargs = kwargs
            self.progress_updated = _Signal()
            self.finished_summary = _Signal()
            self.started = False
            self.instances.append(self)

        def start(self) -> None:
            self.started = True

    class _RunningMetadata:
        def isRunning(self) -> bool:
            return True

    monkeypatch.setattr(_panel_module, "PersistentThumbCacheWorker", _FakePersistentWorker)
    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "7")
    panel = _panel_module.FileListPanel.__new__(_panel_module.FileListPanel)
    panel._background_shutdown_started = False
    panel._file_writes_allowed = lambda *_args, **_kwargs: True
    panel._persistent_thumb_cache_pending_paths = [str(tmp_path / "img.jpg")]
    panel._persistent_thumb_cache_base_dir = str(tmp_path)
    panel._persistent_thumb_cache_pending_priority = 0
    panel._persistent_thumb_cache_worker = None
    panel._report_full_cache = None
    panel._report_cache = {}
    panel._thumb_size = 128
    panel._metadata_loader = _RunningMetadata()

    _panel_module.FileListPanel._start_persistent_thumb_cache_worker(panel)

    assert _FakePersistentWorker.instances
    worker = _FakePersistentWorker.instances[0]
    assert worker.started
    assert worker.kwargs["worker_count"] == 7
    assert panel._persistent_thumb_cache_pending_paths == []


def test_metadata_and_thumbnail_progress_show_their_worker_counts(monkeypatch) -> None:
    class _Progress:
        def __init__(self) -> None:
            self.format = ""
            self.tooltip = ""

        def setRange(self, *_args) -> None:
            pass

        def setValue(self, *_args) -> None:
            pass

        def setFormat(self, value) -> None:
            self.format = value

        def setToolTip(self, value) -> None:
            self.tooltip = value

        def show(self) -> None:
            pass

        def hide(self) -> None:
            pass

    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "13")
    panel = _panel_module.FileListPanel.__new__(_panel_module.FileListPanel)
    panel._meta_progress = _Progress()
    panel._persistent_thumb_progress = _Progress()
    panel._persistent_thumb_cache_total = 20
    panel._persistent_thumb_cache_done = 3
    panel._persistent_thumb_cache_status_text = "生成预览缩略图"
    panel._persistent_thumb_cache_scope_dirs = []
    panel._persistent_thumb_cache_current_path = ""
    panel._persistent_thumb_cache_base_dir = ""
    panel._persistent_thumb_cache_generated = 2
    panel._persistent_thumb_cache_skipped = 1
    panel._persistent_thumb_cache_failed = 0
    panel._thumb_size = 128

    _panel_module.FileListPanel._show_meta_progress_status(
        panel,
        "正在读取元数据",
        busy=False,
        value=3,
        total=20,
        worker_count=5,
    )
    _panel_module.FileListPanel._update_persistent_thumb_progress_widget(panel)

    assert "(5线程)" in panel._meta_progress.format
    assert "(13线程)" in panel._persistent_thumb_progress.format
    assert "- 生成线程: 13" in panel._persistent_thumb_progress.tooltip
