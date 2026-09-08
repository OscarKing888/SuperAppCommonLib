from __future__ import annotations

import builtins
import errno
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_common.exif_io.json_sidecar import json_sidecar_path_for
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._panel import FileListPanel


def _bundle(tmp_path: Path, *, layout="configured", name="白鹭.jpg"):
    source_root = tmp_path / "source"
    dest_root = tmp_path / "destination"
    source_dir = source_root / "day1"
    dest_dir = dest_root / "copies"
    source_dir.mkdir(parents=True)
    dest_dir.mkdir(parents=True)
    if layout != "sibling":
        for root, metadata_dir in ((source_root, "source_metadata"), (dest_root, "external_meta")):
            state = root / ".superpicky"
            state.mkdir()
            if layout == "configured":
                (state / "config.ini").write_text(f"[sidecar]\ndir={metadata_dir}\n", encoding="utf-8")
    photo = source_dir / name
    sources = [photo, photo.with_suffix(".xmp"), json_sidecar_path_for(photo)]
    payloads = [b"complete original photo", "完整 XMP 元数据".encode(), "完整 JSON 元数据".encode()]
    for source, payload in zip(sources, payloads):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
    dest_photo = dest_dir / name
    destinations = [dest_photo] + [
        Path(FileListPanel._sidecar_destination_for_paste(str(photo), str(dest_photo), str(sidecar)))
        for sidecar in sources[1:]
    ]
    return sources, destinations, payloads


def _paste(sources, destinations, *, action):
    return FileListPanel._paste_file_bundle_transaction(
        str(sources[0]), list(map(str, sources[1:])),
        str(destinations[0]), list(map(str, destinations[1:])), action=action,
    )


def _assert_restored(tmp_path, sources, destinations, payloads):
    assert [source.read_bytes() for source in sources] == payloads
    assert not any(dest.exists() for dest in destinations)
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("layout", ["sibling", "central", "configured"])
def test_success_carries_xmp_and_json_to_the_target_library(tmp_path, action, layout):
    sources, destinations, payloads = _bundle(tmp_path, layout=layout)
    if layout != "sibling":
        assert not destinations[2].parent.exists()
    touched = _paste(sources, destinations, action=action)
    assert [dest.read_bytes() for dest in destinations] == payloads
    assert all(source.exists() == (action == "copy") for source in sources)
    if action == "copy":
        assert [source.read_bytes() for source in sources] == payloads
    assert set(touched) == set(map(str, destinations + (sources if action == "cut" else [])))
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))
    if layout == "configured":
        assert "external_meta" in destinations[2].parts
        assert "source_metadata" in sources[2].parts


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("failed_member", [1, 2], ids=["xmp", "json"])
def test_sidecar_staging_failure_restores_the_whole_bundle(tmp_path, monkeypatch, action, failed_member):
    sources, destinations, payloads = _bundle(tmp_path)
    method = "copy2" if action == "copy" else "move"
    real_operation = getattr(panel_module.shutil, method)

    def fail_sidecar(source, dest):
        if Path(source) == sources[failed_member]:
            Path(dest).write_bytes(b"partial sidecar staging")
            raise OSError("injected sidecar staging failure")
        return real_operation(source, dest)

    monkeypatch.setattr(panel_module.shutil, method, fail_sidecar)
    expected_error = RuntimeError if action == "cut" else OSError
    with pytest.raises(expected_error, match="sidecar staging") as error:
        _paste(sources, destinations, action=action)
    if action == "cut":
        # A surviving source alone cannot establish its identity after a failed
        # move. Preserve the staging member even when it happens to be partial.
        assert [source.read_bytes() for source in sources] == payloads
        assert not any(dest.exists() for dest in destinations)
        recovery, = tmp_path.rglob("*.sbt-paste-*.tmp")
        assert recovery.read_bytes() == b"partial sidecar staging"
        assert repr(str(recovery)) in str(error.value)
    else:
        _assert_restored(tmp_path, sources, destinations, payloads)


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("failed_member", [1, 2], ids=["xmp", "json"])
def test_sidecar_commit_failure_rolls_back_committed_members(tmp_path, monkeypatch, action, failed_member):
    sources, destinations, payloads = _bundle(tmp_path)
    real_publish = FileListPanel._publish_paste_file_without_overwrite

    def fail_commit(source, dest):
        if Path(dest) == destinations[failed_member]:
            raise OSError("injected sidecar commit failure")
        return real_publish(source, dest)

    monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_commit))
    with pytest.raises(OSError, match="sidecar commit"):
        _paste(sources, destinations, action=action)
    _assert_restored(tmp_path, sources, destinations, payloads)


