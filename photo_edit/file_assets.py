"""Carry portable LUTs alongside photo/XMP bundles without moving shared assets."""
from pathlib import Path

from app_common.exif_io.xmp_sidecar import find_same_stem_xmp_sidecar
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .assets import copy_recipe_assets
from .model import Recipe
from .storage import RecipeStore, EDIT_FIELD, BAKED_FIELD


def copy_photo_edit_assets(source, destination):
    """Call before moving the source. A later cancelled transfer leaves reusable
    content-addressed assets, never deleted originals or overwritten resources.
    Unknown non-editing XMP passes through unchanged, including malformed XMP
    files that the pre-existing file-operation workflow allows users to move.
    """
    source,destination = Path(source),Path(destination)
    if source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        return
    sidecar = find_same_stem_xmp_sidecar(str(source))
    if not sidecar:
        return
    with Path(sidecar).open("rb") as stream:
        data = stream.read(64*1024*1024+1)
    if not any(name.encode("ascii") in data for name in (EDIT_FIELD,BAKED_FIELD)):
        return
    if len(data)>64*1024*1024:
        raise ValueError("编辑 XMP 过大，未遗漏 LUT 资源后移动照片")
    metadata = PhotoMetaDataXMP()
    envelope = RecipeStore._envelope(metadata.read_json_field(str(source),EDIT_FIELD))
    for record in envelope["photos"].values():
        for entry in record["inputs"].values():
            copy_recipe_assets(source,destination,Recipe.from_dict(entry["recipe"]))
    baked = metadata.read_json_field(str(source),BAKED_FIELD)
    if baked:
        copy_recipe_assets(source,destination,Recipe.from_dict(baked["recipe"]))
