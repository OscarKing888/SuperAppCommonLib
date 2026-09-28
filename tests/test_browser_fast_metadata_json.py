from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from app_common.exif_io import meta_disk_cache
from app_common.exif_io.fast_reader import fast_read_browser_metadata
from app_common.exif_io.json_sidecar import (
    JSON_SIDECAR_SUFFIX, json_sidecar_path_for, write_json_sidecar,
)
from app_common.file_browser import _workers


def test_fast_reader_falls_back_for_jpeg_without_complete_capture_fields(tmp_path):
    photo = tmp_path / "无拍摄参数.jpg"
    Image.new("RGB", (24, 16)).save(photo)
    records, fallback = fast_read_browser_metadata([str(photo)])
    assert records == {}
    assert fallback == [str(photo)]


def test_file_cache_keeps_central_json_fresh_and_prefers_it_to_legacy(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / ".superpicky").mkdir(parents=True)
    photo = root / "day" / "白鹭.jpg"
    photo.parent.mkdir()
    Image.new("RGB", (24, 16)).save(photo)
    legacy = Path(str(photo) + JSON_SIDECAR_SUFFIX)
    legacy.write_text('{"metadata": {"Description": "旧备注", "rating": 1}}', encoding="utf-8")
    assert write_json_sidecar(str(photo), {"metadata": {"Description": "中文备注", "rating": 4}})
    assert json_sidecar_path_for(photo).is_file()

    calls = []

    def read_fast(paths):
        calls.extend(paths)
        return {os.path.normpath(str(photo)): {"SourceFile": str(photo), "Model": "Camera"}}, []

    monkeypatch.setattr(_workers, "fast_read_browser_metadata", read_fast)
    monkeypatch.setattr(_workers, "read_batch_metadata", lambda *_args, **_kwargs: {})
    loader = _workers.MetadataLoader([str(photo)], object(), selected_dir=str(photo.parent))
    try:
        first = loader._read_metadata_batch([str(photo)])[os.path.normpath(str(photo))]
        assert first["Model"] == "Camera"
        assert first["XMP-dc:Description"] == "中文备注"
        assert str(first["XMP-xmp:Rating"]) == "4"
        assert len(calls) == 1

        assert write_json_sidecar(str(photo), {"metadata": {"Description": "更新备注", "rating": 0}})
        second = loader._read_metadata_batch([str(photo)])[os.path.normpath(str(photo))]
        assert second["Model"] == "Camera"
        assert second["XMP-dc:Description"] == "更新备注"
        assert str(second["XMP-xmp:Rating"]) == "0"
        assert len(calls) == 1  # image-derived fields came from the disk cache
    finally:
        meta_disk_cache.close_all()


def test_incomplete_fast_read_uses_exiftool_fallback_then_json(tmp_path, monkeypatch):
    photo = tmp_path / "影像.png"
    Image.new("RGB", (20, 12)).save(photo)
    assert write_json_sidecar(str(photo), {"metadata": {"Description": "侧车优先"}})
    monkeypatch.setattr(_workers, "fast_read_browser_metadata", lambda paths: ({}, paths))
    monkeypatch.setattr(
        _workers, "read_batch_metadata",
        lambda paths, **_kw: {os.path.normpath(str(photo)): {
            "SourceFile": str(photo), "Description": "嵌入备注", "Model": "Fallback"}}
    )
    loader = _workers.MetadataLoader([str(photo)], object(), selected_dir=str(tmp_path))
    try:
        rec = loader._read_metadata_batch([str(photo)])[os.path.normpath(str(photo))]
        assert rec["Model"] == "Fallback"
        assert rec["Description"] == "侧车优先"
    finally:
        meta_disk_cache.close_all()
