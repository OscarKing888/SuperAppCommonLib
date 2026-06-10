from __future__ import annotations

from pathlib import Path

from app_common.file_browser._panel import FileListPanel


def test_clipboard_entries_pair_xmp_sidecar(tmp_path: Path) -> None:
    photo = tmp_path / "GreenCheck.psd"
    xmp = tmp_path / "GreenCheck.xmp"
    photo.write_bytes(b"psd")
    xmp.write_text("xmp", encoding="utf-8")

    entries = FileListPanel._clipboard_entries_from_urls([str(photo), str(xmp)])

    assert len(entries) == 1
    assert entries[0]["source"] == str(photo)
    assert entries[0]["sidecars"] == [str(xmp)]


def test_paste_xmp_sidecar_destination_follows_target_source_name(tmp_path: Path) -> None:
    source = tmp_path / "GreenCheck.psd"
    xmp = tmp_path / "GreenCheck.xmp"
    dest_source = tmp_path / "copies" / "GreenCheck copy.psd"

    assert FileListPanel._sidecar_destination_for_paste(
        str(source),
        str(dest_source),
        str(xmp),
    ) == str(tmp_path / "copies" / "GreenCheck copy.xmp")
