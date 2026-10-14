"""Versioned parameter descriptions shared by Qt forms, validation and CLI.

Numbers use PhotoCraft's native units. This file describes parameters, never
implements an image algorithm. Native-default parity is checked by tests.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Scalar:
    key: str
    label: str
    minimum: float = -100
    maximum: float = 100
    default: float = 0
    decimals: int = 0


CAMERA_GROUPS = (
    ("光线", (Scalar("exposure", "曝光", -5, 5, 0, 2), Scalar("contrast", "对比度"),
                Scalar("highlights", "高光"), Scalar("shadows", "阴影"), Scalar("whites", "白色色阶"), Scalar("blacks", "黑色色阶"))),
    ("颜色", (Scalar("temperature", "相对色温"), Scalar("tint", "色调"), Scalar("vibrance", "自然饱和度"), Scalar("saturation", "饱和度"))),
    ("效果", (Scalar("texture", "纹理"), Scalar("clarity", "清晰度"), Scalar("dehaze", "去朦胧"),
                Scalar("vignetteAmount", "暗角数量"), Scalar("vignetteMidpoint", "暗角中点", 0, 100, 50),
                Scalar("vignetteRoundness", "暗角圆度"), Scalar("vignetteFeather", "暗角羽化", 0, 100, 50),
                Scalar("vignetteHighlights", "暗角高光", 0, 100), Scalar("grainAmount", "颗粒数量", 0, 100),
                Scalar("grainSize", "颗粒大小", 0, 100, 25), Scalar("grainRoughness", "颗粒粗糙度", 0, 100, 50))),
    ("曲线", (Scalar("curveHighlights", "高光"), Scalar("curveLights", "亮调"), Scalar("curveDarks", "暗调"), Scalar("curveShadows", "阴影"))),
    ("颜色混合器", ()),
    ("色彩分级", (Scalar("gradeBlending", "混合", 0, 100, 50), Scalar("gradeBalance", "平衡"))),
    ("细节", (Scalar("sharpenAmount", "锐化数量", 0, 150), Scalar("sharpenRadius", "锐化半径", .5, 3, 1, 2),
                Scalar("sharpenDetail", "锐化细节", 0, 100, 25), Scalar("sharpenMasking", "锐化蒙版", 0, 100),
                Scalar("noiseLuminance", "明亮度降噪", 0, 100), Scalar("noiseLuminanceDetail", "明亮度细节", 0, 100, 50),
                Scalar("noiseColor", "颜色降噪", 0, 100), Scalar("noiseColorDetail", "颜色细节", 0, 100, 50))),
)
CAMERA_SCALARS = {s.key: s for _, items in CAMERA_GROUPS for s in items}
COLOR_BANDS = ("红", "橙", "黄", "绿", "青", "蓝", "紫", "洋红")
HUE_BANDS = ("红", "黄", "绿", "青", "蓝", "洋红")
GRADE_WHEELS = (("gradeShadows", "阴影"), ("gradeMidtones", "中间调"), ("gradeHighlights", "高光"), ("gradeGlobal", "全局"))
VIGNETTE_STYLES = (("highlightPriority", "高光优先"), ("colorPriority", "颜色优先"), ("paintOverlay", "绘画叠加"))


def camera_defaults() -> dict:
    value = {key: s.default for key, s in CAMERA_SCALARS.items()}
    value.update(curveSplits=[25, 50, 75], pointCurve=[], pointCurveRed=[], pointCurveGreen=[], pointCurveBlue=[],
                 hslHue=[0]*8, hslSat=[0]*8, hslLum=[0]*8, vignetteStyle="highlightPriority", seed=0)
    value.update({key: {"hue": 0, "sat": 0, "lum": 0} for key, _ in GRADE_WHEELS})
    return value


def _levels():
    return dict(in_black=0, in_white=1, gamma=1, out_black=0, out_white=1)


def _line():
    return [{"input": 0, "output": 0}, {"input": 1, "output": 1}]


_ADJUSTMENTS = {
    "brightnessContrast": ("亮度／对比度", "BrightnessContrast", dict(brightness=0, contrast=0, legacy=False)),
    "levels": ("色阶", "Levels", dict(master=_levels(), per_channel=[_levels() for _ in range(3)], space="Rgb", black=_levels())),
    "curves": ("曲线", "Curves", dict(master=_line(), per_channel=[_line() for _ in range(3)], space="Rgb", black=[], pencil=False)),
    "exposure": ("曝光", "Exposure", dict(exposure=0, offset=0, gamma=1)),
    "vibrance": ("自然饱和度", "Vibrance", dict(vibrance=0, saturation=0)),
    "hueSaturation": ("色相／饱和度", "HueSaturation", dict(hue=0, saturation=0, lightness=0, colorize=False,
        ranges=[dict(hue=0, saturation=0, lightness=0, bounds=[60*i-45, 60*i-15, 60*i+15, 60*i+45]) for i in range(6)])),
    "colorBalance": ("色彩平衡", "ColorBalance", dict(shadows=[0]*3, midtones=[0]*3, highlights=[0]*3, preserve_luminosity=True)),
    "blackWhite": ("黑白", "BlackWhite", dict(weights=[40,60,40,60,20,80], tint=None)),
    "photoFilter": ("照片滤镜", "PhotoFilter", dict(color=[1,.55,0], density=.25, preserve_luminosity=True)),
    "channelMixer": ("通道混合器", "ChannelMixer", dict(matrix=[[1,0,0,0],[0,1,0,0],[0,0,1,0]], monochrome=False)),
    "colorLookup": ("颜色查找 LUT", "ColorLookup", dict(asset=None, builtin="", tetrahedral=False, dither=False)),
    "invert": ("反相", "Invert", {}),
    "posterize": ("色调分离", "Posterize", dict(levels=4)),
    "threshold": ("阈值", "Threshold", dict(level=128)),
    "gradientMap": ("渐变映射", "GradientMap", dict(stops=[[0,[0,0,0]],[1,[1,1,1]]], reverse=False, dither=False)),
    "selectiveColor": ("可选颜色", "SelectiveColor", dict(relative=True, adjustments=[[0]*4 for _ in range(9)])),
}
ADJUSTMENT_TITLES = {k: v[0] for k, v in _ADJUSTMENTS.items()}
ADJUSTMENT_VARIANTS = {k: v[1] for k, v in _ADJUSTMENTS.items()}


def adjustment_defaults(kind: str) -> dict:
    if kind not in _ADJUSTMENTS:
        raise ValueError(f"不支持的调色步骤：{kind}")
    return deepcopy(_ADJUSTMENTS[kind][2])


def number(value, lo, hi, name="参数", *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} 必须介于 {lo} 和 {hi} 之间")
    if integer and int(value) != value:
        raise ValueError(f"{name} 必须是整数")
    return int(value) if integer else value


def vector(value, size, lo, hi, name="参数"):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{name} 需要 {size} 个数值")
    for v in value:
        number(v, lo, hi, name)


def _merge(default: dict, value: dict):
    if not isinstance(value, dict) or value.keys() - default.keys():
        raise ValueError("未知参数或参数结构不正确")
    result = deepcopy(default)
    result.update(deepcopy(value))
    return result


def curve(points, maximum=255, limit=16, *, objects=False):
    if not isinstance(points, list) or (points and not 2 <= len(points) <= limit):
        raise ValueError(f"曲线需要 2–{limit} 个点，或留空使用线性曲线")
    previous = -1
    for p in points:
        if objects:
            if not isinstance(p, dict) or p.keys() != {"input", "output"}:
                raise ValueError("曲线点结构不正确")
            pair = [p["input"], p["output"]]
        else:
            pair = p
        vector(pair, 2, 0, maximum, "曲线")
        if pair[0] <= previous:
            raise ValueError("曲线输入位置必须严格递增")
        previous = pair[0]


def normalize_camera(value: dict) -> dict:
    result = _merge(camera_defaults(), value)
    for key, s in CAMERA_SCALARS.items():
        number(result[key], s.minimum, s.maximum, s.label)
    for key in ("pointCurve", "pointCurveRed", "pointCurveGreen", "pointCurveBlue"):
        curve(result[key])
        if any(b[0]-a[0] < 1 for a,b in zip(result[key], result[key][1:])):
            raise ValueError("Camera RAW 曲线输入点至少间隔 1")
    vector(result["curveSplits"], 3, 0, 100, "曲线分区")
    if not result["curveSplits"][0] < result["curveSplits"][1] < result["curveSplits"][2]:
        raise ValueError("曲线分区必须递增")
    for key in ("hslHue", "hslSat", "hslLum"):
        vector(result[key], 8, -100, 100, key)
    for key, _ in GRADE_WHEELS:
        wheel = result[key]
        if not isinstance(wheel, dict) or wheel.keys() != {"hue", "sat", "lum"}:
            raise ValueError("色轮需要色相、饱和度、明亮度")
        number(wheel["hue"], 0, 360)
        number(wheel["sat"], 0, 100)
        number(wheel["lum"], -100, 100)
    if result["vignetteStyle"] not in dict(VIGNETTE_STYLES):
        raise ValueError("未知暗角样式")
    number(result["seed"], 0, 2**32-1, "颗粒种子", integer=True)
    return result


def normalize_adjustment(kind: str, value: dict) -> dict:
    result = _merge(adjustment_defaults(kind), value)
    for key, default in adjustment_defaults(kind).items():
        if isinstance(default, bool) and not isinstance(result[key], bool):
            raise ValueError(f"{key} 必须为布尔值")
    ranges = {
        "brightnessContrast": {"brightness":(-150,150),"contrast":(-50,100)},
        "exposure": {"exposure":(-20,20),"offset":(-.5,.5),"gamma":(.01,9.99)},
        "vibrance": {"vibrance":(-100,100),"saturation":(-100,100)},
        "hueSaturation": {"hue":(-180,360),"saturation":(-100,100),"lightness":(-100,100)},
        "photoFilter": {"density":(0,1)}, "threshold": {"level":(0,255)},
    }
    for key, (lo,hi) in ranges.get(kind, {}).items():
        number(result[key],lo,hi,key)
    if kind == "posterize":
        result["levels"] = number(result["levels"],2,255,integer=True)
    elif kind in ("levels", "curves"):
        if result["space"] != "Rgb" or not isinstance(result["per_channel"],list) or len(result["per_channel"]) != 3:
            raise ValueError("照片编辑只接受 RGB 通道")
        if kind == "levels":
            for channel in [result["master"],*result["per_channel"],result["black"]]:
                if not isinstance(channel,dict) or channel.keys() != _levels().keys():
                    raise ValueError("色阶通道结构不正确")
                for key,val in channel.items():
                    number(val,.01 if key=="gamma" else 0,9.99 if key=="gamma" else 1,key)
                if channel["in_black"] >= channel["in_white"]:
                    raise ValueError("输入黑场必须小于输入白场")
        else:
            if result["pencil"]:
                raise ValueError("首版照片曲线使用控制点模式")
            for points in [result["master"],*result["per_channel"],result["black"]]:
                curve(points,1,16,objects=True)
    elif kind == "hueSaturation":
        if not isinstance(result["ranges"],list) or len(result["ranges"]) != 6:
            raise ValueError("色相调整需要六个色域")
        for item in result["ranges"]:
            if not isinstance(item,dict) or item.keys() != {"hue","saturation","lightness","bounds"}:
                raise ValueError("色域结构不正确")
            for key in ("hue","saturation","lightness"):
                number(item[key],-180 if key=="hue" else -100,180 if key=="hue" else 100)
            vector(item["bounds"],4,-360,720)
            if any(b<a for a,b in zip(item["bounds"],item["bounds"][1:])) or item["bounds"][-1]-item["bounds"][0] >= 360:
                raise ValueError("色域边界必须有序且小于一圈")
    elif kind == "colorBalance":
        for key in ("shadows","midtones","highlights"):
            vector(result[key],3,-100,100)
    elif kind == "blackWhite":
        vector(result["weights"],6,-200,300)
        if result["tint"] is not None:
            vector(result["tint"],3,0,1)
    elif kind == "photoFilter":
        vector(result["color"],3,0,1)
    elif kind in ("channelMixer","selectiveColor"):
        key,size,lo,hi = ("matrix",3,-2,2) if kind=="channelMixer" else ("adjustments",9,-100,100)
        if not isinstance(result[key],list) or len(result[key]) != size:
            raise ValueError("通道矩阵结构不正确")
        for row in result[key]:
            vector(row,4,lo,hi)
    elif kind == "gradientMap":
        stops = result["stops"]
        if not isinstance(stops,list) or not 2 <= len(stops) <= 64:
            raise ValueError("渐变需要 2–64 个色标")
        previous = -1
        for stop in stops:
            if not isinstance(stop,list) or len(stop)!=2:
                raise ValueError("渐变色标结构不正确")
            number(stop[0],0,1)
            vector(stop[1],3,0,1)
            if stop[0] <= previous:
                raise ValueError("渐变色标位置必须递增")
            previous = stop[0]
    elif kind == "colorLookup":
        if result["builtin"] not in ("","warm","cool","tealOrange","bleachBypass","fadedFilm","dayForNight","monoContrast","crossProcess"):
            raise ValueError("未知 LUT 预设")
        if result["asset"] is not None:
            asset = result["asset"]
            if not isinstance(asset,dict) or asset.keys() != {"sha256","path"}:
                raise ValueError("LUT 引用结构不正确")
            if not isinstance(asset["path"],str) or not asset["path"] or "\0" in asset["path"]:
                raise ValueError("LUT 路径不正确")
            if not isinstance(asset["sha256"],str) or len(asset["sha256"])!=64 or any(c not in "0123456789abcdef" for c in asset["sha256"]):
                raise ValueError("LUT 校验码不正确")
            if result["builtin"]:
                raise ValueError("LUT 文件和内置预设不能同时使用")
    return result
