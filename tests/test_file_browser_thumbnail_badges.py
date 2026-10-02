"""缩略图卡片：底部固定信息条（星级/精选/对焦等级/色标）、排除压暗、对焦框按等级着色。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from app_common.file_browser._browser_core import (
    _COLOR_LABEL_COLORS,
    _MetaColorRole,
    _MetaFocusBoxRole,
    _MetaFocusRole,
    _MetaPickRole,
    _MetaRatingRole,
    _ThumbPixmapRole,
    _UserRole,
    _focus_status_text_color,
)
from app_common.file_browser._models import (
    _THUMB_FOOTER_BG,
    _THUMB_FOOTER_HEIGHT,
    _THUMB_STAR_ON_COLOR,
    ThumbnailItemDelegate,
)

_APP = QApplication.instance() or QApplication([])

_THUMB = 256
_CELL = QRect(0, 0, _THUMB + 32, _THUMB + 46)
_IMAGE_GRAY = QColor(128, 128, 128)


def _render(**data) -> tuple[QImage, QRect]:
    """用真实 delegate 绘制一个缩略图单元格，返回图像与卡片矩形。"""
    pixmap = QPixmap(300, 200)
    pixmap.fill(_IMAGE_GRAY)
    item = QStandardItem(data.pop("name", "DSC00001.ARW"))
    item.setData("/tmp/thumb/DSC00001.ARW", _UserRole)
    item.setData(pixmap, _ThumbPixmapRole)
    for role, key in (
        (_MetaRatingRole, "rating"),
        (_MetaPickRole, "pick"),
        (_MetaFocusRole, "focus"),
        (_MetaFocusBoxRole, "box"),
        (_MetaColorRole, "color"),
    ):
        if key in data:
            item.setData(data[key], role)
    model = QStandardItemModel()
    model.appendRow(item)

    image = QImage(_CELL.width(), _CELL.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#262626"))
    opt = QStyleOptionViewItem()
    opt.rect = _CELL
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2d7fd6"))
    opt.palette = palette
    opt.state = QStyle.StateFlag.State_Enabled
    painter = QPainter(image)
    try:
        opt.font = painter.font()
        ThumbnailItemDelegate().paint(painter, opt, model.index(0, 0))
        name_height = painter.fontMetrics().lineSpacing() + 6
    finally:
        painter.end()
    cell = _CELL.adjusted(6, 6, -6, -6)
    card = QRect(cell.left(), cell.top(), cell.width(), cell.height() - name_height - 6)
    return image, card


def _footer_center_y(card: QRect) -> int:
    return card.bottom() - _THUMB_FOOTER_HEIGHT // 2 + 1


def _close(color: QColor, want: QColor, tol: int = 12) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(color.getRgb()[:3], want.getRgb()[:3]))


def _row_has_color(image: QImage, y: int, x0: int, x1: int, want: QColor) -> bool:
    return any(_close(image.pixelColor(x, y), want) for x in range(x0, x1))


def _row_has_hue(image: QImage, y: int, x0: int, x1: int, want: QColor) -> bool:
    """细线抗锯齿后会与底图混色，按色相 + 饱和度判断。"""
    for x in range(x0, x1):
        color = image.pixelColor(x, y)
        if color.hsvSaturation() > 120 and abs(color.hsvHue() - want.hsvHue()) <= 8:
            return True
    return False


def test_footer_strip_is_carved_from_card_without_changing_cell_size() -> None:
    image, card = _render(rating=0)
    footer_y = _footer_center_y(card)
    # 信息条在卡片底部、文件名上方，底色为深色信息条色
    assert _close(image.pixelColor(card.left() + 3, footer_y), QColor(_THUMB_FOOTER_BG), tol=4)
    # 信息条上方是缩略槽：横图留白（卡片底色）或图片内容，而不是信息条底色
    above = image.pixelColor(card.center().x(), card.bottom() - _THUMB_FOOTER_HEIGHT - 4)
    assert not _close(above, QColor(_THUMB_FOOTER_BG), tol=4)


def test_rating_stars_are_bright_gold_in_footer_right_side() -> None:
    gold = QColor(_THUMB_STAR_ON_COLOR)
    image, card = _render(rating=4)
    footer_y = _footer_center_y(card)
    assert _row_has_color(image, footer_y, card.center().x(), card.right(), gold)
    # 星级不再画在图片右上角
    assert not any(
        _row_has_color(image, y, card.center().x(), card.right(), gold)
        for y in range(card.top(), card.top() + 60)
    )
    unrated, _ = _render(rating=0)
    assert not _row_has_color(unrated, footer_y, card.center().x(), card.right(), gold)


def test_pick_and_reject_chips_and_reject_dims_image() -> None:
    green = QColor("#22c55e")
    red = QColor("#ef4444")
    picked, card = _render(pick=1)
    rejected, _ = _render(pick=-1)
    footer_y = _footer_center_y(card)
    left_span = (card.left(), card.left() + 30)
    assert _row_has_color(picked, footer_y, *left_span, green)
    assert _row_has_color(rejected, footer_y, *left_span, red)
    center = card.center()
    assert rejected.pixelColor(center).lightness() < picked.pixelColor(center).lightness() - 30


def test_focus_box_and_footer_tag_follow_focus_status_color() -> None:
    want = QColor(_focus_status_text_color("偏移"))
    image, card = _render(focus="BAD", box=(0.4, 0.4, 0.6, 0.6))
    assert _row_has_color(image, _footer_center_y(card), card.left(), card.center().x(), want)
    # 对焦框（图片中部的竖边）同样使用对焦等级颜色
    assert _row_has_hue(image, card.center().y(), card.left() + 4, card.right() - 4, want)


def test_color_label_draws_card_border_and_footer_tag() -> None:
    want = QColor(_COLOR_LABEL_COLORS["Red"][0])
    image, card = _render(color="Red")
    # 描边画在图片之上，横图铺满宽度时仍可见
    assert _close(image.pixelColor(card.left(), card.center().y()), want)
    assert _row_has_color(image, _footer_center_y(card), card.left() + 2, card.center().x(), want)
