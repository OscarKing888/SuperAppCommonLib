"""Background sidecar readers must not pin Windows files against rename/delete."""
import hashlib
import os
from contextlib import contextmanager

import pytest

from app_common.exif_io import metadata_sqlite, xmp_sidecar
from app_common.exif_io import photo_meta
from app_common.exif_io.shared_read import open_shared_read


XMP = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
       'xmlns:dc="http://purl.org/dc/elements/1.1/"><rdf:Description rdf:about="" '
       'dc:title="中文鸟名" /></rdf:RDF>').encode("utf-8")


def mutate_while_reading(path, operation):
    if operation == "delete":
        path.unlink()
    elif operation == "rename":
        path.rename(path.with_name("moved.xmp"))
    else:
        replacement = path.with_name("new.xmp")
        replacement.write_bytes(b"replacement")
        os.replace(replacement, path)


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_sidecar_fingerprint_does_not_block_file_lifecycle(tmp_path, monkeypatch, operation):
    path = tmp_path / "白鹭.xmp"
    path.write_bytes(XMP)
    file_digest = hashlib.file_digest
    completed = []
    def during_digest(stream, algorithm):
        # The real OS handle stays open throughout this mutation. No timing
        # window or sleep is involved, including on Windows.
        mutate_while_reading(path, operation)
        completed.append(operation)
        return file_digest(stream, algorithm)
    monkeypatch.setattr(metadata_sqlite.hashlib, "file_digest", during_digest)
    signature = metadata_sqlite.sidecar_stamp(path)
    assert completed == [operation]
    # Renaming can change ctime on Windows: refusing to cache that generation
    # is valid, but the file operation itself must complete with the handle open.
    assert signature is None or signature[-1] == hashlib.sha256(XMP).hexdigest()


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_xml_parse_does_not_block_file_lifecycle(tmp_path, monkeypatch, operation):
    path = tmp_path / "白鹭.xmp"
    path.write_bytes(XMP)
    @contextmanager
    def during_read(source):
        with open_shared_read(source) as stream:
            mutate_while_reading(path, operation)
            yield stream
    monkeypatch.setattr(xmp_sidecar, "open_shared_read", during_read)
    assert ("XMP-dc", "title", "中文鸟名") in xmp_sidecar.read_xmp_file(str(path))


def test_xml_parse_releases_file_before_parsing(tmp_path, monkeypatch):
    path = tmp_path / "白鹭.xmp"
    path.write_bytes(XMP)
    parse = xmp_sidecar.ET.fromstring
    def during_parse(content):
        mutate_while_reading(path, "replace")
        return parse(content)
    monkeypatch.setattr(xmp_sidecar.ET, "fromstring", during_parse)
    assert ("XMP-dc", "title", "中文鸟名") in xmp_sidecar.read_xmp_file(str(path))
    assert path.read_bytes() == b"replacement"


def test_shared_read_reports_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_shared_read(tmp_path / "missing.xmp")


@pytest.mark.parametrize("operation", ["delete", "rename"])
@pytest.mark.parametrize("reader", ["subjects", "json"])
def test_strict_sidecar_readers_allow_file_lifecycle(tmp_path, monkeypatch, operation, reader):
    source = tmp_path / "白鹭.jpg"
    source.write_bytes(b"source photo")
    sidecar = source.with_suffix(".xmp")
    sidecar.write_text('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                      'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                      'xmlns:sp="https://superbirdtools.local/xmp/superpicky/1.0/">'
                      '<rdf:Description rdf:about=""><dc:subject><rdf:Bag><rdf:li>保留;整项</rdf:li>'
                      '</rdf:Bag></dc:subject><sp:recipe>{"title":"中文"}</sp:recipe>'
                      '</rdf:Description></rdf:RDF>', encoding="utf-8")
    def during_read(path):
        with open_shared_read(path) as stream:
            mutate_while_reading(sidecar, operation)
            return stream.read()
    monkeypatch.setattr(photo_meta, "read_shared_bytes", during_read)
    writer = photo_meta.PhotoMetaDataXMP()
    if reader == "subjects":
        assert writer.read_subjects(str(source), strict=True) == ["保留;整项"]
    else:
        assert writer.read_json_field(str(source), "recipe") == {"title": "中文"}
