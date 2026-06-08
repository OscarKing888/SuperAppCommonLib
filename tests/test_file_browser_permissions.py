from __future__ import annotations

from pathlib import Path

from app_common.file_browser import _permissions as perms


class _DummyAction:
    def __init__(self, text: str) -> None:
        self._text = text
        self.enabled = True
        self.tooltip = ""
        self.status_tip = ""

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setStatusTip(self, status_tip: str) -> None:
        self.status_tip = status_tip


def test_no_superpicky_root_defaults_writable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(perms, "SUPERPICKY_DIRNAME", ".superpicky-does-not-exist-for-test")
    plain = tmp_path / "plain"
    plain.mkdir()
    perms.set_superpicky_root_write_permission_state(root_path="old", writable=False, error="old")

    perms.refresh_superpicky_root_write_permission(plain)

    assert perms.superpicky_root_write_state() == ("", True, "")


def test_directory_with_superpicky_resolves_library_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    leaf = root / "nested" / "leaf"
    (root / ".superpicky").mkdir(parents=True)
    leaf.mkdir(parents=True)

    assert perms.find_nearest_superpicky_root(leaf) == str(root)
    assert perms.find_nearest_superpicky_root(root / ".superpicky") == str(root)


def test_refresh_write_permission_records_probe_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "library"
    leaf = root / "nested"
    (root / ".superpicky").mkdir(parents=True)
    leaf.mkdir()

    monkeypatch.setattr(perms, "probe_directory_writable", lambda path: (False, "denied"))

    perms.refresh_superpicky_root_write_permission(leaf)

    assert perms.superpicky_root_write_state() == (str(root), False, "denied")
    assert not perms.superpicky_root_writable()


def test_refresh_write_permission_success_restores_writable_state(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "library"
    (root / ".superpicky").mkdir(parents=True)
    perms.set_superpicky_root_write_permission_state(root_path=str(root), writable=False, error="denied")
    monkeypatch.setattr(perms, "probe_directory_writable", lambda path: (True, ""))

    perms.refresh_superpicky_root_write_permission(root)

    assert perms.superpicky_root_write_state() == (str(root), True, "")


def test_mark_write_action_disabled_adds_readonly_suffix_before_shortcut() -> None:
    action = _DummyAction("剪切\tCtrl+X")

    perms.mark_write_action_disabled(action, "readonly")
    perms.mark_write_action_disabled(action, "readonly")

    assert not action.enabled
    assert action.text() == "剪切(只读)\tCtrl+X"
    assert action.tooltip == "readonly"
    assert action.status_tip == "readonly"
    assert perms.clear_readonly_label(action.text()) == "剪切\tCtrl+X"
