"""目录改名释放缓存文件，迟到缓存写入不复建旧照片目录；报告只读匹配别名。"""
import os
from pathlib import Path

from app_common.exif_io import meta_disk_cache as cache
from app_common.file_browser import _browser_core as core


def test_directory_operation_releases_only_owned_cache_and_rejects_late_write(tmp_path):
    source, target, other = tmp_path / "照片", tmp_path / "新照片", tmp_path / "其他"
    source.mkdir()
    other.mkdir()
    db = source / ".superpicky/meta_cache/meta_cache.db"
    other_db = other / ".superpicky/meta_cache/meta_cache.db"
    items = {str(source / "鸟.jpg"): (1.0, 10, {"ISO": 100})}
    try:
        assert cache.put_many(str(db), items) == 1
        assert cache.put_many(str(other_db), items) == 1
        with cache.directory_file_operation(source):
            assert os.path.normpath(str(db)) not in cache._CONNECTIONS
            assert os.path.normpath(str(other_db)) in cache._CONNECTIONS
            source.rename(target)
        assert cache.put_many(str(db), items) == 0
        assert not source.exists()
        assert cache.get_many(str(target / db.relative_to(source)),
                              {path: (1.0, 10) for path in items})
    finally:
        cache.close_all()


def test_report_aliases_follow_repeated_rename_without_changing_raw_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "_ACTUAL_PATH_CACHE", {})
    original, middle, current = (str(tmp_path / name / "鸟.jpg") for name in ("原目录", "中间目录", "新目录"))
    row = {"filename": "鸟", "current_path": original, "original_path": original,
           "_current_path_report_raw": original, "bird_species_cn": "白鹭"}
    before = dict(row)
    core._set_cached_actual_path(original, middle)
    core._set_cached_actual_path(middle, current)
    assert core._get_cached_actual_path(original) == current
    assert core._index_report_cache({"鸟": row}).row_for_path(current) == row
    assert row == before
    core._set_cached_actual_path(current, original)
    assert core._get_cached_actual_path(original) is not None  # 循环不会挂起。
