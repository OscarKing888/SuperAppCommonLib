"""Real high-depth exports, baked recipes, metadata and safe publication."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import imagecodecs
import numpy as np
import pytest
import tifffile

from app_common import image_export
from app_common.exif_io import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool_utf8_args
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.image_output_paths import ImageOutputOptions, allocate_outputs, output_directory
from app_common.photo_edit.export import export_recipe, CRS
from app_common.photo_edit.model import AdjustmentStep
from app_common.photo_edit.storage import RecipeStore, EDIT_FIELD, BAKED_FIELD


def exiftool(*args):
    result = run_exiftool_utf8_args(get_exiftool_executable_path(),list(args),timeout=30,
                                  capture_output=True,text=True,encoding="utf-8")
    assert result.returncode==0,result.stderr
    return result.stdout


@pytest.fixture
def photo(tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    path = tmp_path/"中文 原片.tif"
    gradient = np.broadcast_to(np.linspace(6000,30000,1200,dtype=np.uint16)[None,:,None],(8,1200,3)).copy()
    tifffile.imwrite(path,gradient,photometric="rgb",metadata=None)
    exiftool("-overwrite_original","-XMP-crs:Exposure2012=1.2","-Make=SONY",str(path))
    path.with_suffix(".XMP").write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:Exposure2012="2">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">白鹭调色</rdf:li></rdf:Alt></dc:title>
</rdf:Description><rdf:Description rdf:about="https://example.org/other" xmlns:c="urn:custom" c:value="保留"/>
</rdf:RDF></x:xmpmeta>''',encoding="utf-8")
    state = RecipeStore().load(path)
    state.current.recipe.steps.append(AdjustmentStep("invert"))
    state.revision = RecipeStore().save(state)
    return path,state,gradient


@pytest.mark.parametrize("format",["tiff","jpeg","png"])
def test_real_export_bakes_once_preserves_chinese_and_originals(photo,format):
    path,state,original = photo
    unchanged = {p:p.read_bytes() for p in (path,path.with_suffix(".XMP"))}
    output = Path(export_recipe(state,ImageOutputOptions(format=format)))
    assert output.parent==path.parent/"edited"
    meta = PhotoMetaDataXMP()
    baked = meta.read_json_field(str(output),BAKED_FIELD)
    assert baked["baked"] and baked["recipe"]==state.current.recipe.to_dict()
    assert baked["sourceFingerprint"]==state.fingerprint
    assert meta.read_json_field(str(output),EDIT_FIELD) is None
    assert RecipeStore().load(output).current.recipe.is_identity()
    tree = ET.parse(output.with_suffix(".xmp"))
    assert not any(n.tag.startswith("{"+CRS+"}") or any(k.startswith("{"+CRS+"}") for k in n.attrib) for n in tree.iter())
    assert "保留" in output.with_suffix(".xmp").read_text("utf-8")
    embedded = json.loads(exiftool("-j","-G1","-n",str(output)))[0]
    assert embedded["XMP-dc:Title"]=="白鹭调色"
    assert not any(key.startswith("XMP-crs:") for key in embedded)
    assert embedded["XMP-tiff:Orientation"]==1
    if format != "jpeg":
        pixels = tifffile.imread(output) if format=="tiff" else imagecodecs.png_decode(output.read_bytes())
        assert pixels.dtype==np.uint16 and np.unique(pixels[0,:,0]).size>1000
        np.testing.assert_allclose(pixels.astype(float),65535-original,atol=4)
    for p,data in unchanged.items():
        assert p.read_bytes()==data


def test_export_collision_does_not_touch_existing_files(photo):
    path,state,_ = photo
    existing = path.parent/"edited"/"已有.tif"
    existing.parent.mkdir()
    existing.with_suffix(".XMP").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        export_recipe(state,destination=existing)
    assert existing.with_suffix(".XMP").read_bytes()==b"keep"
    assert not existing.exists()
    first,second = allocate_outputs([path,path],ImageOutputOptions())
    assert first!=second


def test_failed_image_publication_rolls_back_owned_sidecar(photo,monkeypatch):
    path,state,_ = photo
    publish = image_export._publish_new
    def fail_image(staged,target):
        if target.suffix==".tif":
            raise OSError("模拟磁盘失败")
        return publish(staged,target)
    monkeypatch.setattr(image_export,"_publish_new",fail_image)
    with pytest.raises(OSError,match="磁盘"):
        export_recipe(state)
    assert not list((path.parent/"edited").iterdir())


@pytest.mark.parametrize("mode,relative",[("source_subdir","photos/edited"),("source_dir","photos"),
                                         ("sibling_dir","photos_edited"),("fixed","chosen"),("ask","chosen")])
def test_shared_output_modes(tmp_path,mode,relative):
    source = tmp_path/"photos"/"a.ARW"
    options = ImageOutputOptions(output_mode=mode,output_directory=str(tmp_path/"chosen"))
    assert output_directory(source,options)==tmp_path/relative
