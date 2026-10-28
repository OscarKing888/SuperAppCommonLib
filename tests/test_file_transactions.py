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


def test_pending_metadata_is_synced_and_new_sidecar_moves_with_photo(tmp_path):
    from app_common.exif_io.metadata_store import get_store
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    source, destination = tmp_path / "白鹭.jpg", tmp_path / "dest/白鹭.jpg"
    source.write_bytes(b"original")
    store = get_store()
    store.submit_edit(str(source), {"title": "湿地白鹭", "rating": 4}).result(3)
    assert not source.with_suffix(".xmp").exists()
    tx.transfer_file_pairs([(str(source), str(destination))], action="cut", no_replace=True)
    assert not source.exists()
    assert not source.with_suffix(".xmp").exists()
    assert destination.read_bytes() == b"original"
    assert PhotoMetaDataXMP().read(str(destination))["Title"] == "湿地白鹭"
    assert not store.pending_status()


def test_failed_pending_sync_keeps_source_and_durable_edit(tmp_path, monkeypatch):
    from app_common.exif_io.metadata_store import get_store
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    source, destination = tmp_path / "白鹭.jpg", tmp_path / "dest/白鹭.jpg"
    source.write_bytes(b"original")
    store = get_store()
    store.submit_edit(str(source), {"description": "不能丢失"}).result(3)
    monkeypatch.setattr(PhotoMetaDataXMP, "write", lambda *_args, **_kw: False)
    with pytest.raises(OSError, match="元数据未同步"):
        tx.transfer_file_pairs([(str(source), str(destination))], action="cut", no_replace=True)
    assert source.read_bytes() == b"original"
    assert not destination.exists()
    assert store.pending_edits(str(source))["fields"]["XMP-dc:Description"] == "不能丢失"


def test_removed_destination_pending_edit_blocks_name_reuse(tmp_path):
    from app_common.exif_io.metadata_store import get_store
    source, destination = tmp_path / "source.jpg", tmp_path / "dest/photo.jpg"
    source.write_bytes(b"new photo")
    destination.parent.mkdir()
    destination.write_bytes(b"old photo")
    store = get_store()
    store.submit_edit(str(destination), {"title": "旧照片尚未同步的鸟名"}).result(3)
    destination.unlink()  # Removed outside the app; its durable edit survives.
    with pytest.raises(OSError, match="元数据未同步"):
        tx.transfer_file_pairs([(str(source), str(destination))], action="copy")
    assert source.read_bytes() == b"new photo"
    assert not destination.exists()
    assert store.pending_edits(str(destination))["fields"]["XMP-dc:Title"] == "旧照片尚未同步的鸟名"
