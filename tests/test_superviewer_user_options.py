import os
import importlib

from app_common import superviewer_user_options
from app_common.superviewer_user_options import normalize_user_options

_browser_core = importlib.import_module("app_common.file_browser._browser_core")
_workers = importlib.import_module("app_common.file_browser._workers")


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


def test_metadata_chunk_size_allows_requested_parallelism(monkeypatch) -> None:
    monkeypatch.setattr(_workers, "_METADATA_CHUNK_SIZE", 150)

    chunk_size = _workers._metadata_chunk_size_for_worker_count(1000, 24)
    chunk_count = (1000 + chunk_size - 1) // chunk_size

    assert chunk_count >= 24
    assert chunk_size < 150
