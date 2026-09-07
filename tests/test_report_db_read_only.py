from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from app_common.report_db import ReportDB


def _legacy_report(tmp_path: Path, layout: str) -> tuple[Path, Path, Path]:
    root = tmp_path / "中文#图库"
    root.mkdir()
    photo = root / "白鹭.jpg"
    photo.write_bytes(b"original photo")
    db_path = root / "report.db" if layout == "root" else root / ".superpicky" / "report.db"
    db_path.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute('''CREATE TABLE photos (
            filename TEXT PRIMARY KEY, rating INTEGER, bird_species_cn TEXT,
            caption TEXT, current_path TEXT, temp_jpeg_path TEXT)''')
        connection.execute("INSERT INTO photos VALUES (?, ?, ?, ?, ?, ?)", (
            photo.stem, 3, "白鹭", "中文旧版备注", str(photo), ".superpicky/previews/白鹭.jpg",
        ))
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    return root, db_path, photo


@pytest.mark.parametrize("layout", ["root", "superpicky"])
@pytest.mark.parametrize("entry_point", ["directory", "db_path", "constructor"])
def test_read_only_open_preserves_legacy_schema_and_bytes(tmp_path, monkeypatch, layout, entry_point) -> None:
    root, db_path, photo = _legacy_report(tmp_path, layout)
    before_bytes = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns
    before_files = set(db_path.parent.iterdir())
    statements = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    if entry_point == "directory":
        db = ReportDB.open_if_exists(str(root))
    elif entry_point == "db_path":
        db = ReportDB.open_db_path_if_exists(str(db_path))
    else:
        db = ReportDB(str(root), db_path_override=str(db_path), read_only=True)
    assert db is not None
    try:
        assert db.count() == 1
        row = db.get_photo(photo.stem)
        assert row["rating"] == 3
        assert row["current_path"] == str(photo)
        assert row["temp_jpeg_path"] == ".superpicky/previews/白鹭.jpg"
        assert db.get_all_photos() == [row]
        assert db.get_meta("schema_version") == "1"
        assert len(db._conn.execute("PRAGMA table_info(photos)").fetchall()) == 6
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db._conn.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        db.close()
    assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "BEGIN"))
                   for sql in statements)
    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime
    assert set(db_path.parent.iterdir()) == before_files


def test_read_only_connection_rejects_writes_at_sqlite_level(tmp_path) -> None:
    root, db_path, _photo = _legacy_report(tmp_path, "root")
    original = db_path.read_bytes()
    with ReportDB.open_if_exists(str(root)) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.set_meta("schema_version", "changed")
        # The URI remains read-only even if a caller disables query_only.
        db._conn.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db._conn.execute("UPDATE photos SET rating=5")
    assert db_path.read_bytes() == original


def test_report_metadata_and_xmp_hydration_do_not_upgrade_the_source_db(tmp_path) -> None:
    root, db_path, photo = _legacy_report(tmp_path, "superpicky")
    original_db = db_path.read_bytes()
    original_photo = photo.read_bytes()
    metadata = PhotoMetaDataReportDB(report_root=str(root)).read(str(photo))
    assert metadata["rating"] == 3
    assert metadata["XMP-dc:Title"] == "白鹭"
    assert metadata["XMP-dc:Description"] == "中文旧版备注"
    assert PhotoMetaDataXMP().write_subjects(str(photo), ["飞行"])
    sidecar_metadata = PhotoMetaDataXMP().read(str(photo))
    assert sidecar_metadata["XMP-dc:Title"] == "白鹭"
    assert sidecar_metadata["XMP-dc:Description"] == "中文旧版备注"
    assert db_path.read_bytes() == original_db
    assert photo.read_bytes() == original_photo


def test_live_wal_updates_remain_visible_without_reader_checkpoint(tmp_path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    producer = ReportDB(str(tmp_path))
    try:
        producer._conn.execute("PRAGMA wal_autocheckpoint=0")
        producer.insert_photo({"filename": photo.stem, "rating": 1})
        db_path = Path(producer.db_path)
        initial_db = db_path.read_bytes()
        initial_mtime = db_path.stat().st_mtime_ns
        provider = PhotoMetaDataReportDB(report_root=str(tmp_path))
        assert provider.read(str(photo))["rating"] == 1
        assert producer.update_photo(photo.stem, {"rating": 5})
        # The producer has committed only to WAL. A read-only open must not
        # ignore those records or reuse indexes keyed only by the main file.
        assert db_path.read_bytes() == initial_db
        assert db_path.stat().st_mtime_ns == initial_mtime
        with ReportDB.open_if_exists(str(tmp_path)) as reader:
            assert reader.get_photo(photo.stem)["rating"] == 5
        assert provider.read(str(photo))["rating"] == 5
        assert db_path.read_bytes() == initial_db
        assert db_path.stat().st_mtime_ns == initial_mtime
    finally:
        producer.close()


def test_read_only_open_never_creates_a_missing_library(tmp_path) -> None:
    missing = tmp_path / "missing"
    assert ReportDB.open_if_exists(str(missing)) is None
    assert ReportDB.open_db_path_if_exists(str(missing / "report.db")) is None
    with pytest.raises(FileNotFoundError):
        ReportDB(str(missing), read_only=True)
    assert not missing.exists()


def test_explicit_producer_constructor_remains_writable(tmp_path) -> None:
    with ReportDB(str(tmp_path)) as producer:
        producer.insert_photo({"filename": "photo", "rating": 1})
        assert producer.update_photo("photo", {"rating": 5})
        assert producer.get_photo("photo")["rating"] == 5
