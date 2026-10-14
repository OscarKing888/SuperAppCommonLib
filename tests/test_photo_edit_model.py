from copy import deepcopy
import pytest

from app_common.photo_edit.catalog import ADJUSTMENT_TITLES, adjustment_defaults, normalize_camera, normalize_adjustment
from app_common.photo_edit.model import Recipe, AdjustmentStep, EditHistory


@pytest.mark.parametrize("kind", ADJUSTMENT_TITLES)
def test_all_adjustments_roundtrip(kind):
    recipe = Recipe(steps=[AdjustmentStep(kind,adjustment_defaults(kind))])
    assert Recipe.from_dict(recipe.to_dict()) == recipe


def test_gesture_undo_redo_and_noop():
    history = EditHistory()
    history.begin()
    for value in (1,2,3):
        recipe = deepcopy(history.current)
        recipe.camera["exposure"] = value
        history.update(recipe,commit=False)
    history.commit()
    assert history.undo().camera["exposure"] == 0
    assert not history.can_undo
    history.update(deepcopy(history.current))
    assert history.can_redo
    assert history.redo().camera["exposure"] == 3


@pytest.mark.parametrize("camera", [
    {"exposure":float("nan")},{"exposure":True},{"exposure":6},
    {"unknown":1},{"pointCurve":[[0,0],[0,200]]},{"curveSplits":[70,50,25]},
    {"gradeGlobal":{"hue":30,"sat":-1,"lum":0}},
])
def test_reject_malformed_camera(camera):
    with pytest.raises(ValueError):
        normalize_camera(camera)


def test_unsupported_versions_and_duplicate_step_ids_are_not_silently_dropped():
    recipe = Recipe(steps=[AdjustmentStep("invert",{})])
    value = recipe.to_dict()
    value["algorithm"] = "future-engine"
    with pytest.raises(ValueError,match="版本"):
        Recipe.from_dict(value)
    value = recipe.to_dict()
    value["steps"] *= 2
    with pytest.raises(ValueError,match="重复"):
        Recipe.from_dict(value)


def test_resource_reference_rejects_malformed_digest():
    with pytest.raises(ValueError,match="校验码"):
        normalize_adjustment("colorLookup",{"asset":{"path":"look.cube","sha256":"bad"}})


def test_history_is_bounded_and_new_change_clears_redo():
    history = EditHistory(limit=2)
    for value in (1,2,3):
        recipe = deepcopy(history.current)
        recipe.camera["contrast"] = value
        history.update(recipe)
    assert history.undo().camera["contrast"] == 2
    assert history.undo().camera["contrast"] == 1
    assert not history.can_undo
    recipe = deepcopy(history.current)
    recipe.camera["contrast"] = 4
    history.update(recipe)
    assert not history.can_redo
