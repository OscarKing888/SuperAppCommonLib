"""独立成片 XMP 保留完整属性，并且只读源文件。"""
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from app_common.exif_io.export_xmp import read_export_xmp, merge_export_xmp, export_xmp_geometry

RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'


def packet(properties):
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="{RDF}" '
            'xmlns:p="urn:private" xmlns:tiff="http://ns.adobe.com/tiff/1.0/">'
            f'<rdf:RDF><rdf:Description rdf:about="">{properties}</rdf:Description>'
            '</rdf:RDF></x:xmpmeta>').encode('utf-8')


def test_merge_and_geometry_preserve_namespace_values_and_chinese(tmp_path):
    source = tmp_path / '原图.CR3'
    base = packet('<p:value>旧值</p:value><tiff:Orientation>6</tiff:Orientation>')
    edits = packet('<p:value>普通翠鸟</p:value><p:typed rdf:datatype="p:type">0</p:typed>')
    merged = merge_export_xmp(base, edits, source=source)
    result = export_xmp_geometry(merged, source=source, size=(40, 30))
    root = ET.fromstring(result)
    assert root.find('.//{urn:private}value').text == '普通翠鸟'
    assert root.find('.//{http://ns.adobe.com/tiff/1.0/}Orientation').text == '1'
    assert b'xmlns:p="urn:private"' in result and b'rdf:datatype="p:type"' in result


@pytest.mark.parametrize('contents', [b'', b'  ', b'<broken>'])
def test_empty_or_malformed_source_sidecar_fails_without_writes(tmp_path, contents):
    source = tmp_path / '原图.jpg'
    with Image.new('RGB', (20, 10)) as image:
        image.save(source)
    sidecar = source.with_suffix('.XMP')
    sidecar.write_bytes(contents)
    before = source.read_bytes()
    with pytest.raises(ValueError, match='XMP'):
        read_export_xmp(source)
    assert source.read_bytes() == before and sidecar.read_bytes() == contents


def test_source_without_metadata_gets_valid_photo_description(tmp_path):
    source = tmp_path / 'plain.png'
    with Image.new('RGB', (20, 10)) as image:
        image.save(source)
    root = ET.fromstring(read_export_xmp(source))
    assert root.find(f'.//{{{RDF}}}Description') is not None
    assert not source.with_suffix('.xmp').exists()
