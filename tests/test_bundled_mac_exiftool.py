"""Verify the vendored Perl runtime without network access or system ExifTool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


_BUNDLE = Path(__file__).resolve().parents[1] / "exif_io" / "exiftools_mac"


def test_bundled_mac_exiftool_matches_upstream_inventory() -> None:
    recorded = {}
    for line in (_BUNDLE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        recorded[relative] = digest

    actual = {"exiftool", "LICENSE", "README.upstream"}
    actual.update(
        path.relative_to(_BUNDLE).as_posix()
        for path in (_BUNDLE / "lib").rglob("*")
        if path.is_file()
    )
    assert actual == recorded.keys(), "Bundled ExifTool differs from the upstream inventory"
    for relative, digest in recorded.items():
        assert hashlib.sha256((_BUNDLE / relative).read_bytes()).hexdigest() == digest, relative
    assert "my $version = '13.50'" in (_BUNDLE / "exiftool").read_text(encoding="utf-8")
    assert "$VERSION = '13.50'" in (_BUNDLE / "lib/Image/ExifTool.pm").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "darwin", reason="Uses the macOS system Perl runtime")
def test_relocated_mac_exiftool_roundtrips_chinese_without_homebrew(tmp_path: Path) -> None:
    # Copying the whole runtime also exercises spaces and non-ASCII installation paths.
    relocated = tmp_path / "复制到其他机器 App" / "exiftools_mac"
    shutil.copytree(_BUNDLE, relocated)
    home = tmp_path / "empty-home"
    home.mkdir()
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LC_ALL": "en_US.UTF-8"}
    command = ["/usr/bin/perl", str(relocated / "exiftool"), "-config", ""]

    def run(*arguments: str) -> str:
        result = subprocess.run(
            [*command, *arguments],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    assert run("-ver").strip() == "13.50"
    title = "白鹭 · 中文标题"
    description = "跨机器导出验证\n拍摄地点：中国上海"
    title_file = tmp_path / "标题.txt"
    description_file = tmp_path / "说明.txt"
    title_file.write_text(title, encoding="utf-8")
    description_file.write_text(description, encoding="utf-8")
    sidecar = tmp_path / "鸟类样片.xmp"
    run(
        "-charset", "filename=UTF8",
        f"-XMP-dc:Title<={title_file}",
        f"-XMP-dc:Description<={description_file}",
        str(sidecar),
    )
    metadata = json.loads(run("-j", "-G1", "-XMP-dc:Title", "-XMP-dc:Description", str(sidecar)))[0]
    assert metadata["XMP-dc:Title"] == title
    assert metadata["XMP-dc:Description"] == description
