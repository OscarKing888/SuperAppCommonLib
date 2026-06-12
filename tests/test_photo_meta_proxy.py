import os
from pathlib import Path

from app_common.exif_io.photo_meta import PhotoMetaDataProxy, PhotoMetaDataReportDB, PhotoMetaDataXMP
from app_common.exif_io.writer import invalidate_metadata_cache, read_batch_metadata
from app_common.report_db import PHOTO_COLUMNS, ReportDB


class _FakeExifMeta:
    def read(self, path: str) -> dict:
        return {"SourceFile": path, "XMP-xmp:Rating": "1"}

    def read_batch(self, paths: list[str]) -> dict:
        return {os.path.normpath(path): self.read(path) for path in paths}

    def write(self, path: str, fields: dict) -> bool:
        return True


class _FakeXmpMeta:
    def __init__(self, rating: str = "4") -> None:
        self.rating = rating
        self.writes: list[tuple[str, dict]] = []

    def read(self, path: str) -> dict:
        return {"SourceFile": path, "XMP-xmp:Rating": self.rating}

    def read_batch(self, paths: list[str]) -> dict:
        return {os.path.normpath(path): self.read(path) for path in paths}

    def write(self, path: str, fields: dict) -> bool:
        self.writes.append((path, dict(fields)))
        return True


class _FakeReportMeta:
    def __init__(self, rating: str = "2") -> None:
        self.rating = rating
        self.writes: list[tuple[str, dict]] = []

    def read(self, path: str) -> dict:
        return {"SourceFile": path, "rating": self.rating}

    def read_batch(self, paths: list[str]) -> dict:
        return {os.path.normpath(path): self.read(path) for path in paths}

    def write(self, path: str, fields: dict) -> bool:
        self.writes.append((path, dict(fields)))
        return True


class _EmptyMeta:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []

    def read(self, path: str) -> dict:
        return {}

    def read_batch(self, paths: list[str]) -> dict:
        return {os.path.normpath(path): {} for path in paths}

    def write(self, path: str, fields: dict) -> bool:
        self.writes.append((path, dict(fields)))
        return True


def _insert_report_row(directory: Path, row: dict) -> None:
    db = ReportDB(str(directory))
    try:
        db.insert_photo(row)
    finally:
        db.close()


def _all_photo_column_values(stem: str) -> dict:
    row: dict = {"filename": stem}
    real_columns = {
        "confidence",
        "head_sharp",
        "left_eye",
        "right_eye",
        "beak",
        "nima_score",
        "flight_conf",
        "focus_x",
        "focus_y",
        "adj_sharpness",
        "adj_topiq",
        "focal_length",
        "gps_latitude",
        "gps_longitude",
        "gps_altitude",
        "birdid_confidence",
        "rarity_index",
        "gbif_rarity_100",
    }
    int_columns = {
        "has_bird",
        "is_flying",
        "rating",
        "pick",
        "iso",
        "focal_length_35mm",
        "burst_id",
        "burst_position",
    }
    for column_name, _type_def, _default in PHOTO_COLUMNS:
        if column_name == "filename":
            continue
        if column_name in real_columns:
            row[column_name] = 0.0 if column_name == "confidence" else 0.75
        elif column_name in int_columns:
            row[column_name] = 0 if column_name == "has_bird" else 7
        else:
            row[column_name] = f"{column_name}-value"
    row["bird_species_cn"] = "白鹭"
    row["bird_species_en"] = "Little Egret"
    row["title"] = "Report Title"
    row["caption"] = "Report Caption"
    row["date_time_original"] = "2026:02:16 09:14:00"
    row["shutter_speed"] = "1/1250"
    row["aperture"] = "5.6"
    row["camera_model"] = "Sony ILCE-1"
    row["lens_model"] = "Sony FE 600mm"
    row["burst_id"] = 12
    row["burst_position"] = 3
    return row


def test_proxy_merges_sidecar_over_exif_and_normalizes_rating() -> None:
    proxy = PhotoMetaDataProxy(exif=_FakeExifMeta(), xmp=_FakeXmpMeta("4"))

    assert proxy.read("/tmp/img.jpg")["rating"] == 4
    assert proxy.read_batch(["/tmp/img.jpg"])[os.path.normpath("/tmp/img.jpg")]["rating"] == 4


