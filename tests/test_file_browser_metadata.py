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
