import os

from app_common.superviewer_user_options import normalize_user_options


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