@pytest.mark.parametrize("failed_member", [0, 1, 2], ids=["photo", "xmp", "json"])
def test_cut_keeps_the_only_complete_copy_when_restore_fails(tmp_path, monkeypatch, failed_member):
    sources, destinations, payloads = _bundle(tmp_path)
    real_publish = FileListPanel._publish_paste_file_without_overwrite

    def fail_commit(source, dest):
        if Path(dest) == destinations[1]:
            raise OSError("injected commit failure")
        if Path(dest) == sources[failed_member]:
            Path(dest).write_bytes(b"partial restore is not the full original")
            raise OSError("injected restore failure")
        return real_publish(source, dest)

    monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_commit))
    with pytest.raises(RuntimeError, match="recoverable files retained at") as error:
        _paste(sources, destinations, action="cut")

    survivors = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert all(payload in survivors.values() for payload in payloads)
    recovery, = [path for path, value in survivors.items() if value == payloads[failed_member]]
    assert recovery != sources[failed_member]
    assert repr(str(recovery)) in str(error.value)
    assert list(tmp_path.rglob("*.sbt-paste-*.tmp")) == ([recovery] if ".sbt-paste-" in recovery.name else [])


def test_collision_and_failed_restore_preserve_existing_json_and_all_originals(tmp_path, monkeypatch):
    sources, destinations, payloads = _bundle(tmp_path)
    destinations[2].parent.mkdir(parents=True)
    destinations[2].write_bytes(b"existing destination JSON must survive")
    real_publish = FileListPanel._publish_paste_file_without_overwrite

    def fail_restore(source, dest):
        if Path(dest) == sources[0]:
            raise PermissionError("source directory refuses photo restore")
        return real_publish(source, dest)

    monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_restore))
    with pytest.raises(RuntimeError, match="recoverable files retained at") as error:
        _paste(sources, destinations, action="cut")
    assert destinations[2].read_bytes() == b"existing destination JSON must survive"
    assert [source.read_bytes() for source in sources[1:]] == payloads[1:]
    recovery, = tmp_path.rglob("*.sbt-paste-*.tmp")
    assert recovery.read_bytes() == payloads[0]
    assert repr(str(recovery)) in str(error.value)


@pytest.mark.parametrize("action", ["copy", "cut"])
def test_destination_created_during_commit_is_not_overwritten(tmp_path, monkeypatch, action):
    sources, destinations, payloads = _bundle(tmp_path)
    real_publish = FileListPanel._publish_paste_file_without_overwrite

    def create_collision(source, dest):
        result = real_publish(source, dest)
        if Path(dest) == destinations[0]:
            destinations[1].write_bytes(b"new destination from another process")
        return result

    monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(create_collision))
    with pytest.raises(FileExistsError):
        _paste(sources, destinations, action=action)
    assert destinations[1].read_bytes() == b"new destination from another process"
    assert [source.read_bytes() for source in sources] == payloads
    assert not destinations[0].exists()
    assert not destinations[2].exists()
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


@pytest.mark.parametrize("failed_member", [0, 2], ids=["photo", "json"])
def test_move_that_completes_before_raising_is_restored(tmp_path, monkeypatch, failed_member):
    sources, destinations, payloads = _bundle(tmp_path)
    real_move = panel_module.shutil.move

    def move_then_raise(source, dest):
        result = real_move(source, dest)
        if Path(source) == sources[failed_member]:
            raise OSError("injected post-move failure")
        return result

    monkeypatch.setattr(panel_module.shutil, "move", move_then_raise)
    with pytest.raises(OSError, match="post-move"):
        _paste(sources, destinations, action="cut")
    _assert_restored(tmp_path, sources, destinations, payloads)


