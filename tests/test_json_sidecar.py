from __future__ import annotations

import json
from pathlib import Path

from app_common.exif_io.json_sidecar import (
    JSON_SIDECAR_SUFFIX,
    central_json_sidecar_path_for,
    find_json_sidecar,
    json_sidecar_path_for,
    read_json_sidecar,
    write_json_sidecar,
)


def _make_library(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "library"
    superpicky = root / ".superpicky"
    image_dir = root / "day1" / "nested"
    image_dir.mkdir(parents=True)
    superpicky.mkdir()
    return root, image_dir / "img.psd"


def test_json_sidecar_writes_to_configured_superpicky_metadata_dir(tmp_path: Path) -> None:
    root, image_path = _make_library(tmp_path)
    image_path.write_bytes(b"psd")
    (root / ".superpicky" / "config.ini").write_text("[sidecar]\ndir = metadata\n", encoding="utf-8")

    assert write_json_sidecar(str(image_path), {"metadata": {"rating": 4}})

    expected = root / ".superpicky" / "metadata" / "day1" / "nested" / f"img.psd{JSON_SIDECAR_SUFFIX}"
    assert json_sidecar_path_for(str(image_path)) == expected
    assert central_json_sidecar_path_for(str(image_path)) == expected
    assert find_json_sidecar(str(image_path)) == str(expected)
    assert read_json_sidecar(str(image_path))["metadata"]["rating"] == 4


def test_json_sidecar_defaults_to_metadata_dir_without_config(tmp_path: Path) -> None:
    root, image_path = _make_library(tmp_path)
    image_path.write_bytes(b"psd")

    expected = root / ".superpicky" / "metadata" / "day1" / "nested" / f"img.psd{JSON_SIDECAR_SUFFIX}"
    assert json_sidecar_path_for(str(image_path)) == expected


def test_json_sidecar_rejects_config_paths_outside_superpicky(tmp_path: Path) -> None:
    root, image_path = _make_library(tmp_path)
    image_path.write_bytes(b"psd")
    (root / ".superpicky" / "config.ini").write_text("[sidecar]\ndir = ../outside\n", encoding="utf-8")

    expected = root / ".superpicky" / "metadata" / "day1" / "nested" / f"img.psd{JSON_SIDECAR_SUFFIX}"
    assert json_sidecar_path_for(str(image_path)) == expected


def test_json_sidecar_reads_central_before_legacy_sibling(tmp_path: Path) -> None:
    root, image_path = _make_library(tmp_path)
    image_path.write_bytes(b"psd")
    legacy = Path(str(image_path) + JSON_SIDECAR_SUFFIX)
    legacy.write_text(json.dumps({"metadata": {"rating": 1}}), encoding="utf-8")
    central = json_sidecar_path_for(str(image_path))
    central.parent.mkdir(parents=True)
    central.write_text(json.dumps({"metadata": {"rating": 5}}), encoding="utf-8")

    assert find_json_sidecar(str(image_path)) == str(central)
    assert read_json_sidecar(str(image_path))["metadata"]["rating"] == 5


def test_json_sidecar_migrates_legacy_payload_on_next_write(tmp_path: Path) -> None:
    root, image_path = _make_library(tmp_path)
    image_path.write_bytes(b"psd")
    legacy = Path(str(image_path) + JSON_SIDECAR_SUFFIX)
    legacy.write_text(json.dumps({"metadata": {"rating": 2}}), encoding="utf-8")

    payload = read_json_sidecar(str(image_path))
    payload.setdefault("metadata", {})["pick"] = 1
    assert write_json_sidecar(str(image_path), payload)

    central = root / ".superpicky" / "metadata" / "day1" / "nested" / f"img.psd{JSON_SIDECAR_SUFFIX}"
    assert central.is_file()
    assert legacy.is_file()
    metadata = read_json_sidecar(str(image_path))["metadata"]
    assert metadata["rating"] == 2
    assert metadata["pick"] == 1
