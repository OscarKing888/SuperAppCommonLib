"""Offline native/high-depth/export smoke check for source and frozen builds."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import tempfile
import traceback


def check():
    import numpy as np
    import tifffile
    from PIL import Image
    from .native import backend, render_pixels
    from .model import Recipe, AdjustmentStep
    from .storage import RecipeStore
    from .export import export_recipe
    from app_common.image_output_paths import ImageOutputOptions
    pixels = np.ones((24,32,4),np.float32)
    pixels[:,:,:3] = np.linspace(.02,.8,32,dtype=np.float32)[None,:,None]
    recipe = Recipe()
    recipe.camera["exposure"] = .4
    recipe.steps.append(AdjustmentStep("brightnessContrast",{"brightness":.1,"contrast":.15}))
    recipe = recipe.validated()
    result = render_pixels(pixels,recipe)
    assert np.isfinite(result).all() and not np.array_equal(result,pixels)
    with tempfile.TemporaryDirectory(prefix="photo-edit-check-") as folder:
        source = Path(folder)/"中文原图.tif"
        tifffile.imwrite(source,(pixels[:,:,:3]*65535).astype(np.uint16),photometric="rgb")
        original = source.read_bytes()
        store = RecipeStore()
        state = store.load(source)
        state.current.recipe = recipe
        state.revision = store.save(state)
        assert store.load(source).current.recipe.signature()==recipe.signature()
        outputs = []
        for fmt in ("tiff","png","jpeg"):
            path = export_recipe(state,ImageOutputOptions(format=fmt))
            with Image.open(path) as image:
                assert image.size==(32,24)
            if fmt=="tiff":
                assert tifffile.imread(path).dtype==np.uint16
            assert store.load(path).current.recipe.is_identity()
            outputs.append(fmt)
        assert source.read_bytes()==original
    return {"ok":True,"backend":json.loads(backend().info_json())["revision"],"formats":outputs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    args = parser.parse_args(argv)
    try:
        report = check()
    except Exception:
        report = {"ok":False,"error":traceback.format_exc()}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if report["ok"] else 1
