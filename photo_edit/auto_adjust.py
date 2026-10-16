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


def _natural_levels(rgb, *, backlit):
    """One shared RGB mapping; automatic contrast must not rebalance channels."""
    params = adjustment_defaults("levels")
    # 参考 Auto Contrast 的合并直方图／0.1% 裁切思路，保留高位深采样。
    # 默认最多放大两倍反差，避免把雾气和轻微噪声变成生硬的黑白端点。
    black, white = np.percentile(rgb, [.1, 99.9])
    if white - black < .5:
        black = max(0., min(float((black + white) / 2) - .25, .5))
        white = black + .5
    if backlit:
        black, white = 0., max(.98, white)
    params["master"].update(in_black=round(float(black), 3), in_white=round(float(white), 3))
    return params


def _natural_curve(luma, *, backlit):
    """Correct residual tones once, with visible master-curve control points."""
    params = adjustment_defaults("curves")
    if backlit:
        # 暗主体与亮背景分离时局部抬暗部，中高光固定；不能按天空的中位数压暗。
        params["master"][1:1] = [dict(input=.15, output=.23),
                                  dict(input=.5, output=.5), dict(input=.8, output=.8)]
        return params
    middle = float(np.clip(np.median(luma), .01, .99))
    target = float(np.clip(middle, .35, .65))
    gamma = float(np.clip(math.log(middle) / math.log(target), .5, 2.))
    if abs(gamma - 1) < .01:
        return params
    # 曝光／亮度保持中性，只用这一条曲线补偿色阶之后的剩余偏差。
    # 黑白端点固定，避免为了达到目标中位数而进一步裁掉阴影／高光。
    inputs = [0., .01, .03, .08, .15, .25, .4, .6, .8, 1.]
    params["master"] = [dict(input=x, output=round(x ** (1 / gamma), 3)) for x in inputs]
    return params


def _neutral_color_balance(rgb):
    """Return a small correction only for consistent, well-supported midtones.

    Low chroma alone is not proof of a neutral object. Require evidence across
    three tonal bands and agreement within/between them; bright skies, isolated
    patches and mixed casts must not drive a global channel-gamma correction.
    None means insufficient evidence; a zero vector means confidently neutral.
    """
    if len(rgb) < 96:
        return None
    luma = rgb @ np.array([.2126, .7152, .0722])
    neutral = ((luma >= .15) & (luma < .75)
               & (np.ptp(rgb, axis=1) <= .12 * np.maximum(rgb.max(axis=1), .01)))
    if neutral.sum() < max(96, math.ceil(len(rgb) * .05)):
        return None
    samples, values = rgb[neutral], luma[neutral]
    colors = np.log(samples / values[:, None])
    if np.ptp(np.percentile(colors, [10, 90], axis=0), axis=0).max() > .04:
        return None
    centers, casts = [], []
    for low, high in ((.15, .35), (.35, .55), (.55, .75)):
        band = (values >= low) & (values < high)
        if band.sum() < max(32, math.ceil(len(rgb) * .01)):
            return None
        centers.append(np.median(samples[band], axis=0))
        casts.append(np.median(colors[band], axis=0))
    if np.ptp(casts, axis=0).max() > .025:
        return None
    # 三个亮度区间等权拟合，防止面积最大的天空／墙面主导；只做一半校正。
    centers = np.asarray(centers)
    logs = np.log(centers)
    targets = np.log(centers @ np.array([.2126, .7152, .0722]))
    powers = np.sum(logs * targets[:, None], axis=0) / np.sum(logs * logs, axis=0)
    sliders = -100 * np.log2(powers)
    sliders -= (sliders.max() + sliders.min()) / 2
    sliders *= .5
    # 按同一比例限制强度，不分别截断各通道；最多 ±6，避免暗部被 gamma 放大色偏。
    peak = float(np.abs(sliders).max())
    if peak > 6:
        sliders *= 6 / peak
    return [round(float(value)) for value in sliders]


def auto_basic_parameters(rgba, kind, *, backlit=None, allow_color_balance=True):
    """Coordinate endpoint contrast, one tonal curve and evidence-based color.

    Brightness/contrast, exposure and vibrance remain neutral instead of each
    pushing toward another histogram/saturation target. Every returned value is
    an ordinary, editable parameter in that group's displayed units.
    """
    if kind not in AUTO_BASIC_KINDS:
        raise ValueError("不支持自动调节的参数组")
    params = adjustment_defaults(kind)
    rgb = _sample_rgb(rgba)
    if len(rgb) < 32 or np.ptp(np.percentile(rgb, [.5, 99.5], axis=0), axis=0).max() < .03:
        return params
    luma = rgb @ np.array([.2126, .7152, .0722])
    if backlit is None:
        backlit = _is_backlit(luma)
    if kind == "levels":
        return _natural_levels(rgb, backlit=backlit)
    if kind == "curves":
        return _natural_curve(luma, backlit=backlit)
    if kind == "colorBalance" and allow_color_balance and not backlit:
        sliders = _neutral_color_balance(rgb)
        if sliders is not None:
            params["midtones"] = sliders
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
    allow_color_balance = False
    for step in planned.steps:
        check_cancelled(cancelled)
        if step.id in targets:
            snapshot.current.recipe.steps = list(prefix)
            preview = render_recipe(snapshot, proxy=True, cache=cache, cancelled=cancelled)
            if backlit is None:
                # 一轮内固定场景判断：不能因前一组已抬起暗部，后一组又当作过曝压回去。
                original_rgb = _sample_rgb(preview.rgba)
                backlit = _is_backlit(original_rgb @ np.array([.2126, .7152, .0722]))
                # 原始分析没有可靠中性色时，不能把提亮后碰巧落入阈值的颜色当作依据。
                allow_color_balance = not backlit and _neutral_color_balance(original_rgb) is not None
            step.params = auto_basic_parameters(preview.rgba, step.kind, backlit=backlit,
                                                allow_color_balance=allow_color_balance)
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
