"""HIF 的报告/侧车字段完整，也不能替代相机焦点和方向。"""
import os

import pytest

from app_common.file_browser import _workers
from app_common.focus_calc import extract_focus_box_for_display


@pytest.mark.parametrize("extension", ["HIF", "heif", "HEIC"])
@pytest.mark.parametrize("orientation", [1, 6, 8])
@pytest.mark.parametrize("fast_source", ["report", "xmp"])
def test_heif_focus_survives_complete_browser_metadata(
    tmp_path, monkeypatch, extension, orientation, fast_source,
):
    path = os.path.normpath(str(tmp_path / f"红耳鹎.{extension}"))
    (tmp_path / f"红耳鹎.{extension}").write_bytes(b"heif placeholder")
    row = {"iso": 800, "shutter_speed": "0.002", "aperture": "5.6",
           "focal_length": 400, "camera_model": "ILCE-1M2", "lens_model": "FE 400mm",
           "focus_x": 0.1, "focus_y": 0.1}
    sidecar = {f"XMP-superpicky:{k}": v for k, v in row.items()} if fast_source == "xmp" else {}
    sidecar["XMP-dc:Title"] = "红耳鹎"
    source = {"SourceFile": path, "IFD0:Make": "SONY", "IFD0:Model": "ILCE-1M2",
              "ExifIFD:ExifImageWidth": 8640, "ExifIFD:ExifImageHeight": 5760,
              "Sony:FocusLocation": "8640 5760 4320 2880",
              "Sony:FocusFrameSize": "918 732 257", "Sony:CameraOrientation": orientation}
    reads = []

    def read(paths, **kwargs):
        reads.append(list(paths))
        return {path: {**source, **sidecar}}

    monkeypatch.setattr(_workers, "_batch_read_xmp_sidecar", lambda paths, **kwargs: {path: sidecar})
    monkeypatch.setattr(_workers, "read_batch_metadata", read)
    monkeypatch.setattr(_workers, "fast_read_browser_metadata", lambda paths: ({}, paths))
    monkeypatch.setattr(_workers.meta_disk_cache, "get_many", lambda *args: {})
    monkeypatch.setattr(_workers, "read_raw_embedded_focus_metadata",
                        lambda path: pytest.fail("HEIF must not use the RAW focus reader"))
    loader = _workers.MetadataLoader(
        [path], object(), report_rows_by_path={path: row} if fast_source == "report" else {},
    )
    parsed, _, count = loader._read_parse_chunk([path])

    assert reads == [[path]]
    assert count == 1 and parsed[path]["focus_box_checked"]
    assert parsed[path]["focus_box"] == pytest.approx(extract_focus_box_for_display(source, 1, 1))
    assert parsed[path]["title"] == "红耳鹎"
    assert parsed[path]["iso"] == "800"
