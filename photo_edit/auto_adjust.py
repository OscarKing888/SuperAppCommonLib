"""Estimate editable RGB levels from a bounded preview; no Qt or image writes."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from app_common.image_processing_types import check_cancelled
from .catalog import adjustment_defaults
from .model import AdjustmentStep, Recipe

AUTO_MODES = {"tone": "自动色调", "contrast": "自动对比度", "color": "自动颜色"}
_PREFIX = "auto-levels-"
_MAX_SAMPLES = 262144


def auto_step_mode(step):
    if step.kind == "levels" and step.id.startswith(_PREFIX):
        mode = step.id[len(_PREFIX):].split("-", 1)[0]
        if mode in AUTO_MODES:
            return mode
    return None


def _replaceable_step(recipe):
    return bool(recipe.steps and recipe.steps[-1].enabled and auto_step_mode(recipe.steps[-1]))


def can_auto_adjust(recipe):
    return len(recipe.steps) < 64 or _replaceable_step(recipe)


def _endpoints(low, high):
    # 最多放大四倍反差，避免雾天／暗部噪声被窄直方图过度拉伸。
    if high - low < .25:
        low = max(0., min(float((low + high) / 2) - .125, .75))
        high = low + .25
    return round(float(low), 6), round(float(high), 6)


def auto_levels(rgba, mode="tone"):
    """Return ordinary levels parameters for float SDR/sRGB RGBA pixels.

    Tone stretches channels independently; contrast uses common endpoints;
    color additionally balances sufficiently represented near-neutral midtones.
    Ignore invisible pixels, clip 0.5% tails, and leave flat images unchanged.
    """
    if mode not in AUTO_MODES:
        raise ValueError("未知自动调节模式")
    array = np.asarray(rgba)
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError("自动调节需要 RGBA 像素")
    params = adjustment_defaults("levels")
    stride = max(1, math.ceil(math.sqrt(array.shape[0] * array.shape[1] / _MAX_SAMPLES)))
    column_stride = max(1, math.ceil(array.shape[1] * math.ceil(array.shape[0] / stride) / _MAX_SAMPLES))
    sampled = array[::stride, ::column_stride]
    valid = np.isfinite(sampled).all(axis=2) & (sampled[..., 3] >= .5)
    rgb = np.clip(sampled[..., :3][valid], 0, 1).astype(np.float64)
    if len(rgb) < 32:
        return params
    low, high = np.percentile(rgb, [.5, 99.5], axis=0)
    spans = high - low
    if float(spans.max()) < .03:
        return params
    if mode == "contrast":
        black, white = _endpoints(low.min(), high.max())
        params["master"].update(in_black=black, in_white=white)
        return params
    corrected = rgb.copy()
    for i, channel in enumerate(params["per_channel"]):
        if spans[i] < .03:
            continue
        black, white = _endpoints(low[i], high[i])
        channel.update(in_black=black, in_white=white)
        corrected[:, i] = np.clip((rgb[:, i] - black) / (white - black), 0, 1)
    if mode == "color":
        luminance = corrected @ np.array([.2126, .7152, .0722])
        neutral = ((np.ptp(corrected, axis=1) <= .25 * np.maximum(corrected.max(axis=1), .01))
                   & (luminance > .1) & (luminance < .9))
        # 只用近中性色，不能把整片绿叶／晚霞的平均色强行变灰。
        if neutral.sum() >= max(32, math.ceil(len(rgb) * .01)):
            middle = np.median(corrected[neutral], axis=0)
            target = float(middle @ np.array([.2126, .7152, .0722]))
            for i, channel in enumerate(params["per_channel"]):
                if spans[i] >= .03:
                    channel["gamma"] = round(float(np.clip(math.log(middle[i]) / math.log(target), .8, 1.25)), 4)
    return params


@dataclass
class AutoAdjustmentResult:
    recipe: Recipe
    mode: str
    changed: bool


def prepare_auto_adjustment(state, mode="tone", *, cache=None, cancelled=None):
    """Analyze the current input and edits in a worker, without mutating state.

    Recompute a trailing enabled auto step from its input, keeping repeated
    clicks stable. Other edits, disabled steps and their order remain intact.
    """
    from .pipeline import render_recipe
    from .storage import PhotoEditState

    if mode not in AUTO_MODES:
        raise ValueError("未知自动调节模式")
    check_cancelled(cancelled)
    snapshot = PhotoEditState.from_dict(state.source, state.to_dict())
    original = snapshot.current.recipe.validated()
    if not can_auto_adjust(original):
        raise ValueError("最多支持 64 个步骤，请移除一个步骤后再自动调节。")
    recipe = snapshot.current.recipe
    previous = recipe.steps.pop() if _replaceable_step(recipe) else None
    preview = render_recipe(snapshot, proxy=True, cache=cache, cancelled=cancelled)
    check_cancelled(cancelled)
    params = auto_levels(preview.rgba, mode)
    check_cancelled(cancelled)
    if params != adjustment_defaults("levels"):
        step = AdjustmentStep("levels", params)
        step.id = (previous.id if previous and auto_step_mode(previous) == mode
                   else _PREFIX + mode + "-" + step.id)
        recipe.steps.append(step)
    recipe = recipe.validated()
    return AutoAdjustmentResult(recipe, mode, recipe != original)
