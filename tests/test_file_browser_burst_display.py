import os
import math
from pathlib import Path
import threading
import time

import app_common.file_browser._workers as _workers
from app_common.file_browser._browser_core import (
    _DisplayRole,
    _ForegroundRole,
    _MetaBurstTextRole,
    _MetaFocusBoxRole,
    _SortRole,
    _ToolTipRole,
    _TREE_COL_AESTHETIC,
    _TREE_COL_APERTURE,
    _TREE_COL_BURST,
    _TREE_COL_CAMERA,
    _TREE_COL_CAPTURE_TIME,
    _TREE_COL_FOCAL,
    _TREE_COL_FOCUS,
    _TREE_COL_ISO,
    _TREE_COL_LENS,
    _TREE_COL_NAME,
    _TREE_COL_SHARP,
    _TREE_COL_SHUTTER,
    _focus_status_text_color,
    _focus_status_to_display,
)
from app_common.file_browser._models import FileTableModel, ThumbnailListModel
from app_common.file_browser._panel import FileListPanel
from app_common.file_browser._workers import MetadataLoader


def _tooltip(path: str) -> str:
    return f"Path: {path}"


def _mismatch(_path: str) -> bool:
    return False


def test_focus_status_display_and_color_mapping() -> None:
    expected = {
        "BEST": "精焦",
        "GOOD": "合焦",
        "BAD": "偏移",
        "WORST": "失焦",
    }

    for raw, display in expected.items():
        assert _focus_status_to_display(raw) == display
        assert _focus_status_text_color(raw) == _focus_status_text_color(display)