def _fake_panel(dest_dir, entries, action, monkeypatch):
    events = []
    panel = SimpleNamespace(
        get_current_dir=lambda: str(dest_dir),
        _clipboard_file_payload=lambda: (action, entries),
        _file_writes_allowed=lambda *_args, **_kwargs: True,
        _file_operation_paths_allowed=lambda *_args, **_kwargs: True,
        _sidecar_paths_from_clipboard_entry=FileListPanel._sidecar_paths_from_clipboard_entry,
        _sidecar_destination_for_paste=FileListPanel._sidecar_destination_for_paste,
        _same_file_path=FileListPanel._same_file_path,
        _paste_path_pairs_transaction=FileListPanel._paste_path_pairs_transaction,
        load_directory=lambda *_args, **_kwargs: events.append("reload"),
        set_pending_selection=lambda paths, **_kwargs: events.append(tuple(paths)),
    )
    panel._unique_paste_destinations = lambda *args, **kwargs: FileListPanel._unique_paste_destinations(panel, *args, **kwargs)
    panel.paste_errors = []
    monkeypatch.setattr(panel_module, "QMessageBox", SimpleNamespace(
        warning=lambda _parent, _title, text: panel.paste_errors.append(text),
    ))
    # Avoid reading or changing the system clipboard or constructing widgets.
    monkeypatch.setattr(panel_module, "QApplication", SimpleNamespace(
        clipboard=lambda: SimpleNamespace(clear=lambda: events.append("clear")),
    ))
    return panel, events


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("fail_later_bundle", [False, True])
def test_clipboard_payload_is_one_transaction_with_reserved_names(tmp_path, monkeypatch, action, fail_later_bundle):
    first_sources, first_destinations, first_payloads = _bundle(tmp_path / "first")
    second_sources, _second_destinations, second_payloads = _bundle(tmp_path / "second")
    entries = [
        {"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))}
        for sources in (first_sources, second_sources)
    ]
    panel, events = _fake_panel(first_destinations[0].parent, entries, action, monkeypatch)
    if fail_later_bundle:
        real_publish = FileListPanel._publish_paste_file_without_overwrite
        commits = 0

        def fail_fourth_commit(source, dest):
            nonlocal commits
            commits += 1
            if commits == 4:
                raise OSError("later bundle failed")
            return real_publish(source, dest)

        monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_fourth_commit))
    FileListPanel._paste_clipboard_to_current_dir(panel)
    if fail_later_bundle:
        _assert_restored(tmp_path, first_sources, first_destinations, first_payloads)
        assert [source.read_bytes() for source in second_sources] == second_payloads
        assert events == []
    else:
        assert events.count("reload") == 1
        assert events.count("clear") == (1 if action == "cut" else 0)
        selected, = [event for event in events if isinstance(event, tuple)]
        assert len(set(selected)) == 2
        for source, destination in zip((first_sources[0], second_sources[0]), selected):
            assert Path(destination).is_file()
            assert Path(destination).with_suffix(".xmp").is_file()
            assert json_sidecar_path_for(destination).read_bytes() == first_payloads[2]
            assert source.exists() == (action == "copy")


