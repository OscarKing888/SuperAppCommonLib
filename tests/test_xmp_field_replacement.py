from __future__ import annotations

import os
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from app_common.exif_io.writer import _batch_read_xmp_sidecar


RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC = "http://purl.org/dc/elements/1.1/"
XMP = "http://ns.adobe.com/xap/1.0/"
PICK = "http://ns.adobe.com/xmp/1.0/DynamicMedia/"
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
ABOUT = f"{{{RDF}}}about"


@pytest.fixture
def photo(tmp_path, monkeypatch) -> Path:
    # Hydration is separately covered below; never consult an ancestor library.
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    path = tmp_path / "翠鸟照片.jpg"
    path.write_bytes(b"original photo must remain unchanged")
    return path


def _packet(photo: Path, descriptions: str) -> Path:
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_text(
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="{RDF}" '
        f'xmlns:dc="{DC}" xmlns:xmp="{XMP}" xmlns:xmpDM="{PICK}" '
        'xmlns:custom="https://example.test/unknown/">'
        f"<rdf:RDF>{descriptions}</rdf:RDF></x:xmpmeta>",
        encoding="utf-8",
    )
    return sidecar


def _descriptions(sidecar: Path) -> list[ET.Element]:
    return ET.parse(sidecar).getroot().findall(f"{{{RDF}}}RDF/{{{RDF}}}Description")


def _structure(element: ET.Element):
    return (element.tag, element.attrib, (element.text or "").strip(),
            [_structure(child) for child in element])


@pytest.mark.parametrize(
    "field,namespace,local,key,value",
    [
        ("subject", DC, "subject", "XMP-dc:Subject", ["翠鸟", "捕食观察"]),
        ("subject", DC, "subject", "XMP-dc:Subject", []),
        ("title", DC, "title", "XMP-dc:Title", "新的中文标题"),
        ("title", DC, "title", "XMP-dc:Title", ""),
        ("description", DC, "description", "XMP-dc:Description", "新的中文备注"),
        ("description", DC, "description", "XMP-dc:Description", ""),
        ("rating", XMP, "Rating", "XMP-xmp:Rating", 5),
        ("pick", PICK, "pick", "XMP-xmpDM:pick", -1),
    ],
)
def test_replacement_removes_attributes_and_all_photo_duplicates(
    photo, field, namespace, local, key, value,
) -> None:
    tag = f"{{{namespace}}}{local}"
    prefix = {DC: "dc", XMP: "xmp", PICK: "xmpDM"}[namespace]
    sidecar = _packet(photo, f'''
      <rdf:Description rdf:about="urn:other-resource" {prefix}:{local}="其它资源原值">
        <custom:note>其它资源保留</custom:note>
      </rdf:Description>
      <rdf:Description rdf:about="" {prefix}:{local}="旧属性">
        <custom:note custom:flag="保留">未知字段</custom:note>
        <custom:structure><rdf:Description dc:subject="嵌套资源" /></custom:structure>
      </rdf:Description>
      <rdf:Description rdf:about="">
        <{prefix}:{local}>旧子节点</{prefix}:{local}>
      </rdf:Description>''')
    before = _descriptions(sidecar)
    foreign_before = _structure(before[0])
    unrelated_before = [_structure(child) for child in before[1]]
    original = photo.read_bytes()
    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo), {key: value})

    after = _descriptions(sidecar)
    assert _structure(after[0]) == foreign_before
    assert [_structure(child) for child in after[1] if child.tag != tag] == unrelated_before
    photo_descriptions = after[1:]
    assert all(tag not in desc.attrib for desc in photo_descriptions)
    assert sum(len(desc.findall(tag)) for desc in photo_descriptions) == (1 if value else 0)
    read = metadata.read(str(photo))
    expected = "; ".join(value) if field == "subject" else str(value)
    assert read.get(key, "") == expected
    if field == "subject":
        assert metadata.read_subjects(str(photo), strict=True) == value
    batch = _batch_read_xmp_sidecar([str(photo)])[os.path.normpath(str(photo))]
    assert batch.get(key, "") == expected
    assert photo.read_bytes() == original


