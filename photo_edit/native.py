"""Lazy native backend. Importing metadata/CLI help never loads Qt or the extension."""
from __future__ import annotations

from functools import lru_cache
import json

from .catalog import ADJUSTMENT_VARIANTS, camera_defaults, normalize_camera
from .model import Recipe, canonical_json


class NativeUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def backend():
    try:
        from . import _native
    except ImportError as exc:
        raise NativeUnavailable("照片编辑原生组件未安装或不匹配。源码环境请运行 app_common/native/photo_edit/tools/build_native.py；安装版请重新安装完整程序。") from exc
    info = json.loads(_native.info_json())
    if info["revision"] != "369704ac551e8e409800e423ff550c353d5522ef" or info["bridgeVersion"] != 1:
        raise NativeUnavailable("照片编辑原生组件版本不匹配，请重新构建或安装")
    return _native


def native_steps(recipe: Recipe, *, asset_loader=None):
    result = []
    for step in recipe.steps:
        if not step.enabled:
            continue
        params = dict(step.params)
        if step.kind == "invert":
            result.append("Invert")
            continue
        if step.kind == "colorLookup":
            asset,builtin = params.pop("asset"),params.pop("builtin")
            lut = None
            if asset:
                if asset_loader is None:
                    raise ValueError("需要 LUT 资源加载器")
                name,data = asset_loader(asset)
                lut = json.loads(backend().load_lut_json(name,data))
            elif builtin:
                lut = json.loads(backend().load_lut_json("",b"",builtin))
            params.update(name=lut["name"] if lut else "",lut=lut["lut"] if lut else None,size=lut["size"] if lut else 0)
        result.append({ADJUSTMENT_VARIANTS[step.kind]:params})
    return result


def render_pixels(pixels, recipe: Recipe, *, pixel_scale=1., asset_loader=None, cancelled=None):
    import numpy as np
    from app_common.image_processing_types import check_cancelled
    recipe = recipe.validated()
    camera = recipe.camera if recipe.camera_enabled else camera_defaults()
    result = backend().render(np.ascontiguousarray(pixels,dtype=np.float32),canonical_json(camera),
                            canonical_json(native_steps(recipe,asset_loader=asset_loader)),pixel_scale,
                            (lambda:check_cancelled(cancelled)) if cancelled else None)
    for start in range(0,result.shape[0],256):
        check_cancelled(cancelled)
        if not np.isfinite(result[start:start+256]).all():
            raise ValueError("编辑算法返回无效像素，未输出成片")
    return result


def import_settings(camera: dict, xmp: bytes):
    result = json.loads(backend().import_settings_json(canonical_json(camera),xmp))
    # The bridge fixes these pipeline-only fields, while persisted user recipes
    # contain only editable settings. Never re-import a proxy radius scale.
    result["cameraRaw"].pop("pixelScale",None)
    result["cameraRaw"].pop("encodingGamma",None)
    result["cameraRaw"] = normalize_camera(result["cameraRaw"])
    return result


def export_settings(camera: dict) -> bytes:
    return backend().export_settings(canonical_json(camera)).encode("utf-8")