def test_file_table_focus_column_uses_status_foreground_color() -> None:
    path = os.path.normpath("C:/photos/focus.jpg")
    model = FileTableModel()
    model.rebuild(
        [path],
        meta_cache={path: {"focus_status": "BAD"}},
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    assert model.data(model.index(0, _TREE_COL_FOCUS), _DisplayRole) == "偏移"
    brush = model.data(model.index(0, _TREE_COL_FOCUS), _ForegroundRole)
    assert brush is not None
    assert brush.color().name().lower() == _focus_status_text_color("BAD").lower()


def test_file_table_displays_burst_column_from_metadata_sources() -> None:
    paths = [
        os.path.normpath("C:/photos/a.jpg"),
        os.path.normpath("C:/photos/b.jpg"),
        os.path.normpath("C:/photos/c.jpg"),
        os.path.normpath("C:/photos/d.jpg"),
    ]
    meta_cache = {
        paths[0]: {"burst_position": 3, "burst_id": 12},
        paths[1]: {"XMP-superpicky:burst_id": "12"},
        paths[2]: {"report.burst_position": "2"},
        paths[3]: {},
    }
    model = FileTableModel()
    model.rebuild(paths, meta_cache=meta_cache, tooltip_fn=_tooltip, mismatch_fn=_mismatch)

    assert model.data(model.index(0, _TREE_COL_NAME), _DisplayRole) == "a.jpg"
    assert model.data(model.index(0, _TREE_COL_BURST), _DisplayRole) == "(3/12)"
    assert model.data(model.index(1, _TREE_COL_BURST), _DisplayRole) == "(-/12)"
    assert model.data(model.index(2, _TREE_COL_BURST), _DisplayRole) == "(2/-)"
    assert model.data(model.index(3, _TREE_COL_BURST), _DisplayRole) == ""
    assert "连拍: (3/12)" in model.data(model.index(0, _TREE_COL_BURST), _ToolTipRole)


def test_file_table_burst_sort_key_orders_by_group_then_position() -> None:
    paths = [
        os.path.normpath("C:/photos/missing.jpg"),
        os.path.normpath("C:/photos/group12_pos3.jpg"),
        os.path.normpath("C:/photos/group11_pos9.jpg"),
        os.path.normpath("C:/photos/group12_pos1.jpg"),
    ]
    meta_cache = {
        paths[1]: {"burst_id": 12, "burst_position": 3},
        paths[2]: {"burst_id": 11, "burst_position": 9},
        paths[3]: {"burst_id": 12, "burst_position": 1},
    }
    model = FileTableModel()
    model.rebuild(paths, meta_cache=meta_cache, tooltip_fn=_tooltip, mismatch_fn=_mismatch)

    sorted_names = [
        model.data(model.index(row, _TREE_COL_NAME), _DisplayRole)
        for row in sorted(
            range(model.rowCount()),
            key=lambda item_row: model.data(model.index(item_row, _TREE_COL_BURST), _SortRole),
        )
    ]

    assert sorted_names == [
        "group11_pos9.jpg",
        "group12_pos1.jpg",
        "group12_pos3.jpg",
        "missing.jpg",
    ]


def test_file_table_displays_camera_and_analysis_metadata() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    model = FileTableModel()
    model.rebuild(
        [path],
        meta_cache={
            path: {
                "report.shutter_speed": "0.0005",
                "XMP-superpicky:aperture": "5.6",
                "EXIF:ISO": "800",
                "FocalLength": "600",
                "XMP-tiff:Model": "Alpha 1",
                "XMP-aux:LensModel": "FE 600mm F4 GM OSS",
                "EXIF:DateTimeOriginal": "2026:02:16 16:23:00",
                "report.adj_sharpness": 0.96,
                "XMP-superpicky:adj_topiq": "0.83",
                "focus_status": "BEST",
            }
        },
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    assert model.data(model.index(0, _TREE_COL_SHUTTER), _DisplayRole) == "1/2000s"
    assert model.data(model.index(0, _TREE_COL_APERTURE), _DisplayRole) == "f/5.6"
    assert model.data(model.index(0, _TREE_COL_ISO), _DisplayRole) == "800"
    assert model.data(model.index(0, _TREE_COL_FOCAL), _DisplayRole) == "600mm"
    assert model.data(model.index(0, _TREE_COL_CAMERA), _DisplayRole) == "Alpha 1"
    assert model.data(model.index(0, _TREE_COL_LENS), _DisplayRole) == "FE 600mm F4 GM OSS"
    assert model.data(model.index(0, _TREE_COL_CAPTURE_TIME), _DisplayRole) == "2026/02/16 16:23"
    assert model.data(model.index(0, _TREE_COL_SHARP), _DisplayRole) == "0.96"
    assert model.data(model.index(0, _TREE_COL_AESTHETIC), _DisplayRole) == "0.83"
    assert model.data(model.index(0, _TREE_COL_FOCUS), _DisplayRole) == "精焦"


def test_metadata_loader_merges_report_rows_into_browser_meta(monkeypatch) -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    row = {
        "filename": "a",
        "burst_id": 12,
        "burst_position": 3,
        "iso": 800,
        "shutter_speed": "0.0005",
        "aperture": "5.6",
        "focal_length": 600,
        "camera_model": "Alpha 1",
        "lens_model": "FE 600mm F4 GM OSS",
        "date_time_original": "2026:02:16 16:23:00",
        "adj_sharpness": 0.96,
        "adj_topiq": 0.83,
        "focus_status": "BEST",
    }

    monkeypatch.setattr(_workers, "read_batch_metadata", lambda *args, **kwargs: {})

    loader = MetadataLoader(
        [path],
        meta_proxy=object(),
        metadata_tags=[],
        report_rows_by_path={path: row},
    )
    raw = loader._read_metadata_batch([path])
    meta = loader._parse_rec(raw[path])

    assert meta["burst_id"] == 12
    assert meta["burst_position"] == 3
    assert meta["shutter"] == "1/2000s"
    assert meta["aperture"] == "f/5.6"
    assert meta["iso"] == "800"
    assert meta["focal_length"] == "600mm"
    assert meta["camera_model"] == "Alpha 1"
    assert meta["lens_model"] == "FE 600mm F4 GM OSS"
    assert meta["date_time_original"] == "2026/02/16 16:23"
    assert meta["sharpness"] == "0.96"
    assert meta["aesthetic"] == "0.83"
    assert meta["focus_status"] == "精焦"


def test_metadata_loader_lets_file_or_sidecar_metadata_override_report(monkeypatch) -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    row = {
        "filename": "a",
        "burst_id": 12,
        "iso": 800,
        "lens_model": "Report Lens",
    }

    def fake_read_batch(paths, tags=None, use_cache=True):
        return {
            path: {
                "XMP-superpicky:burst_id": 99,
                "XMP-exif:PhotographicSensitivity": 1600,
                "XMP-aux:LensModel": "Sidecar Lens",
            }
        }

    monkeypatch.setattr(_workers, "read_batch_metadata", fake_read_batch)

    loader = MetadataLoader(
        [path],
        meta_proxy=object(),
        metadata_tags=["-EXIF:ISO"],
        report_rows_by_path={path: row},
    )
    raw = loader._read_metadata_batch([path])
    meta = loader._parse_rec(raw[path])

    assert meta["burst_id"] == 99
    assert meta["iso"] == "1600"
    assert meta["lens_model"] == "Sidecar Lens"


def test_thumbnail_model_keeps_display_role_filename_and_exposes_burst_role() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    model = ThumbnailListModel()
    model.rebuild(
        [path],
        meta_cache={path: {"XMP-superpicky:burst_position": "3", "report.burst_id": "12"}},
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )
    index = model.index(0, 0)

    assert model.data(index, _DisplayRole) == "a.jpg"
    assert model.data(index, _MetaBurstTextRole) == "(3/12)"
    assert "连拍: (3/12)" in model.data(index, _ToolTipRole)


def test_thumbnail_model_exposes_focus_box_role_from_metadata() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    focus_box = (0.25, 0.3, 0.4, 0.5)
    model = ThumbnailListModel()
    model.rebuild(
        [path],
        meta_cache={path: {"focus_box": focus_box}},
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    assert model.data(model.index(0, 0), _MetaFocusBoxRole) == focus_box


def test_thumbnail_model_ignores_downstream_focus_box_sources() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    model = ThumbnailListModel()
    model.rebuild(
        [path],
        meta_cache={
            path: {
                "XMP-superpicky:focus_box": "(0.1, 0.2, 0.8, 0.9)",
                "report.focus_box": "(0.2, 0.3, 0.7, 0.8)",
            }
        },
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    assert model.data(model.index(0, 0), _MetaFocusBoxRole) is None


def test_thumbnail_model_does_not_derive_focus_box_from_focus_xy() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    model = ThumbnailListModel()
    model.rebuild(
        [path],
        meta_cache={path: {"focus_x": 0.25, "focus_y": 0.3}},
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    assert model.data(model.index(0, 0), _MetaFocusBoxRole) is None


def test_metadata_loader_derives_focus_box_from_focus_metadata() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    loader = MetadataLoader([path], meta_proxy=object())
    meta = loader._parse_rec(
        {
            "SourceFile": path,
            "Make": "SONY",
            "Model": "ILCE-1M2",
            "ExifImageWidth": 5472,
            "ExifImageHeight": 3648,
            "MakernoteTag0x2027": "5472 3648 2736 1824 640 480",
        }
    )

    assert "focus_box" in meta
    assert meta["focus_box_checked"] is True
    expected = (
        0.4415204678362573,
        0.4342105263157895,
        0.5584795321637427,
        0.5657894736842105,
    )
    assert all(
        math.isclose(actual, target, rel_tol=1e-9, abs_tol=1e-9)
        for actual, target in zip(meta["focus_box"], expected)
    )


def test_metadata_loader_marks_default_center_focus_as_checked_without_box() -> None:
    path = os.path.normpath("C:/photos/a.arw")
    loader = MetadataLoader([path], meta_proxy=object())
    meta = loader._parse_rec(
        {
            "SourceFile": path,
            "Make": "SONY",
            "Model": "ILCE-1M2",
            "ExifImageWidth": 5616,
            "ExifImageHeight": 3744,
            "MakerNote Tag 0x2027": "5616 3744 2816 1864",
        }
    )

    assert meta["focus_box_checked"] is True
    assert "focus_box" not in meta


def test_metadata_cache_requires_focus_box_checked_marker() -> None:
    assert not FileListPanel._metadata_cache_has_browser_fields(
        {
            "rating": 3,
            "focus_status": "GOOD",
            "XMP-superpicky:focus_box": "(0.1,0.2,0.8,0.9)",
        }
    )
    assert FileListPanel._metadata_cache_has_browser_fields(
        {
            "rating": 3,
            "focus_box_checked": True,
        }
    )


def test_file_list_cached_focus_box_state_distinguishes_checked_none() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    panel = FileListPanel.__new__(FileListPanel)
    panel._meta_cache = {path: {"focus_box_checked": True}}

    assert FileListPanel.get_cached_focus_box_state_for_path(panel, path) == (True, None)


def test_pending_persistent_thumb_queue_is_not_running_thumbnail_work() -> None:
    panel = FileListPanel.__new__(FileListPanel)
    panel._thumbnail_loader = None
    panel._persistent_thumb_cache_worker = None
    panel._persistent_thumb_cache_pending_paths = ["C:/photos/a.jpg"]

    class ActiveTimer:
        def isActive(self) -> bool:
            return True

    panel._persistent_thumb_cache_timer = ActiveTimer()

    assert FileListPanel._thumbnail_work_actively_running(panel) is False
    assert FileListPanel._thumbnail_work_active_or_pending(panel) is True


def test_metadata_loader_uses_batch_focus_metadata_without_raw_rescan(monkeypatch, tmp_path) -> None:
    path = os.path.normpath(str(tmp_path / "sample.ARW"))
    Path(path).write_bytes(b"raw")

    monkeypatch.setattr(
        _workers,
        "read_batch_metadata",
        lambda *args, **kwargs: {
            path: {
                "SourceFile": path,
                "Make": "SONY",
                "Model": "ILCE-1M2",
                "ExifImageWidth": 5472,
                "ExifImageHeight": 3648,
                "MakernoteTag0x2027": "5472 3648 2736 1824 640 480",
            }
        },
    )

    def _fail_raw_focus(_path):
        raise AssertionError("普通 metadata 批量路径不应重复读取 RAW 内嵌焦点")

    monkeypatch.setattr(_workers, "read_raw_embedded_focus_metadata", _fail_raw_focus)

    loader = MetadataLoader([path], meta_proxy=object())
    raw = loader._read_metadata_batch([path])
    meta = loader._parse_rec(raw[path])

    assert "focus_box" in meta


def test_metadata_loader_report_fast_path_uses_raw_embedded_focus(monkeypatch, tmp_path) -> None:
    path = os.path.normpath(str(tmp_path / "sample.ARW"))
    Path(path).write_bytes(b"raw")
    row = {
        "filename": "sample",
        "iso": 800,
        "shutter_speed": "0.0005",
        "aperture": "5.6",
        "focal_length": 600,
        "camera_model": "Report Camera",
        "lens_model": "Report Lens",
        "date_time_original": "2026:02:16 16:23:00",
    }

    monkeypatch.setattr(_workers, "read_batch_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        _workers,
        "read_raw_embedded_focus_metadata",
        lambda _path: {
            "SourceFile": path,
            "Make": "SONY",
            "Model": "ILCE-1M2",
            "ExifImageWidth": 5472,
            "ExifImageHeight": 3648,
            "MakerNote Tag 0x2027": "5472 3648 2736 1824 640 480",
        },
    )

    loader = MetadataLoader([path], meta_proxy=object(), report_rows_by_path={path: row})
    raw = loader._read_metadata_batch([path])
    meta = loader._parse_rec(raw[path])

    assert "focus_box" in meta


def test_metadata_loader_report_fast_path_skips_exiftool_but_keeps_raw_focus(monkeypatch, tmp_path) -> None:
    path = os.path.normpath(str(tmp_path / "sample.ARW"))
    Path(path).write_bytes(b"raw")
    row = {
        "filename": "sample",
        "iso": 800,
        "shutter_speed": "0.0005",
        "aperture": "5.6",
        "focal_length": 600,
        "camera_model": "Report Camera",
        "lens_model": "Report Lens",
        "date_time_original": "2026:02:16 16:23:00",
        "focus_x": 0.1,
        "focus_y": 0.1,
    }

    def _fail_read_batch(*_args, **_kwargs):
        raise AssertionError("complete report rows should not call exiftool during browser metadata load")

    monkeypatch.setattr(_workers, "read_batch_metadata", _fail_read_batch)
    monkeypatch.setattr(
        _workers,
        "read_raw_embedded_focus_metadata",
        lambda _path: {
            "SourceFile": path,
            "Make": "SONY",
            "Model": "ILCE-1M2",
            "ExifImageWidth": 5472,
            "ExifImageHeight": 3648,
            "MakerNote Tag 0x2027": "5472 3648 2736 1824 640 480",
        },
    )

    loader = MetadataLoader(
        [path],
        meta_proxy=object(),
        metadata_tags=["-EXIF:ISO"],
        report_rows_by_path={path: row},
    )
    raw = loader._read_metadata_batch([path])
    meta = loader._parse_rec(raw[path])

    assert meta["camera_model"] == "Report Camera"
    assert meta["iso"] == "800"
    assert meta["focus_box"][0] > 0.4


def test_metadata_loader_does_not_fallback_to_report_focus_when_raw_focus_checked(monkeypatch, tmp_path) -> None:
    path = os.path.normpath(str(tmp_path / "sample.ARW"))
    Path(path).write_bytes(b"raw")
    row = {
        "filename": "sample",
        "iso": 800,
        "shutter_speed": "0.0005",
        "aperture": "5.6",
        "focal_length": 600,
        "camera_model": "Report Camera",
        "lens_model": "Report Lens",
        "date_time_original": "2026:02:16 16:23:00",
        "focus_x": 0.1,
        "focus_y": 0.1,
    }

    monkeypatch.setattr(
        _workers,
        "read_batch_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fast path should skip exiftool")),
    )
    monkeypatch.setattr(_workers, "read_raw_embedded_focus_metadata", lambda _path: {})

    loader = MetadataLoader(
        [path],
        meta_proxy=object(),
        metadata_tags=["-EXIF:ISO"],
        report_rows_by_path={path: row},
    )
    raw = loader._read_metadata_batch([path])
    meta = loader._parse_rec(raw[path])

    assert meta["focus_box_checked"] is True
    assert "focus_box" not in meta


def test_focus_cache_batch_uses_raw_embedded_focus_without_batch_metadata(monkeypatch, tmp_path) -> None:
    path = os.path.normpath(str(tmp_path / "sample.ARW"))
    Path(path).write_bytes(b"raw")

    def _fail_read_batch(*_args, **_kwargs):
        raise AssertionError("RAW focus cache should not call read_batch_metadata when embedded focus is available")

    monkeypatch.setattr(_workers, "read_batch_metadata", _fail_read_batch)
    monkeypatch.setattr(
        _workers,
        "read_raw_embedded_focus_metadata",
        lambda _path: {
            "SourceFile": path,
            "Make": "SONY",
            "Model": "ILCE-1M2",
            "ExifImageWidth": 5472,
            "ExifImageHeight": 3648,
            "MakerNote Tag 0x2027": "5472 3648 2736 1824 640 480",
        },
    )

    loader = MetadataLoader([path], meta_proxy=object())
    focus_batch = loader._build_focus_cache_batch([path])

    assert path in focus_batch
    assert focus_batch[path]["focus_box"] is not None


def test_metadata_loader_reads_chunks_with_configured_worker_pool(monkeypatch) -> None:
    paths = [os.path.normpath(f"C:/photos/{i}.jpg") for i in range(8)]
    thread_names: set[str] = set()
    emitted_paths: list[str] = []

    def fake_read_batch(batch_paths, tags=None, use_cache=True):
        thread_names.add(threading.current_thread().name)
        first = os.path.normpath(batch_paths[0])
        if first.endswith("0.jpg"):
            time.sleep(0.04)
        else:
            time.sleep(0.005)
        return {
            os.path.normpath(path): {"SourceFile": os.path.normpath(path)}
            for path in batch_paths
        }

    monkeypatch.setattr(_workers, "_METADATA_CHUNK_SIZE", 1)
    monkeypatch.setattr(_workers, "read_batch_metadata", fake_read_batch)

    loader = MetadataLoader(paths, meta_proxy=object(), worker_count=4)
    loader.metadata_batch_ready.connect(lambda batch: emitted_paths.extend(batch.keys()))
    loader.run()

    assert len(thread_names) > 1
    assert set(emitted_paths) == set(paths)
    assert emitted_paths[0] != paths[0]
