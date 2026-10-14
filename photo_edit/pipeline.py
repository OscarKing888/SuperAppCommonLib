"""High-depth editing pipeline. All entry points here must run off the GUI thread."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import json
from pathlib import Path
import threading

import numpy as np
from PIL import Image

from app_common.image_pixels import decode_image, probe_image
from app_common.image_processing_types import check_cancelled
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from .assets import load_asset
from .cache import ImageCache
from .model import ALGORITHM_VERSION
from .native import render_pixels
from .storage import PhotoEditState, fingerprint, EditConflict

_FULL_SLOT = threading.Semaphore(1)
PROXY_SIDE = 900
MAX_PIXELS = 64_000_000


@dataclass
class RenderResult:
    rgba: np.ndarray
    full_size: tuple[int,int]
    source: str
    input_path: str
    recipe_signature: str
    before: bool
    approximate: bool
    camera_crop: tuple | None = None
    histogram: dict | None = None


def resolve_input(state: PhotoEditState) -> Path:
    return resolve_input_geometry(state)[0]


def resolve_input_geometry(state: PhotoEditState):
    source = Path(state.source)
    if fingerprint(source) != state.fingerprint:
        raise EditConflict("源照片已变化，未使用过期编辑记录")
    entry = state.current
    if entry.mode == "original":
        return source,None
    target = Path(entry.path)
    if not target.is_absolute():
        target = source.parent/target
    if fingerprint(target) != entry.fingerprint:
        raise EditConflict("所选降噪版本已变化，请明确选择新的降噪版本")
    metadata = PhotoMetaDataXMP().read(str(target))
    def field(name):
        return metadata.get("XMP-superpicky:"+name)
    recorded = field("denoise_source_path")
    if (not recorded or os.path.normcase(os.path.abspath(recorded)) != os.path.normcase(os.path.abspath(source))
            or str(field("denoise_source_size")) != str(state.fingerprint["size"])
            or str(field("denoise_source_mtime_ns")) != str(state.fingerprint["mtimeNs"])):
        raise EditConflict("降噪成片的来源记录不匹配，未把其他照片作为编辑底图")
    raw_crop = field("denoise_camera_crop")
    crop = json.loads(raw_crop) if isinstance(raw_crop,str) else raw_crop
    if crop is not None:
        if not isinstance(crop,(list,tuple)) or len(crop)!=4:
            raise EditConflict("降噪成片的画面几何记录无效")
        crop = tuple(float(v) for v in crop)
        if not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1):
            raise EditConflict("降噪成片的画面几何记录无效")
    return target,crop


def available_memory():
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:
        return 4*1024**3


def check_memory(width, height):
    count = int(width)*int(height)
    if width <= 0 or height <= 0 or count > MAX_PIXELS:
        raise ValueError("照片编辑支持最多 6400 万像素；不会缩小正式成片")
    # Conservative estimate covering float RGBA, source/decode buffers and the
    # guided-filter temporaries. One full-resolution operation at a time.
    needed = count*192 + 128*1024**2
    if needed > available_memory()*.75:
        raise MemoryError("可用内存不足以安全处理完整照片，请关闭其他大图任务后重试")


@contextmanager
def full_resolution_slot(cancelled=None):
    while not _FULL_SLOT.acquire(timeout=.05):
        check_cancelled(cancelled)
    try:
        check_cancelled(cancelled)
        yield
    finally:
        _FULL_SLOT.release()


def resize_proxy(rgba, side=PROXY_SIDE):
    height,width = rgba.shape[:2]
    if max(width,height) <= side:
        return rgba
    scale = side/max(width,height)
    size = (max(1,round(width*scale)),max(1,round(height*scale)))
    # Premultiplied float resampling prevents transparent edges bleeding colour.
    alpha = rgba[...,3]
    planes = [rgba[...,i]*alpha for i in range(3)] + [alpha]
    resized = []
    for plane in planes:
        with Image.fromarray(plane) as image:
            with image.resize(size,Image.Resampling.LANCZOS) as output:
                resized.append(np.asarray(output,dtype=np.float32).copy())
    result = np.stack(resized,axis=-1)
    result[...,3] = np.clip(result[...,3],0,1)
    np.divide(result[...,:3],result[...,3,None],out=result[...,:3],where=result[...,3,None]>1e-8)
    result[...,:3][result[...,3]<=1e-8] = 0
    return np.ascontiguousarray(np.clip(result,0,1),dtype=np.float32)


def decode_source(state: PhotoEditState, *, proxy=False, cache=None, cancelled=None):
    check_cancelled(cancelled)
    path = resolve_input(state)
    source_key = ["photo-edit-decode-v1",str(path.absolute()),fingerprint(path)]
    key = ImageCache.key([source_key,"proxy" if proxy else "full"])
    hit = cache.get(key) if cache else None
    if hit is not None:
        return hit[0],hit[1],path
    full_key = ImageCache.key([source_key,"full"])
    full = cache.get(full_key) if cache and proxy else None
    if full is None:
        width,height = probe_image(path)
        check_memory(width,height)
        check_cancelled(cancelled)
        decoded = decode_image(path,cancelled=cancelled)
        height,width = decoded.rgb.shape[:2]
        check_cancelled(cancelled)
        alpha = decoded.alpha if decoded.alpha is not None else np.ones((height,width),dtype=np.float32)
        rgba = np.ascontiguousarray(np.concatenate((decoded.rgb,alpha[...,None]),axis=2),dtype=np.float32)
        rgba = np.clip(rgba,0,1)
        if not np.isfinite(rgba).all():
            raise ValueError("解码结果含有无效像素")
        if fingerprint(path) != source_key[2]:
            raise EditConflict("照片在解码期间已变化")
        size = (width,height)
        if cache:
            cache.put(full_key,rgba,size)
    else:
        rgba,size = full
    if proxy:
        rgba = resize_proxy(rgba)
        if cache:
            cache.put(key,rgba,size,persist=True)
    return rgba,size,path


def render_recipe(state: PhotoEditState, *, proxy=True, before=False, cache=None, cancelled=None) -> RenderResult:
    state = PhotoEditState.from_dict(state.source,state.to_dict())
    with full_resolution_slot(cancelled):
        pixels,size,path = decode_source(state,proxy=proxy,cache=cache,cancelled=cancelled)
        check_cancelled(cancelled)
        recipe = state.current.recipe
        for step in recipe.steps:
            if step.enabled and step.kind == "colorLookup" and step.params.get("asset"):
                load_asset(state.source,step.params["asset"])
        signature = recipe.signature()
        key = ImageCache.key([ALGORITHM_VERSION,str(path.absolute()),fingerprint(path),signature,before,proxy])
        found = cache.get(key) if cache else None
        if found is not None:
            result = found[0]
        elif before:
            result = pixels
        else:
            if not proxy:
                check_memory(*size)
            result = render_pixels(pixels,recipe,pixel_scale=min(pixels.shape[1]/size[0],pixels.shape[0]/size[1]),
                                   asset_loader=lambda ref:load_asset(state.source,ref),cancelled=cancelled)
            check_cancelled(cancelled)
            if cache:
                cache.put(key,result,size,persist=proxy)
        check_cancelled(cancelled)
        # Revalidate inputs after long processing before publishing any result.
        _,camera_crop = resolve_input_geometry(state)
        return RenderResult(result,size,state.source,str(path),signature,before,
                            (result.shape[1],result.shape[0]) != size,camera_crop,rgb_histogram(result))


def rgb_histogram(rgba):
    channels = [np.zeros(256,dtype=np.int64) for _ in range(3)]
    samples = shadows = highlights = 0
    # Bound scratch memory even for full-resolution export/100% previews.
    rows = max(1,262144//max(1,rgba.shape[1]))
    for start in range(0,rgba.shape[0],rows):
        tile = rgba[start:start+rows]
        pixels = tile[...,:3][tile[...,3]>0]
        valid = pixels[np.isfinite(pixels).all(axis=1)]
        for i in range(3):
            channels[i] += np.histogram(valid[:,i],bins=256,range=(0,1))[0]
        samples += len(valid)
        shadows += int(np.any(valid<=0,axis=1).sum())
        highlights += int(np.any(valid>=1,axis=1).sum())
    return dict(channels=channels,samples=samples,shadows=shadows,highlights=highlights)
