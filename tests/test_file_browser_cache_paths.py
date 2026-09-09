from pathlib import Path

from PIL import Image
import pytest

from app_common.file_browser import _browser_core as browser_core

from app_common.file_browser._browser_core import (
    _build_report_scope_maps_for_files,
    _collect_image_files_impl,
    _effective_persistent_thumb_cache_sizes,
    _find_cache_superpicky_dir_for_file,
    _find_superpicky_dir,
    _existing_persistent_thumb_cache_path_for_exact_size,
    _is_within_volume_root_depth,
    _path_depth_from_volume_root,
    _persistent_thumb_cache_path_for_file,
    _preview_cache_target_for_file,
    _report_row_from_cache_for_path,
    _select_report_scope_files,
    _superpicky_cache_root_dir,
    _THUMB_SIZE_STEPS,
    _thumb_disk_cache_path,
)
from app_common.file_browser._thumbnail import PersistentThumbCacheWorker
from app_common.report_db import ReportDB
from app_common.superviewer_user_options import get_persistent_thumb_sizes


def _write_jpeg(path: Path, size: tuple[int, int]) -> None:
    Image.new("RGB", size, (80, 120, 160)).save(path, format="JPEG", quality=85)


def test_persistent_thumb_size_levels_include_2048() -> None:
    assert _THUMB_SIZE_STEPS == [128, 256, 512, 1024, 2048]
    assert get_persistent_thumb_sizes(2048) == [128, 256, 512, 1024, 2048]
    assert 2048 in _effective_persistent_thumb_cache_sizes(2048)


