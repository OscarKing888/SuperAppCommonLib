"""Runs against the real compiled backend; never downloads/builds during pytest."""
import json
import numpy as np
import pytest

native = pytest.importorskip("app_common.photo_edit._native")
from app_common.photo_edit.catalog import ADJUSTMENT_TITLES, ADJUSTMENT_VARIANTS, adjustment_defaults, camera_defaults
from app_common.photo_edit.model import Recipe, AdjustmentStep
from app_common.photo_edit.native import render_pixels, import_settings, export_settings


def pixels():
    image = np.random.default_rng(123).uniform(.1,.9,(32,48,4)).astype(np.float32)
    return image


def test_python_defaults_match_pinned_upstream():
    info = json.loads(native.info_json())
    camera = info["cameraRaw"]
    camera.pop("pixelScale")
    camera.pop("encodingGamma")
    assert camera == camera_defaults()
    prototypes = {next(iter(v)):next(iter(v.values())) for v in info["adjustments"] if isinstance(v,dict)}
    for kind in ADJUSTMENT_TITLES:
        if kind in ("colorLookup","invert"):
            continue
        def same(actual,expected):
            if isinstance(expected,dict):
                assert actual.keys() == expected.keys()
                for key in expected: same(actual[key],expected[key])
            elif isinstance(expected,list):
                assert len(actual)==len(expected)
                for a,b in zip(actual,expected): same(a,b)
            elif isinstance(expected,(float,int)) and not isinstance(expected,bool):
                assert actual == pytest.approx(expected,abs=1e-7)
            else:
                assert actual == expected
        same(prototypes[ADJUSTMENT_VARIANTS[kind]],adjustment_defaults(kind))


def test_identity_is_exact_and_does_not_mutate_input():
    image = pixels()
    before = image.copy()
    result = render_pixels(image,Recipe())
    np.testing.assert_array_equal(result,image)
    np.testing.assert_array_equal(image,before)
    assert result.dtype == np.float32
    assert not np.shares_memory(result,image)


@pytest.mark.parametrize("kind",ADJUSTMENT_TITLES)
def test_all_sixteen_adjustments_render_and_keep_alpha(kind):
    image = pixels()
    params = adjustment_defaults(kind)
    if kind == "colorLookup":
        params["builtin"] = "tealOrange"
    recipe = Recipe(steps=[AdjustmentStep(kind,params)])
    result = render_pixels(image,recipe)
    assert np.isfinite(result).all()
    assert result.min() >= 0 and result.max() <= 1
    np.testing.assert_array_equal(result[...,3],image[...,3])


def test_camera_exposure_direction_and_step_order():
    image = pixels()
    recipe = Recipe()
    recipe.camera["exposure"] = 1
    result = render_pixels(image,recipe)
    assert result[...,:3].mean() > image[...,:3].mean()
    recipe.steps = [AdjustmentStep("exposure",dict(exposure=1,offset=0,gamma=1)),AdjustmentStep("invert",{})]
    a = render_pixels(image,recipe)
    recipe.steps.reverse()
    b = render_pixels(image,recipe)
    assert np.max(np.abs(a-b)) > .1


def test_xmp_settings_roundtrip_and_reports_unsupported_fields():
    camera = camera_defaults()
    camera["exposure"] = 1.25
    camera["hslHue"][0] = 20
    output = export_settings(camera)
    restored = import_settings(camera_defaults(),output)
    assert restored["cameraRaw"]["exposure"] == 1.25
    assert restored["cameraRaw"]["hslHue"][0] == 20
    modified = output.replace(b"crs:Exposure2012=",b'crs:Temperature="5600" crs:Exposure2012=')
    assert "Temperature" in import_settings(camera_defaults(),modified)["ignored"]


@pytest.mark.parametrize("mutation",["nan","shape","noncontiguous","range"])
def test_native_rejects_invalid_pixels_without_panicking(mutation):
    image = pixels()
    if mutation == "nan": image[0,0,0] = np.nan
    elif mutation == "shape": image = image[...,:3].copy()
    elif mutation == "noncontiguous": image = image[:,::2]
    else: image[0,0,0] = 2
    with pytest.raises((ValueError,RuntimeError)):
        native.render(image,"{}","[]")
def test_native_stage_checkpoints_cancel_without_modifying_input():
    import numpy as np
    import pytest
    from app_common.photo_edit.native import render_pixels
    from app_common.photo_edit.model import Recipe,AdjustmentStep
    from app_common.image_processing_types import ImageProcessingCancelled
    pytest.importorskip("app_common.photo_edit._native")
    pixels = np.full((32,48,4),.5,np.float32)
    before = pixels.copy()
    checkpoints = []
    def cancelled():
        checkpoints.append(1)
        return len(checkpoints)>=3
    with pytest.raises(ImageProcessingCancelled):
        render_pixels(pixels,Recipe(steps=[AdjustmentStep("invert")]),cancelled=cancelled)
    assert len(checkpoints)==3
    np.testing.assert_array_equal(pixels,before)