@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.parametrize("text", ["新默认文本：白鹭", ""])
def test_alt_default_replacement_keeps_other_languages(photo, field, text) -> None:
    sidecar = _packet(photo, f'''
      <rdf:Description rdf:about="" dc:{field}="旧属性">
        <dc:{field}><rdf:Alt>
          <rdf:li xml:lang="x-default">旧默认文本</rdf:li>
          <rdf:li xml:lang="zh-CN" custom:flag="保留">中文翻译</rdf:li>
        </rdf:Alt></dc:{field}>
      </rdf:Description>
      <rdf:Description rdf:about="">
        <dc:{field}><rdf:Alt>
          <rdf:li>旧无语言文本</rdf:li>
          <rdf:li xml:lang="fr-FR">Aigrette</rdf:li>
        </rdf:Alt></dc:{field}>
      </rdf:Description>''')
    metadata = PhotoMetaDataXMP()
    assert getattr(metadata, f"write_{field}")(str(photo), text)
    nodes = [child for desc in _descriptions(sidecar) for child in desc.findall(f"{{{DC}}}{field}")]
    assert len(nodes) == 1
    items = nodes[0].findall(f"{{{RDF}}}Alt/{{{RDF}}}li")
    assert [(item.get(LANG), item.text or "") for item in items] == [
        ("x-default", text), ("zh-CN", "中文翻译"), ("fr-FR", "Aigrette"),
    ]
    assert items[1].get("{https://example.test/unknown/}flag") == "保留"
    assert metadata.read(str(photo))[f"XMP-dc:{field.title()}"] == text


@pytest.mark.parametrize("reference", ["filename", "absolute", "uri", "missing"])
def test_photo_about_references_share_one_replacement_scope(photo, reference) -> None:
    value = {"filename": photo.name, "absolute": str(photo), "uri": photo.as_uri(), "missing": ""}[reference]
    about_attr = "" if reference == "missing" else f"rdf:about={quoteattr(value)}"
    sidecar = _packet(photo, f'''
      <rdf:Description rdf:about="#region" dc:subject="保留区域标签" />
      <rdf:Description rdf:nodeID="region" dc:subject="保留匿名资源" />
      <rdf:Description {about_attr} dc:subject="旧属性" />
      <rdf:Description {about_attr}><dc:subject>另一个旧标签</dc:subject></rdf:Description>''')
    metadata = PhotoMetaDataXMP()
    assert metadata.read_subjects(str(photo)) == ["旧属性", "另一个旧标签"]
    assert metadata.write_subjects(str(photo), ["飞行"])
    assert metadata.read_subjects(str(photo)) == ["飞行"]
    descriptions = _descriptions(sidecar)
    assert descriptions[0].get(f"{{{DC}}}subject") == "保留区域标签"
    assert descriptions[1].get(f"{{{DC}}}subject") == "保留匿名资源"


def test_foreign_description_is_not_used_as_the_photo_write_target(photo) -> None:
    sidecar = _packet(photo, '<rdf:Description rdf:about="other.jpg" dc:title="其它照片" />')
    before = _structure(_descriptions(sidecar)[0])
    assert PhotoMetaDataXMP().write_title(str(photo), "当前照片")
    descriptions = _descriptions(sidecar)
    assert _structure(descriptions[0]) == before
    assert descriptions[1].get(ABOUT) == ""
    assert PhotoMetaDataXMP().read(str(photo))["Title"] == "当前照片"


