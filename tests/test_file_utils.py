from pathlib import Path
import sys
from types import SimpleNamespace

from app_common.exif_io import find_same_stem_xmp_sidecar, find_xmp_sidecar
from app_common.file_utils import is_apple_double_metadata_file, move_to_trash


def test_is_apple_double_metadata_file_matches_basename_cross_platform() -> None:
    assert is_apple_double_metadata_file("/tmp/photos/._DSC06705.jpg")
    assert is_apple_double_metadata_file(r"C:\photos\._DSC06705.jpg")
    assert not is_apple_double_metadata_file("/tmp/photos/DSC06705.jpg")


def test_move_to_trash_sends_sibling_xmp_sidecar_to_system_trash(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "library"
    image_dir = root / "day1"
    superpicky = root / ".superpicky"
    image_dir.mkdir(parents=True)
    superpicky.mkdir()
    photo = image_dir / "DSC06705.ARW"
    sidecar = image_dir / "DSC06705.xmp"
    photo.write_bytes(b"raw")
    sidecar.write_text("xmp", encoding="utf-8")
    sent: list[str] = []

    def fake_send2trash(value):
        if isinstance(value, list):
            sent.extend(str(path) for path in value)
        else:
            sent.append(str(value))

    monkeypatch.setitem(sys.modules, "send2trash", SimpleNamespace(send2trash=fake_send2trash))

    assert move_to_trash(str(photo))
    assert sent == [str(photo), str(sidecar)]
    assert not (superpicky / "deleted").exists()


def test_move_to_trash_does_not_move_parent_sidecar_for_derived_file(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "library"
    export_dir = root / "dxo"
    export_dir.mkdir(parents=True)
    exported = export_dir / "DSC06705-DxO_DeepPRIME.jpg"
    parent_sidecar = root / "DSC06705.xmp"
    exported.write_bytes(b"jpeg")
    parent_sidecar.write_text("original xmp", encoding="utf-8")
    sent: list[str] = []

    def fake_send2trash(value):
        if isinstance(value, list):
            sent.extend(str(path) for path in value)
        else:
            sent.append(str(value))

    monkeypatch.setitem(sys.modules, "send2trash", SimpleNamespace(send2trash=fake_send2trash))

    assert move_to_trash(str(exported))
    assert sent == [str(exported)]


def test_strict_same_stem_xmp_stays_in_source_directory(tmp_path: Path) -> None:
    export_dir = tmp_path / "DxO"
    export_dir.mkdir()
    source = export_dir / "Bird-DxO_DeepPRIME.jpg"
    source.write_bytes(b"jpg")
    parent_sidecar = tmp_path / "Bird.xmp"
    parent_sidecar.write_text("parent", encoding="utf-8")

    assert find_xmp_sidecar(str(source)) == str(parent_sidecar)
    assert find_same_stem_xmp_sidecar(str(source)) is None


def test_strict_same_stem_xmp_accepts_suffix_case_but_not_derived_stem(tmp_path: Path) -> None:
    source = tmp_path / "Photo.RAW"
    source.write_bytes(b"raw")
    wrong = tmp_path / "Photo-edit.xmp"
    wrong.write_text("wrong", encoding="utf-8")

    assert find_same_stem_xmp_sidecar(str(source)) is None

    exact = tmp_path / "pHoTo.XmP"
    exact.write_text("exact", encoding="utf-8")
    found = find_same_stem_xmp_sidecar(str(source))
    assert found is not None
    assert Path(found).samefile(exact)
