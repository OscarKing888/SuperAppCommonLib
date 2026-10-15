"""Estimate ordinary color-editor parameters from bounded previews; no Qt or writes."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from app_common.image_processing_types import check_cancelled
from .catalog import ADJUSTMENT_TITLES, adjustment_defaults
from .model import AdjustmentStep, Recipe

AUTO_MODES = {"all": "一键自动调节", "tone": "自动色调", "contrast": "自动对比度", "color": "自动颜色"}
AUTO_BASIC_KINDS = ("brightnessContrast", "levels", "curves", "exposure", "vibrance", "colorBalance")
_PREFIX = "auto-levels-"
_BASIC_PREFIX = "auto-basic-"
_MAX_SAMPLES = 262144


def auto_step_mode(step):
    if step.kind in AUTO_BASIC_KINDS and step.id.startswith(_BASIC_PREFIX):
        return "all"
    if step.kind == "levels" and step.id.startswith(_PREFIX):
        mode = step.id[len(_PREFIX):].split("-", 1)[0]
        if mode in AUTO_MODES:
            return mode
    return None


def _replaceable_step(recipe):
    return bool(recipe.steps and recipe.steps[-1].enabled
                and auto_step_mode(recipe.steps[-1]) in {"tone", "contrast", "color"})


def can_auto_adjust(recipe, mode="all"):
    if mode == "all":
        missing = set(AUTO_BASIC_KINDS) - {step.kind for step in recipe.steps}
        return len(recipe.steps) + len(missing) <= 64
    return len(recipe.steps) < 64 or _replaceable_step(recipe)


def _endpoints(low, high):
    # 最多放大四倍反差，避免雾天／暗部噪声被窄直方图过度拉伸。
    if high - low < .25:
        low = max(0., min(float((low + high) / 2) - .125, .75))
        high = low + .25
    return round(float(low), 6), round(float(high), 6)


def _sample_rgb(rgba):
    array = np.asarray(rgba)
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError("自动调节需要 RGBA 像素")
    stride = max(1, math.ceil(math.sqrt(array.shape[0] * array.shape[1] / _MAX_SAMPLES)))
    column_stride = max(1, math.ceil(array.shape[1] * math.ceil(array.shape[0] / stride) / _MAX_SAMPLES))
    sampled = array[::stride, ::column_stride]
    valid = np.isfinite(sampled).all(axis=2) & (sampled[..., 3] >= .5)
    return np.clip(sampled[..., :3][valid], 0, 1).astype(np.float64)


def auto_levels(rgba, mode="tone"):
    """Legacy single-purpose levels estimators, retained for saved CLI workflows."""
    if mode not in {"tone", "contrast", "color"}:
        raise ValueError("未知自动调节模式")
    params = adjustment_defaults("levels")
    rgb = _sample_rgb(rgba)
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
    step_ids: tuple[str, ...] = ()


def prepare_auto_adjustment(state, mode="all", *, cache=None, cancelled=None):
    """Analyze the current input and edits in a worker, without mutating state.

    Default to integrated basic-group correction. Explicit legacy modes retain
    their trailing-levels replacement behavior for existing scripts.
    """
    from .pipeline import render_recipe
    from .storage import PhotoEditState

    if mode not in AUTO_MODES:
        raise ValueError("未知自动调节模式")
    if mode == "all":
        return _prepare_all_adjustments(state, cache=cache, cancelled=cancelled)
    check_cancelled(cancelled)
    snapshot = PhotoEditState.from_dict(state.source, state.to_dict())
    original = snapshot.current.recipe.validated()
    if not can_auto_adjust(original, mode):
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


def _is_backlit(luma):
    if len(luma) < 32:
        return False
    p10, middle, p90 = np.percentile(luma, [10, 50, 90])
    # 大面积亮背景与暗主体并存时，中位数代表天空，不能据此全局压暗。
    # 同时要求明显暗部和稀疏中间调，避免把一般偏亮照片当成逆光场景。
    return bool(middle > .65 and p10 < .25 and p90 > .8
                and np.mean((luma >= .3) & (luma <= .65)) < .15)


def auto_basic_parameters(rgba, kind, *, backlit=None):
    """Estimate one ordinary editor group's parameters in its own input space.

    Each estimate is conservative and quantized to that form's displayed units.
    The caller renders preceding steps before estimating the next group, so
    tone/contrast/color corrections never stretch the same histogram three times.
    """
    if kind not in AUTO_BASIC_KINDS:
        raise ValueError("不支持自动调节的参数组")
    params = adjustment_defaults(kind)
    rgb = _sample_rgb(rgba)
    if len(rgb) < 32 or np.ptp(np.percentile(rgb, [.5, 99.5], axis=0), axis=0).max() < .03:
        return params
    luma = rgb @ np.array([.2126, .7152, .0722])
    p10, middle, p90 = np.percentile(luma, [10, 50, 90])
    if backlit is None:
        backlit = _is_backlit(luma)
    if kind == "brightnessContrast":
        if not backlit:
            params.update(brightness=round(float(np.clip((.45 - middle) * 60, -18, 18))),
                          contrast=round(float(np.clip((.65 - (p90 - p10)) * 35, -8, 16))))
    elif kind == "levels":
        # RGB 总通道直接显示黑白点，不再把所有自动值藏在单独颜色通道中。
        params = auto_levels(rgba, "contrast")
        for key in ("in_black", "in_white"):
            params["master"][key] = round(params["master"][key], 3)
        if backlit:
            # 不减去暗主体的黑位，也不把已有亮天空进一步拉到剪切边缘。
            params["master"].update(in_black=0, in_white=max(.98, params["master"]["in_white"]))
    elif kind == "curves":
        if backlit:
            # 用实际可编辑曲线局部抬升暗调；中间调和高光锚点维持原亮度。
            params["master"][1:1] = [dict(input=.15, output=.23),
                                      dict(input=.5, output=.5), dict(input=.8, output=.8)]
        else:
            lift = round(float(np.clip((.45 - middle) * .12, -.03, .03)), 3)
            if abs(lift) >= .003:
                params["master"].insert(1, dict(input=.5, output=.5 + lift))
    elif kind == "exposure":
        # 在高光允许的范围内轻微提亮，剩余中间调偏差交给可见 gamma 控件。
        top = float(np.percentile(rgb.max(axis=1), 99.5))
        if middle < .35 and 0 < top < .98:
            params["exposure"] = round(min(.35, 2.2 * math.log2(.98 / top)), 2)
        projected = float(np.clip(middle * 2 ** (params["exposure"] / 2.2), .01, .99))
        target = float(np.clip(projected, .35, .65))
        # 色阶可能已覆盖黑白端点，但主体仍偏暗／偏亮；不能把剩余中间调校正
        # 限在 0.9～1.1，否则带高光的欠曝照片几乎不变。gamma 保留黑白端点，
        # 按实际偏差校正并限制在半倍到两倍，正常亮度保持 1。
        params["gamma"] = round(float(np.clip(math.log(projected) / math.log(target), .5, 2.0)), 2)
        if backlit:
            params["gamma"] = max(1, params["gamma"])
    elif kind == "vibrance":
        saturation = np.ptp(rgb, axis=1) / np.maximum(rgb.max(axis=1), .01)
        colored = saturation[saturation > .02]
        if len(colored) >= max(32, math.ceil(len(rgb) * .01)):
            params["vibrance"] = round(float(np.clip((.35 - np.median(colored)) * 50, -10, 18)))
    elif kind == "colorBalance":
        neutral = ((np.ptp(rgb, axis=1) <= .28 * np.maximum(rgb.max(axis=1), .01))
                   & (luma > .1) & (luma < .9))
        if neutral.sum() >= max(32, math.ceil(len(rgb) * .01)):
            center = np.median(rgb[neutral], axis=0)
            target = float(center @ np.array([.2126, .7152, .0722]))
            sliders = 100 * np.log2(np.log(center) / math.log(target))
            sliders -= (sliders.max() + sliders.min()) / 2
            params["midtones"] = [round(float(v)) for v in np.clip(sliders, -18, 18)]
    return params


def _prepare_all_adjustments(state, *, cache=None, cancelled=None):
    """Recompute the first group of each basic kind, preserving step order/IDs.

    Additional instances and creative effects remain untouched. Missing groups
    occupy the same insertion slots as the panel; identity placeholders are not
    saved. Every group is estimated from the real pipeline's preceding effects.
    """
    from .cache import ImageCache
    from .pipeline import render_recipe
    from .storage import PhotoEditState

    check_cancelled(cancelled)
    snapshot = PhotoEditState.from_dict(state.source, state.to_dict())
    original = snapshot.current.recipe.validated()
    if not can_auto_adjust(original):
        raise ValueError("最多支持 64 个步骤；请为缺少的基础调节组腾出位置后重试。")
    planned = original.validated()
    old_ids = {step.id for step in planned.steps}
    ranks = {kind: index for index, kind in enumerate(ADJUSTMENT_TITLES)}
    targets = {}
    for kind in AUTO_BASIC_KINDS:
        step = next((step for step in planned.steps if step.kind == kind), None)
        if step is None:
            step = AdjustmentStep(kind, adjustment_defaults(kind), enabled=False)
            step.id = _BASIC_PREFIX + step.id
            index = next((i for i, other in enumerate(planned.steps) if ranks[other.kind] > ranks[kind]), len(planned.steps))
            planned.steps.insert(index, step)
        targets[step.id] = kind
    # CLI 也保留代理缓存：六个分析阶段只解码一次，不在每组重读 RAW。
    cache = cache if cache is not None else ImageCache()
    prefix = []
    updated_ids = []
    backlit = None
    for step in planned.steps:
        check_cancelled(cancelled)
        if step.id in targets:
            snapshot.current.recipe.steps = list(prefix)
            preview = render_recipe(snapshot, proxy=True, cache=cache, cancelled=cancelled)
            if backlit is None:
                # 一轮内固定场景判断：不能因前一组已抬起暗部，后一组又当作过曝压回去。
                backlit = _is_backlit(_sample_rgb(preview.rgba) @ np.array([.2126, .7152, .0722]))
            step.params = auto_basic_parameters(preview.rgba, step.kind, backlit=backlit)
            step.enabled = step.params != adjustment_defaults(step.kind)
            if step.enabled or step.id in old_ids:
                prefix.append(step)
                updated_ids.append(step.id)
        else:
            prefix.append(step)
    check_cancelled(cancelled)
    planned.steps = prefix
    recipe = planned.validated()
    return AutoAdjustmentResult(recipe, "all", recipe != original, tuple(updated_ids))
