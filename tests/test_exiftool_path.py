from unittest.mock import patch

from app_common.exif_io import exiftool_path


def test_get_exiftool_executable_path_prefers_env_override() -> None:
    with (
        patch.dict(exiftool_path.os.environ, {"EXIFTOOL_EXE": r"C:\tools\exiftool.exe"}, clear=False),
        patch.object(exiftool_path, "_is_usable_exiftool", return_value=True),
    ):
        resolved = exiftool_path.get_exiftool_executable_path()

    assert resolved == r"C:\tools\exiftool.exe"


def test_get_exiftool_executable_path_uses_absolute_mac_fallback_when_path_is_missing() -> None:
    usable_paths: list[str] = []

    def _fake_is_usable(path: str) -> bool:
        usable_paths.append(path)
        return path == "/opt/homebrew/bin/exiftool"

    with (
        patch.object(exiftool_path.sys, "platform", "darwin"),
        patch.object(exiftool_path.sys, "frozen", True, create=True),
        patch.object(exiftool_path.sys, "_MEIPASS", "/tmp/meipass", create=True),
        patch.object(exiftool_path, "_module_dir", return_value="/tmp/module"),
        patch.object(exiftool_path, "_is_usable_exiftool", side_effect=_fake_is_usable),
        patch.object(exiftool_path.shutil, "which", return_value=None),
    ):
        resolved = exiftool_path.get_exiftool_executable_path()

    assert resolved == "/opt/homebrew/bin/exiftool"
    assert "/opt/homebrew/bin/exiftool" in usable_paths


def test_get_exiftool_executable_path_uses_adjacent_superpicky_fallback_on_windows() -> None:
    module_dir = r"C:\Apps\SBT\SuperBirdTools\app_common\exif_io"
    sibling_exiftool = r"C:\Apps\SuperPicky\exiftools_win\exiftool.exe"
    usable_paths: list[str] = []

    def _fake_is_usable(path: str) -> bool:
        usable_paths.append(path)
        return path == sibling_exiftool

    with (
        patch.dict(exiftool_path.os.environ, {}, clear=True),
        patch.object(exiftool_path.sys, "platform", "win32"),
        patch.object(exiftool_path, "_module_dir", return_value=module_dir),
        patch.object(exiftool_path, "_is_usable_exiftool", side_effect=_fake_is_usable),
        patch.object(exiftool_path, "_absolute_exiftool_candidates", return_value=[]),
        patch.object(exiftool_path.shutil, "which", return_value=None),
    ):
        resolved = exiftool_path.get_exiftool_executable_path()

    assert resolved == sibling_exiftool
    assert sibling_exiftool in usable_paths
