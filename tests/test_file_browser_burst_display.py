import os
import threading
import time

import app_common.file_browser._workers as _workers
from app_common.file_browser._browser_core import (
    _DisplayRole,
    _ForegroundRole,
    _MetaBurstTextRole,
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


def test_metadata_loader_reads_chunks_with_configured_worker_pool(monkeypatch) -> None:
    paths = [os.path.normpath(f"C:/photos/{i}.jpg") for i in range(8)]
    thread_names: set[str] = set()

    def fake_read_batch(batch_paths, tags=None, use_cache=True):
        thread_names.add(threading.current_thread().name)
        time.sleep(0.02)
        return {
            os.path.normpath(path): {"SourceFile": os.path.normpath(path)}
            for path in batch_paths
        }

    monkeypatch.setattr(_workers, "_METADATA_CHUNK_SIZE", 1)
    monkeypatch.setattr(_workers, "read_batch_metadata", fake_read_batch)

    loader = MetadataLoader(paths, meta_proxy=object(), worker_count=4)
    loader.run()

    assert len(thread_names) > 1
