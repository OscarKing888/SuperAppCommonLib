from pathlib import Path
import sys
from types import SimpleNamespace

from app_common.file_utils import move_to_trash


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
