"""连拍分组底框：列表 / 缩略图模型分组、配色交替、过滤模式关闭、缩略图色带绘制。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._browser_core import (
    _BURST_GROUP_COLORS,
    _BURST_GROUP_LIST_ALPHA,
    _BackgroundRole,
    _MetaBurstGroupRole,
    _TREE_COL_BURST,
    _TREE_COL_NAME,
    _burst_group_color,
    _compute_burst_group_rows,
)
from app_common.file_browser._models import (
    FileTableModel,
    ThumbnailListModel,
    _paint_burst_group_band,
)
from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


def _tooltip(path: str) -> str:
    return f"Path: {path}"


def _mismatch(_path: str) -> bool:
    return False


def _paths(*names: str, folder: str = "C:/photos") -> list[str]:
    return [os.path.normpath(f"{folder}/{name}") for name in names]


def _burst_meta(paths: list[str], burst_ids: list[int | None]) -> dict:
    meta: dict = {}
    for index, (path, burst_id) in enumerate(zip(paths, burst_ids)):
        if burst_id is None:
            meta[path] = {}
        else:
            meta[path] = {"burst_id": burst_id, "burst_position": index + 1}
    return meta


def test_compute_burst_group_rows_alternates_colors_and_skips_singletons() -> None:
    keys = [
        ("d", 1), ("d", 1), ("d", 1),   # 组 A → 配色 0
        None,
        ("d", 7),                      # 单张，不显示，也不占配色序号
        ("d", 2), ("d", 2),            # 组 B → 配色 1
        ("d", 3), ("d", 3),            # 组 C → 配色 0
    ]
    result = _compute_burst_group_rows(keys)

    assert result[0] == (0, True, False)
    assert result[1] == (0, False, False)
    assert result[2] == (0, False, True)
    assert result[3] is None
    assert result[4] is None
    assert result[5] == (1, True, False)
    assert result[6] == (1, False, True)
    assert result[7] == (0, True, False)
    assert result[8] == (0, False, True)


def test_file_table_background_frames_burst_groups_with_two_alternating_colors() -> None:
    paths = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg")
    model = FileTableModel()
    model.rebuild(
        paths,
        meta_cache=_burst_meta(paths, [5, 5, None, 9, 9, 42]),
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    def bg(row: int, column: int = _TREE_COL_NAME) -> QColor | None:
        brush = model.data(model.index(row, column), _BackgroundRole)
        return None if brush is None else brush.color()

    first_group = bg(0)
    second_group = bg(3)
    assert first_group is not None and second_group is not None
    assert first_group == bg(1) == bg(1, _TREE_COL_BURST)
    assert second_group == bg(4)
    assert first_group.name() != second_group.name()
    assert first_group.name().lower() == _BURST_GROUP_COLORS[0].lower()
    assert second_group.name().lower() == _BURST_GROUP_COLORS[1].lower()
    assert first_group.alpha() == _BURST_GROUP_LIST_ALPHA
    # 无连拍与单张连拍都不画底框
    assert bg(2) is None
    assert bg(5) is None


def test_file_table_burst_groups_are_scoped_per_directory() -> None:
    paths = _paths("a.jpg", "b.jpg") + _paths("c.jpg", "d.jpg", folder="C:/photos/sub")
    model = FileTableModel()
    model.rebuild(
        paths,
        meta_cache=_burst_meta(paths, [1, 1, 1, 1]),
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    groups = [model.burst_group_for_row(row) for row in range(4)]
    assert groups[0] == (0, True, False)
    assert groups[1] == (0, False, True)
    # 子目录中相同 burst_id 是另一组，配色交替
    assert groups[2] == (1, True, False)
    assert groups[3] == (1, False, True)


def test_file_table_burst_background_disabled_in_filter_mode() -> None:
    paths = _paths("a.jpg", "b.jpg")
    model = FileTableModel()
    model.rebuild(paths, meta_cache=_burst_meta(paths, [3, 3]), tooltip_fn=_tooltip, mismatch_fn=_mismatch)
    emitted: list = []
    model.dataChanged.connect(lambda tl, br, roles: emitted.append((tl.row(), br.row(), list(roles))))

    assert model.data(model.index(0, 0), _BackgroundRole) is not None
    assert model.set_burst_group_display_enabled(False) is True
    assert model.data(model.index(0, 0), _BackgroundRole) is None
    assert model.burst_group_for_row(0) is None
    assert emitted and emitted[-1][:2] == (0, 1) and _BackgroundRole in emitted[-1][2]
    # 重复设置同一状态不再发信号
    assert model.set_burst_group_display_enabled(False) is False
    assert model.set_burst_group_display_enabled(True) is True
    assert model.data(model.index(1, 0), _BackgroundRole) is not None


def test_file_table_burst_groups_follow_async_metadata_and_appends() -> None:
    paths = _paths("a.jpg", "b.jpg", "c.jpg")
    model = FileTableModel()
    model.rebuild(paths[:2], meta_cache={}, tooltip_fn=_tooltip, mismatch_fn=_mismatch)
    assert model.data(model.index(0, 0), _BackgroundRole) is None

    emitted: list = []
    model.dataChanged.connect(lambda tl, br, roles: emitted.append((tl.row(), br.row(), list(roles))))
    # 元数据异步到达后形成一组：两行都要刷新背景
    model.set_meta_for_paths([(paths[0], {"burst_id": 8, "burst_position": 1})])
    assert model.data(model.index(0, 0), _BackgroundRole) is None  # 仍是单张
    model.set_meta_for_paths([(paths[1], {"burst_id": 8, "burst_position": 2})])
    assert model.data(model.index(0, 0), _BackgroundRole) is not None
    assert model.data(model.index(1, 0), _BackgroundRole) is not None
    assert any(tl == 0 and br == 1 and _BackgroundRole in roles for tl, br, roles in emitted)
    assert model.burst_group_for_row(1) == (0, False, True)

    # 分批追加的行进入同组后，末张标记跟着后移
    model.append_paths([paths[2]], meta_cache=_burst_meta(paths, [8, 8, 8]), tooltip_fn=_tooltip, mismatch_fn=_mismatch)
    assert model.burst_group_for_row(1) == (0, False, False)
    assert model.burst_group_for_row(2) == (0, False, True)

    # 星级等非连拍元数据更新不会触发整表背景刷新
    emitted.clear()
    model.set_meta_for_paths([(paths[0], {"burst_id": 8, "burst_position": 1, "rating": 4})])
    assert not any(tl == 0 and br == 2 for tl, br, _roles in emitted)


def test_thumbnail_model_exposes_burst_group_role_and_respects_display_flag() -> None:
    paths = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg")
    model = ThumbnailListModel()
    model.rebuild(
        paths,
        meta_cache=_burst_meta(paths, [2, 2, None, 2]),
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )

    assert model.data(model.index(0, 0), _MetaBurstGroupRole) == (0, True, False)
    assert model.data(model.index(1, 0), _MetaBurstGroupRole) == (0, False, False)
    assert model.data(model.index(2, 0), _MetaBurstGroupRole) is None
    assert model.data(model.index(3, 0), _MetaBurstGroupRole) == (0, False, True)

    emitted: list = []
    model.dataChanged.connect(lambda tl, br, roles: emitted.append((tl.row(), br.row(), list(roles))))
    model.set_burst_group_display_enabled(False)
    assert model.data(model.index(0, 0), _MetaBurstGroupRole) is None
    assert emitted[-1] == (0, 3, [_MetaBurstGroupRole])

    model.set_burst_group_display_enabled(True)
    # 连拍 id 变化 → 整表刷新分组角色；这里 c.jpg 加入后成为中间张，d.jpg 仍为末张
    emitted.clear()
    assert model.set_meta_for_path(paths[2], {"burst_id": 2, "burst_position": 3}) is True
    assert model.data(model.index(2, 0), _MetaBurstGroupRole) == (0, False, False)
    assert any(tl == 0 and br == 3 and _MetaBurstGroupRole in roles for tl, br, roles in emitted)


def test_panel_sync_burst_group_display_disables_frames_while_filtering() -> None:
    class _Edit:
        def __init__(self, text: str) -> None:
            self._text = text

        def text(self) -> str:
            return self._text

    panel = FileListPanel.__new__(FileListPanel)
    panel._create_filter_bar = True
    panel._filter_edit = _Edit("")
    panel._filter_pick = False
    panel._filter_reject = False
    panel._filter_min_rating = 0
    panel._filter_focus_status = ""
    panel._file_table_model = FileTableModel()
    panel._thumb_list_model = ThumbnailListModel()

    assert FileListPanel._sync_burst_group_display(panel) is True
    assert panel._file_table_model.burst_group_display_enabled() is True
    assert panel._thumb_list_model.burst_group_display_enabled() is True

    panel._filter_min_rating = 3
    assert FileListPanel._sync_burst_group_display(panel) is False
    assert panel._file_table_model.burst_group_display_enabled() is False
    assert panel._thumb_list_model.burst_group_display_enabled() is False

    panel._filter_min_rating = 0
    panel._filter_edit = _Edit("bird")
    assert FileListPanel._sync_burst_group_display(panel) is False

    panel._filter_edit = _Edit("")
    assert FileListPanel._sync_burst_group_display(panel) is True


def test_paint_burst_group_band_is_continuous_across_cells_without_seams() -> None:
    width, height = 240, 60
    image = QImage(width * 3, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    try:
        _paint_burst_group_band(painter, QRect(0, 0, width, height), (1, True, False))
        _paint_burst_group_band(painter, QRect(width, 0, width, height), (1, False, False))
        _paint_burst_group_band(painter, QRect(width * 2, 0, width, height), (1, False, True))
    finally:
        painter.end()

    expected = _burst_group_color(1, 60)
    mid_y = height // 2
    # 三格之间的连接处以及格内各处 alpha 一致，没有叠色接缝或空隙
    samples = [image.pixelColor(x, mid_y).alpha() for x in (width // 2, width - 1, width, width + width // 2, width * 2 - 1, width * 2, width * 2 + width // 2)]
    assert all(abs(alpha - expected.alpha()) <= 2 for alpha in samples), samples
    # 首张左侧、末张右侧留有边距（圆角 + 内缩），中间格填满到格边
    assert image.pixelColor(0, mid_y).alpha() == 0
    assert image.pixelColor(width * 3 - 1, mid_y).alpha() == 0
    # 上下各留 2px 间距，避免相邻行色带粘连
    assert image.pixelColor(width + width // 2, 0).alpha() == 0
    assert image.pixelColor(width + width // 2, height - 1).alpha() == 0
    assert image.pixelColor(width + width // 2, 2).alpha() > 0
    # 色相来自第二种配色（预乘 alpha 存储有取整误差，用容差比较）
    sample = image.pixelColor(width + width // 2, mid_y)
    for got, want in zip((sample.red(), sample.green(), sample.blue()), (expected.red(), expected.green(), expected.blue())):
        assert abs(got - want) <= 4, (got, want)
