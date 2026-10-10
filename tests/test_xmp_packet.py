import xml.etree.ElementTree as ET

import pytest

from app_common.exif_io.xmp_sidecar import parse_xmp_metadata, read_xmp_sidecar


def test_embedded_packet_uses_the_sidecar_parser_without_journals(tmp_path):
    packet = '''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="0">
    <dc:description>中文备注</dc:description></rdf:Description></rdf:RDF>'''
    photo = tmp_path / "中文.jpg"
    photo.with_suffix(".xmp").write_text(packet, encoding="utf-8")
    rows = parse_xmp_metadata(packet.encode("utf-8"))
    assert rows == read_xmp_sidecar(str(photo))
    assert ("XMP-dc", "description", "中文备注") in rows
    assert ("XMP-xmp", "Rating", "0") in rows


def test_malformed_embedded_packet_is_reported_to_caller():
    with pytest.raises(ET.ParseError):
        parse_xmp_metadata(b"<broken>")
