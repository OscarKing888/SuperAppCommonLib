from contextlib import closing
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataProxy, PhotoMetaDataReportDB, PhotoMetaDataXMP
from app_common.report_db import ReportDB, report_pick_value, report_row_to_exiftool_style


V10_VALUES = {
    "picked": 1,
    "aesthetic_index": 78.5,
    "alt_species_cn": "白鹭",
    "alt_species_en": "Little Egret",
    "alt_confidence": 0.63,
}


def _make_report(root: Path, version: int, *, legacy_pick: bool = False):
    """独立构建生产者结构，避免用消费者 PHOTO_COLUMNS 自证兼容。"""
    root.mkdir()
    photo = root / "白鹭.jpg"
    Image.new("RGB", (8, 8), "white").save(photo)
    db_path = root / ".superpicky" / "report.db"
    db_path.parent.mkdir()
    columns = {
        "filename": "TEXT PRIMARY KEY", "current_path": "TEXT",
        "original_path": "TEXT", "rating": "INTEGER", "bird_species_cn": "TEXT",
    }
    values = {
        "filename": photo.stem, "current_path": photo.name,
        "original_path": photo.name, "rating": 3, "bird_species_cn": None,
    }
    if version >= 8:
        flag = "pick" if legacy_pick else "picked"
        columns[flag] = "INTEGER"
        values[flag] = 1
    if version >= 9:
        columns["aesthetic_index"] = "REAL"
        values["aesthetic_index"] = V10_VALUES["aesthetic_index"]
    if version >= 10:
        for name, sql_type in (("alt_species_cn", "TEXT"), ("alt_species_en", "TEXT"), ("alt_confidence", "REAL")):
            columns[name] = sql_type
            values[name] = V10_VALUES[name]
    if version > 10:
        columns["future_field"] = "TEXT"
        values["future_field"] = "future"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE photos (" + ", ".join(f"{key} {value}" for key, value in columns.items()) + ")")
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(version),))
        connection.execute(
            "INSERT INTO photos (" + ", ".join(values) + ") VALUES (" + ", ".join("?" for _ in values) + ")",
            tuple(values.values()),
        )
    return photo, db_path, values


@pytest.mark.parametrize("version,legacy_pick", [(1, False), (8, True), (8, False), (9, False), (10, False), (99, False)])
def test_read_compatibility_is_based_on_columns_without_migrating(tmp_path, version, legacy_pick):
    root = tmp_path / "中文#图库"
    photo, db_path, values = _make_report(root, version, legacy_pick=legacy_pick)
    original = (db_path.read_bytes(), db_path.stat().st_mtime_ns)
    with ReportDB.open_if_exists(str(root)) as db:
        assert db.get_meta("schema_version") == str(version)
        assert db.get_all_photos() == [values]
    metadata = PhotoMetaDataReportDB(report_root=str(root)).read(str(photo))
    assert metadata["rating"] == 3
    for key, value in V10_VALUES.items():
        if key in values:
            for prefix in ("", "report.", "XMP-superpicky:"):
                assert metadata[prefix + key] == value
        else:
            assert key not in metadata
    if version >= 8:
        assert metadata["XMP-xmpDM:pick"] == 1
    assert "XMP-dc:Title" not in metadata  # 候选鸟种不冒充已确认鸟名。
    assert (db_path.read_bytes(), db_path.stat().st_mtime_ns) == original


@pytest.mark.parametrize("row,expected", [
    ({"rating": 3, "picked": 1}, 1),
    ({"rating": 3, "picked": 0}, 0),
    ({"rating": -1, "picked": 1}, -1),
    ({"rating": -1, "picked": 0}, -1),
    ({"picked": 1, "pick": 0}, 1),
    ({"picked": 0, "pick": 1}, 0),
    ({"picked": None, "pick": -1}, -1),
    ({"picked": "bad", "pick": 1}, 1),
    ({"pick": 1}, 1),
    ({"picked": False}, 0),
    ({}, None),
])
def test_report_pick_uses_new_flag_and_preserves_legacy_rejection(row, expected):
    assert report_pick_value(row) == expected
    assert report_row_to_exiftool_style(row, "photo.jpg").get("XMP-xmpDM:pick") == expected


@pytest.mark.parametrize("manual_pick", [0, -1])
def test_v10_hydration_and_manual_edits_only_write_xmp(tmp_path, monkeypatch, manual_pick):
    root = tmp_path / "中文#图库"
    photo, db_path, _values = _make_report(root, 10)
    original_db = (db_path.read_bytes(), db_path.stat().st_mtime_ns)
    original_photo = photo.read_bytes()
    statements = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)

    class EmptyExif:
        def read(self, path):
            return {}

        def read_batch(self, paths):
            return {}

    proxy = PhotoMetaDataProxy(exif=EmptyExif())
    # 首次编辑就清除/排除，验证 report 的精选不能覆盖本次用户操作。
    assert proxy.write(str(photo), {"XMP-xmpDM:pick": manual_pick})
    sidecar = PhotoMetaDataXMP().read(str(photo))
    for key, value in V10_VALUES.items():
        assert str(sidecar["XMP-superpicky:" + key]) == str(value)
    assert int(sidecar["XMP-xmpDM:pick"]) == manual_pick
    assert proxy.write(str(photo), {
        "XMP-dc:Title": "手工确认：白鹭",
        "XMP-dc:Description": "中文说明与标签",
        "XMP-dc:Subject": ["湿地", "飞行"],
        "XMP-xmp:Rating": 5,
    })
    for metadata in (proxy.read(str(photo)), proxy.read_batch([str(photo)])[str(photo)]):
        assert metadata["pick"] == manual_pick
        assert metadata["rating"] == 5
        assert metadata["XMP-dc:Title"] == "手工确认：白鹭"
        assert metadata["XMP-dc:Description"] == "中文说明与标签"
        assert metadata["picked"] == "1"  # 保留生产者结果，与用户标记独立。
    assert (db_path.read_bytes(), db_path.stat().st_mtime_ns) == original_db
    assert photo.read_bytes() == original_photo
    assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "BEGIN")) for sql in statements)


def test_existing_pick_alias_survives_hydration_and_is_replaced_on_edit(tmp_path):
    root = tmp_path / "图库"
    photo, db_path, _values = _make_report(root, 10)
    original = db_path.read_bytes()
    photo.with_suffix(".xmp").write_text(
        '''<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Pick="0" />
  </rdf:RDF>
</x:xmpmeta>''', encoding="utf-8",
    )
    xmp = PhotoMetaDataXMP()
    assert xmp.write_subjects(str(photo), ["中文标签"])
    assert int(xmp.read(str(photo))["XMP-xmpDM:pick"]) == 0
    assert xmp.write_rating_pick(str(photo), pick=-1)
    assert int(xmp.read(str(photo))["XMP-xmpDM:pick"]) == -1
    tree = ET.parse(photo.with_suffix(".xmp"))
    assert all("{http://ns.adobe.com/xap/1.0/}Pick" not in element.attrib for element in tree.iter())
    assert db_path.read_bytes() == original
