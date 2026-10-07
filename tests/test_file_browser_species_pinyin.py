"""粘贴鸟名必须一并保存拼音，写失败时保留原有元数据。"""
from pathlib import Path
import sqlite3

from PIL import Image
from PyQt6.QtWidgets import QApplication
import pytest

from app_common.bird_pinyin import PINYIN_ALIASES, stored_pinyin
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser import FileListPanel
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._workers import MetadataLoader

_APP = QApplication.instance() or QApplication([])


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
