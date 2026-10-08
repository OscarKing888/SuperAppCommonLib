"""Thumbnail bird names use the same title aliases as the file table."""
import pytest
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._browser_core import _MetaSpeciesCnRole
from app_common.file_browser._models import ThumbnailListModel

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("field", ["title", "XMP-dc:Title", "bird_species_cn",
                                  "XMP-superpicky:bird_species_cn", "report.bird_species_cn"])
def test_thumbnail_name_initial_load_and_incremental_edit(field):
    model = ThumbnailListModel()
    path = "bird.jpg"
    model.append_paths([path], meta_cache={path: {field: "白鹭"}}, tooltip_fn=None, mismatch_fn=None)
    index = model.index(0, 0)
    assert index.data(_MetaSpeciesCnRole) == "白鹭"
    changes = []
    model.dataChanged.connect(lambda _first, _last, roles: changes.extend(roles))
    assert model.set_meta_for_path(path, {field: "黑脸琵鹭"})
    assert index.data(_MetaSpeciesCnRole) == "黑脸琵鹭"
    assert _MetaSpeciesCnRole in changes
    model.set_meta_for_paths([(path, {})])
    assert index.data(_MetaSpeciesCnRole) == ""
