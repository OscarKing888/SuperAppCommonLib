from __future__ import annotations

from pathlib import Path

from app_common.exif_io.json_sidecar import JSON_SIDECAR_SUFFIX
from app_common.file_browser._panel import FileListPanel


def test_clipboard_entries_pair_xmp_and_json_sidecars(tmp_path: Path) -> None:
    photo = tmp_path / "GreenCheck.psd"
    xmp = tmp_path / "GreenCheck.xmp"
    json_sidecar = tmp_path / f"GreenCheck.psd{JSON_SIDECAR_SUFFIX}"
    photo.write_bytes(b"psd")
    xmp.write_text("xmp", encoding="utf-8")
    json_sidecar.write_text("{}", encoding="utf-8")

    entries = FileListPanel._clipboard_entries_from_urls([str(photo), str(xmp), str(json_sidecar)])

    assert len(entries) == 1
    assert entries[0]["source"] == str(photo)
    assert entries[0]["sidecars"] == [str(xmp), str(json_sidecar)]


def test_paste_sidecar_destinations_follow_target_source_name(tmp_path: Path) -> None:
    source = tmp_path / "GreenCheck.psd"
    json_sidecar = tmp_path / f"GreenCheck.psd{JSON_SIDECAR_SUFFIX}"
    dest_source = tmp_path / "copies" / "GreenCheck copy.psd"

    assert FileListPanel._sidecar_destination_for_paste(
        str(source),
        str(dest_source),
        str(json_sidecar),
    ) == str(dest_source) + JSON_SIDECAR_SUFFIX