@pytest.mark.parametrize("prefix", ["uuid:", "urn:uuid:"])
@pytest.mark.parametrize("split_descriptions", [False, True])
def test_unique_uuid_photo_resource_keeps_its_identity_when_edited(photo, prefix, split_descriptions) -> None:
    about = prefix + "0357d4f1-d8fe-4ad9-b1a9-439b27c45152"
    contents = f'<rdf:Description rdf:about="{about}" dc:title="中文鸟名" dc:subject="旧标签" />'
    if split_descriptions:
        contents += f'''
          <rdf:Description rdf:about="{about}"><dc:subject>另一个标签</dc:subject></rdf:Description>
          <rdf:Description rdf:about="urn:other-resource" dc:subject="其它资源标签" />'''
    sidecar = _packet(photo, contents)
    metadata = PhotoMetaDataXMP()
    assert metadata.read(str(photo))["Title"] == "中文鸟名"
    assert metadata.read_subjects(str(photo)) == (["旧标签", "另一个标签"] if split_descriptions else ["旧标签"])
    assert metadata.write_subjects(str(photo), ["捕食"])
    assert metadata.read_subjects(str(photo)) == ["捕食"]
    assert metadata.write_title(str(photo), "新的中文鸟名")
    assert metadata.read(str(photo))["Title"] == "新的中文鸟名"
    assert metadata.write_subjects(str(photo), [])
    assert metadata.read_subjects(str(photo)) == []
    descriptions = _descriptions(sidecar)
    expected_about = [about, about, "urn:other-resource"] if split_descriptions else [about]
    assert [desc.get(ABOUT) for desc in descriptions] == expected_about
    if split_descriptions:
        assert descriptions[2].get(f"{{{DC}}}subject") == "其它资源标签"


def test_uuid_resource_does_not_override_an_explicit_photo_subject(photo) -> None:
    sidecar = _packet(photo, '''
      <rdf:Description rdf:about="" dc:subject="照片标签" />
      <rdf:Description rdf:about="urn:uuid:0357d4f1-d8fe-4ad9-b1a9-439b27c45152"
                       dc:subject="其它UUID资源" />''')
    before = _structure(_descriptions(sidecar)[1])
    metadata = PhotoMetaDataXMP()
    assert metadata.read_subjects(str(photo)) == ["照片标签"]
    assert metadata.write_subjects(str(photo), [])
    assert metadata.read_subjects(str(photo)) == []
    assert _structure(_descriptions(sidecar)[1]) == before


def test_derived_export_read_still_uses_parent_sidecar_explicit_reference(photo) -> None:
    _packet(photo, f'<rdf:Description rdf:about={quoteattr(photo.name)} dc:title="原片中文信息" />')
    export = photo.parent / "exports" / f"{photo.stem}-DxO_DeepPRIME.jpg"
    export.parent.mkdir()
    export.write_bytes(b"derived photo")
    assert PhotoMetaDataXMP().read(str(export))["Title"] == "原片中文信息"
    assert _batch_read_xmp_sidecar([str(export)])[str(export)]["XMP-dc:Title"] == "原片中文信息"


def test_hydration_respects_photo_scope_and_current_clear(photo, monkeypatch) -> None:
    sidecar = _packet(photo, '''
      <rdf:Description rdf:about="urn:other-resource" dc:title="其它资源鸟名" />
      <rdf:Description rdf:about="" dc:description="旧备注" />''')
    before = _structure(_descriptions(sidecar)[0])
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: {
        "bird_species_cn": "白鹭", "caption": "数据库旧备注", "rating": 3,
    })
    metadata = PhotoMetaDataXMP()
    assert metadata.write_description(str(photo), "")
    read = metadata.read(str(photo))
    assert read["Title"] == "白鹭"
    assert not read.get("Description")
    assert _structure(_descriptions(sidecar)[0]) == before


@pytest.mark.parametrize("key,value", [
    ("XMP-dc:Subject", ["翠鸟"]), ("XMP-dc:Title", "翠鸟"),
    ("XMP-dc:Description", "中文备注"), ("rating", 5),
])
def test_malformed_sidecar_is_not_overwritten(photo, key, value) -> None:
    sidecar = photo.with_suffix(".xmp")
    original = "<损坏的XMP".encode("utf-8")
    sidecar.write_bytes(original)
    assert not PhotoMetaDataXMP().write(str(photo), {key: value})
    assert sidecar.read_bytes() == original
