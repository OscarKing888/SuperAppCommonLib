from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP


def test_strict_subject_read_preserves_chinese_and_literal_semicolons(tmp_path) -> None:
    source = tmp_path / "photo.jpg"
    metadata = PhotoMetaDataXMP()
    subjects = ["捕食", "行为;特殊", "Lightroom"]
    assert metadata.write_subjects(str(source), subjects)
    assert metadata.read_subjects(str(source), strict=True) == subjects
    assert metadata.read_subjects(str(source)) == subjects


def test_strict_subject_read_preserves_rdf_resources_and_deduplication(tmp_path) -> None:
    sidecar = tmp_path / "photo.xmp"
    sidecar.write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:subject><rdf:Bag><rdf:li rdf:resource="飞行" />'
        '<rdf:li>行为;特殊</rdf:li><rdf:li>飞行</rdf:li></rdf:Bag></dc:subject>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>',
        encoding="utf-8",
    )
    assert PhotoMetaDataXMP().read_subjects(str(sidecar), strict=True) == ["飞行", "行为;特殊"]


@pytest.mark.parametrize("strict", [False, True])
def test_missing_sidecar_has_no_subjects_and_is_not_created(tmp_path, strict) -> None:
    source = tmp_path / "missing" / "photo.jpg"
    assert PhotoMetaDataXMP().read_subjects(str(source), strict=strict) == []
    assert not source.parent.exists()


@pytest.mark.parametrize("strict", [False, True])
def test_valid_sidecar_without_subject_has_no_tags(tmp_path, strict) -> None:
    sidecar = tmp_path / "photo.xmp"
    sidecar.write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description /></rdf:RDF></x:xmpmeta>',
        encoding="utf-8",
    )
    assert PhotoMetaDataXMP().read_subjects(str(sidecar), strict=strict) == []


def test_strict_subject_read_raises_for_corrupt_xml_without_modifying_it(tmp_path) -> None:
    sidecar = tmp_path / "photo.xmp"
    contents = b"<xmp>broken"
    sidecar.write_bytes(contents)
    metadata = PhotoMetaDataXMP()
    assert metadata.read_subjects(str(sidecar)) == []
    with pytest.raises(ET.ParseError):
        metadata.read_subjects(str(sidecar), strict=True)
    assert sidecar.read_bytes() == contents


def test_strict_subject_read_propagates_open_permission_error(tmp_path, monkeypatch) -> None:
    sidecar = tmp_path / "photo.xmp"
    sidecar.write_text("<xmp />", encoding="utf-8")
    real_open = Path.open

    def denied_open(path, *args, **kwargs):
        if path == sidecar:
            raise PermissionError("sidecar unavailable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(PermissionError, match="sidecar unavailable"):
        PhotoMetaDataXMP().read_subjects(str(sidecar), strict=True)


def test_default_subject_read_still_swallows_parser_io_errors(tmp_path, monkeypatch) -> None:
    sidecar = tmp_path / "photo.xmp"
    sidecar.write_text("<xmp />", encoding="utf-8")

    def denied_parse(*args, **kwargs):
        raise PermissionError("sidecar unavailable")

    monkeypatch.setattr(ET, "parse", denied_parse)
    metadata = PhotoMetaDataXMP()
    assert metadata.read_subjects(str(sidecar)) == []
    with pytest.raises(PermissionError, match="sidecar unavailable"):
        metadata.read_subjects(str(sidecar), strict=True)
