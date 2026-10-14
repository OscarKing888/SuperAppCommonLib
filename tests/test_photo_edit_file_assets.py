import hashlib
from pathlib import Path
import shutil

from app_common.file_transactions import transfer_file_pairs
from app_common.photo_edit.assets import _publish,asset_path
from app_common.photo_edit.model import AdjustmentStep
from app_common.photo_edit.storage import RecipeStore,EDIT_FIELD
from app_common.exif_io.photo_meta import PhotoMetaDataXMP


def edited_photo(directory):
    directory.mkdir()
    source = directory/"白鹭.ARW"
    source.write_bytes(b"original raw untouched by file operations")
    cube = b"LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"
    digest = hashlib.sha256(cube).hexdigest()
    ref = {"sha256":digest,"path":f".superbird-assets/{digest}.cube"}
    _publish(source,ref,cube)
    state = RecipeStore().load(source)
    state.current.recipe.steps = [AdjustmentStep("colorLookup",dict(asset=ref,builtin="",tetrahedral=True,dither=False))]
    state.revision = RecipeStore().save(state)
    return source,ref


def test_copy_renames_recipe_identity_and_carries_shared_lut(tmp_path):
    source,ref = edited_photo(tmp_path/"source")
    target = tmp_path/"target"/"白鹭_2.ARW"
    transfer_file_pairs([(str(source),str(target)),(str(source.with_suffix(".xmp")),str(target.with_suffix(".xmp")))],action="copy")
    assert asset_path(target,ref).read_bytes()==asset_path(source,ref).read_bytes()
    store = RecipeStore()
    state = store.load(target)
    assert state.current.recipe.steps[0].params["asset"]==ref
    state.current.recipe.camera["exposure"] = 1
    store.save(state)
    envelope = PhotoMetaDataXMP().read_json_field(str(target),EDIT_FIELD)
    assert set(envelope["photos"])=={"白鹭_2.arw"}
    assert store.load(source).current.recipe.camera["exposure"]==0


def test_cut_keeps_lut_shared_by_other_source_photos(tmp_path):
    source,ref = edited_photo(tmp_path/"source")
    target = tmp_path/"target"/source.name
    transfer_file_pairs([(str(source),str(target)),(str(source.with_suffix(".xmp")),str(target.with_suffix(".xmp")))],action="cut")
    assert not source.exists() and target.exists()
    assert asset_path(source,ref).exists() and asset_path(target,ref).exists()
    assert RecipeStore().load(target).current.recipe.steps


def test_missing_lut_stops_before_moving_original(tmp_path):
    import pytest
    source,ref = edited_photo(tmp_path/"source")
    asset_path(source,ref).unlink()
    target = tmp_path/"target"/source.name
    with pytest.raises(FileNotFoundError):
        transfer_file_pairs([(str(source),str(target)),(str(source.with_suffix(".xmp")),str(target.with_suffix(".xmp")))],action="cut")
    assert source.exists() and source.with_suffix(".xmp").exists()
    assert not target.exists()


def test_plain_rename_adopts_only_matching_full_filename_extension(tmp_path):
    source,ref = edited_photo(tmp_path/"source")
    jpeg = source.with_suffix(".jpg")
    jpeg.write_bytes(b"jpeg pixels")
    jpeg_state = RecipeStore().load(jpeg)
    jpeg_state.current.recipe.camera["exposure"] = 2
    RecipeStore().save(jpeg_state)
    renamed = source.with_name("新名字.ARW")
    source.rename(renamed)
    source.with_suffix(".xmp").rename(renamed.with_suffix(".xmp"))
    state = RecipeStore().load(renamed)
    assert state.current.recipe.steps[0].kind=="colorLookup"
    assert state.current.recipe.camera["exposure"]==0
    RecipeStore().save(state)
    envelope = PhotoMetaDataXMP().read_json_field(str(renamed),EDIT_FIELD)
    assert "白鹭.jpg" in envelope["photos"]
