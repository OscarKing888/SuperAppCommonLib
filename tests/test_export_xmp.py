"""独立成片 XMP 保留完整属性，并且只读源文件。"""
from subprocess import CompletedProcess
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from app_common.exif_io import exiftool_path, exiftool_runner
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


@pytest.mark.parametrize('extra_packet', [False, True])
def test_embedded_xmp_accepts_trailing_nul_padding(tmp_path, monkeypatch, extra_packet):
    source = tmp_path / '原图.RW2'
    source.write_bytes(b'RAW')
    embedded = (b"<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>"
                + packet('<p:value>内嵌值</p:value><p:embedded>相机数据</p:embedded>')
                + b"<?xpacket end='w'?>")
    if extra_packet:
        embedded += packet('<p:extra>扩展数据</p:extra>')
    sidecar = source.with_suffix('.xmp')
    sidecar_bytes = packet('<p:value>白脸䴓</p:value>')
    sidecar.write_bytes(sidecar_bytes)
    monkeypatch.setattr(exiftool_path, 'get_exiftool_executable_path', lambda: 'exiftool')
    monkeypatch.setattr(exiftool_runner, 'run_exiftool_once', lambda *args, **kwargs:
                        CompletedProcess(args[0], 0, embedded + b' \x00\x00\r\n', b''))

    root = ET.fromstring(read_export_xmp(source))
    assert root.find('.//{urn:private}value').text == '白脸䴓'
    assert root.find('.//{urn:private}embedded').text == '相机数据'
    if extra_packet:
        assert root.find('.//{urn:private}extra').text == '扩展数据'
    assert source.read_bytes() == b'RAW' and sidecar.read_bytes() == sidecar_bytes


def test_nul_inside_embedded_xmp_is_still_invalid(tmp_path, monkeypatch):
    source = tmp_path / '原图.RW2'
    embedded = packet('<p:value>损坏\x00字段</p:value>')
    monkeypatch.setattr(exiftool_path, 'get_exiftool_executable_path', lambda: 'exiftool')
    monkeypatch.setattr(exiftool_runner, 'run_exiftool_once', lambda *args, **kwargs:
                        CompletedProcess(args[0], 0, embedded + b'\x00', b''))

    with pytest.raises(ValueError, match='无法读取原图 XMP.*invalid token'):
        read_export_xmp(source)
