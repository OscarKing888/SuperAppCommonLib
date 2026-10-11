import os

from app_common.exif_io import writer


def test_same_stem_invalidation_drops_raw_and_jpeg_records_of_one_sidecar(tmp_path) -> None:
    folder = str(tmp_path / "鸟片")
    jpeg, raw = os.path.join(folder, "白鹭_A.JPG"), os.path.join(folder, "白鹭_a.arw")
    kept = [os.path.join(folder, "白鹭_A2.jpg"), str(tmp_path / "白鹭_A.jpg")]
    for path in (jpeg, raw, *kept):
        writer.inject_metadata_cache(path, {"XMP-dc:Title": "白鹭"})
    try:
        writer.invalidate_same_stem_metadata_cache([jpeg])
        with writer._METADATA_CACHE_LOCK:
            cached = set(writer._METADATA_CACHE)
        assert os.path.normpath(jpeg) not in cached and os.path.normpath(raw) not in cached
        assert {os.path.normpath(p) for p in kept} <= cached
    finally:
        writer.invalidate_metadata_cache(kept)
