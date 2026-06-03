from pathlib import Path

from app_common.exif_io.json_sidecar import JSON_SIDECAR_SUFFIX
from app_common.file_utils import move_to_trash


def test_move_to_trash_moves_sibling_xmp_sidecar_to_superpicky_deleted(tmp_path: Path) -> None:
    root = tmp_path / "library"
    image_dir = root / "day1"
    superpicky = root / ".superpicky"
    image_dir.mkdir(parents=True)
    superpicky.mkdir()
    photo = image_dir / "DSC06705.ARW"
    sidecar = image_dir / "DSC06705.xmp"
    photo.write_bytes(b"raw")
    sidecar.write_text("xmp", encoding="utf-8")

    assert move_to_trash(str(photo))

    assert not photo.exists()
    assert not sidecar.exists()
    assert (superpicky / "deleted" / "day1" / "DSC06705.ARW").read_bytes() == b"raw"
    assert (superpicky / "deleted" / "day1" / "DSC06705.xmp").read_text(encoding="utf-8") == "xmp"


def test_move_to_trash_moves_json_sidecar_to_superpicky_deleted(tmp_path: Path) -> None:
    root = tmp_path / "library"
    image_dir = root / "day1"
    superpicky = root / ".superpicky"
    image_dir.mkdir(parents=True)
    superpicky.mkdir()
    photo = image_dir / "GreenCheck.psd"
    json_sidecar = image_dir / f"GreenCheck.psd{JSON_SIDECAR_SUFFIX}"
    photo.write_bytes(b"psd")
    json_sidecar.write_text('{"metadata": {"rating": 4}}', encoding="utf-8")

    assert move_to_trash(str(photo))

    assert not photo.exists()
    assert not json_sidecar.exists()
    assert (superpicky / "deleted" / "day1" / "GreenCheck.psd").read_bytes() == b"psd"
    assert (
        superpicky / "deleted" / "day1" / f"GreenCheck.psd{JSON_SIDECAR_SUFFIX}"
    ).read_text(encoding="utf-8") == '{"metadata": {"rating": 4}}'


def test_move_to_trash_keeps_source_and_sidecar_conflict_suffix_in_sync(tmp_path: Path) -> None:
    root = tmp_path / "library"
    image_dir = root / "day1"
    deleted_dir = root / ".superpicky" / "deleted" / "day1"
    image_dir.mkdir(parents=True)
    deleted_dir.mkdir(parents=True)
    (deleted_dir / "DSC06705.ARW").write_bytes(b"old")
    photo = image_dir / "DSC06705.ARW"
    sidecar = image_dir / "DSC06705.xmp"
    photo.write_bytes(b"raw")
    sidecar.write_text("xmp", encoding="utf-8")

    assert move_to_trash(str(photo))

    assert (deleted_dir / "DSC06705.ARW").read_bytes() == b"old"
    assert (deleted_dir / "DSC06705 (1).ARW").read_bytes() == b"raw"
    assert (deleted_dir / "DSC06705 (1).xmp").read_text(encoding="utf-8") == "xmp"
    assert not (deleted_dir / "DSC06705.xmp").exists()


def test_move_to_trash_keeps_json_sidecar_conflict_suffix_in_sync(tmp_path: Path) -> None:
    root = tmp_path / "library"
    image_dir = root / "day1"
    deleted_dir = root / ".superpicky" / "deleted" / "day1"
    image_dir.mkdir(parents=True)
    deleted_dir.mkdir(parents=True)
    (deleted_dir / "GreenCheck.psd").write_bytes(b"old")
    photo = image_dir / "GreenCheck.psd"
    json_sidecar = image_dir / f"GreenCheck.psd{JSON_SIDECAR_SUFFIX}"
    photo.write_bytes(b"psd")
    json_sidecar.write_text('{"metadata": {"rating": 4}}', encoding="utf-8")

    assert move_to_trash(str(photo))

    assert (deleted_dir / "GreenCheck.psd").read_bytes() == b"old"
    assert (deleted_dir / "GreenCheck (1).psd").read_bytes() == b"psd"
    assert (
        deleted_dir / f"GreenCheck (1).psd{JSON_SIDECAR_SUFFIX}"
    ).read_text(encoding="utf-8") == '{"metadata": {"rating": 4}}'
    assert not (deleted_dir / f"GreenCheck.psd{JSON_SIDECAR_SUFFIX}").exists()


def test_move_to_trash_does_not_move_parent_sidecar_for_derived_file(tmp_path: Path) -> None:
    root = tmp_path / "library"
    export_dir = root / "dxo"
    superpicky = root / ".superpicky"
    export_dir.mkdir(parents=True)
    superpicky.mkdir()
    exported = export_dir / "DSC06705-DxO_DeepPRIME.jpg"
    parent_sidecar = root / "DSC06705.xmp"
    exported.write_bytes(b"jpeg")
    parent_sidecar.write_text("original xmp", encoding="utf-8")

    assert move_to_trash(str(exported))

    assert not exported.exists()
    assert parent_sidecar.read_text(encoding="utf-8") == "original xmp"
    assert (superpicky / "deleted" / "dxo" / "DSC06705-DxO_DeepPRIME.jpg").read_bytes() == b"jpeg"
    assert not (superpicky / "deleted" / "DSC06705.xmp").exists()
