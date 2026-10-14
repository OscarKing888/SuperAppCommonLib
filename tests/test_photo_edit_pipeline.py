from pathlib import Path
import numpy as np
from PIL import Image
import pytest
import tifffile

from app_common.photo_edit import pipeline
from app_common.photo_edit.cache import ImageCache
from app_common.photo_edit.model import Recipe, AdjustmentStep
from app_common.photo_edit.storage import RecipeStore, EditConflict
from app_common.photo_edit.assets import import_lut, load_asset, copy_recipe_assets
from app_common.image_processing_types import ImageProcessingCancelled


@pytest.fixture
def source(tmp_path):
    path = tmp_path/"中文高位深.tif"
    values = np.linspace(.123,.456,1200,dtype=np.float32)
    image = np.broadcast_to(values[None,:,None],(32,1200,3))
    tifffile.imwrite(path,np.rint(image*65535).astype(np.uint16),photometric="rgb",metadata=None)
    return path


def test_proxy_and_full_keep_high_depth_and_source_identity(source):
    pytest.importorskip("app_common.photo_edit._native")
    state = RecipeStore().load(source)
    cache = ImageCache()
    result = pipeline.render_recipe(state,cache=cache)
    assert result.approximate and result.full_size==(1200,32)
    assert result.rgba.shape[1]==900 and result.source==str(source)
    full = pipeline.render_recipe(state,proxy=False,cache=cache)
    assert not full.approximate
    assert np.unique(full.rgba[0,:,0]).size==1200
    assert full.rgba.dtype==np.float32
    assert np.all(full.rgba[...,3]==1)


def test_proxy_cache_skips_redecode_but_file_change_invalidates(source,monkeypatch):
    state = RecipeStore().load(source)
    cache = ImageCache()
    first,_,_ = pipeline.decode_source(state,proxy=True,cache=cache)
    monkeypatch.setattr(pipeline,"decode_image",lambda *a,**k:pytest.fail("decoded cached input"))
    second,_,_ = pipeline.decode_source(state,proxy=True,cache=cache)
    assert first is second
    source.write_bytes(b"changed")
    with pytest.raises(EditConflict):
        pipeline.decode_source(state,proxy=True,cache=cache)


def test_unverified_denoise_cannot_become_editing_input(source,tmp_path):
    state = RecipeStore().load(source)
    output = tmp_path/"foreign_denoised.tif"
    output.write_bytes(source.read_bytes())
    state.use_denoised(output)
    with pytest.raises(EditConflict,match="来源记录"):
        pipeline.resolve_input(state)


def test_cancelled_decode_never_reads_pixels_and_slot_is_released(source,monkeypatch):
    state = RecipeStore().load(source)
    monkeypatch.setattr(pipeline,"decode_image",lambda *a,**k:pytest.fail("decoded cancelled request"))
    with pytest.raises(ImageProcessingCancelled):
        pipeline.render_recipe(state,cancelled=lambda:True)
    with pipeline.full_resolution_slot():
        pass


def test_memory_limit_does_not_silently_downsample(source,monkeypatch):
    state = RecipeStore().load(source)
    monkeypatch.setattr(pipeline,"available_memory",lambda:1)
    with pytest.raises(MemoryError):
        pipeline.decode_source(state,proxy=False)


def test_cache_is_bounded_persistent_and_disposable(tmp_path):
    cache = ImageCache(tmp_path/"cache",memory_bytes=1024,disk_bytes=4096)
    keys = [cache.key([n]) for n in range(4)]
    for key in keys:
        cache.put(key,np.ones((8,8,4),np.float32),(8,8),persist=True)
    assert len(cache._items)==1
    assert cache._bytes==1024
    second = ImageCache(tmp_path/"cache",memory_bytes=1024)
    assert second.get(keys[-1])[1]==(8,8)
    cache.clear(disk=True)
    assert not list((tmp_path/"cache").glob("*.npz"))


def test_histogram_excludes_transparent_pixels_and_reports_clipping():
    rgba = np.array([[[0,1,.5,1],[1,1,1,0],[.25,.25,.25,.5]]],dtype=np.float32)
    result = pipeline.rgb_histogram(rgba)
    assert result["samples"]==2
    assert result["shadows"]==result["highlights"]==1
    assert all(c.sum()==2 for c in result["channels"])


def test_lut_resource_is_portable_and_missing_or_changed_file_fails(source,tmp_path):
    native = pytest.importorskip("app_common.photo_edit._native")
    import json
    data = json.loads(native.load_lut_json("",b"","cool"))["cube"]
    incoming = tmp_path/"调色.cube"
    incoming.write_text(data,encoding="utf-8")
    reference = import_lut(source,incoming)
    name,contents = load_asset(source,reference)
    assert name.endswith(".cube")
    recipe = Recipe(steps=[AdjustmentStep("colorLookup",dict(asset=reference,builtin="",tetrahedral=True,dither=False))])
    destination = tmp_path/"moved"/source.name
    copy_recipe_assets(source,destination,recipe)
    assert load_asset(destination,reference)[1]==contents
    state = RecipeStore().load(source)
    state.current.recipe = recipe
    cache = ImageCache()
    pipeline.render_recipe(state,cache=cache)
    (source.parent/reference["path"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError,match="已变化"):
        pipeline.render_recipe(state,cache=cache)
def test_denoised_geometry_travels_with_actual_rendered_pixels(source,tmp_path):
    from image_denoise.export import _prepare_sidecar
    output = tmp_path/"denoised"/"verified.tif"
    output.parent.mkdir()
    output.write_bytes(source.read_bytes())
    crop = (.03,.04,.97,.96)
    _prepare_sidecar(source,output.with_suffix(".xmp"),1200,32,camera_crop=crop)
    state = RecipeStore().load(source)
    state.use_denoised(output)
    result = pipeline.render_recipe(state,proxy=True)
    assert result.camera_crop==crop and result.input_path==str(output)


def test_disk_cache_write_failure_keeps_rendering_in_memory(tmp_path,monkeypatch):
    cache = ImageCache(tmp_path/"cache")
    monkeypatch.setattr("app_common.photo_edit.cache.tempfile.mkstemp",lambda **kw:(_ for _ in ()).throw(OSError("full")))
    key = cache.key(["input"])
    pixels = np.ones((8,8,4),np.float32)
    cache.put(key,pixels,(8,8),persist=True)
    assert cache.get(key)[0] is pixels