@pytest.mark.parametrize("denied_gate", ["destination", "source"])
def test_clipboard_permission_gates_prevent_any_file_changes(tmp_path, monkeypatch, denied_gate):
    sources, destinations, payloads = _bundle(tmp_path)
    entries = [{"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))}]
    panel, events = _fake_panel(destinations[0].parent, entries, "cut", monkeypatch)
    gate = "_file_writes_allowed" if denied_gate == "destination" else "_file_operation_paths_allowed"
    setattr(panel, gate, lambda *_args, **_kwargs: False)
    FileListPanel._paste_clipboard_to_current_dir(panel)
    _assert_restored(tmp_path, sources, destinations, payloads)
    assert events == []


@pytest.mark.parametrize("action", ["copy", "cut"])
def test_existing_central_json_reserves_the_entire_destination_name(tmp_path, monkeypatch, action):
    sources, destinations, payloads = _bundle(tmp_path)
    destinations[2].parent.mkdir(parents=True)
    destinations[2].write_bytes(b"existing JSON from another photo")
    entries = [{"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))}]
    panel, events = _fake_panel(destinations[0].parent, entries, action, monkeypatch)
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert destinations[2].read_bytes() == b"existing JSON from another photo"
    selected, = [event for event in events if isinstance(event, tuple)]
    dest_photo = Path(selected[0])
    assert dest_photo != destinations[0]
    assert dest_photo.read_bytes() == payloads[0]
    assert dest_photo.with_suffix(".xmp").read_bytes() == payloads[1]
    assert json_sidecar_path_for(dest_photo).read_bytes() == payloads[2]


def _panel_with_mime_payload(dest_dir, entries, action, monkeypatch):
    """Exercise the real Qt MIME parser without touching the system clipboard."""
    panel, events = _fake_panel(dest_dir, entries, action, monkeypatch)
    mime = panel_module.QMimeData()
    mime.setData(panel_module._FILE_CLIPBOARD_ACTION_MIME, action.encode())
    mime.setData(panel_module._FILE_CLIPBOARD_ENTRIES_MIME, json.dumps(entries, ensure_ascii=False).encode())
    clipboard = SimpleNamespace(mimeData=lambda: mime, clear=lambda: events.append("clear"))
    monkeypatch.setattr(panel_module, "QApplication", SimpleNamespace(clipboard=lambda: clipboard))
    panel._clipboard_entries_from_urls = FileListPanel._clipboard_entries_from_urls
    panel._clipboard_file_payload = lambda: FileListPanel._clipboard_file_payload(panel)
    return panel, events


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("missing_member", [0, 1, 2], ids=["photo", "xmp", "central-json"])
def test_real_clipboard_parser_keeps_missing_members_and_aborts_the_payload(tmp_path, monkeypatch, action, missing_member):
    sources, destinations, payloads = _bundle(tmp_path)
    other_photo = sources[0].with_name("other.jpg")
    other_photo.write_bytes(b"other original")
    entries = [
        {"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))},
        {"source": str(other_photo), "sidecars": []},
    ]
    panel, events = _panel_with_mime_payload(destinations[0].parent, entries, action, monkeypatch)
    sources[missing_member].unlink()
    parsed_action, parsed_entries = panel._clipboard_file_payload()
    assert parsed_action == action
    assert len(parsed_entries) == 2
    assert parsed_entries[0]["sidecars"] == list(map(str, sources[1:]))
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert events == []
    assert other_photo.read_bytes() == b"other original"
    assert not sources[missing_member].exists()
    for index, source in enumerate(sources):
        if index != missing_member:
            assert source.read_bytes() == payloads[index]
    assert not any(dest.exists() for dest in destinations)
    assert not list(destinations[0].parent.iterdir())
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("primitive", ["link", "exclusive-copy"])
def test_atomic_publish_does_not_replace_a_destination_created_at_the_primitive(tmp_path, monkeypatch, action, primitive):
    sources, destinations, payloads = _bundle(tmp_path)
    collision = destinations[1]
    foreign_bytes = b"another process owns this destination"

    def unsupported_rename(*_args):
        raise OSError(errno.EXDEV, "exercise the cross-volume publication path")

    monkeypatch.setattr(panel_module.os, "rename", unsupported_rename)
    if primitive == "link":
        real_link = panel_module.os.link

        def link_with_collision(source, dest):
            if Path(dest) == collision:
                collision.write_bytes(foreign_bytes)
            return real_link(source, dest)

        monkeypatch.setattr(panel_module.os, "link", link_with_collision)
    else:
        def unsupported_link(*_args):
            raise OSError(errno.EOPNOTSUPP, "hard links unsupported on this volume")

        real_open = builtins.open

        def open_with_collision(file, mode="r", *args, **kwargs):
            if Path(file) == collision and mode == "xb":
                collision.write_bytes(foreign_bytes)
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(panel_module.os, "link", unsupported_link)
        monkeypatch.setattr(builtins, "open", open_with_collision)
    with pytest.raises(FileExistsError):
        _paste(sources, destinations, action=action)
    assert collision.read_bytes() == foreign_bytes
    assert [source.read_bytes() for source in sources] == payloads
    assert not destinations[0].exists()
    assert not destinations[2].exists()
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("fail_copy", [False, True])
def test_volume_without_hardlinks_copies_exclusively_and_rolls_back_partial_output(tmp_path, monkeypatch, action, fail_copy):
    sources, destinations, payloads = _bundle(tmp_path)

    def unsupported_link(*_args):
        raise OSError(errno.EOPNOTSUPP, "hard links unsupported on this volume")

    monkeypatch.setattr(panel_module.os, "rename", unsupported_link)
    monkeypatch.setattr(panel_module.os, "link", unsupported_link)
    if fail_copy:
        real_copy = panel_module.shutil.copyfileobj

        def fail_json_copy(input_file, output, *args):
            if Path(output.name) == destinations[2]:
                output.write(b"partial output")
                raise OSError("injected exclusive copy failure")
            return real_copy(input_file, output, *args)

        monkeypatch.setattr(panel_module.shutil, "copyfileobj", fail_json_copy)
        with pytest.raises(OSError, match="exclusive copy failure"):
            _paste(sources, destinations, action=action)
        _assert_restored(tmp_path, sources, destinations, payloads)
    else:
        source_mtimes = [source.stat().st_mtime_ns for source in sources]
        _paste(sources, destinations, action=action)
        assert [dest.read_bytes() for dest in destinations] == payloads
        assert [dest.stat().st_mtime_ns for dest in destinations] == source_mtimes
        assert all(source.exists() == (action == "copy") for source in sources)
        assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


@pytest.mark.parametrize("action", ["copy", "cut"])
@pytest.mark.parametrize("fail_commit", [False, True])
def test_real_clipboard_shared_raw_jpg_xmp_is_paired_for_each_target(tmp_path, monkeypatch, action, fail_commit):
    sources, destinations, payloads = _bundle(tmp_path)
    raw = sources[0].with_suffix(".arw")
    raw_json = json_sidecar_path_for(raw)
    raw.write_bytes(b"complete original RAW")
    raw_json.write_bytes("RAW 独立 JSON".encode())
    collector = SimpleNamespace(
        _resolve_source_path_for_action=lambda path: path,
        _resolve_sidecar_path=lambda path: str(Path(path).with_suffix(".xmp")),
    )
    collector._metadata_sidecars_for_source_path = lambda *args: FileListPanel._metadata_sidecars_for_source_path(collector, *args)
    entries = FileListPanel._collect_file_clipboard_entries(collector, [str(sources[0]), str(raw)])
    assert entries[0]["sidecars"][0] == entries[1]["sidecars"][0] == str(sources[1])
    panel, events = _panel_with_mime_payload(destinations[0].parent, entries, action, monkeypatch)
    if fail_commit:
        real_publish = FileListPanel._publish_paste_file_without_overwrite

        def fail_raw_json_commit(source, dest):
            if str(dest).endswith(".arw.superviewer.json") and Path(dest) != raw_json:
                raise OSError("injected final RAW JSON commit failure")
            return real_publish(source, dest)

        monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_raw_json_commit))
    FileListPanel._paste_clipboard_to_current_dir(panel)
    if fail_commit:
        _assert_restored(tmp_path, sources, destinations, payloads)
        assert raw.read_bytes() == b"complete original RAW"
        assert raw_json.read_text(encoding="utf-8") == "RAW 独立 JSON"
        assert events == []
    else:
        selected, = [event for event in events if isinstance(event, tuple)]
        assert len(selected) == 2
        for destination, expected_photo, expected_json in zip(
            map(Path, selected), [payloads[0], b"complete original RAW"], [payloads[2], "RAW 独立 JSON".encode()]
        ):
            assert destination.read_bytes() == expected_photo
            assert destination.with_suffix(".xmp").read_bytes() == payloads[1]
            assert json_sidecar_path_for(destination).read_bytes() == expected_json
        assert all(source.exists() == (action == "copy") for source in sources + [raw, raw_json])
        assert events.count("clear") == (action == "cut")
        assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


def test_cut_rollback_preserves_a_source_recreated_at_the_atomic_restore(tmp_path, monkeypatch):
    sources, destinations, payloads = _bundle(tmp_path)
    real_publish = FileListPanel._publish_paste_file_without_overwrite
    real_link = panel_module.os.link
    real_rename = panel_module.os.rename

    def fail_commit(source, dest):
        if Path(dest) == destinations[1]:
            raise OSError("injected XMP commit failure")
        return real_publish(source, dest)

    def source_appears_during_restore(source, dest):
        if Path(dest) == sources[0]:
            sources[0].write_bytes(b"new source from another process")
        return real_link(source, dest)

    def source_appears_during_rename(source, dest):
        if Path(dest) == sources[0]:
            sources[0].write_bytes(b"new source from another process")
        return real_rename(source, dest)

    monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_commit))
    monkeypatch.setattr(panel_module.os, "link", source_appears_during_restore)
    monkeypatch.setattr(panel_module.os, "rename", source_appears_during_rename)
    with pytest.raises(RuntimeError, match="recoverable files retained at") as error:
        _paste(sources, destinations, action="cut")
    assert sources[0].read_bytes() == b"new source from another process"
    assert [source.read_bytes() for source in sources[1:]] == payloads[1:]
    recovery, = [path for path in tmp_path.rglob("*") if path.is_file() and path.read_bytes() == payloads[0]]
    assert recovery.read_bytes() == payloads[0]
    assert repr(str(recovery)) in str(error.value)
    assert not any(dest.exists() for dest in destinations if dest != recovery)


@pytest.mark.parametrize("missing_json", [False, True])
def test_same_directory_shared_xmp_cut_is_a_noop_but_still_validates_members(tmp_path, monkeypatch, missing_json):
    sources, _destinations, payloads = _bundle(tmp_path)
    raw = sources[0].with_suffix(".arw")
    raw.write_bytes(b"original RAW")
    entries = [
        {"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))},
        {"source": str(raw), "sidecars": [str(sources[1])]},
    ]
    panel, events = _panel_with_mime_payload(sources[0].parent, entries, "cut", monkeypatch)
    if missing_json:
        sources[2].unlink()
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert sources[0].read_bytes() == payloads[0]
    assert sources[1].read_bytes() == payloads[1]
    assert raw.read_bytes() == b"original RAW"
    assert set(sources[0].parent.iterdir()) == {sources[0], sources[1], raw}
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))
    if missing_json:
        assert events == []
    else:
        assert sources[2].read_bytes() == payloads[2]
        selected, = [event for event in events if isinstance(event, tuple)]
        assert selected == (str(sources[0]), str(raw))
        assert events.count("clear") == 1


