"""粘贴鸟名必须一并保存拼音，写失败时保留原有元数据。"""
from pathlib import Path
import sqlite3

from PIL import Image
from PyQt6.QtWidgets import QApplication
import pytest

from app_common.bird_pinyin import PINYIN_ALIASES, stored_pinyin
from app_common.bird_rarity import rarity_metadata
from app_common.bird_species_copy import species_snapshot
from app_common.shooting_location import shooting_location
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser import FileListPanel
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._workers import MetadataLoader

_APP = QApplication.instance() or QApplication([])


def test_snapshot_merges_report_cache_and_sparse_sidecar():
    snapshot = species_snapshot(
        {"bird_species_cn": "白头鹎", "bird_species_en": "Light-vented Bulbul",
         "gbif_rarity_100": 80, "pinyin_name": "报告拼音", "shooting_location": "旧地点"},
        {"title": "白头鹎", "pinyin_name": "当前拼音", "shooting_location": ""},
        {"XMP-superpicky:birdid_rarity_source": "白头鹎", "XMP-superpicky:gbif_rarity_100": 0},
    )
    assert snapshot["bird_species_en"] == "Light-vented Bulbul"
    assert snapshot["pinyin_name"] == "当前拼音"
    assert snapshot["gbif_rarity_100"] == 0
    assert snapshot["shooting_location"] == ""


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kwargs: None)
    widget = FileListPanel()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def make_photo(tmp_path, filename):
    photo = tmp_path / filename
    Image.new("RGB", (16, 12)).save(photo)
    fields = {"XMP-dc:Title": "家燕", "XMP-superpicky:bird_species_cn": "家燕",
              "XMP-dc:Description": "保留中文备注", "XMP-xmp:Rating": 4,
              "XMP-superpicky:pinyin_name_source": "家燕"}
    fields.update({f"XMP-superpicky:{key}": "jiā yàn" for key in PINYIN_ALIASES})
    assert PhotoMetaDataXMP().write(str(photo), fields)
    return str(photo)


@pytest.mark.parametrize("name,expected", [("白头鹎", "bái tóu bēi"), ("词表未知鸟名", "")])
def test_paste_updates_real_xmp_and_cache_for_multiple_photos(panel, tmp_path, monkeypatch, name, expected):
    paths = [make_photo(tmp_path, f"中文照片{i}.jpg") for i in range(2)]
    report = tmp_path / ".superpicky" / "report.db"
    report.parent.mkdir()
    with sqlite3.connect(report) as db:
        db.execute("CREATE TABLE photos (filename TEXT, current_path TEXT, bird_species_cn TEXT, pinyin_name TEXT)")
        db.executemany("INSERT INTO photos VALUES (?,?,?,?)", [
            (Path(path).stem, Path(path).name, "家燕", "jiā yàn") for path in paths
        ])
    report_before = report.read_bytes()
    monkeypatch.setattr(panel._meta_proxy.exif, "read", lambda _path: {})
    originals = [Path(path).read_bytes() for path in paths]
    store = PhotoMetaDataXMP()
    panel._meta_cache = {path: store.read(path) for path in paths}
    panel._copied_species_payload = {"bird_species_cn": name}
    panel._paste_species_to_paths(paths + paths[:1])
    loader = MetadataLoader([], meta_proxy=object())
    try:
        for path, original in zip(paths, originals):
            rec = store.read(path)
            assert rec["Title"] == rec["bird_species_cn"] == name
            assert stored_pinyin(rec) == expected
            assert rec["pinyin_name_source"] == name
            assert stored_pinyin(panel._meta_cache[path]) == expected
            assert stored_pinyin(loader._parse_rec(rec)) == expected
            assert stored_pinyin(panel._meta_proxy.read(path)) == expected
            assert rec["Description"] == "保留中文备注" and rec["rating"] == 4
            assert Path(path).read_bytes() == original
            for alias in PINYIN_ALIASES[1:]:
                assert not rec.get(alias)
            assert name in Path(path).with_suffix(".xmp").read_text(encoding="utf-8")
        assert report.read_bytes() == report_before
    finally:
        loader.deleteLater()


@pytest.mark.parametrize("raises", [False, True])
def test_partial_failure_keeps_failed_photo_and_cache(panel, tmp_path, monkeypatch, raises):
    paths = [make_photo(tmp_path, f"鸟{i}.jpg") for i in range(2)]
    store = PhotoMetaDataXMP()
    panel._meta_cache = {path: store.read(path) for path in paths}
    previous = dict(panel._meta_cache[paths[0]])
    sidecar = Path(paths[0]).with_suffix(".xmp")
    before = sidecar.read_bytes()
    write = panel._meta_proxy.write

    def fail_one(path, fields):
        if path == paths[0]:
            if raises:
                raise OSError("模拟写入失败")
            return False
        return write(path, fields)

    monkeypatch.setattr(panel._meta_proxy, "write", fail_one)
    panel._copied_species_payload = {"bird_species_cn": "白头鹎"}
    panel._paste_species_to_paths(paths)
    assert sidecar.read_bytes() == before
    assert panel._meta_cache[paths[0]] == previous
    assert stored_pinyin(store.read(paths[1])) == "bái tóu bēi"
    assert stored_pinyin(panel._meta_cache[paths[1]]) == "bái tóu bēi"


