"""Automatic correction must stay editable, bounded and non-destructive."""
import numpy as np
import pytest
from PIL import Image

from app_common.image_processing_types import ImageProcessingCancelled
from app_common.photo_edit.auto_adjust import (AUTO_BASIC_KINDS, auto_levels, auto_basic_parameters,
                                              prepare_auto_adjustment, auto_step_mode, can_auto_adjust)
from app_common.photo_edit.catalog import adjustment_defaults
from app_common.photo_edit.model import Recipe, AdjustmentStep
from app_common.photo_edit.native import render_pixels
from app_common.photo_edit.storage import RecipeStore


def ramp(low=.2, high=.7):
    values = np.linspace(low, high, 256, dtype=np.float32)
    rgba = np.ones((32, 256, 4), dtype=np.float32)
    rgba[..., :3] = values[None, :, None]
    return rgba


def render(rgba, params):
    pytest.importorskip("app_common.photo_edit._native")
    return render_pixels(rgba, Recipe(steps=[AdjustmentStep("levels", params)]))


@pytest.mark.parametrize("mode", ["tone", "contrast", "color"])
def test_low_contrast_stretches_dynamic_range_and_preserves_alpha(mode):
    rgba = ramp()
    rgba[..., 3] = .7
    before = rgba.copy()
    output = render(rgba, auto_levels(rgba, mode))
    assert np.ptp(output[..., 0]) > .98
    assert np.allclose(output[..., 0], output[..., 1])
    assert np.array_equal(output[..., 3], rgba[..., 3])
    assert np.array_equal(rgba, before)


def test_contrast_keeps_common_channel_mapping_while_tone_removes_endpoint_cast():
    rgba = ramp()
    rgba[..., 0] += .12
    rgba[..., 2] -= .08
    contrast = render(rgba, auto_levels(rgba, "contrast"))
    tone = render(rgba, auto_levels(rgba, "tone"))
    assert np.mean(contrast[..., 0] - contrast[..., 2]) > .15
    assert np.max(np.abs(tone[..., 0] - tone[..., 2])) < 1e-4


def test_color_neutralizes_midtones_without_moving_grayscale():
    rgba = ramp(.05, .95)
    rgba[..., 0] = rgba[..., 0] ** .9
    tone = render(rgba, auto_levels(rgba, "tone"))
    params = auto_levels(rgba, "color")
    color = render(rgba, params)
    assert any(c["gamma"] != 1 for c in params["per_channel"])
    assert abs(np.median(color[..., 0] - color[..., 1])) < abs(np.median(tone[..., 0] - tone[..., 1]))
    gray = auto_levels(ramp(), "color")
    assert all(c["gamma"] == 1 for c in gray["per_channel"])


@pytest.mark.parametrize("value", [0., .3, 1.])
@pytest.mark.parametrize("mode", ["tone", "contrast", "color"])
def test_flat_black_white_and_color_are_safe_noops(value, mode):
    rgba = np.ones((40, 40, 4), dtype=np.float32)
    rgba[..., :3] = [value, .25, .85]
    assert auto_levels(rgba, mode) == adjustment_defaults("levels")


def test_transparency_invalid_pixels_tiny_input_and_outliers():
    rgba = ramp()
    expected = auto_levels(rgba)
    hidden = np.ones_like(rgba)
    hidden[..., :3] = [np.nan, 1, 0]
    hidden[..., 3] = 0
    assert auto_levels(np.concatenate([rgba, hidden])) == expected
    assert auto_levels(hidden) == adjustment_defaults("levels")
    assert auto_levels(rgba[:1, :1]) == adjustment_defaults("levels")
    rgba[0, 0, :3] = 0
    rgba[-1, -1, :3] = 1
    actual = auto_levels(rgba)["per_channel"][0]
    assert .19 < actual["in_black"] < .21 and .69 < actual["in_white"] < .71


def test_narrow_histogram_never_amplifies_more_than_four_times():
    params = auto_levels(ramp(.2, .24))
    assert all(c["in_white"] - c["in_black"] >= .25 - 1e-6 for c in params["per_channel"])


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "自动调节-白鹭.png"
    Image.fromarray(np.rint(ramp()[..., :3] * 255).astype(np.uint8)).save(source)
    return source


