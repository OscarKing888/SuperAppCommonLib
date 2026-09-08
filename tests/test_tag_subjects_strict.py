from __future__ import annotations

import builtins
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app_common.exif_io import json_sidecar
from app_common.exif_io.photo_meta import PhotoMetaDataJSON, PhotoMetaDataXMP


@pytest.mark.parametrize("content", ['{"metadata":', '[]', '{"metadata": []}'])
def test_strict_json_rejects_invalid_payload_without_changing_default(tmp_path, content):
    photo = tmp_path / "中文.png"
    photo.write_bytes(b"source")
    sidecar = json_sidecar.json_sidecar_path_for(photo)
    sidecar.write_text(content, encoding="utf-8")
    meta = PhotoMetaDataJSON()
    assert meta.read_subjects(str(photo)) == []
    with pytest.raises(ValueError):
        meta.read_subjects(str(photo), strict=True)
    assert sidecar.read_text(encoding="utf-8") == content


def test_strict_json_uses_central_then_legacy_and_never_skips_damage(tmp_path):
    (tmp_path / ".superpicky").mkdir()
    photo = tmp_path / "中文.png"
    photo.write_bytes(b"source")
    legacy = json_sidecar.sibling_json_sidecar_path_for(photo)
    legacy.write_text(json.dumps({"Subject": ["旧标签"]}, ensure_ascii=False), encoding="utf-8")
    meta = PhotoMetaDataJSON()
    assert meta.read_subjects(str(photo), strict=True) == ["旧标签"]
    central = json_sidecar.json_sidecar_path_for(photo)
    central.parent.mkdir(parents=True)
    central.write_text('{"metadata": {"Subject": ["集中标签"]}}', encoding="utf-8")
    assert meta.read_subjects(str(photo), strict=True) == ["集中标签"]
    central.write_text('{"metadata":', encoding="utf-8")
    with pytest.raises(ValueError):
        meta.read_subjects(str(photo), strict=True)


def test_strict_json_propagates_read_error(tmp_path, monkeypatch):
    photo = tmp_path / "readonly.png"
    sidecar = json_sidecar.json_sidecar_path_for(photo)
    sidecar.write_text('{"metadata": {"Subject": ["保留"]}}', encoding="utf-8")

    def denied(path, *args, **kwargs):
        if Path(path) == sidecar:
            raise PermissionError("test sidecar read failure")
        return builtins.open(path, *args, **kwargs)

    monkeypatch.setattr(json_sidecar, "open", denied, raising=False)
    meta = PhotoMetaDataJSON()
    assert meta.read_subjects(str(photo)) == []
    with pytest.raises(PermissionError):
        meta.read_subjects(str(photo), strict=True)


def test_strict_xmp_fallback_distinguishes_missing_empty_and_invalid(tmp_path):
    photo = tmp_path / "source.png"
    photo.write_bytes(b"source")
    meta = PhotoMetaDataJSON(fallback=PhotoMetaDataXMP())
    assert meta.read_subjects(str(photo), strict=True) == []
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"/>', encoding="utf-8")
    assert meta.read_subjects(str(photo), strict=True) == []
    sidecar.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"><broken', encoding="utf-8")
    assert meta.read_subjects(str(photo)) == []
    with pytest.raises(ET.ParseError):
        meta.read_subjects(str(photo), strict=True)


def test_strict_xmp_fallback_propagates_read_error(tmp_path, monkeypatch):
    photo = tmp_path / "source.png"
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"/>', encoding="utf-8")
    original = ET.parse

    def denied(path, *args, **kwargs):
        if Path(path) == sidecar:
            raise PermissionError("test XMP read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(ET, "parse", denied)
    meta = PhotoMetaDataJSON(fallback=PhotoMetaDataXMP())
    assert meta.read_subjects(str(photo)) == []
    with pytest.raises(PermissionError):
        meta.read_subjects(str(photo), strict=True)