@pytest.mark.skipif(panel_module.os.name != "nt", reason="Windows rename provides native no-replace semantics")
@pytest.mark.parametrize("action", ["copy", "cut"])
def test_windows_native_rename_refuses_a_destination_arriving_at_commit(tmp_path, monkeypatch, action):
    sources, destinations, payloads = _bundle(tmp_path)
    real_rename = panel_module.os.rename

    def destination_appears(source, dest):
        if Path(dest) == destinations[1]:
            destinations[1].write_bytes(b"new destination XMP")
        return real_rename(source, dest)

    monkeypatch.setattr(panel_module.os, "rename", destination_appears)
    with pytest.raises(FileExistsError):
        _paste(sources, destinations, action=action)
    assert destinations[1].read_bytes() == b"new destination XMP"
    assert [source.read_bytes() for source in sources] == payloads
    assert not destinations[0].exists()
    assert not destinations[2].exists()
    assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))


@pytest.mark.parametrize("action", ["copy", "cut"])
def test_readonly_photo_success_does_not_require_unlinking_a_readonly_staging_link(tmp_path, action):
    sources, destinations, payloads = _bundle(tmp_path)
    sources[0].chmod(stat.S_IREAD)
    try:
        _paste(sources, destinations, action=action)
        assert [dest.read_bytes() for dest in destinations] == payloads
        assert all(source.exists() == (action == "copy") for source in sources)
        assert destinations[0].stat().st_mode & stat.S_IWUSR == 0
        assert not list(tmp_path.rglob("*.sbt-paste-*.tmp"))
    finally:
        for path in tmp_path.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("fail_restore", [False, True])
