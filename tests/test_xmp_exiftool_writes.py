from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP


@pytest.fixture
def photo(tmp_path, monkeypatch) -> Path:
    if not get_exiftool_executable_path():
        pytest.skip("ExifTool is unavailable")
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    photo = tmp_path / "中文照片.png"
    Image.new("RGB", (8, 6), "green").save(photo)
    return photo


def _existing_sidecar(photo: Path) -> Path:
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_text('''<?xml version="1.0" encoding="UTF-8"?>
      <x:xmpmeta xmlns:x="adobe:ns:meta/">
        <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
          <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
             xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:PhotographicSensitivity="100"
             xmlns:custom="https://example.test/unknown/" custom:note="保留未知字段">
            <dc:title><rdf:Alt><rdf:li xml:lang="x-default">原来鸟名</rdf:li></rdf:Alt></dc:title>
            <dc:subject><rdf:Bag><rdf:li>原来标签</rdf:li></rdf:Bag></dc:subject>
          </rdf:Description>
        </rdf:RDF>
      </x:xmpmeta>''', encoding="utf-8")
    return sidecar


@pytest.mark.parametrize("existing", [False, True])
def test_real_exiftool_roundtrips_iso_camera_shutter_and_chinese(photo, existing) -> None:
    if existing:
        _existing_sidecar(photo)
    original = photo.read_bytes()
    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo), {
        "EXIF:ISO": "1600", "EXIF:Model": "中文相机 Ω",
        "EXIF:ExposureTime": "0.001", "EXIF:LensModel": "中文镜头",
        "XMP-dc:Description": "中文观察备注", "XMP-dc:Subject": ["飞行", "捕食"],
    })

    read = metadata.read(str(photo))
    assert read["XMP-exif:ISOSpeedRatings"] == "1600"
    assert "XMP-exif:PhotographicSensitivity" not in read
    assert read["XMP-tiff:Model"] == "中文相机 Ω"
    assert read["XMP-aux:Lens"] == "中文镜头"
    assert read["XMP-exif:ExposureTime"] == "1/1000"
    assert read["Description"] == "中文观察备注"
    assert metadata.read_subjects(str(photo), strict=True) == ["飞行", "捕食"]
    assert photo.read_bytes() == original
    assert sorted(path.name for path in photo.parent.iterdir()) == sorted([photo.name, photo.with_suffix(".xmp").name])
    if existing:
        assert read["Title"] == "原来鸟名"
        assert read["XMP-unknown:note"] == "保留未知字段"


@pytest.mark.parametrize("key", [
    "ExifIFD:ISO", "EXIF:PhotographicSensitivity", "ExifIFD:ISOSpeedRatings",
    "XMP-exif:ISO", "XMP-exif:PhotographicSensitivity", "XMP-exif:ISOSpeedRatings",
])
def test_iso_aliases_use_exiftool_iso_name(photo, key) -> None:
    metadata = PhotoMetaDataXMP()
    assert metadata.write(str(photo), {key: "800"})
    assert metadata.read(str(photo))["XMP-exif:ISOSpeedRatings"] == "800"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("fields", [
    {"XMP-exif:NotARealWritableTag": "value"},
    {"XMP-exif:NotARealWritableTag": "value", "EXIF:Model": "不应保存的相机"},
    {"XMP-exif:NotARealWritableTag": "value", "XMP-dc:Title": "不应保存的鸟名"},
    {"File:FileName": "must-not-rename.png"},
    {"EXIF:ISO": "not-an-ISO-number"},
])
def test_rejected_assignments_preserve_original_photo_and_sidecar(photo, existing, fields) -> None:
    sidecar = _existing_sidecar(photo) if existing else photo.with_suffix(".xmp")
    before = {path.name: path.read_bytes() for path in photo.parent.iterdir()}
    assert not PhotoMetaDataXMP().write(str(photo), fields)
    assert {path.name: path.read_bytes() for path in photo.parent.iterdir()} == before
    assert sidecar.exists() is existing


def test_generic_write_keeps_corrupt_existing_sidecar(photo) -> None:
    sidecar = photo.with_suffix(".xmp")
    original = "<损坏XMP".encode("utf-8")
    sidecar.write_bytes(original)
    assert not PhotoMetaDataXMP().write(str(photo), {"EXIF:ISO": "800"})
    assert sidecar.read_bytes() == original
