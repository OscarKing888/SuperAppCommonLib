from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from app_common.exif_io import exiftool_path


def test_bundled_windows_exiftool_has_matching_perl_library() -> None:
    bundle = Path(exiftool_path.__file__).resolve().parent / "exiftools_win"
    library = bundle / "exiftool_files" / "lib" / "Image" / "ExifTool.pm"
    script = bundle / "exiftool_files" / "exiftool.pl"
    assert (bundle / "exiftool.exe").is_file()
    assert library.is_file()
    assert (library.parent / "ExifTool" / "JPEG.pm").is_file()
    assert "$VERSION = '13.49'" in library.read_text(encoding="utf-8")
    assert "my $version = '13.49'" in script.read_text(encoding="utf-8")

    if sys.platform.startswith("win"):
        command = [str(bundle / "exiftool.exe"), "-ver"]
    else:
        perl = shutil.which("perl")
        if not perl:
            pytest.skip("Perl unavailable for bundled ExifTool library smoke test")
        command = [perl, str(script), "-ver"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "13.49"


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


def test_get_exiftool_executable_path_finds_frozen_windows_bundle(tmp_path) -> None:
    bundled = tmp_path / "app_common" / "exif_io" / "exiftools_win" / "exiftool.exe"
    with (
        patch.dict(exiftool_path.os.environ, {}, clear=True),
        patch.object(exiftool_path.sys, "platform", "win32"),
        patch.object(exiftool_path.sys, "frozen", True, create=True),
        patch.object(exiftool_path.sys, "_MEIPASS", str(tmp_path), create=True),
        patch.object(exiftool_path, "_module_dir", return_value=str(bundled.parent.parent)),
        patch.object(exiftool_path, "_is_usable_exiftool", side_effect=lambda path: path == str(bundled)),
    ):
        assert exiftool_path.get_exiftool_executable_path() == str(bundled)


def test_get_exiftool_executable_path_uses_adjacent_superpicky_fallback_on_windows(tmp_path) -> None:
    module_dir = str(tmp_path / "SBT" / "SuperBirdTools" / "app_common" / "exif_io")
    sibling_exiftool = str(tmp_path / "SuperPicky" / "exiftools_win" / "exiftool.exe")
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
