from __future__ import annotations

from pathlib import Path

import pytest

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


def test_copy_bundle_rolls_back_when_sidecar_staging_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    photo = source_dir / "IMG_0001.jpg"
    sidecar = source_dir / "IMG_0001.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_bytes(b"xmp")
    dest_photo = dest_dir / photo.name
    dest_sidecar = dest_dir / sidecar.name

    import app_common.file_browser._panel as panel_module

    real_copy2 = panel_module.shutil.copy2
    calls = 0

    def failing_copy2(source, dest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected sidecar copy failure")
        return real_copy2(source, dest)

    monkeypatch.setattr(panel_module.shutil, "copy2", failing_copy2)

    with pytest.raises(OSError, match="injected"):
        FileListPanel._paste_file_bundle_transaction(
            str(photo),
            [str(sidecar)],
            str(dest_photo),
            [str(dest_sidecar)],
            action="copy",
        )

    assert photo.read_bytes() == b"photo"
    assert sidecar.read_bytes() == b"xmp"
    assert not dest_photo.exists()
    assert not dest_sidecar.exists()
    assert not list(dest_dir.glob("*.sbt-paste-*.tmp"))


def test_cut_bundle_restores_all_sources_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    photo = source_dir / "IMG_0001.jpg"
    sidecar = source_dir / "IMG_0001.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_bytes(b"xmp")
    dest_photo = dest_dir / photo.name
    dest_sidecar = dest_dir / sidecar.name

    import app_common.file_browser._panel as panel_module

    real_replace = panel_module.os.replace
    calls = 0

    def failing_replace(source, dest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected sidecar commit failure")
        return real_replace(source, dest)

    monkeypatch.setattr(panel_module.os, "replace", failing_replace)

    with pytest.raises(OSError, match="injected"):
        FileListPanel._paste_file_bundle_transaction(
            str(photo),
            [str(sidecar)],
            str(dest_photo),
            [str(dest_sidecar)],
            action="cut",
        )

    assert photo.read_bytes() == b"photo"
    assert sidecar.read_bytes() == b"xmp"
    assert not dest_photo.exists()
    assert not dest_sidecar.exists()
    assert not list(dest_dir.glob("*.sbt-paste-*.tmp"))


def test_cut_bundle_restores_source_when_move_completes_then_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    photo = source_dir / "IMG_0001.jpg"
    sidecar = source_dir / "IMG_0001.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_bytes(b"xmp")

    import app_common.file_browser._panel as panel_module

    real_move = panel_module.shutil.move
    calls = 0

    def move_then_raise(source, dest):
        nonlocal calls
        calls += 1
        result = real_move(source, dest)
        if calls == 1:
            raise OSError("injected post-move failure")
        return result

    monkeypatch.setattr(panel_module.shutil, "move", move_then_raise)

    with pytest.raises(OSError, match="post-move"):
        FileListPanel._paste_file_bundle_transaction(
            str(photo),
            [str(sidecar)],
            str(dest_dir / photo.name),
            [str(dest_dir / sidecar.name)],
            action="cut",
        )

    assert photo.read_bytes() == b"photo"
    assert sidecar.read_bytes() == b"xmp"
    assert not (dest_dir / photo.name).exists()
    assert not (dest_dir / sidecar.name).exists()
    assert not list(dest_dir.glob("*.sbt-paste-*.tmp"))


def test_multi_entry_cut_rolls_back_earlier_bundles_when_later_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    sources = []
    pairs = []
    for stem in ("IMG_0001", "IMG_0002"):
        photo = source_dir / f"{stem}.jpg"
        sidecar = source_dir / f"{stem}.xmp"
        photo.write_bytes(f"{stem}-photo".encode())
        sidecar.write_bytes(f"{stem}-xmp".encode())
        sources.extend([photo, sidecar])
        pairs.extend(
            [
                (str(photo), str(dest_dir / photo.name)),
                (str(sidecar), str(dest_dir / sidecar.name)),
            ]
        )

    import app_common.file_browser._panel as panel_module

    real_replace = panel_module.os.replace
    calls = 0

    def fail_during_second_bundle(source, dest):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected second bundle failure")
        return real_replace(source, dest)

    monkeypatch.setattr(panel_module.os, "replace", fail_during_second_bundle)

    with pytest.raises(OSError, match="second bundle"):
        FileListPanel._paste_path_pairs_transaction(pairs, action="cut")

    for source in sources:
        assert source.is_file()
    assert not list(dest_dir.iterdir())


def test_url_pairing_is_case_insensitive_for_same_stem(tmp_path: Path) -> None:
    photo = tmp_path / "GreenCheck.psd"
    xmp = tmp_path / "greEncheck.XMP"
    photo.write_bytes(b"psd")
    xmp.write_text("xmp", encoding="utf-8")

    entries = FileListPanel._clipboard_entries_from_urls([str(photo), str(xmp)])

    assert len(entries) == 1
    assert entries[0]["sidecars"] == [str(xmp)]


@pytest.mark.parametrize("restore_failure", ["photo", "sidecar"])
def test_cut_keeps_recoverable_original_when_rollback_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, restore_failure: str,
) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    photo = source_dir / "sample.jpg"
    sidecar = source_dir / "sample.xmp"
    photo.write_bytes(b"only original photo")
    sidecar.write_bytes(b"only original XMP")
    failed_source = photo if restore_failure == "photo" else sidecar

    import app_common.file_browser._panel as panel_module

    real_replace = panel_module.os.replace
    real_move = panel_module.shutil.move
    commits = 0

    def fail_sidecar_commit(source, dest):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OSError("injected sidecar commit failure")
        return real_replace(source, dest)

    def fail_restore(source, dest):
        if Path(dest) == failed_source:
            # Cross-volume restore can create a partial destination before
            # failing. Its existence must not justify deleting the full copy.
            Path(dest).write_bytes(b"partial restore")
            raise OSError("injected restore failure")
        return real_move(source, dest)

    monkeypatch.setattr(panel_module.os, "replace", fail_sidecar_commit)
    monkeypatch.setattr(panel_module.shutil, "move", fail_restore)
    with pytest.raises(RuntimeError, match="recoverable files retained at") as error:
        FileListPanel._paste_file_bundle_transaction(
            str(photo), [str(sidecar)], str(dest_dir / photo.name),
            [str(dest_dir / sidecar.name)], action="cut",
        )

    survivors = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    for payload in (b"only original photo", b"only original XMP"):
        assert payload in survivors.values()
    expected_payload = b"only original photo" if restore_failure == "photo" else b"only original XMP"
    recovery_path = next(path for path, payload in survivors.items() if payload == expected_payload)
    assert recovery_path.parent == dest_dir
    assert repr(str(recovery_path)) in str(error.value)
    # The temporary staging copy is retained only if that member failed to
    # restore; a committed member instead remains at its final destination.
    staging = list(dest_dir.glob("*.sbt-paste-*.tmp"))
    assert staging == ([recovery_path] if restore_failure == "sidecar" else [])


def test_cut_rollback_failure_preserves_existing_destination_and_staged_photo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    photo = source_dir / "sample.jpg"
    sidecar = source_dir / "sample.xmp"
    photo.write_bytes(b"original photo")
    sidecar.write_bytes(b"source sidecar")
    existing_sidecar = dest_dir / sidecar.name
    existing_sidecar.write_bytes(b"pre-existing destination")

    import app_common.file_browser._panel as panel_module

    real_move = panel_module.shutil.move

    def fail_restore(source, dest):
        if Path(dest) == photo:
            raise PermissionError("source directory denies restore")
        return real_move(source, dest)

    monkeypatch.setattr(panel_module.shutil, "move", fail_restore)
    with pytest.raises(RuntimeError, match="recoverable files retained at") as error:
        FileListPanel._paste_file_bundle_transaction(
            str(photo), [str(sidecar)], str(dest_dir / photo.name),
            [str(existing_sidecar)], action="cut",
        )

    assert sidecar.read_bytes() == b"source sidecar"
    assert existing_sidecar.read_bytes() == b"pre-existing destination"
    recovery_path, = dest_dir.glob("*.sbt-paste-*.tmp")
    assert recovery_path.read_bytes() == b"original photo"
    assert repr(str(recovery_path)) in str(error.value)


@pytest.mark.parametrize("action", ["copy", "cut"])
def test_successful_bundle_leaves_no_staging_files(tmp_path: Path, action: str) -> None:
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()
    photo = source_dir / "sample.jpg"
    sidecar = source_dir / "sample.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_bytes(b"XMP")

    FileListPanel._paste_file_bundle_transaction(
        str(photo), [str(sidecar)], str(dest_dir / photo.name),
        [str(dest_dir / sidecar.name)], action=action,
    )

    assert (dest_dir / photo.name).read_bytes() == b"photo"
    assert (dest_dir / sidecar.name).read_bytes() == b"XMP"
    assert photo.exists() == (action == "copy")
    assert sidecar.exists() == (action == "copy")
    assert not list(dest_dir.glob("*.sbt-paste-*.tmp"))