def test_real_clipboard_entry_reports_one_failure_with_recovery_paths(tmp_path, monkeypatch, fail_restore):
    sources, destinations, payloads = _bundle(tmp_path)
    entries = [{"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))}]
    panel, events = _panel_with_mime_payload(destinations[0].parent, entries, "cut", monkeypatch)
    real_publish = FileListPanel._publish_paste_file_without_overwrite

    def fail_transaction(source, dest):
        if Path(dest) == destinations[1]:
            raise OSError("injected sidecar commit failure")
        if fail_restore and Path(dest) == sources[0]:
            raise PermissionError("source directory refuses restoration")
        return real_publish(source, dest)

    monkeypatch.setattr(FileListPanel, "_publish_paste_file_without_overwrite", staticmethod(fail_transaction))
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert events == []
    assert len(panel.paste_errors) == 1
    assert "injected sidecar commit failure" in panel.paste_errors[0]
    if fail_restore:
        recovery, = [path for path in tmp_path.rglob("*") if path.is_file() and path.read_bytes() == payloads[0]]
        assert recovery != sources[0]
        assert repr(str(recovery)) in panel.paste_errors[0]
        assert [source.read_bytes() for source in sources[1:]] == payloads[1:]
    else:
        _assert_restored(tmp_path, sources, destinations, payloads)


def test_real_clipboard_success_does_not_show_a_failure_message(tmp_path, monkeypatch):
    sources, destinations, payloads = _bundle(tmp_path)
    entries = [{"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))}]
    panel, events = _panel_with_mime_payload(destinations[0].parent, entries, "copy", monkeypatch)
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert [dest.read_bytes() for dest in destinations] == payloads
    assert panel.paste_errors == []
    assert "reload" in events


@pytest.mark.parametrize("failed_member", [0, 1, 2], ids=["photo", "shared-xmp", "central-json"])
def test_completed_cut_stage_with_recreated_source_keeps_original_and_reports_recovery(tmp_path, monkeypatch, failed_member):
    sources, destinations, payloads = _bundle(tmp_path)
    raw = sources[0].with_suffix(".arw")
    raw_json = json_sidecar_path_for(raw)
    raw.write_bytes(b"complete RAW original")
    raw_json.write_bytes("RAW 独立元数据".encode())
    entries = [
        {"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))},
        {"source": str(raw), "sidecars": [str(sources[1]), str(raw_json)]},
    ]
    panel, events = _panel_with_mime_payload(destinations[0].parent, entries, "cut", monkeypatch)
    real_move = panel_module.shutil.move
    replacement = b"new file created by another process"

    def move_then_recreate_source_and_raise(source, dest):
        result = real_move(source, dest)
        if Path(source) == sources[failed_member]:
            Path(source).write_bytes(replacement)
            raise OSError("move completed before failing; source was recreated")
        return result

    monkeypatch.setattr(panel_module.shutil, "move", move_then_recreate_source_and_raise)
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert events == []
    assert sources[failed_member].read_bytes() == replacement
    recovery, = tmp_path.rglob("*.sbt-paste-*.tmp")
    assert recovery.read_bytes() == payloads[failed_member]
    assert len(panel.paste_errors) == 1
    assert "recoverable files retained at" in panel.paste_errors[0]
    assert repr(str(recovery)) in panel.paste_errors[0]
    for index, source in enumerate(sources):
        if index != failed_member:
            assert source.read_bytes() == payloads[index]
    assert raw.read_bytes() == b"complete RAW original"
    assert raw_json.read_bytes() == "RAW 独立元数据".encode()
    assert not any(dest.exists() for dest in destinations)


def test_shared_xmp_copy_stage_failure_preserves_owned_backup_when_source_reappears(tmp_path, monkeypatch):
    sources, destinations, payloads = _bundle(tmp_path)
    raw = sources[0].with_suffix(".arw")
    raw.write_bytes(b"complete RAW original")
    entries = [
        {"source": str(sources[0]), "sidecars": list(map(str, sources[1:]))},
        {"source": str(raw), "sidecars": [str(sources[1])]},
    ]
    panel, events = _panel_with_mime_payload(destinations[0].parent, entries, "cut", monkeypatch)
    real_copy = panel_module.shutil.copy2

    def fail_duplicate_staging(source, dest):
        if ".xmp.sbt-paste-" in Path(source).name:
            Path(dest).write_bytes(b"partial duplicate sidecar")
            sources[1].write_bytes(b"replacement XMP from another process")
            raise OSError("duplicate XMP staging failed")
        return real_copy(source, dest)

    monkeypatch.setattr(panel_module.shutil, "copy2", fail_duplicate_staging)
    FileListPanel._paste_clipboard_to_current_dir(panel)
    assert events == []
    assert sources[0].read_bytes() == payloads[0]
    assert sources[1].read_bytes() == b"replacement XMP from another process"
    assert sources[2].read_bytes() == payloads[2]
    assert raw.read_bytes() == b"complete RAW original"
    recovery, = tmp_path.rglob("*.sbt-paste-*.tmp")
    assert recovery.read_bytes() == payloads[1]
    assert len(panel.paste_errors) == 1
    assert repr(str(recovery)) in panel.paste_errors[0]
    assert not any(dest.exists() for dest in destinations)
