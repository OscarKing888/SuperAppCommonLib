import os
import json

from app_common.file_browser._panel import FileListPanel


def test_individual_bird_records_survive_browser_metadata_reload():
    from app_common.file_browser._workers import MetadataLoader
    records = json.dumps([{"cn_name": "白鹭", "box": [.1, .2, .4, .8]}], ensure_ascii=False)
    loader = MetadataLoader([], meta_proxy=object())
    for prefix in ("", "XMP-superpicky:"):
        parsed = loader._parse_rec({prefix + "birdid_individuals": records,
                                    prefix + "birdid_individuals_info": '{"schema":1}'})
        assert parsed["birdid_individuals"] == records
        assert parsed["birdid_individuals_info"] == '{"schema":1}'
    assert loader._parse_rec({"XMP-superpicky:birdid_individuals": "[]"})["birdid_individuals"] == "[]"


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


def test_listed_path_ignores_view_mode_and_follows_new_listings(tmp_path) -> None:
    # The list-view model is built lazily, so membership must come from the listing itself.
    first, second = str(tmp_path / "鸟A.jpg"), str(tmp_path / "鸟B.jpg")
    panel = FileListPanel.__new__(FileListPanel)
    panel._listed_path_index_source = None
    panel._all_files = [first]
    assert FileListPanel.is_listed_path(panel, os.path.join(str(tmp_path), ".", "鸟A.jpg"))
    assert not FileListPanel.is_listed_path(panel, second)
    assert not FileListPanel.is_listed_path(panel, "")
    panel._all_files = [second]
    assert not FileListPanel.is_listed_path(panel, first)
    assert FileListPanel.is_listed_path(panel, second)


def test_listed_sidecar_siblings_group_raw_and_jpeg_of_one_shot(tmp_path) -> None:
    folder = str(tmp_path / "鸟片")
    jpeg, raw = os.path.join(folder, "白鹭_A.JPG"), os.path.join(folder, "白鹭_a.arw")
    other, elsewhere = os.path.join(folder, "白鹭_2.jpg"), str(tmp_path / "白鹭.jpg")
    panel = FileListPanel.__new__(FileListPanel)
    panel._listed_path_index_source = None
    panel._all_files = [jpeg, raw, other]
    assert FileListPanel.listed_sidecar_siblings(panel, jpeg) == [jpeg, raw]
    # An unlisted sibling (e.g. the RAW actually analysed) still finds the listed rows.
    assert FileListPanel.listed_sidecar_siblings(panel, os.path.join(folder, "白鹭_A.dng")) == [jpeg, raw]
    assert FileListPanel.listed_sidecar_siblings(panel, other) == [other]
    assert FileListPanel.listed_sidecar_siblings(panel, elsewhere) == []
    assert FileListPanel.listed_sidecar_siblings(panel, "") == []


def test_pending_only_snapshot_preserves_unrelated_browser_fields(tmp_path):
    from types import SimpleNamespace
    from app_common.exif_io.metadata_store import get_store
    from app_common.file_browser._workers import MetadataLoader

    path = str(tmp_path / "白鹭.jpg")
    store = get_store()
    store.remember_many({path: {"ExifIFD:ISO": 800, "XMP-dc:Subject": ["飞行"],
                               "XMP-dc:Description": "原备注"}}).result(3)
    old = MetadataLoader._parse_rec(store.read_many([path])[path])
    store.invalidate_paths([path]).result(3)
    store.submit_edit(path, {"description": "新中文备注"}).result(3)
    partial = MetadataLoader._parse_rec(store.read_many([path])[path])
    assert partial.get("_metadata_complete") is False
    assert "focus_box_checked" not in partial
    queued = []
    panel = SimpleNamespace(_meta_cache={path: old}, _is_current_metadata_sender=lambda: True,
                            _remember_directory_snapshot=lambda **kwargs: None,
                            _enqueue_meta_apply=queued.append)
    FileListPanel._on_metadata_batch_ready(panel, {path: partial})
    assert panel._meta_cache[path]["comment"] == "新中文备注"
    assert panel._meta_cache[path]["iso"] == "800"
    assert panel._meta_cache[path]["tags"] == ["飞行"]
    assert queued[-1][path] == panel._meta_cache[path]


def test_partial_browser_snapshot_preserves_explicit_clears_and_zero():
    from types import SimpleNamespace
    from app_common.file_browser._workers import MetadataLoader
    partial = MetadataLoader._parse_rec({"_metadata_complete": False, "_metadata_revision": 2,
                                         "XMP-dc:Description": "", "XMP-dc:Subject": [],
                                         "XMP-xmp:Rating": 0, "XMP-xmpDM:pick": False})
    panel = SimpleNamespace(_meta_cache={"photo": {"comment": "旧备注", "tags": ["飞行"],
                                                  "rating": 5, "pick": 1, "iso": "800"}},
                            _is_current_metadata_sender=lambda: True,
                            _remember_directory_snapshot=lambda **kwargs: None,
                            _enqueue_meta_apply=lambda batch: None)
    FileListPanel._on_metadata_batch_ready(panel, {"photo": partial})
    result = panel._meta_cache["photo"]
    assert (result["comment"], result["tags"], result["rating"], result["pick"]) == ("", [], 0, 0)
    assert result["iso"] == "800"
    # A completed empty read must still replace and clear previous values.
    FileListPanel._on_metadata_batch_ready(panel, {"photo": MetadataLoader._parse_rec({"_metadata_revision": 3})})
    assert panel._meta_cache["photo"]["iso"] == ""


