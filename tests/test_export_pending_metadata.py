"""Exports freeze accepted edits without writing the source sidecar."""
from xml.etree import ElementTree as ET

from PIL import Image

from app_common.exif_io.export_xmp import read_export_xmp
from app_common.exif_io.metadata_store import get_store
from app_common.exif_io.photo_meta import PhotoMetaDataXMP


def test_pending_chinese_title_and_tag_delta_are_exported(tmp_path):
    source = tmp_path / "白鹭.jpg"
    Image.new("RGB", (20, 10)).save(source)
    writer = PhotoMetaDataXMP()
    assert writer.write(str(source), {"XMP-dc:Title": "原鸟名", "XMP-dc:Subject": ["未知标签", "移除"]})
    sidecar = source.with_suffix(".xmp")
    before = sidecar.read_bytes()
    store = get_store()
    store.submit_edit(str(source), {"XMP-dc:Title": "新鸟名", "XMP-xmp:Rating": 0},
                      subject_states={"移除": False, "新增": True}).result(timeout=5)
    root = ET.fromstring(read_export_xmp(source))
    dc = "http://purl.org/dc/elements/1.1/"
    rdf = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    assert root.find(f".//{{{dc}}}title/{{{rdf}}}Alt/{{{rdf}}}li").text == "新鸟名"
    assert {node.text for node in root.findall(f".//{{{dc}}}subject/{{{rdf}}}Bag/{{{rdf}}}li")} == {"未知标签", "新增"}
    assert root.find(".//{http://ns.adobe.com/xap/1.0/}Rating").text == "0"
    assert sidecar.read_bytes() == before
    assert store.pending_edits(str(source)) is not None


def test_pending_fields_export_without_creating_source_sidecar(tmp_path):
    source = tmp_path / "未同步.png"
    Image.new("RGB", (20, 10)).save(source)
    get_store().submit_edit(str(source), {"XMP-dc:Description": "中文备注"}).result(timeout=5)
    root = ET.fromstring(read_export_xmp(source))
    assert "中文备注" in "".join(root.itertext())
    assert not source.with_suffix(".xmp").exists()


def test_export_clear_removes_embedded_value_and_roundtrips_as_empty(tmp_path):
    from app_common.exif_io.export_xmp import merge_export_xmp, apply_pending_export_edits
    from app_common.exif_io.xmp_sidecar import read_xmp_file
    source = tmp_path / "bird.jpg"
    packet = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
       xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
       xmlns:s="https://superbirdtools.local/xmp/superpicky/1.0/">
       <rdf:Description rdf:about="" tiff:Model="Camera"/></rdf:RDF>'''
    pending = {"fields": {"XMP-tiff:Model": "", "XMP-dc:Title": ""}}
    output = apply_pending_export_edits(packet, pending, source=source)
    sidecar = source.with_suffix(".xmp")
    sidecar.write_bytes(output)
    rows = {(group, name): value for group, name, value in read_xmp_file(str(sidecar))}
    assert rows["XMP-tiff", "Model"] == ""
    assert rows["XMP-dc", "Title"] == ""
    # Once synced, the sidecar may carry only the marker for typed fields.
    marker = packet.replace(b'tiff:Model="Camera"',
        b"s:metadata_cleared_fields='[&quot;XMP-tiff:Model&quot;]'")
    sidecar.write_bytes(merge_export_xmp(packet, marker, source=source))
    rows = {(group, name): value for group, name, value in read_xmp_file(str(sidecar))}
    assert rows["XMP-tiff", "Model"] == ""
    # A later external sidecar property takes priority over its old marker.
    sidecar.write_bytes(marker.replace(b'rdf:about=""', b'rdf:about="" tiff:Model="External"'))
    rows = {(group, name): value for group, name, value in read_xmp_file(str(sidecar))}
    assert rows["XMP-tiff", "Model"] == "External"