def test_repeat_is_stable_preserves_edits_and_roundtrips_chinese_xmp(source):
    pytest.importorskip("app_common.photo_edit._native")
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    PhotoMetaDataXMP().write(str(source), {"XMP-dc:Title": "白鹭自动调色", "XMP-dc:Description": "保留中文备注"})
    original_bytes = source.read_bytes()
    state = RecipeStore().load(source)
    state.current.recipe.camera["exposure"] = .15
    state.current.recipe.steps = [AdjustmentStep("vibrance", {"vibrance": 10})]
    state.current.recipe = state.current.recipe.validated()
    before = state.to_dict()
    result = prepare_auto_adjustment(state, "tone")
    assert state.to_dict() == before and result.changed
    assert result.recipe.steps[:-1] == state.current.recipe.steps
    assert result.recipe.camera == state.current.recipe.camera
    assert auto_step_mode(result.recipe.steps[-1]) == "tone"
    state.current.recipe = result.recipe
    again = prepare_auto_adjustment(state, "tone")
    assert not again.changed and again.recipe == result.recipe
    RecipeStore().save(state)
    loaded = RecipeStore().load(source)
    assert loaded.current.recipe == result.recipe
    metadata = PhotoMetaDataXMP().read(str(source))
    assert metadata["XMP-dc:Title"] == "白鹭自动调色"
    assert metadata["XMP-dc:Description"] == "保留中文备注"
    assert source.read_bytes() == original_bytes


def test_step_limit_cancel_and_decode_failure_leave_state_unchanged(source, monkeypatch):
    from app_common.photo_edit import pipeline
    state = RecipeStore().load(source)
    before = state.to_dict()
    with pytest.raises(ImageProcessingCancelled):
        prepare_auto_adjustment(state, cancelled=lambda: True)
    with pytest.raises(ValueError, match="模式"):
        prepare_auto_adjustment(state, "unknown")
    monkeypatch.setattr(pipeline, "render_recipe", lambda *a, **k: (_ for _ in ()).throw(OSError("无法读取")))
    with pytest.raises(OSError):
        prepare_auto_adjustment(state)
    assert state.to_dict() == before
    state.current.recipe.steps = [AdjustmentStep("invert") for _ in range(64)]
    with pytest.raises(ValueError, match="64"):
        prepare_auto_adjustment(state)


