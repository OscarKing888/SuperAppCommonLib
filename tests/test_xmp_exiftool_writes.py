from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

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


@pytest.mark.parametrize("mixed", [False, True])
def test_first_sidecar_camera_edit_overrides_original_exif(photo, mixed):
    jpeg = photo.with_suffix(".jpg")
    exif = Image.Exif()
    exif[272] = "CameraBefore"
    exif[270] = "Original camera comment"
    Image.new("RGB", (8, 8), "green").save(jpeg, exif=exif)
    original = jpeg.read_bytes()
    fields = {"XMP-tiff:Model": "中文新相机 Ω"}
    if mixed:
        fields.update({"XMP-dc:Title": "新鸟名", "XMP-dc:Description": "新注释"})
    store = PhotoMetaDataXMP()
    assert store.write(str(jpeg), fields)
    assert store.read(str(jpeg))["XMP-tiff:Model"] == "中文新相机 Ω"
    if mixed:
        assert store.read(str(jpeg))["Title"] == "新鸟名"
        assert store.read(str(jpeg))["Description"] == "新注释"
    assert jpeg.read_bytes() == original


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


def test_nested_lang_alt_from_external_editor_is_flattened_for_exiftool(photo) -> None:
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_text('''<?xml version="1.0" encoding="UTF-8"?>
      <x:xmpmeta xmlns:x="adobe:ns:meta/">
        <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
          <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
             xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:Exposure2012="+2.18">
            <dc:description><rdf:Alt><rdf:li xml:lang="x-default">
              <rdf:Alt><rdf:li xml:lang="x-default">相机里的中文说明</rdf:li></rdf:Alt>
            </rdf:li></rdf:Alt></dc:description>
          </rdf:Description>
        </rdf:RDF>
      </x:xmpmeta>''', encoding="utf-8")
    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo), {"XMP-iptcExt:Event": "41.55", "XMP-dc:Title": "赤翡翠"})

    read = metadata.read(str(photo))
    assert read["Title"] == "赤翡翠"
    assert read["XMP-Iptc4xmpExt:Event"] == "41.55"
    assert read["Description"] == "相机里的中文说明"
    assert read["XMP-crs:Exposure2012"] == "+2.18"


@pytest.mark.parametrize('reject', [False, True])
def test_legacy_duplicate_scores_only_repaired_on_successful_staged_write(photo, reject):
    sidecar = photo.with_suffix('.xmp')
    sidecar.write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/"
        xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/"
        xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:custom="https://example.test/unknown/">
      <rdf:RDF>
        <rdf:Description rdf:about="" xmp:Rating="2" xmpDM:pick="0" photoshop:City="652.6489857543405">
          <photoshop:State>6.3203125</photoshop:State><photoshop:Country>GOOD</photoshop:Country>
          <dc:description><rdf:Alt><rdf:li xml:lang="x-default">保留中文拍摄笔记</rdf:li></rdf:Alt></dc:description>
          <custom:note>保留未知字段</custom:note>
        </rdf:Description>
        <rdf:Description rdf:about=""><xmp:Rating>3</xmp:Rating><xmpDM:pick>1</xmpDM:pick>
          <photoshop:City>652.65</photoshop:City><photoshop:State>06.32</photoshop:State>
          <photoshop:Country>GOOD</photoshop:Country></rdf:Description>
        <rdf:Description rdf:about=""><photoshop:City /></rdf:Description>
      </rdf:RDF></x:xmpmeta>''', encoding='utf-8')
    before = sidecar.read_bytes(), photo.read_bytes()
    store = PhotoMetaDataXMP()
    metadata = store.read(str(photo))
    fields = {'XMP-iptcExt:Event': '0', 'XMP-dc:Title': '反嘴鹬'}
    if reject:
        fields['XMP-exif:NotARealWritableTag'] = '禁止部分保存'
    assert store.write(str(photo), fields) is not reject
    assert photo.read_bytes() == before[1]
    if reject:
        assert sidecar.read_bytes() == before[0]
    else:
        read = store.read(str(photo))
        assert read['Title'] == '反嘴鹬' and read['XMP-Iptc4xmpExt:Event'] == '0'
        for key in ['rating', 'pick', 'XMP-photoshop:City', 'XMP-photoshop:State', 'XMP-photoshop:Country',
                    'Description', 'XMP-unknown:note']:
            assert read[key] == metadata[key]
        assert read['rating'] == 3 and read['pick'] == 1
    assert sorted(p.name for p in photo.parent.iterdir()) == sorted([photo.name, sidecar.name])


def test_duplicate_scalar_repair_preserves_foreign_resources_and_structured_values(photo):
    root = ET.fromstring('''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:custom="https://example.test/unknown/">
      <rdf:Description rdf:about="other.jpg" xmp:Rating="1"><xmp:Rating>2</xmp:Rating></rdf:Description>
      <rdf:Description rdf:about="" xmp:Rating="3"><xmp:Rating custom:note="限定值">4</xmp:Rating>
        <custom:note>未知1</custom:note><custom:note>未知2</custom:note></rdf:Description>
    </rdf:RDF>''')
    before = ET.tostring(root)
    assert not PhotoMetaDataXMP._deduplicate_photo_scalars(root, str(photo))
    assert ET.tostring(root) == before
