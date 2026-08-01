from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app_common.file_browser._models import FileTableModel, ThumbnailListModel
from app_common.file_browser._panel import FileListPanel

_APP = QApplication.instance() or QApplication([])


def _rebuild(model, paths: list[str]) -> None:
    model.rebuild(
        paths,
        meta_cache={},
        tooltip_fn=lambda _path: "",
        mismatch_fn=lambda _path: False,
    )


def test_table_model_batches_contiguous_metadata_rows() -> None:
    model = FileTableModel()
    paths = [os.path.normpath(f"folder/photo-{index}.jpg") for index in range(5)]
    _rebuild(model, paths)
    emissions: list[tuple[int, int]] = []
    model.dataChanged.connect(
        lambda first, last, _roles: emissions.append((first.row(), last.row()))
    )

    changed = model.set_meta_for_paths(
        [
            (paths[3], {"comment": "d"}),
            (paths[0], {"comment": "a"}),
            (paths[1], {"comment": "b"}),
            ("missing.jpg", {"comment": "x"}),
        ]
    )

    assert changed == 3
    assert emissions == [(0, 1), (3, 3)]


def test_thumbnail_model_batches_only_changed_rows() -> None:
    model = ThumbnailListModel()
    paths = [os.path.normpath(f"folder/photo-{index}.jpg") for index in range(5)]
    _rebuild(model, paths)
    emissions: list[tuple[int, int]] = []
    model.dataChanged.connect(
        lambda first, last, _roles: emissions.append((first.row(), last.row()))
    )

    changed = model.set_meta_for_paths(
        [
            (paths[0], {"rating": 1}),
            (paths[1], {"rating": 2}),
            (paths[2], {}),
            (paths[4], {"pick": 1}),
        ]
    )

    assert changed == 3
    assert emissions == [(0, 1), (4, 4)]
    assert model.set_meta_for_path(paths[2], {}) is False


def test_metadata_batch_order_uses_session_rank() -> None:
    panel = SimpleNamespace(
        _meta_apply_order_by_path={
            os.path.normpath("folder/a.jpg"): 0,
            os.path.normpath("folder/b.jpg"): 1,
            os.path.normpath("folder/c.jpg"): 2,
        }
    )
    unordered = {
        os.path.normpath("folder/c.jpg"): {"rating": 3},
        os.path.normpath("folder/a.jpg"): {"rating": 1},
        os.path.normpath("folder/unknown.jpg"): {"rating": 0},
    }

    ordered = FileListPanel._order_meta_items_by_file_list(panel, unordered)

    assert [path for path, _meta in ordered] == [
        os.path.normpath("folder/a.jpg"),
        os.path.normpath("folder/c.jpg"),
        os.path.normpath("folder/unknown.jpg"),
    ]


def test_stale_metadata_worker_batch_and_progress_are_ignored() -> None:
    current_loader = object()
    stale_loader = object()

    class _Harness:
        _metadata_loader = current_loader
        _meta_apply_expected_total = 7

        @staticmethod
        def sender():
            return stale_loader

        def _enqueue_meta_apply(self, _meta_dict) -> None:
            raise AssertionError("stale metadata batch must not enter apply queue")

        def _show_meta_progress_status(self, *_args, **_kwargs) -> None:
            raise AssertionError("stale metadata progress must not update UI")

    harness = _Harness()
    FileListPanel._on_metadata_batch_ready(
        harness,
        {os.path.normpath("old/photo.jpg"): {"rating": 5}},
    )
    FileListPanel._on_metadata_progress(harness, 99, 100)

    assert harness._meta_apply_expected_total == 7
