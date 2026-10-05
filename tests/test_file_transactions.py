"""共享文件事务的归档扩展：独占发布、混合移动/复制与跨卷回退。"""
from pathlib import Path
import pytest
from app_common import file_transactions as tx


def test_mixed_move_and_copy_keeps_source_sidecar(tmp_path):
    photo, sidecar = tmp_path / "bird.jpg", tmp_path / "bird.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_bytes(b"sidecar")
    dest = tmp_path / "dest"
    pairs = [(str(p), str(dest / p.name)) for p in (photo, sidecar)]
    tx.transfer_file_pairs(pairs, action="cut", copy_sources=(str(sidecar),), no_replace=True)
    assert not photo.exists() and sidecar.read_bytes() == b"sidecar"
    assert (dest / photo.name).read_bytes() == b"photo"


def test_exclusive_publication_keeps_concurrent_destination(tmp_path):
    src, dest = tmp_path / "staging", tmp_path / "bird.jpg"
    src.write_bytes(b"ours")
    dest.write_bytes(b"concurrent")
    with pytest.raises(FileExistsError):
        tx.publish_without_overwrite(str(src), str(dest))
    assert src.read_bytes() == b"ours" and dest.read_bytes() == b"concurrent"


def test_volume_without_hardlinks_uses_exclusive_copy(tmp_path, monkeypatch):
    src, dest = tmp_path / "staging", tmp_path / "bird.jpg"
    src.write_bytes(b"photo")
    def unsupported(*_):
        raise OSError("hardlinks unavailable")
    monkeypatch.setattr(tx.os, "link", unsupported)
    tx.publish_without_overwrite(str(src), str(dest))
    assert not src.exists() and dest.read_bytes() == b"photo"


def test_mixed_rollback_retains_complete_photo_when_restore_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    sidecar = tmp_path / "source.xmp"
    source.write_bytes(b"complete-photo")
    sidecar.write_bytes(b"metadata")
    original_move = tx.shutil.move
    original_publish = tx.publish_without_overwrite
    dest = tmp_path / "dest"

    def bad_restore(src, dst):
        if Path(dst) == source:
            source.write_bytes(b"partial")
            raise OSError("restore failed")
        return original_move(src, dst)

    def fail_sidecar(src, dst):
        if Path(dst).suffix == ".xmp":
            raise OSError("sidecar publication failed")
        original_publish(src, dst)

    monkeypatch.setattr(tx.shutil, "move", bad_restore)
    monkeypatch.setattr(tx, "publish_without_overwrite", fail_sidecar)
    with pytest.raises(RuntimeError, match="recoverable files retained") as error:
        tx.transfer_file_pairs([(str(p), str(dest / p.name)) for p in (source, sidecar)],
                               action="cut", copy_sources=(str(sidecar),), no_replace=True)
    assert (dest / source.name).read_bytes() == b"complete-photo"
    assert str(dest / source.name) in str(error.value)
    assert sidecar.read_bytes() == b"metadata"


def test_staging_error_preserves_complete_copy_even_if_source_reappears(tmp_path, monkeypatch):
    source, destination = tmp_path / "source.jpg", tmp_path / "dest/photo.jpg"
    source.write_bytes(b"complete")
    def ambiguous_move(src, dst):
        Path(dst).write_bytes(b"complete")
        Path(src).write_bytes(b"partial")
        raise OSError("cross-volume failure")
    monkeypatch.setattr(tx.shutil, "move", ambiguous_move)
    with pytest.raises(RuntimeError, match="recoverable file retained") as error:
        tx.transfer_file_pairs([(str(source), str(destination))], action="cut", no_replace=True)
    staging, = destination.parent.glob("*.tmp")
    assert staging.read_bytes() == b"complete"
    assert str(staging) in str(error.value)
