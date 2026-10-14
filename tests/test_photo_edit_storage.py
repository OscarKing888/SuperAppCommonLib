from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import xml.etree.ElementTree as ET
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.photo_edit.storage import EDIT_FIELD, RecipeStore, RecoveryStore, EditConflict


def photo(tmp_path,name="翠鸟.ARW"):
    result = tmp_path/name
    result.write_bytes(b"source pixels never modified")
    return result


def test_chinese_roundtrip_preserves_unknown_metadata_and_different_resources(tmp_path):
    source = photo(tmp_path)
    sidecar = source.with_suffix(".xmp")
    sidecar.write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns:custom="https://example.invalid/custom/" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <rdf:RDF><rdf:Description rdf:about="" custom:Keep="中文保留"><dc:title><rdf:Alt>
      <rdf:li xml:lang="x-default">池塘边的翠鸟</rdf:li></rdf:Alt></dc:title></rdf:Description>
      <rdf:Description rdf:about="unrelated.jpg" custom:Keep="另一资源"/></rdf:RDF></x:xmpmeta>''',encoding="utf-8")
    original = source.read_bytes()
    store = RecipeStore()
    state = store.load(source)
    state.current.recipe.camera["exposure"] = 1.25
    state.revision = store.save(state)
    loaded = store.load(source)
    assert loaded.current.recipe.camera["exposure"] == 1.25
    assert loaded.revision == state.revision
    metadata = PhotoMetaDataXMP().read(str(source))
    assert metadata["XMP-dc:Title"] == "池塘边的翠鸟"
    xml = ET.parse(sidecar).getroot()
    values = [n.get("{https://example.invalid/custom/}Keep") for n in xml.iter()]
    assert "中文保留" in values and "另一资源" in values
    assert source.read_bytes() == original


def test_same_stem_raw_jpeg_and_rating_writes_merge(tmp_path):
    raw,jpg = photo(tmp_path,"001.ARW"),photo(tmp_path,"001.JPG")
    store = RecipeStore()
    a,b = store.load(raw),store.load(jpg)
    a.current.recipe.camera["exposure"] = 1
    b.current.recipe.camera["exposure"] = -1
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(store.save,a),executor.submit(store.save,b),
                   executor.submit(PhotoMetaDataXMP().write_rating_pick,str(raw),rating=5)]
        assert all(f.result() for f in futures)
    assert store.load(raw).current.recipe.camera["exposure"] == 1
    assert store.load(jpg).current.recipe.camera["exposure"] == -1
    assert PhotoMetaDataXMP().read(str(raw))["XMP-xmp:Rating"] == "5"


def test_stale_editor_cannot_overwrite_new_recipe(tmp_path):
    source = photo(tmp_path)
    store = RecipeStore()
    a,b = store.load(source),store.load(source)
    a.current.recipe.camera["exposure"] = 2
    a.revision = store.save(a)
    b.current.recipe.camera["exposure"] = -2
    with pytest.raises(EditConflict):
        store.save(b)
    assert store.load(source).current.recipe.camera["exposure"] == 2


def test_malformed_sidecar_and_missing_source_are_not_replaced(tmp_path):
    source = photo(tmp_path)
    store = RecipeStore()
    state = store.load(source)
    sidecar = source.with_suffix(".xmp")
    sidecar.write_bytes(b"<not-complete")
    with pytest.raises(ET.ParseError):
        store.load(source)
    with pytest.raises(ET.ParseError):
        store.save(state)
    assert sidecar.read_bytes() == b"<not-complete"
    source.unlink()
    with pytest.raises(FileNotFoundError):
        store.save(state)


def test_external_sidecar_change_during_transaction_is_retained(tmp_path):
    source = photo(tmp_path)
    metadata = PhotoMetaDataXMP()
    def external_writer(value):
        source.with_suffix(".xmp").write_bytes(b"externally written bytes")
        return {"hello":"中文"}
    with pytest.raises(RuntimeError,match="externally"):
        metadata.update_json_field(str(source),EDIT_FIELD,external_writer)
    assert source.with_suffix(".xmp").read_bytes() == b"externally written bytes"


def test_input_versions_are_distinct_and_recovery_keeps_failed_draft(tmp_path):
    source = photo(tmp_path)
    denoised = photo(tmp_path,"001_denoised.tif")
    store = RecipeStore()
    state = store.load(source)
    state.current.recipe.camera["exposure"] = 1
    state.use_denoised(denoised)
    key = state.active
    assert state.current.recipe.camera["exposure"] == 0
    state.current.recipe.camera["exposure"] = -1
    recovery = RecoveryStore(tmp_path/"user-state")
    recovery.save(state)
    restored = recovery.load(source)
    assert restored == state
    state.revision = store.save(state)
    loaded = store.load(source)
    assert loaded.active == key
    assert loaded.inputs["original"].recipe.camera["exposure"] == 1
    assert loaded.current.recipe.camera["exposure"] == -1
    denoised.write_bytes(b"different denoise version")
    loaded.use_denoised(denoised)
    assert loaded.active != key
    assert loaded.inputs[key].recipe.camera["exposure"] == -1
    assert loaded.current.recipe.camera["exposure"] == 0
    recovery.remove(source)
    assert recovery.load(source) is None


def test_changed_source_does_not_reuse_old_parameters(tmp_path):
    source = photo(tmp_path)
    store = RecipeStore()
    state = store.load(source)
    state.revision = store.save(state)
    source.write_bytes(b"replacement image")
    with pytest.raises(EditConflict):
        store.load(source)
    with pytest.raises(EditConflict):
        store.save(state)