def test_pending_only_directory_snapshot_keeps_previous_camera_and_tags(tmp_path):
    from types import SimpleNamespace
    from app_common.exif_io.metadata_store import get_store

    path = str(tmp_path / "白鹭.jpg")
    store = get_store()
    store.submit_edit(path, {"description": "新备注"}).result(3)
    old = {"iso": "800", "tags": ["飞行"], "comment": "旧备注", "_metadata_revision": 0}
    panel = SimpleNamespace(_meta_cache={}, _current_dir=str(tmp_path), _include_subdirectories=False,
                            _apply_directory_listing_result=lambda *args, **kwargs: None)
    FileListPanel._show_directory_snapshot(panel, {"files": [path], "metadata": {path: old}})
    assert panel._meta_cache[path]["comment"] == "新备注"
    assert panel._meta_cache[path]["iso"] == "800"
    assert panel._meta_cache[path]["tags"] == ["飞行"]
    assert not FileListPanel._metadata_cache_has_browser_fields(panel._meta_cache[path])


def test_stale_partial_browser_update_cannot_override_newer_metadata():
    from types import SimpleNamespace
    old = {"iso": "800", "comment": "新备注", "_metadata_revision": 3}
    panel = SimpleNamespace(_meta_cache={"photo": old.copy()}, _is_current_metadata_sender=lambda: True,
                            _remember_directory_snapshot=lambda **kwargs: None,
                            _enqueue_meta_apply=lambda batch: None)
    FileListPanel._on_metadata_batch_ready(panel, {"photo": {"_metadata_complete": False,
                                                           "_metadata_revision": 2, "comment": "迟到旧备注"}})
    assert panel._meta_cache["photo"] == old


def test_partial_browser_snapshot_keeps_optional_explicit_resets():
    from app_common.file_browser._workers import MetadataLoader, _merge_browser_metadata
    partial = MetadataLoader._parse_rec({"_metadata_complete": False,
                                         "XMP-superpicky:burst_id": "",
                                         "XMP-superpicky:bird_species_cn": "",
                                         "XMP-superpicky:bird_sharpness_sigma": ""})
    result = _merge_browser_metadata({"burst_id": 4, "bird_species_cn": "白鹭",
                                       "bird_sharpness_sigma": 2.0, "iso": "800"}, partial)
    assert result["burst_id"] is None
    assert result["bird_species_cn"] == ""
    assert result["bird_sharpness_sigma"] is None
    assert result["iso"] == "800"


def test_partial_part_scores_clear_explicit_fields_and_preserve_absent_fields():
    from app_common import bird_sharpness_fields as bsf
    from app_common.file_browser._workers import MetadataLoader, _merge_browser_metadata

    previous = {bsf.FIELD_HEAD_SCORE: 900, bsf.FIELD_BODY_SCORE: 501,
                bsf.FIELD_MEAN_SCORE: 700.5, "iso": "800"}
    for prefix in ("", "XMP-superpicky:", "report."):
        partial = MetadataLoader._parse_rec({
            "_metadata_complete": False, prefix + bsf.FIELD_HEAD_SCORE: 0,
            prefix + bsf.FIELD_MEAN_SCORE: "",
        })
        result = _merge_browser_metadata(previous, partial)
        assert result[bsf.FIELD_HEAD_SCORE] == 0
        assert result[bsf.FIELD_BODY_SCORE] == 501
        assert result[bsf.FIELD_MEAN_SCORE] is None
        assert result["iso"] == "800"

        clear = MetadataLoader._parse_rec({
            "_metadata_complete": False,
            **{prefix + key: "" for key in (bsf.FIELD_HEAD_SCORE, bsf.FIELD_BODY_SCORE, bsf.FIELD_MEAN_SCORE)},
        })
        cleared = _merge_browser_metadata(previous, clear)
        assert all(cleared[key] is None for key in (bsf.FIELD_HEAD_SCORE, bsf.FIELD_BODY_SCORE, bsf.FIELD_MEAN_SCORE))


def test_complete_browser_reload_keeps_part_scores_and_replaces_old_missing_values():
    from app_common import bird_sharpness_fields as bsf
    from app_common.file_browser._workers import MetadataLoader, _merge_browser_metadata

    complete = MetadataLoader._parse_rec({
        bsf.xmp_key(bsf.FIELD_HEAD_SCORE): "0", bsf.xmp_key(bsf.FIELD_BODY_SCORE): "501",
        bsf.xmp_key(bsf.FIELD_MEAN_SCORE): "250.5",
    })
    assert complete[bsf.FIELD_HEAD_SCORE] == "0"
    assert complete[bsf.FIELD_BODY_SCORE] == "501"
    assert complete[bsf.FIELD_MEAN_SCORE] == "250.5"
    refreshed = _merge_browser_metadata(complete, MetadataLoader._parse_rec({"ExifIFD:ISO": 800}))
    assert not any(key in refreshed for key in (bsf.FIELD_HEAD_SCORE, bsf.FIELD_BODY_SCORE, bsf.FIELD_MEAN_SCORE))
