"""Recipe export produces baked pixels and an inactive provenance snapshot."""
from __future__ import annotations

from pathlib import Path
from functools import partial
import xml.etree.ElementTree as ET
import numpy as np

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.xmp_sidecar import _photo_descriptions
from app_common.image_export import export_pixels, copy_output_metadata
from app_common.image_output_paths import ImageOutputOptions, allocate_outputs, validate_output_options
from app_common.image_processing_types import check_cancelled
from .assets import copy_recipe_assets
from .model import canonical_json
from .pipeline import render_recipe, resolve_input
from .storage import EDIT_FIELD, BAKED_FIELD, PhotoEditState

NS = "https://superbirdtools.local/xmp/superpicky/1.0/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
TIFF = "http://ns.adobe.com/tiff/1.0/"
EXIF = "http://ns.adobe.com/exif/1.0/"
XMP = "http://ns.adobe.com/xap/1.0/"
CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"


def _prepare(state, staged, width, height, camera_crop=None):
    metadata = PhotoMetaDataXMP()
    source_sidecar = metadata.sidecar_path_for(state.source)
    tree = ET.parse(source_sidecar) if source_sidecar.exists() else metadata._new_xmp_tree()
    metadata._hydrate_report_db_fields_if_needed(state.source, tree)
    descriptions = _photo_descriptions(tree.getroot(),state.source,sidecar_path=str(source_sidecar))
    if not descriptions:
        raise ValueError("源 XMP 没有匹配照片的描述，未导出不完整元数据")
    # An exported image already includes the processing. Do not copy live edit
    # instructions to a new photo and apply them for a second time on reopen.
    for description in descriptions:
        for key in list(description.attrib):
            if key.startswith("{"+CRS+"}"):
                del description.attrib[key]
        for node in list(description):
            if node.tag.startswith("{"+CRS+"}"):
                description.remove(node)
        description.set("{"+RDF+"}about","")
    for key in (f"{{{NS}}}{EDIT_FIELD}",f"{{{XMP}}}Thumbnails",f"{{{TIFF}}}BitsPerSample",f"{{{TIFF}}}Compression"):
        metadata._remove_property(descriptions,key)
    input_record = state.current.to_dict()
    recipe = input_record.pop("recipe")
    snapshot = dict(schema=1,baked=True,source=str(Path(state.source).resolve()),sourceFingerprint=state.fingerprint,
                    input=input_record,recipe=recipe,cameraCrop=camera_crop)
    for uri,key,value in ((NS,BAKED_FIELD,canonical_json(snapshot)),(TIFF,"Orientation",1),
                          (TIFF,"ImageWidth",width),(TIFF,"ImageLength",height),
                          (EXIF,"PixelXDimension",width),(EXIF,"PixelYDimension",height),
                          (EXIF,"ColorSpace",1),(XMP,"CreatorTool","SuperViewer photo edit / PhotoCraft")):
        metadata._replace_text_node(descriptions,f"{{{uri}}}{key}",str(value))
    tree.write(staged,encoding="utf-8",xml_declaration=True)


def export_recipe(state: PhotoEditState, options: ImageOutputOptions | None = None, *, destination=None,
                  cache=None, cancelled=None):
    state = PhotoEditState.from_dict(state.source,state.to_dict())
    options = options or ImageOutputOptions()
    validate_output_options(options)
    path = Path(destination or allocate_outputs([state.source],options)[0])
    allowed = {"tiff":{ ".tif",".tiff"},"jpeg":{ ".jpg",".jpeg"},"png":{ ".png"}}
    if options.format not in allowed or path.suffix.lower() not in allowed[options.format]:
        raise ValueError("成片扩展名与所选输出格式不匹配")
    result = render_recipe(state,proxy=False,cache=cache,cancelled=cancelled)
    check_cancelled(cancelled)
    copy_recipe_assets(state.source,path,state.current.recipe)
    alpha = result.rgba[...,3]
    if np.all(alpha==1):
        alpha = None
    export_pixels(state.source,path,result.rgba[...,:3],alpha,options.format,
                  prepare_sidecar=lambda staged,w,h:_prepare(state,staged,w,h,result.camera_crop),
                  cancelled=cancelled,validate_source=lambda:resolve_input(state),
                  metadata_writer=partial(copy_output_metadata,strip_edit_settings=True))
    return str(path)