@pytest.mark.parametrize("score", [0, 87.25])
def test_copy_paste_associated_metadata_snapshot(panel, tmp_path, monkeypatch, score):
    source = make_photo(tmp_path, "来源.jpg")
    targets = [make_photo(tmp_path, f"目标{i}.jpg") for i in range(2)]
    store = PhotoMetaDataXMP()
    assert store.write(source, {
        "XMP-dc:Title": "白头鹎", "XMP-superpicky:bird_species_cn": "白头鹎",
        "XMP-superpicky:bird_species_en": "Light-vented Bulbul",
        "XMP-superpicky:pinyin_name": "保存的拼音 bái tóu bēi",
        "XMP-superpicky:pinyin_name_source": "白头鹎",
        "XMP-superpicky:gbif_rarity_100": score,
        "XMP-superpicky:iucn_category": "LC",
        "XMP-superpicky:birdid_rarity_source": "白头鹎",
        "XMP-superpicky:shooting_location": "深圳湾·红树林",
    })
    # Old report/cache entries must not override the source's saved XMP.
    old = {"bird_species_cn": "家燕", "bird_species_en": "Barn Swallow",
           "pinyin_name": "jiā yàn", "gbif_rarity_100": 25, "iucn_category": "EN"}
    panel._meta_cache[source] = dict(old)
    monkeypatch.setattr(panel, "_get_report_row_for_path", lambda _path: dict(old))
    display = str(tmp_path / "过期路径.jpg")
    monkeypatch.setattr(panel, "_resolve_source_path_for_action", lambda path: source if path == display else path)
    monkeypatch.setattr(panel_module, "read_batch_metadata", lambda *_args: pytest.fail("unexpected EXIF read"))
    copied_text = []
    monkeypatch.setattr(panel, "_copy_text_to_clipboard", copied_text.append)
    panel._copy_species_from_path(display)
    assert copied_text == ["白头鹎"]
    # Copy is a snapshot: changing the source afterwards must not change paste.
    assert store.write(source, {"XMP-superpicky:shooting_location": "另一个地点"})
    originals = [Path(path).read_bytes() for path in targets]
    panel._paste_species_to_paths(targets)
    loader = MetadataLoader([], meta_proxy=object())
    try:
        for path, original in zip(targets, originals):
            saved = store.read(path)
            for rec in (saved, panel._meta_cache[path], loader._parse_rec(saved)):
                assert stored_pinyin(rec) == "保存的拼音 bái tóu bēi"
                assert rarity_metadata(rec) == (score, "LC")
                assert shooting_location(rec) == "深圳湾·红树林"
            assert saved["bird_species_en"] == "Light-vented Bulbul"
            assert saved["Description"] == "保留中文备注" and saved["rating"] == 4
            assert Path(path).read_bytes() == original
    finally:
        loader.deleteLater()


def test_missing_source_fields_clear_previous_species_data(panel, tmp_path, monkeypatch):
    source = make_photo(tmp_path, "来源.jpg")
    target = make_photo(tmp_path, "目标.jpg")
    store = PhotoMetaDataXMP()
    assert store.write(source, {
        "XMP-dc:Title": "未知鸟种", "XMP-superpicky:bird_species_cn": "未知鸟种",
        "XMP-superpicky:pinyin_name_source": "家燕",
        "XMP-superpicky:gbif_rarity_100": 90, "XMP-superpicky:birdid_rarity_source": "家燕",
    })
    assert store.write(target, {
        "XMP-superpicky:bird_species_en": "Barn Swallow",
        "XMP-superpicky:gbif_rarity_100": 80, "XMP-superpicky:iucn_category": "EN",
        "XMP-iptcExt:Event": "80", "XMP-iptcCore:IntellectualGenre": "EN",
        "XMP-superpicky:shooting_location": "旧地点",
    })
    monkeypatch.setattr(panel, "_get_report_row_for_path", lambda _path: {
        "bird_species_cn": "家燕", "bird_species_en": "Barn Swallow",
        "pinyin_name": "jiā yàn", "gbif_rarity_100": 70, "iucn_category": "EN",
    })
    monkeypatch.setattr(panel, "_copy_text_to_clipboard", lambda _text: None)
    panel._copy_species_from_path(source)
    panel._paste_species_to_paths([target])
    rec = store.read(target)
    # Marker fields prevent stale report values from being hydrated on reload.
    assert rarity_metadata({"report.gbif_rarity_100": 70, **rec}) == (None, "")
    assert stored_pinyin(rec) == "" and shooting_location(rec) == ""
    assert not rec.get("bird_species_en")
    assert rec["birdid_rarity_source"] == "未知鸟种"
    assert set(rec["birdid_rarity_missing"].split(",")) == {"gbif_rarity_100", "iucn_category"}
