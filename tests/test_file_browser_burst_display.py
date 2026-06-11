import os

from app_common.file_browser._browser_core import (
    _DisplayRole,
    _MetaBurstTextRole,
    _SortRole,
    _ToolTipRole,
    _TREE_COL_BURST,
    _TREE_COL_NAME,
)
from app_common.file_browser._models import FileTableModel, ThumbnailListModel


def _tooltip(path: str) -> str:
    return f"Path: {path}"


def _mismatch(_path: str) -> bool:
    return False


def test_file_table_displays_burst_column_from_metadata_sources() -> None:
    paths = [
        os.path.normpath("C:/photos/a.jpg"),
        os.path.normpath("C:/photos/b.jpg"),
        os.path.normpath("C:/photos/c.jpg"),
        os.path.normpath("C:/photos/d.jpg"),
    ]
    meta_cache = {
        paths[0]: {"burst_position": 3, "burst_id": 12},
        paths[1]: {"XMP-superpicky:burst_id": "12"},
        paths[2]: {"report.burst_position": "2"},
        paths[3]: {},
    }
    model = FileTableModel()
    model.rebuild(paths, meta_cache=meta_cache, tooltip_fn=_tooltip, mismatch_fn=_mismatch)

    assert model.data(model.index(0, _TREE_COL_NAME), _DisplayRole) == "a.jpg"
    assert model.data(model.index(0, _TREE_COL_BURST), _DisplayRole) == "(3/12)"
    assert model.data(model.index(1, _TREE_COL_BURST), _DisplayRole) == "(-/12)"
    assert model.data(model.index(2, _TREE_COL_BURST), _DisplayRole) == "(2/-)"
    assert model.data(model.index(3, _TREE_COL_BURST), _DisplayRole) == ""
    assert "连拍: (3/12)" in model.data(model.index(0, _TREE_COL_BURST), _ToolTipRole)


def test_file_table_burst_sort_key_orders_by_group_then_position() -> None:
    paths = [
        os.path.normpath("C:/photos/missing.jpg"),
        os.path.normpath("C:/photos/group12_pos3.jpg"),
        os.path.normpath("C:/photos/group11_pos9.jpg"),
        os.path.normpath("C:/photos/group12_pos1.jpg"),
    ]
    meta_cache = {
        paths[1]: {"burst_id": 12, "burst_position": 3},
        paths[2]: {"burst_id": 11, "burst_position": 9},
        paths[3]: {"burst_id": 12, "burst_position": 1},
    }
    model = FileTableModel()
    model.rebuild(paths, meta_cache=meta_cache, tooltip_fn=_tooltip, mismatch_fn=_mismatch)

    sorted_names = [
        model.data(model.index(row, _TREE_COL_NAME), _DisplayRole)
        for row in sorted(
            range(model.rowCount()),
            key=lambda item_row: model.data(model.index(item_row, _TREE_COL_BURST), _SortRole),
        )
    ]

    assert sorted_names == [
        "group11_pos9.jpg",
        "group12_pos1.jpg",
        "group12_pos3.jpg",
        "missing.jpg",
    ]


def test_thumbnail_model_keeps_display_role_filename_and_exposes_burst_role() -> None:
    path = os.path.normpath("C:/photos/a.jpg")
    model = ThumbnailListModel()
    model.rebuild(
        [path],
        meta_cache={path: {"XMP-superpicky:burst_position": "3", "report.burst_id": "12"}},
        tooltip_fn=_tooltip,
        mismatch_fn=_mismatch,
    )
    index = model.index(0, 0)

    assert model.data(index, _DisplayRole) == "a.jpg"
    assert model.data(index, _MetaBurstTextRole) == "(3/12)"
    assert "连拍: (3/12)" in model.data(index, _ToolTipRole)