def test_verified_denoised_input_keeps_original_recipe(source, tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    from image_denoise.export import _prepare_sidecar
    output = tmp_path / "降噪.png"
    output.write_bytes(source.read_bytes())
    _prepare_sidecar(source, output.with_suffix(".xmp"), 256, 32)
    state = RecipeStore().load(source)
    state.use_denoised(output)
    result = prepare_auto_adjustment(state, "contrast")
    state.current.recipe = result.recipe
    assert result.changed and result.recipe.steps
    assert state.inputs["original"].recipe.is_identity()


@pytest.mark.parametrize("kind", ["levels", "curves", "colorBalance"])
def test_coordinated_corrections_have_real_editable_parameters_and_pixel_effects(kind):
    pytest.importorskip("app_common.photo_edit._native")
    rgba = ramp(.12, .42)
    if kind == "colorBalance":
        rgba = ramp(.15, .78)
        rgba[..., :3] *= [1.025, 1, .975]
    rgba[..., 3] = .8
    before = rgba.copy()
    params = auto_basic_parameters(rgba, kind)
    recipe = Recipe(steps=[AdjustmentStep(kind, params)]).validated()
    assert params != adjustment_defaults(kind)
    output = render_pixels(rgba, recipe)
    assert np.isfinite(output).all() and not np.allclose(output, rgba)
    assert np.array_equal(output[..., 3], rgba[..., 3])
    assert np.array_equal(rgba, before)
    if kind == "curves":
        assert np.median(output[..., :3]) > np.median(rgba[..., :3])
    elif kind == "colorBalance":
        assert abs(np.median(output[..., 0] - output[..., 2])) < abs(np.median(rgba[..., 0] - rgba[..., 2]))
    elif kind == "levels":
        assert params["master"]["in_black"] > 0
        assert all(c == adjustment_defaults("levels")["master"] for c in params["per_channel"])


@pytest.mark.parametrize("kind", ["brightnessContrast", "exposure", "vibrance"])
def test_auto_does_not_stack_extra_tone_or_saturation_targets(kind):
    for rgba in (ramp(.05, .3), ramp(.7, .95), ramp(.2, .7)):
        assert auto_basic_parameters(rgba, kind) == adjustment_defaults(kind)


def test_natural_contrast_shares_endpoints_and_limits_noise_amplification():
    rgba = ramp(.2, .24)
    rgba[..., :3] *= [1, 1.1, .9]
    params = auto_basic_parameters(rgba, "levels")
    assert params["master"]["in_white"] - params["master"]["in_black"] >= .5 - 1e-6
    assert params["per_channel"] == adjustment_defaults("levels")["per_channel"]
    rgba = ramp(.2, .7)
    rgba[0, 0, :3] = 0
    rgba[-1, -1, :3] = 1
    params = auto_basic_parameters(rgba, "levels")
    assert .19 < params["master"]["in_black"] < .21
    assert .69 < params["master"]["in_white"] < .71


@pytest.mark.parametrize("kind", AUTO_BASIC_KINDS)
def test_natural_analysis_ignores_hidden_invalid_and_tiny_samples(kind):
    rgba = ramp(.15, .78)
    rgba[..., :3] *= [1.025, 1, .975]
    hidden = np.ones_like(rgba)
    hidden[..., :3] = [np.nan, 1, 0]
    hidden[..., 3] = 0
    assert auto_basic_parameters(np.concatenate([rgba, hidden]), kind) == auto_basic_parameters(rgba, kind)
    assert auto_basic_parameters(hidden, kind) == adjustment_defaults(kind)
    assert auto_basic_parameters(rgba[:1, :1], kind) == adjustment_defaults(kind)


@pytest.mark.parametrize("scale", [[1.025, 1, .975], [.975, 1, 1.025], [1.06, 1, .94]])
def test_supported_neutral_cast_gets_only_partial_bounded_correction(scale):
    pytest.importorskip("app_common.photo_edit._native")
    rgba = ramp(.15, .78)
    rgba[..., :3] *= scale
    params = auto_basic_parameters(rgba, "colorBalance")
    assert 0 < max(abs(value) for value in params["midtones"]) <= 6
    assert params["shadows"] == params["highlights"] == [0, 0, 0]
    output = render_pixels(rgba, Recipe(steps=[AdjustmentStep("colorBalance", params)]))
    before = np.median(rgba[..., 0] - rgba[..., 2])
    after = np.median(output[..., 0] - output[..., 2])
    assert .2 < after / before < .9  # 保留部分原有色调，不追求强行变灰。


@pytest.mark.parametrize("scene", ["bright_sky", "one_tonal_band", "mixed_casts", "tiny_patch", "foliage", "sunset"])
def test_scene_colors_without_reliable_neutral_evidence_are_not_white_balanced(scene):
    rgba = ramp(.15, .78)
    rgba[..., :3] *= [1.025, 1, .975]
    if scene == "bright_sky":
        rgba = ramp(.78, .95)
        rgba[..., :3] *= [.95, 1, 1.02]
    elif scene == "one_tonal_band":
        rgba = ramp(.36, .54)
        rgba[..., :3] *= [1.025, 1, .975]
    elif scene == "mixed_casts":
        rgba[:, 128:, :3] *= [.95, 1, 1.05]
    elif scene == "tiny_patch":
        rgba = np.tile(rgba, (32, 1, 1))
        rgba[32:, :, :3] = [.15, .4, .1]
    elif scene == "foliage":
        rgba[..., :3] *= [.5, 1, .4]
    elif scene == "sunset":
        rgba[..., :3] *= [1, .6, .4]
    assert auto_basic_parameters(rgba, "colorBalance") == adjustment_defaults("colorBalance")
    assert auto_basic_parameters(rgba, "vibrance") == adjustment_defaults("vibrance")


def test_tone_stretch_cannot_fabricate_neutral_evidence_missing_from_input(source):
    pytest.importorskip("app_common.photo_edit._native")
    rgba = ramp(.04, .34)
    rgba[..., :3] *= [1.03, 1, .97]
    Image.fromarray(np.rint(rgba[..., :3] * 255).astype(np.uint8)).save(source)
    result = prepare_auto_adjustment(RecipeStore().load(source))
    output = render_pixels(rgba, result.recipe)
    assert auto_basic_parameters(output, "colorBalance") != adjustment_defaults("colorBalance")
    assert not any(step.enabled and step.kind == "colorBalance" for step in result.recipe.steps)


def test_unified_recomputes_existing_groups_preserves_ids_duplicates_and_order(source, monkeypatch):
    pytest.importorskip("app_common.photo_edit._native")
    from app_common.photo_edit import pipeline
    state = RecipeStore().load(source)
    first = AdjustmentStep("exposure", {"exposure": 2})
    creative = AdjustmentStep("photoFilter", {"density": .12})
    duplicate = AdjustmentStep("exposure", {"exposure": -.15})
    legacy = AdjustmentStep("levels", auto_levels(ramp()), id="auto-levels-tone-old")
    state.current.recipe.steps = [first, creative, duplicate, legacy]
    state.current.recipe = state.current.recipe.validated()
    state.current.recipe.camera["exposure"] = .1
    before = state.to_dict()
    decode = pipeline.decode_image
    decodes = []
    def counted(*args, **kwargs):
        decodes.append(args[0])
        return decode(*args, **kwargs)
    monkeypatch.setattr(pipeline, "decode_image", counted)
    result = prepare_auto_adjustment(state)
    assert result.mode == "all" and result.changed
    assert state.to_dict() == before and len(decodes) == 1
    assert result.recipe.camera == state.current.recipe.camera
    old_ids = [step.id for step in state.current.recipe.steps]
    assert [step.id for step in result.recipe.steps if step.id in old_ids] == old_ids
    by_id = {step.id: step for step in result.recipe.steps}
    assert by_id[creative.id] == state.current.recipe.steps[1]
    assert by_id[duplicate.id] == state.current.recipe.steps[2]
    assert by_id[first.id].params["exposure"] != 2
    assert sum(step.kind == "levels" for step in result.recipe.steps) == 1
    state.current.recipe = result.recipe
    repeat = prepare_auto_adjustment(state)
    assert not repeat.changed and repeat.recipe == result.recipe
    RecipeStore().save(state)
    assert RecipeStore().load(source).current.recipe == result.recipe


def test_unified_limits_reuses_existing_slots_and_cancels_mid_analysis(source, monkeypatch):
    pytest.importorskip("app_common.photo_edit._native")
    from app_common.photo_edit import auto_adjust
    state = RecipeStore().load(source)
    state.current.recipe.steps = [AdjustmentStep(kind) for kind in AUTO_BASIC_KINDS]
    state.current.recipe.steps += [AdjustmentStep("invert", enabled=False) for _ in range(58)]
    assert can_auto_adjust(state.current.recipe)
    result = prepare_auto_adjustment(state)
    assert len(result.recipe.steps) == 64
    state.current.recipe.steps = [AdjustmentStep("invert", enabled=False) for _ in range(59)]
    assert not can_auto_adjust(state.current.recipe)
    with pytest.raises(ValueError, match="64"):
        prepare_auto_adjustment(state)
    state.current.recipe = Recipe()
    before = state.to_dict()
    calls = []
    estimate = auto_adjust.auto_basic_parameters
    def cancel_after_first(*args, **kwargs):
        calls.append(1)
        return estimate(*args, **kwargs)
    monkeypatch.setattr(auto_adjust, "auto_basic_parameters", cancel_after_first)
    with pytest.raises(ImageProcessingCancelled):
        prepare_auto_adjustment(state, cancelled=lambda: bool(calls))
    assert len(calls) == 1 and state.to_dict() == before


def test_unified_flat_image_remains_noop(source):
    pytest.importorskip("app_common.photo_edit._native")
    Image.new("RGB", (64, 64), (64, 80, 100)).save(source)
    state = RecipeStore().load(source)
    result = prepare_auto_adjustment(state)
    assert not result.changed and result.recipe.is_identity() and not result.step_ids


@pytest.mark.parametrize("bright",[False,True])
def test_unified_corrects_midtone_exposure_when_black_and_white_points_already_span_range(source,bright):
    pytest.importorskip("app_common.photo_edit._native")
    rgba = ramp(0,1)
    rgba[...,:3] **= 4
    if bright:
        rgba[...,:3] = 1-rgba[...,:3]
    Image.fromarray(np.rint(rgba[...,:3]*255).astype(np.uint8)).save(source)
    state = RecipeStore().load(source)
    result = prepare_auto_adjustment(state)
    output = render_pixels(rgba,result.recipe)
    before,after = np.median(rgba[...,0]),np.median(output[...,0])
    if bright:
        # 高调画面温和回收亮度，不把大面积亮背景强压到中灰。
        assert .8 < after < .92 and before-after > .04
    else:
        assert .22 < after < .5 and after-before > .1
    assert np.allclose(output[...,0],output[...,1])
    assert np.allclose(output[...,1],output[...,2])
    assert np.array_equal(output[...,3],rgba[...,3])
    # Gamma must preserve the endpoints instead of clipping them further to exaggerate the change.
    assert output[...,:3].min()==pytest.approx(0,abs=1e-5)
    assert output[...,:3].max()==pytest.approx(1,abs=1e-5)
    state.current.recipe = result.recipe
    assert not prepare_auto_adjustment(state).changed


def test_unified_balanced_grayscale_stays_close_to_original(source):
    pytest.importorskip("app_common.photo_edit._native")
    rgba = ramp(0,1)
    Image.fromarray(np.rint(rgba[...,:3]*255).astype(np.uint8)).save(source)
    result = prepare_auto_adjustment(RecipeStore().load(source))
    output = render_pixels(rgba,result.recipe)
    assert np.abs(output[...,:3]-rgba[...,:3]).mean()<.02
    assert abs(np.median(output[...,0])-.5)<.03


@pytest.mark.parametrize("colored", [False, True])
@pytest.mark.parametrize("shadow_range", [(.02, .22), (.18, .29)])
def test_unified_backlit_subject_is_lifted_without_darkening_it_to_match_bright_sky(source,colored,shadow_range):
    pytest.importorskip("app_common.photo_edit._native")
    rgba = np.ones((40, 400, 4), dtype=np.float32)
    rgba[:, :120, :3] = np.linspace(*shadow_range, 120)[None, :, None]
    rgba[:, 120:, :3] = np.linspace(.82, .95, 280)[None, :, None]
    if colored:
        rgba[..., 0] *= .94
        rgba[..., 2] *= 1.02
    rgba[..., 3] = .8
    Image.fromarray(np.rint(rgba*255).astype(np.uint8)).save(source)
    state = RecipeStore().load(source)
    result = prepare_auto_adjustment(state)
    output = render_pixels(rgba, result.recipe)
    before = rgba[..., :3] @ np.array([.2126, .7152, .0722])
    after = output[..., :3] @ np.array([.2126, .7152, .0722])
    assert np.median(after[:, :120] - before[:, :120]) > .03
    assert np.all(after[:, :120] >= before[:, :120])
    assert np.abs(after[:, 120:] - before[:, 120:]).mean() < .05
    assert np.array_equal(output[..., 3], rgba[..., 3])
    assert not any(step.enabled and step.kind in {"colorBalance", "vibrance"} for step in result.recipe.steps)
    if colored:
        # RGB 共用色阶／曲线后，暗部仍偏冷；不能被天空校色反转成暖棕色。
        assert np.all(output[:, :120, 0] < output[:, :120, 2])
    # 同一轮的后续组不能因阴影已提亮而又反向压暗；重算与 XMP 回读也稳定。
    state.current.recipe = result.recipe
    assert not prepare_auto_adjustment(state).changed
    RecipeStore().save(state)
    assert RecipeStore().load(source).current.recipe == result.recipe
