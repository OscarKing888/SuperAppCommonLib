import os

from app_common.file_browser._panel import FileListPanel


class _DescriptionProxy:
    def read(self, path: str) -> dict:
        return {"SourceFile": path, "Description": "sidecar note"}


class _FilterEdit:
    def __init__(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


def test_photo_metadata_provider_does_not_stop_at_tags_only_cache() -> None:
    path = os.path.normpath("C:/img001.jpg")
    panel = FileListPanel.__new__(FileListPanel)
    panel._meta_cache = {path: {"tags": ["configured"]}}
    panel._selected_display_path = ""
    panel._meta_proxy = _DescriptionProxy()

    meta = FileListPanel.get_photo_metadata_for_path(panel, path, allow_slow_read=True)

    assert meta["Description"] == "sidecar note"
    assert meta["tags"] == ["configured"]


def test_filename_filter_matches_comment_metadata() -> None:
    path = os.path.normpath("C:/images/DSC06705.jpg")
    panel = FileListPanel.__new__(FileListPanel)
    panel._meta_cache = {path: {"comment": "nesting behavior"}}

    assert FileListPanel._path_matches_filters(panel, path, filter_text="nesting")
    assert FileListPanel._path_matches_filters(panel, path, filter_text="dsc06705")
    assert not FileListPanel._path_matches_filters(panel, path, filter_text="missing")


def test_text_filter_rebuilds_after_comment_metadata_arrives() -> None:
    path = os.path.normpath("C:/images/DSC06705.jpg")
    panel = FileListPanel.__new__(FileListPanel)
    panel._meta_cache = {path: {"comment": "nesting behavior"}}
    panel._filtered_files = []
    panel._filter_edit = _FilterEdit("nesting")
    panel._filter_pick = False
    panel._filter_reject = False
    panel._filter_min_rating = 0
    panel._filter_focus_status = ""

    assert FileListPanel._filters_require_rebuild_after_metadata_refresh(panel, [path])


def test_deleted_photo_report_tombstone_is_exact_for_duplicate_filenames(tmp_path) -> None:
    root = tmp_path / "library"
    first = root / "day1" / "IMG_0001.CR3"
    second = root / "day2" / "IMG_0001.CR3"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    row_first = {
        "filename": "IMG_0001",
        "current_path": str(first.relative_to(root)),
        "original_path": str(first.relative_to(root)),
        "_report_root_dir": str(root),
    }
    row_second = {
        "filename": "IMG_0001",
        "current_path": str(second.relative_to(root)),
        "original_path": str(second.relative_to(root)),
        "_report_root_dir": str(root),
    }
    panel = FileListPanel.__new__(FileListPanel)
    panel._report_root_dir = str(root)
    panel._report_deleted_path_tombstones = set()
    panel._report_full_cache = {
        "IMG_0001": row_first,
        "IMG_0001\0duplicate": row_second,
    }
    panel._report_cache = dict(panel._report_full_cache)
    panel._report_row_by_path = {
        os.path.normpath(str(first)): row_first,
        os.path.normpath(str(second)): row_second,
    }

    result = FileListPanel._delete_report_rows_for_paths(
        panel,
        [str(first)],
        resolved_paths=[str(first)],
    )

    assert result == 0
    assert list(panel._report_full_cache.values()) == [row_second]
    assert list(panel._report_cache.values()) == [row_second]
    assert panel._report_row_by_path == {os.path.normpath(str(second)): row_second}
    assert len(panel._report_deleted_path_tombstones) == 1


def test_deleted_report_tombstone_suppresses_reload_but_not_recreated_file(tmp_path) -> None:
    root = tmp_path / "library"
    path = root / "day1" / "IMG_0001.CR3"
    path.parent.mkdir(parents=True)
    row = {
        "filename": "IMG_0001",
        "current_path": str(path.relative_to(root)),
        "original_path": str(path.relative_to(root)),
        "_report_root_dir": str(root),
    }
    panel = FileListPanel.__new__(FileListPanel)
    panel._report_root_dir = str(root)
    panel._report_deleted_path_tombstones = {
        (
            os.path.normcase(os.path.normpath(os.path.abspath(root))),
            os.path.normcase(os.path.normpath(os.path.abspath(path))),
        )
    }

    files, cache, full_cache, path_rows = FileListPanel._filter_deleted_report_tombstones(
        panel,
        [str(path)],
        {"IMG_0001": row},
        {"IMG_0001": row},
        {os.path.normpath(str(path)): row},
    )

    assert files == []
    assert cache == {}
    assert full_cache == {}
    assert path_rows == {}

    path.write_bytes(b"new raw")
    files, *_ = FileListPanel._filter_deleted_report_tombstones(
        panel,
        [str(path)],
        {"IMG_0001": row},
        {"IMG_0001": row},
        {os.path.normpath(str(path)): row},
    )
    assert files == [str(path)]
