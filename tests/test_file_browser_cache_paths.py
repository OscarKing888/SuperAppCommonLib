from pathlib import Path

from app_common.file_browser._browser_core import (
    _find_superpicky_dir,
    _existing_persistent_thumb_cache_path_for_exact_size,
    _persistent_thumb_cache_path_for_file,
    _preview_cache_target_for_file,
    _superpicky_cache_root_dir,
    _thumb_disk_cache_path,
)


def test_persistent_thumb_cache_uses_existing_superpicky_root_for_descendants(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "2026" / "birds" / "day1" / "set1" / "raw"
    superpicky = root / ".superpicky"
    superpicky.mkdir(parents=True)
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.jpg"

    assert _find_superpicky_dir(str(nested)) == str(superpicky)
    assert _superpicky_cache_root_dir(str(nested)) == str(root)

    cache_path = Path(_persistent_thumb_cache_path_for_file(str(photo), str(nested), 128))
    assert cache_path == (
        superpicky
        / "thumb_cache"
        / "128"
        / "2026__birds__day1__set1__raw__DSC00024.jpg.thumb.jpg"
    )

    disk_cache_path = Path(_thumb_disk_cache_path(str(photo), 1.0, 128))
    assert disk_cache_path.parent == superpicky / "thumb_cache" / "128"
    assert disk_cache_path.suffix == ".jpg"


def test_cache_paths_do_not_create_or_target_missing_superpicky(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    nested = root / "images"
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.jpg"

    assert _find_superpicky_dir(str(nested)) == ""
    assert _superpicky_cache_root_dir(str(nested)) == ""
    assert _persistent_thumb_cache_path_for_file(str(photo), str(nested), 128) == ""
    assert _preview_cache_target_for_file(str(photo), str(nested)) == ""
    assert Path(_thumb_disk_cache_path(str(photo), 1.0, 128)).parent != (
        root / ".superpicky" / "thumb_cache" / "128"
    )
    assert not (nested / ".superpicky").exists()
    assert not (root / ".superpicky").exists()


def test_persistent_thumb_cache_migrates_legacy_thumb_cache_dir(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "2026" / "birds"
    superpicky = root / ".superpicky"
    legacy_dir = superpicky / "cache" / "thumb_cache_128"
    legacy_dir.mkdir(parents=True)
    nested.mkdir(parents=True)
    photo = nested / "DSC00024.jpg"
    photo.write_bytes(b"image")
    legacy_path = legacy_dir / "2026__birds__DSC00024.jpg.thumb.jpg"
    legacy_path.write_bytes(b"thumb")

    migrated = Path(
        _existing_persistent_thumb_cache_path_for_exact_size(str(photo), str(nested), 128)
    )

    assert migrated == superpicky / "thumb_cache" / "128" / legacy_path.name
    assert migrated.read_bytes() == b"thumb"
    assert not legacy_path.exists()
