"""鸟清晰度检测结果：XMP-superpicky 字段写入/读回、浏览器列表与缩略图显示、目录菜单扩展。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeWidgetItem

from app_common import bird_sharpness_fields as bsf
from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from app_common.exif_io.writer import _apply_browser_metadata_aliases, _batch_read_xmp_sidecar
from app_common.file_browser import _directory_browser as directory_browser
from app_common.file_browser._browser_core import (
    _DisplayRole,
    _ForegroundRole,
    _MetaBirdSharpRole,
    _SortRole,
    _ThumbPixmapRole,
    _ToolTipRole,
    _TREE_COL_BIRD_SHARP,
    _UserRole,
    _metadata_sharpness_text,
)
from app_common.file_browser._models import (
    _THUMB_FOOTER_HEIGHT,
    FileTableModel,
    ThumbnailItemDelegate,
    ThumbnailListModel,
)

_APP = QApplication.instance() or QApplication([])
_SP = "https://superbirdtools.local/xmp/superpicky/1.0/"


@pytest.fixture(autouse=True)
def _no_report_db(monkeypatch):
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)


@pytest.fixture
def photo(tmp_path) -> Path:
    path = tmp_path / "中文照片.png"
    Image.new("RGB", (8, 6), "green").save(path)
    return path


def _superpicky_text(sidecar: Path, name: str) -> str | None:
    root = ET.parse(sidecar).getroot()
    for element in root.iter(f"{{{_SP}}}{name}"):
        return element.text
    return None


def test_display_parses_raw_and_prefixed_keys() -> None:
    meta = {"XMP-superpicky:bird_sharpness_verdict": "SHARP", "bird_sharpness_head_sigma": "0.624"}
    display = bsf.bird_sharpness_from_meta(meta)
    assert display is not None
    assert display.label == "清晰"
    assert display.text() == "清晰 0.62"
    assert bsf.bird_sharpness_from_meta({}) is None
    no_bird = bsf.bird_sharpness_from_meta({"bird_sharpness_verdict": "no_bird"})
    assert no_bird.text() == "无鸟"
    # 无鸟眼时退回身体模糊半径
    no_eye = bsf.bird_sharpness_from_meta({"bird_sharpness_verdict": "no_eye", "bird_sharpness_body_sigma": "1.7"})
    assert no_eye.sigma == pytest.approx(1.7)
    assert display.sort_key() < no_eye.sort_key()


def test_superpicky_fields_write_directly_without_exiftool(photo, monkeypatch) -> None:
    import app_common.exif_io.exiftool_path as exiftool_path

    monkeypatch.setattr(exiftool_path, "get_exiftool_executable_path", lambda: None)
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), {
        "XMP-superpicky:bird_sharpness_verdict": "soft",
        "XMP-superpicky:bird_sharpness_head_sigma": "1.270",
    })
    sidecar = photo.with_suffix(".xmp")
    assert _superpicky_text(sidecar, "bird_sharpness_verdict") == "soft"
    rec = writer.read(str(photo))
    assert rec["bird_sharpness_verdict"] == "soft"
    assert rec["XMP-superpicky:bird_sharpness_head_sigma"] == "1.270"

    # 空字符串删除旧值；其它字段不受影响
    assert writer.write(str(photo), {"XMP-superpicky:bird_sharpness_head_sigma": ""})
    assert _superpicky_text(sidecar, "bird_sharpness_head_sigma") is None
    assert _superpicky_text(sidecar, "bird_sharpness_verdict") == "soft"


def test_superpicky_field_names_must_be_safe_xml_names(photo) -> None:
    writer = PhotoMetaDataXMP()
    assert not writer.write(str(photo), {"XMP-superpicky:bad name": "x"})
    assert not photo.with_suffix(".xmp").exists()


def test_mixed_sharpness_superpicky_and_chinese_title_roundtrip(photo) -> None:
    if not get_exiftool_executable_path():
        pytest.skip("ExifTool is unavailable")
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), {
        bsf.SHARPNESS_XMP_KEY: "0465.00",
        "XMP-superpicky:bird_sharpness_verdict": "usable",
        "XMP-dc:Title": "鹰鹃 中文标题",
    })
    rec = writer.read(str(photo))
    assert rec["bird_sharpness_verdict"] == "usable"
    assert float(rec["XMP-photoshop:City"]) == pytest.approx(465.0)
    assert rec["Title"] == "鹰鹃 中文标题"
    flat = _batch_read_xmp_sidecar([str(photo)])[os.path.normpath(str(photo))]
    assert _metadata_sharpness_text(flat) == "465"
    assert bsf.browser_meta_fields(flat) == {"bird_sharpness_verdict": "usable"}


def test_photoshop_city_aliases_to_xmp_city_so_report_db_cannot_shadow_it() -> None:
    rec = {"XMP-photoshop:City": "0193.00", "XMP-photoshop:State": "4.5"}
    _apply_browser_metadata_aliases(rec)
    assert rec["XMP:City"] == "0193.00"
    assert rec["XMP:State"] == "4.5"
    # 模拟 report.db 合并：XMP 键用 setdefault，不能覆盖 sidecar 值
    rec.setdefault("XMP:City", 512.0)
    assert _metadata_sharpness_text(rec) == "193"


def _tooltip(path: str) -> str:
    return path


def _mismatch(_path: str) -> bool:
    return False


def test_table_and_thumbnail_models_show_bird_sharpness() -> None:
    sharp = os.path.normpath("/photos/a.ARW")
    soft = os.path.normpath("/photos/b.ARW")
    untested = os.path.normpath("/photos/c.ARW")
    meta = {
        sharp: {"bird_sharpness_verdict": "sharp", "bird_sharpness_head_sigma": "0.71"},
        soft: {"bird_sharpness_verdict": "soft", "bird_sharpness_head_sigma": "1.27"},
    }
    table = FileTableModel()
    table.rebuild([soft, untested, sharp], meta_cache=meta, tooltip_fn=_tooltip, mismatch_fn=_mismatch)
    texts = [table.data(table.index(row, _TREE_COL_BIRD_SHARP), _DisplayRole) for row in range(3)]
    assert texts == ["失焦 1.27", "", "清晰 0.71"]
    sort_values = [table.data(table.index(row, _TREE_COL_BIRD_SHARP), _SortRole) for row in range(3)]
    assert sort_values[2] < sort_values[0] < sort_values[1]
    brush = table.data(table.index(0, _TREE_COL_BIRD_SHARP), _ForegroundRole)
    assert brush.color() == QColor(bsf.VERDICT_STYLES["soft"].color)

    thumbs = ThumbnailListModel()
    thumbs.rebuild([sharp], meta_cache=meta, tooltip_fn=_tooltip, mismatch_fn=_mismatch)
    index = thumbs.index(0, 0)
    display = thumbs.data(index, _MetaBirdSharpRole)
    assert isinstance(display, bsf.BirdSharpnessDisplay) and display.verdict == "sharp"
    assert "鸟清晰度：清晰 0.71" in thumbs.data(index, _ToolTipRole)
    thumbs.set_meta_for_path(sharp, {"bird_sharpness_verdict": "motion", "bird_sharpness_body_sigma": "1.6"})
    assert thumbs.data(index, _MetaBirdSharpRole).verdict == "motion"


def test_thumbnail_footer_draws_verdict_dot() -> None:
    pixmap = QPixmap(300, 200)
    pixmap.fill(QColor(128, 128, 128))
    item = QStandardItem("DSC04393.ARW")
    item.setData("/tmp/thumb/DSC04393.ARW", _UserRole)
    item.setData(pixmap, _ThumbPixmapRole)
    item.setData(bsf.BirdSharpnessDisplay("soft", 1.27, None), _MetaBirdSharpRole)
    model = QStandardItemModel()
    model.appendRow(item)
    cell = QRect(0, 0, 288, 302)
    image = QImage(cell.width(), cell.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#262626"))
    opt = QStyleOptionViewItem()
    opt.rect = cell
    opt.palette = QPalette()
    opt.state = QStyle.StateFlag.State_Enabled
    painter = QPainter(image)
    try:
        opt.font = painter.font()
        ThumbnailItemDelegate().paint(painter, opt, model.index(0, 0))
        name_height = painter.fontMetrics().lineSpacing() + 6
    finally:
        painter.end()
    inner = cell.adjusted(6, 6, -6, -6)
    card_bottom = inner.top() + inner.height() - name_height - 6 - 1
    cy = card_bottom - _THUMB_FOOTER_HEIGHT // 2 + 1
    want = QColor(bsf.VERDICT_STYLES["soft"].color)
    found = any(
        all(abs(a - b) <= 24 for a, b in zip(image.pixelColor(x, y).getRgb()[:3], want.getRgb()[:3]))
        for x in range(inner.left(), inner.left() + 60)
        for y in range(cy - 3, cy + 4)
    )
    assert found


def test_directory_menu_extender_receives_context_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(directory_browser.DirectoryBrowserWidget, "_populate_roots", lambda self: None)
    widget = directory_browser.DirectoryBrowserWidget()
    try:
        folder = tmp_path / "鸟片"
        folder.mkdir()
        item = QTreeWidgetItem(["鸟片"])
        item.setData(0, Qt.ItemDataRole.UserRole, str(folder))
        widget._tree.addTopLevelItem(item)
        received = []

        def extender(menu, path):
            received.append(path)
            menu.addAction("鸟清晰度检测")

        def broken(menu, path):
            raise RuntimeError("extender failure must not break the menu")

        widget.add_context_menu_extender(broken)
        widget.add_context_menu_extender(extender)
        widget.add_context_menu_extender(extender)  # duplicate registration ignored
        monkeypatch.setattr(widget._tree, "itemAt", lambda pos: item)
        shown = []
        monkeypatch.setattr(directory_browser, "_exec_menu", lambda menu, pos: shown.extend(a.text() for a in menu.actions()))
        widget._on_dir_context_menu(QPoint(0, 0))
        assert received == [os.path.normpath(str(folder))]
        assert "鸟清晰度检测" in shown and "删除所有空目录" in shown
    finally:
        widget.close()
        widget.deleteLater()
        _APP.processEvents()


def test_v2_display_uses_final_sigma_region_and_bird_count() -> None:
    meta = {
        "bird_sharpness_verdict": "sharp", "bird_sharpness_head_sigma": "0.70",
        "XMP-superpicky:bird_sharpness_sigma": "0.66", "bird_sharpness_region": "bird",
        "bird_sharpness_bird_count": "2",
    }
    display = bsf.bird_sharpness_from_meta(meta)
    assert display.sigma == pytest.approx(0.66)  # final sigma wins over head/body
    assert display.text() == "清晰 0.66"
    assert display.bird_count == 2 and display.region_label == "鸟体"
    focus = bsf.bird_sharpness_from_meta({"bird_sharpness_verdict": "no_bird", "bird_sharpness_sigma": "0.91",
                                          "bird_sharpness_region": "focus"})
    assert focus.text() == "无鸟·焦点 0.91"
    full = bsf.bird_sharpness_from_meta({"bird_sharpness_verdict": "no_bird", "bird_sharpness_region": "full"})
    assert full.text() == "无鸟·全图"
    assert set(bsf.browser_meta_fields(meta)) == {
        "bird_sharpness_verdict", "bird_sharpness_head_sigma", "bird_sharpness_sigma",
        "bird_sharpness_region", "bird_sharpness_bird_count",
    }