def test_proxy_reads_report_db_after_sidecar_before_embedded_exif() -> None:
    proxy = PhotoMetaDataProxy(exif=_FakeExifMeta(), xmp=_FakeXmpMeta("4"), report_db=_FakeReportMeta("2"))
    assert proxy.read("/tmp/img.jpg")["rating"] == 4

    proxy = PhotoMetaDataProxy(exif=_FakeExifMeta(), xmp=_EmptyMeta(), report_db=_FakeReportMeta("2"))
    assert proxy.read("/tmp/img.jpg")["rating"] == 2
    assert proxy.read_batch(["/tmp/img.jpg"])[os.path.normpath("/tmp/img.jpg")]["rating"] == 2


def test_report_db_read_exposes_all_photo_columns_and_superpicky_aliases(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    report_row = _all_photo_column_values("img001")
    report_row["rarity_index"] = 8.5
    report_row["iucn_category"] = "LC"
    report_row["gbif_rarity_100"] = 72.25
    _insert_report_row(tmp_path, report_row)

    meta = PhotoMetaDataReportDB(report_root=str(tmp_path)).read(str(photo_path))

    for column_name, _type_def, _default in PHOTO_COLUMNS:
        if column_name == "filename":
            continue
        assert column_name in meta
        assert f"report.{column_name}" in meta
        assert f"XMP-superpicky:{column_name}" in meta
    assert meta["rarity_index"] == 8.5
    assert meta["report.iucn_category"] == "LC"
    assert meta["XMP-superpicky:gbif_rarity_100"] == 72.25


def test_report_db_read_remains_compatible_with_old_rows_missing_new_columns(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    old_row = {
        "filename": "img001",
        "rating": 2,
        "pick": 1,
        "bird_species_cn": "旧库鸟名",
    }

    meta = PhotoMetaDataReportDB(report_root=str(tmp_path), cache={"img001": old_row}).read(str(photo_path))

    assert meta["rating"] == 2
    assert meta["pick"] == 1
    assert meta["XMP-superpicky:bird_species_cn"] == "旧库鸟名"
    assert "rarity_index" not in meta
    assert "iucn_category" not in meta
    assert "gbif_rarity_100" not in meta


def test_proxy_write_routes_only_to_xmp_source() -> None:
    exif = _EmptyMeta()
    xmp = _FakeXmpMeta("4")
    report_db = _FakeReportMeta("2")
    proxy = PhotoMetaDataProxy(exif=exif, xmp=xmp, report_db=report_db)

    assert proxy.write("/tmp/img.jpg", {"rating": 5, "EXIF:ISO": 800})

    assert xmp.writes == [("/tmp/img.jpg", {"rating": 5, "EXIF:ISO": 800})]
    assert exif.writes == []
    assert report_db.writes == []


def test_proxy_writes_rating_pick_to_xmp_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    assert PhotoMetaDataProxy().write(str(photo_path), {"rating": 5, "pick": 1})

    assert (tmp_path / "img001.xmp").is_file()
    meta = PhotoMetaDataProxy().read(str(photo_path))
    assert meta.get("rating") == 5
    assert meta.get("pick") == 1


def test_proxy_writes_description_and_subject_to_xmp_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    assert PhotoMetaDataProxy().write(
        str(photo_path),
        {"XMP-dc:Description": "sidecar note", "XMP-dc:Subject": ["bird", "flight"]},
    )

    meta = PhotoMetaDataProxy().read(str(photo_path))
    assert meta.get("XMP-dc:Description") == "sidecar note"
    assert meta.get("Description") == "sidecar note"
    assert meta.get("XMP-dc:Subject") == "bird; flight"


def test_xmp_write_maps_legacy_exif_title_to_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    metadata = PhotoMetaDataProxy()

    assert metadata.write(str(photo_path), {"IFD0:XPTitle": "sidecar title"})

    meta = PhotoMetaDataProxy().read(str(photo_path))
    assert meta.get("XMP-dc:Title") == "sidecar title"
    assert meta.get("Title") == "sidecar title"


def test_read_batch_metadata_exposes_sidecar_description_aliases(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    assert PhotoMetaDataProxy().write(str(photo_path), {"XMP-dc:Description": "sidecar note"})
    invalidate_metadata_cache(str(photo_path))
    meta = read_batch_metadata([str(photo_path)]).get(os.path.normpath(str(photo_path)), {})

    assert meta.get("XMP-dc:Description") == "sidecar note"
    assert meta.get("XMP:Description") == "sidecar note"
    assert meta.get("Description") == "sidecar note"


def test_xmp_write_hydrates_report_db_photo_columns_when_bird_marker_missing(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    report_row = _all_photo_column_values("img001")
    _insert_report_row(tmp_path, report_row)

    assert PhotoMetaDataXMP().write_rating_pick(str(photo_path), rating=5)

    meta = PhotoMetaDataXMP().read(str(photo_path))
    assert meta.get("XMP-superpicky:bird_species_cn") == "白鹭"
    assert meta.get("bird_species_cn") == "白鹭"
    assert meta.get("report.bird_species_cn") == "白鹭"
    assert meta.get("XMP-dc:Title") == "白鹭"
    assert meta.get("XMP-superpicky:confidence") == "0.0"
    assert meta.get("XMP-superpicky:has_bird") == "0"
    assert meta.get("XMP-superpicky:burst_id") == "12"
    assert meta.get("XMP-superpicky:original_path") == "original_path-value"
    assert meta.get("XMP-superpicky:rarity_index") == "0.75"
    assert meta.get("XMP-superpicky:iucn_category") == "iucn_category-value"
    assert meta.get("XMP-superpicky:gbif_rarity_100") == "0.75"
    assert meta.get("XMP-superpicky:rating") == "5"
    assert meta.get("XMP-xmp:Rating") == "5"


def test_xmp_write_does_not_hydrate_when_bird_marker_exists(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    _insert_report_row(tmp_path, _all_photo_column_values("img001"))

    assert PhotoMetaDataXMP().write_title(str(photo_path), "Manual Bird")

    meta = PhotoMetaDataXMP().read(str(photo_path))
    assert meta.get("XMP-dc:Title") == "Manual Bird"
    assert "XMP-superpicky:confidence" not in meta
    assert "XMP-superpicky:bird_species_cn" not in meta


def test_xmp_report_hydration_preserves_existing_sidecar_values(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    (tmp_path / "img001.xmp").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description
      rdf:about=""
      xmlns:superpicky="https://superbirdtools.local/xmp/superpicky/1.0/">
      <superpicky:confidence>0.42</superpicky:confidence>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
""",
        encoding="utf-8",
    )
    report_row = _all_photo_column_values("img001")
    report_row["confidence"] = 0.99
    report_row["rating"] = 2
    _insert_report_row(tmp_path, report_row)

    assert PhotoMetaDataXMP().write_rating_pick(str(photo_path), rating=5)

    meta = PhotoMetaDataXMP().read(str(photo_path))
    assert meta.get("XMP-superpicky:confidence") == "0.42"
    assert meta.get("XMP-superpicky:rating") == "5"
    assert meta.get("XMP-superpicky:bird_species_cn") == "白鹭"


def test_xmp_superpicky_fields_read_back_as_raw_report_columns(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    (tmp_path / "img001.xmp").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description
      rdf:about=""
      xmlns:superpicky="https://superbirdtools.local/xmp/superpicky/1.0/"
      superpicky:has_bird="0"
      superpicky:confidence="0.88"
      superpicky:burst_id="12" />
  </rdf:RDF>
</x:xmpmeta>
""",
        encoding="utf-8",
    )

    meta = PhotoMetaDataXMP().read(str(photo_path))
    assert meta.get("XMP-superpicky:has_bird") == "0"
    assert meta.get("has_bird") == "0"
    assert meta.get("report.has_bird") == "0"
    assert meta.get("confidence") == "0.88"
    assert meta.get("burst_id") == "12"
