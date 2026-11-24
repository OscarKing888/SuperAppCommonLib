from pathlib import Path
import os

from app_common.send_to_app.receive import normalize_file_paths


def test_discovers_birdstamp_in_windows_suite_and_source_build(tmp_path, monkeypatch):
    from app_common.send_to_app import config

    suite = tmp_path / "dist" / "SuperBirdTools"
    suite.mkdir(parents=True)
    program = suite / "SuperBirdStamp.exe"
    program.touch()
    monkeypatch.setattr(config.sys, "platform", "win32")
    for root in (tmp_path, suite.parent, suite):
        monkeypatch.setattr(config, "_candidate_probe_roots", lambda: [str(root)])
        assert config._discover_birdstamp_app()["path"] == str(program)


def test_normalize_file_paths_skips_apple_double_metadata_files(tmp_path: Path) -> None:
    photo = tmp_path / "DSC06705.jpg"
    apple_double = tmp_path / "._DSC06705.jpg"

    normalized = normalize_file_paths([str(photo), str(apple_double), str(photo)])

    assert normalized == [os.path.abspath(os.path.normpath(str(photo)))]