def test_persistent_thumb_cache_uses_existing_superpicky_root_for_descendants(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "2026" / "birds" / "day1" / "set1" / "raw"
    superpicky = root / ".superpicky"
    superpicky.mkdir(parents=True)
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.jpg"

    assert _find_superpicky_dir(str(nested)) == str(superpicky)
    assert _superpicky_cache_root_dir(str(nested)) == str(root)

    cache_path = Path(_persistent_thumb_cache_path_for_file(str(photo), str(nested), 128))
    assert cache_path == (
        superpicky
        / "thumb_cache"
        / "128"
        / "2026__birds__day1__set1__raw__DSC00024.jpg.thumb.jpg"
    )

    disk_cache_path = Path(_thumb_disk_cache_path(str(photo), 1.0, 128))
    assert disk_cache_path.parent == superpicky / "thumb_cache" / "128"
    assert disk_cache_path.suffix == ".jpg"


def test_per_file_cache_scope_uses_child_superpicky_dirs(tmp_path: Path) -> None:
    selected = tmp_path / "library"
    day1 = selected / "day1"
    day2 = selected / "day2"
    (day1 / ".superpicky").mkdir(parents=True)
    (day2 / ".superpicky").mkdir(parents=True)
    photo1 = day1 / "IMG0001.jpg"
    photo2 = day2 / "IMG0001.jpg"

    path1 = Path(_persistent_thumb_cache_path_for_file(str(photo1), str(selected), 128, selected_dir=str(selected)))
    path2 = Path(_persistent_thumb_cache_path_for_file(str(photo2), str(selected), 128, selected_dir=str(selected)))

    assert path1.parent == day1 / ".superpicky" / "thumb_cache" / "128"
    assert path2.parent == day2 / ".superpicky" / "thumb_cache" / "128"
    assert path1.name == path2.name == "IMG0001.jpg.thumb.jpg"


def test_ancestor_superpicky_without_report_is_not_reused_with_selected_policy(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "deep" / "set"
    (root / ".superpicky").mkdir(parents=True)
    nested.mkdir(parents=True)
    photo = nested / "IMG0001.jpg"

    assert _find_cache_superpicky_dir_for_file(str(photo), str(nested)) == ""
    assert _persistent_thumb_cache_path_for_file(str(photo), str(nested), 128, selected_dir=str(nested)) == ""


def test_volume_root_depth_rule_for_ancestor_superpicky() -> None:
    assert _path_depth_from_volume_root(r"F:\A") == 1
    assert _path_depth_from_volume_root(r"F:\A\B\C") == 3
    assert _is_within_volume_root_depth(r"F:\A\B\C")
    assert not _is_within_volume_root_depth(r"F:\A\B\C\D")


def test_cache_paths_do_not_create_or_target_missing_superpicky(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    nested = root / "images"
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.jpg"

    assert _find_superpicky_dir(str(nested)) == ""
    assert _superpicky_cache_root_dir(str(nested)) == ""
    assert _persistent_thumb_cache_path_for_file(str(photo), str(nested), 128) == ""
    assert _preview_cache_target_for_file(str(photo), str(nested)) == ""
    assert Path(_thumb_disk_cache_path(str(photo), 1.0, 128)).parent != (
        root / ".superpicky" / "thumb_cache" / "128"
    )
    assert not (nested / ".superpicky").exists()
    assert not (root / ".superpicky").exists()


def test_persistent_thumb_cache_migrates_legacy_thumb_cache_dir(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "2026" / "birds"
    superpicky = root / ".superpicky"
    legacy_dir = superpicky / "cache" / "thumb_cache_128"
    legacy_dir.mkdir(parents=True)
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.jpg"
    photo.write_bytes(b"image")
    legacy_path = legacy_dir / "2026__birds__DSC00024.jpg.thumb.jpg"
    _write_jpeg(legacy_path, (128, 96))

    migrated = Path(
        _existing_persistent_thumb_cache_path_for_exact_size(str(photo), str(nested), 128)
    )

    assert migrated == superpicky / "thumb_cache" / "128" / legacy_path.name
    assert migrated.is_file()
    assert not legacy_path.exists()


def test_persistent_thumb_cache_rejects_undersized_raw_large_cache(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "2026" / "birds"
    superpicky = root / ".superpicky"
    cache_dir = superpicky / "thumb_cache" / "1024"
    cache_dir.mkdir(parents=True)
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.ARW"
    photo.write_bytes(b"raw")
    cache_path = Path(_persistent_thumb_cache_path_for_file(str(photo), str(nested), 1024))
    _write_jpeg(cache_path, (160, 120))

    assert _existing_persistent_thumb_cache_path_for_exact_size(str(photo), str(nested), 1024) == ""


def test_collect_image_files_skips_apple_double_metadata_files(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    nested = image_dir / "nested"
    nested.mkdir(parents=True)
    photo = image_dir / "DSC06705.jpg"
    apple_double = image_dir / "._DSC06705.jpg"
    nested_photo = nested / "DSC06706.ARW"
    nested_apple_double = nested / "._DSC06706.ARW"
    for path in (photo, apple_double, nested_photo, nested_apple_double):
        path.write_bytes(b"image")

    flat = [Path(path).name for path in _collect_image_files_impl(str(image_dir), recursive=False)]
    recursive = {
        Path(path).relative_to(image_dir).as_posix()
        for path in _collect_image_files_impl(str(image_dir), recursive=True)
    }

    assert flat == ["DSC06705.jpg"]
    assert recursive == {"DSC06705.jpg", "nested/DSC06706.ARW"}


def test_report_scope_file_selection_skips_apple_double_metadata_files(tmp_path: Path) -> None:
    selected_dir = tmp_path / "images"
    selected_dir.mkdir()
    full_report_cache = {
        "DSC06705": {"filename": "DSC06705", "current_path": "DSC06705.jpg"},
        "._DSC06705": {"filename": "._DSC06705", "current_path": "._DSC06705.jpg"},
    }

    files, report_cache = _select_report_scope_files(str(selected_dir), str(selected_dir), full_report_cache)

    assert [Path(path).name for path in files] == ["DSC06705.jpg"]
    assert set(report_cache) == {"DSC06705"}


def test_report_scope_maps_match_duplicate_stems_by_full_path(tmp_path: Path) -> None:
    selected = tmp_path / "library"
    day1 = selected / "day1"
    day2 = selected / "day2"
    day1.mkdir(parents=True)
    day2.mkdir(parents=True)
    photo1 = day1 / "IMG0001.jpg"
    photo2 = day2 / "IMG0001.jpg"
    photo1.write_bytes(b"image")
    photo2.write_bytes(b"image")

    db1 = ReportDB(str(day1))
    db2 = ReportDB(str(day2))
    try:
        db1.insert_photo({
            "filename": "IMG0001",
            "current_path": "IMG0001.jpg",
            "bird_species_cn": "白鹭",
        })
        db2.insert_photo({
            "filename": "IMG0001",
            "current_path": "IMG0001.jpg",
            "bird_species_cn": "燕子",
        })
    finally:
        db1.close()
        db2.close()

    selected_cache, full_cache, row_by_path = _build_report_scope_maps_for_files(
        [str(photo1), str(photo2)],
        str(selected),
    )

    assert len(selected_cache) == 1
    assert row_by_path[str(photo1)]["bird_species_cn"] == "白鹭"
    assert row_by_path[str(photo2)]["bird_species_cn"] == "燕子"
    assert _report_row_from_cache_for_path(str(photo1), full_cache)["bird_species_cn"] == "白鹭"
    assert _report_row_from_cache_for_path(str(photo2), full_cache)["bird_species_cn"] == "燕子"


def test_persistent_thumb_worker_builds_tasks_for_multiple_cache_scopes(tmp_path: Path) -> None:
    selected = tmp_path / "library"
    day1 = selected / "day1"
    day2 = selected / "day2"
    (day1 / ".superpicky").mkdir(parents=True)
    (day2 / ".superpicky").mkdir(parents=True)
    photo1 = day1 / "IMG0001.jpg"
    photo2 = day2 / "IMG0002.jpg"
    photo1.write_bytes(b"image")
    photo2.write_bytes(b"image")

    tasks = PersistentThumbCacheWorker._build_tasks(
        [str(photo1), str(photo2)],
        str(selected),
        report_cache={},
        sizes=[128],
    )

    assert len(tasks) == 2
    cache_paths = {
        Path(
            _persistent_thumb_cache_path_for_file(
                task.source_path,
                task.current_dir,
                128,
                selected_dir=task.current_dir,
            )
        ).parent
        for task in tasks
    }
    assert cache_paths == {
        day1 / ".superpicky" / "thumb_cache" / "128",
        day2 / ".superpicky" / "thumb_cache" / "128",
    }


def test_indexed_report_lookup_preserves_path_matching_and_stem_priority(tmp_path: Path) -> None:
    root = tmp_path / "中文目录"
    current = root / "当前" / "重复.ARW"
    sidecar = root / "当前" / "重复.xmp"
    original = root / "原始" / "重复.ARW"
    other = root / "另一组" / "重复.ARW"
    earlier_same_path = {"filename": "other", "current_path": str(current)}
    preferred = browser_core._normalize_report_row_paths({
        "filename": "重复",
        "current_path": "当前\\重复.xmp",
        "original_path": "原始/重复.ARW",
        "_report_root_dir": str(root),
    })
    other_row = {"filename": "重复", "current_path": str(other)}
    pathless = {"filename": "无路径", "bird_species_cn": "白鹭"}
    stale = {"filename": "陈旧", "current_path": str(root / "旧目录" / "陈旧.HIF")}
    cache = {
        "first": earlier_same_path,
        "重复": preferred,
        "重复\0second": other_row,
        "pathless": pathless,
        "陈旧": stale,
        "invalid": None,
    }
    indexed = browser_core._index_report_cache(cache)
    for path, expected in (
        (current, preferred),
        (sidecar, preferred),
        (original, preferred),
        (other, other_row),
        (root / "无路径.jpg", pathless),
        (root / "新目录" / "陈旧.HIF", None),
        (root / "missing.jpg", None),
    ):
        assert _report_row_from_cache_for_path(str(path), cache) is expected
        assert _report_row_from_cache_for_path(str(path), indexed) is expected
    assert preferred["_current_path_report_raw"].endswith("重复.xmp")
    assert preferred["current_path"].endswith("重复.ARW")


@pytest.mark.parametrize("pathless_first", [True, False])
def test_indexed_report_lookup_retains_first_matching_row_order(
    tmp_path: Path, pathless_first: bool,
) -> None:
    photo = tmp_path / "IMG0001.jpg"
    path_row = {"filename": "IMG0001", "current_path": str(photo)}
    pathless_row = {"filename": "IMG0001"}
    rows = [pathless_row, path_row] if pathless_first else [path_row, pathless_row]
    # Duplicate stems receive suffixed keys, so the insertion-order fallback
    # decides between a full-path hit and a pathless legacy row.
    cache = {f"IMG0001\0{index}": row for index, row in enumerate(rows)}
    indexed = browser_core._index_report_cache(cache)
    assert _report_row_from_cache_for_path(str(photo), indexed) is rows[0]


def test_indexed_report_lookup_invalidates_replaced_and_removed_rows(tmp_path: Path) -> None:
    old_photo = tmp_path / "before.jpg"
    new_photo = tmp_path / "after.jpg"
    old_row = {"filename": "old", "current_path": str(old_photo)}
    new_row = {"filename": "new", "current_path": str(new_photo)}
    cache = browser_core._index_report_cache({"row": old_row})
    cache["row"] = new_row
    assert _report_row_from_cache_for_path(str(old_photo), cache) is None
    assert _report_row_from_cache_for_path(str(new_photo), cache) is new_row
    cache.pop("row")
    assert _report_row_from_cache_for_path(str(new_photo), cache) is None
    cache.update({"row": old_row})
    assert _report_row_from_cache_for_path(str(old_photo), cache) is old_row
    cache.clear()
    assert _report_row_from_cache_for_path(str(old_photo), cache) is None


def test_indexed_report_cache_copy_preserves_snapshot_without_reindexing(tmp_path: Path, monkeypatch) -> None:
    photo = tmp_path / "photo.jpg"
    row = {"filename": "photo", "current_path": str(photo)}
    cache = browser_core._index_report_cache({"row": row})
    original_candidates = browser_core._report_row_candidate_paths

    def unexpected_reindex(*args):
        raise AssertionError("copy must reuse the published index")

    monkeypatch.setattr(browser_core, "_report_row_candidate_paths", unexpected_reindex)
    copied = cache.copy()
    assert copied is not cache and copied == cache
    assert _report_row_from_cache_for_path(str(photo), copied) is row
    monkeypatch.setattr(browser_core, "_report_row_candidate_paths", original_candidates)
    copied.clear()
    assert _report_row_from_cache_for_path(str(photo), copied) is None
    assert _report_row_from_cache_for_path(str(photo), cache) is row
    copied = cache.copy()
    cache.clear()
    assert _report_row_from_cache_for_path(str(photo), cache) is None
    assert _report_row_from_cache_for_path(str(photo), copied) is row


def test_report_scope_missing_paths_are_linear_and_reuse_index(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "report"
    count = 600
    files = [str(root / "new" / f"DSC{index:05d}.HIF") for index in range(count)]
    cache = {
        f"DSC{index:05d}": {
            "filename": f"DSC{index:05d}",
            "current_path": f"old/DSC{index:05d}.HIF",
            "_report_root_dir": str(root),
        }
        for index in range(count)
    }
    root_lookups = []
    loads = []
    candidate_calls = []
    original_candidates = browser_core._report_row_candidate_paths

    def find_root(path, selected_dir):
        root_lookups.append(path)
        return str(root)

    def load_cache(report_root, **kwargs):
        loads.append(report_root)
        return cache

    def counted_candidates(row, report_root):
        candidate_calls.append(row)
        return original_candidates(row, report_root)

    monkeypatch.setattr(browser_core, "_report_root_dir_for_file", find_root)
    monkeypatch.setattr(browser_core, "_load_report_cache_for_root", load_cache)
    monkeypatch.setattr(browser_core, "_report_row_candidate_paths", counted_candidates)
    progress = []
    selected, full, by_path = _build_report_scope_maps_for_files(
        files, str(root), progress_callback=lambda *args: progress.append(args),
    )
    assert not selected and not by_path
    assert len(full) == count
    assert len(root_lookups) == len(loads) == 1
    # One index per root, one merged index, and at most one stem candidate per
    # file. Previously these stale paths compared every file with every row.
    assert len(candidate_calls) <= 3 * count
    assert progress[0][:2] == (0, count)
    assert progress[-1][:2] == (count, count)
    calls_after_build = len(candidate_calls)
    for index in range(count):
        assert _report_row_from_cache_for_path(str(root / f"unknown{index}.HIF"), full) is None
    assert len(candidate_calls) == calls_after_build


def test_report_scope_build_can_cancel_during_indexing(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "report"
    rows = {
        str(index): {"filename": str(index), "current_path": str(root / f"{index}.jpg")}
        for index in range(1000)
    }
    candidate_calls = []
    original_candidates = browser_core._report_row_candidate_paths

    def counted_candidates(row, report_root):
        candidate_calls.append(row)
        return original_candidates(row, report_root)

    monkeypatch.setattr(browser_core, "_report_root_dir_for_file", lambda *args: str(root))
    monkeypatch.setattr(browser_core, "_load_report_cache_for_root", lambda *args, **kwargs: rows)
    monkeypatch.setattr(browser_core, "_report_row_candidate_paths", counted_candidates)
    with pytest.raises(browser_core._ReportScopeBuildCancelled):
        _build_report_scope_maps_for_files(
            [str(root / "missing.jpg")],
            str(root),
            should_cancel=lambda: len(candidate_calls) >= 5,
        )
    assert 5 <= len(candidate_calls) <= 128


def test_report_scope_build_cancelled_before_work_does_not_load(tmp_path: Path, monkeypatch) -> None:
    def unexpected_load(*args, **kwargs):
        raise AssertionError("cancelled scan must not load a report")

    monkeypatch.setattr(browser_core, "_load_report_cache_for_root", unexpected_load)
    with pytest.raises(browser_core._ReportScopeBuildCancelled):
        _build_report_scope_maps_for_files(
            [str(tmp_path / "photo.jpg")], str(tmp_path), should_cancel=lambda: True,
        )
