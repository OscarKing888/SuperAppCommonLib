"""User configuration must survive switching launchers and build types."""
import json
from pathlib import Path

import pytest

from app_common import superviewer_user_options as options


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("platform,relative", [
    ("darwin", "home/Library/Application Support/SuperViewer"),
    ("win32", "roaming/SuperViewer"),
    ("linux", "home/.superviewer"),
])
def test_config_path_is_independent_of_launcher(tmp_path, monkeypatch, frozen, platform, relative):
    monkeypatch.setattr(options.sys, "platform", platform)
    monkeypatch.setattr(options.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(options.os.path, "expanduser", lambda _: str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(options, "_get_app_dir", lambda: str(tmp_path / "installation"))
    assert Path(options.get_user_state_dir()) == tmp_path / relative
    assert Path(options.get_user_options_path()) == tmp_path / relative / "Config" / options.USER_OPTIONS_FILENAME


def test_options_read_legacy_then_save_shared_config(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    legacy = app / options.USER_OPTIONS_FILENAME
    legacy.write_text('{"key_navigation_fps": 30, "rarity_badge_legendary_text": "珍稀"}', encoding="utf-8")
    original = legacy.read_bytes()
    monkeypatch.setattr(options, "_get_app_dir", lambda: str(app))
    monkeypatch.setattr(options, "get_user_config_dir", lambda: str(tmp_path / "user/Config"))
    loaded = options.load_user_options()
    assert loaded["key_navigation_fps"] == 30
    loaded["key_navigation_fps"] = 60
    options.save_user_options(loaded)
    saved = Path(options.get_user_options_path())
    assert json.loads(saved.read_text(encoding="utf-8"))["rarity_badge_legendary_text"] == "珍稀"
    for frozen in (False, True):
        monkeypatch.setattr(options.sys, "frozen", frozen, raising=False)
        assert options.load_user_options()["key_navigation_fps"] == 60
    assert legacy.read_bytes() == original


def test_explicit_options_path_remains_supported(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "get_user_config_dir", lambda: str(tmp_path / "unused"))
    explicit = tmp_path / "custom/options.cfg"
    options.save_user_options({"key_navigation_fps": 30}, str(explicit))
    assert options.load_user_options(str(explicit))["key_navigation_fps"] == 30
    assert not (tmp_path / "unused").exists()
