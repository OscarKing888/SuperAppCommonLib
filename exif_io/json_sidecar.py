# -*- coding: utf-8 -*-
"""JSON metadata sidecar helpers.

The JSON sidecar intentionally mirrors the browser's exiftool-style metadata
keys so callers can move from XMP sidecars without changing the rest of the
metadata pipeline.
"""
from __future__ import annotations

import json
import os
import tempfile
import configparser
from collections.abc import Iterable
from pathlib import Path
from typing import Any


JSON_SIDECAR_FORMAT = "superviewer-json-sidecar"
JSON_SIDECAR_VERSION = 1
JSON_SIDECAR_SUFFIX = ".superviewer.json"
SUPERPICKY_DIRNAME = ".superpicky"
SUPERPICKY_CONFIG_FILENAME = "config.ini"
SIDECAR_CONFIG_SECTION = "sidecar"
SIDECAR_CONFIG_DIR_KEY = "dir"
DEFAULT_SUPERPICKY_SIDECAR_DIRNAME = "metadata"


def _normalise_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.fspath(path))


def _path_key(path: str | os.PathLike[str]) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
    except Exception:
        return ""


def _is_same_or_child_path(parent: str | os.PathLike[str], child: str | os.PathLike[str]) -> bool:
    parent_key = _path_key(parent)
    child_key = _path_key(child)
    if not parent_key or not child_key:
        return False
    try:
        return os.path.commonpath([parent_key, child_key]) == parent_key
    except Exception:
        return False


def sibling_json_sidecar_path_for(image_path: str | os.PathLike[str]) -> Path:
    """Return the legacy sibling JSON sidecar path for an image path."""
    return Path(_normalise_path(image_path) + JSON_SIDECAR_SUFFIX)


def find_nearest_superpicky_root(path: str | os.PathLike[str] | None) -> str:
    """Return the nearest directory that owns an existing .superpicky folder."""
    if not path:
        return ""
    try:
        candidate = os.path.normpath(os.path.abspath(os.fspath(path)))
    except Exception:
        return ""
    if os.path.isfile(candidate) or (not os.path.isdir(candidate) and Path(candidate).suffix):
        candidate = os.path.dirname(candidate)
    if os.path.basename(candidate) == SUPERPICKY_DIRNAME and os.path.isdir(candidate):
        candidate = os.path.dirname(candidate)

    while candidate:
        superpicky_dir = os.path.join(candidate, SUPERPICKY_DIRNAME)
        if os.path.isdir(superpicky_dir):
            return os.path.normpath(candidate)
        parent = os.path.dirname(candidate)
        if not parent or _path_key(parent) == _path_key(candidate):
            break
        candidate = parent
    return ""


def _safe_configured_sidecar_dir(superpicky_dir: Path, configured_value: str) -> Path | None:
    raw = str(configured_value or "").strip().replace("\\", os.sep).replace("/", os.sep)
    if not raw:
        return None
    if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
        return None
    candidate = Path(os.path.normpath(os.path.join(os.fspath(superpicky_dir), raw)))
    superpicky_abs = os.path.abspath(os.fspath(superpicky_dir))
    candidate_abs = os.path.abspath(os.fspath(candidate))
    if _path_key(superpicky_abs) == _path_key(candidate_abs):
        return None
    if not _is_same_or_child_path(superpicky_abs, candidate_abs):
        return None
    return Path(candidate_abs)


def load_superpicky_sidecar_config(root: str | os.PathLike[str] | None) -> str:
    """Return configured .superpicky sidecar subdirectory name, or the default."""
    if not root:
        return DEFAULT_SUPERPICKY_SIDECAR_DIRNAME
    superpicky_dir = Path(os.path.abspath(os.path.join(os.fspath(root), SUPERPICKY_DIRNAME)))
    config_path = superpicky_dir / SUPERPICKY_CONFIG_FILENAME
    if not config_path.is_file():
        return DEFAULT_SUPERPICKY_SIDECAR_DIRNAME
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8-sig")
    except Exception:
        return DEFAULT_SUPERPICKY_SIDECAR_DIRNAME
    try:
        configured = parser.get(SIDECAR_CONFIG_SECTION, SIDECAR_CONFIG_DIR_KEY, fallback="")
    except Exception:
        configured = ""
    safe_dir = _safe_configured_sidecar_dir(superpicky_dir, configured)
    if safe_dir is None:
        return DEFAULT_SUPERPICKY_SIDECAR_DIRNAME
    try:
        return os.path.relpath(os.fspath(safe_dir), os.fspath(superpicky_dir))
    except Exception:
        return DEFAULT_SUPERPICKY_SIDECAR_DIRNAME


def superpicky_sidecar_dir_for_root(root: str | os.PathLike[str] | None) -> Path | None:
    """Return the configured central JSON sidecar directory for a library root."""
    if not root:
        return None
    superpicky_dir = Path(os.path.abspath(os.path.join(os.fspath(root), SUPERPICKY_DIRNAME)))
    if not superpicky_dir.is_dir():
        return None
    configured = load_superpicky_sidecar_config(root)
    safe_dir = _safe_configured_sidecar_dir(superpicky_dir, configured)
    if safe_dir is not None:
        return safe_dir
    return superpicky_dir / DEFAULT_SUPERPICKY_SIDECAR_DIRNAME


def central_json_sidecar_path_for(image_path: str | os.PathLike[str]) -> Path | None:
    """Return the central .superpicky JSON sidecar path, if image_path is in a library."""
    if not image_path:
        return None
    source_abs = os.path.normpath(os.path.abspath(os.fspath(image_path)))
    root = find_nearest_superpicky_root(source_abs)
    if not root:
        return None
    superpicky_dir = os.path.join(root, SUPERPICKY_DIRNAME)
    if _is_same_or_child_path(superpicky_dir, source_abs):
        return None
    if not _is_same_or_child_path(root, source_abs):
        return None
    try:
        rel_path = os.path.relpath(source_abs, root)
    except Exception:
        return None
    if (
        not rel_path
        or rel_path == os.curdir
        or rel_path == os.pardir
        or rel_path.startswith(os.pardir + os.sep)
    ):
        return None
    sidecar_dir = superpicky_sidecar_dir_for_root(root)
    if sidecar_dir is None:
        return None
    return sidecar_dir / Path(rel_path + JSON_SIDECAR_SUFFIX)


def json_sidecar_path_for(image_path: str | os.PathLike[str]) -> Path:
    """Return the canonical JSON sidecar path for an image path."""
    central_path = central_json_sidecar_path_for(image_path)
    if central_path is not None:
        return central_path
    return sibling_json_sidecar_path_for(image_path)


def json_sidecar_candidate_paths_for(image_path: str | os.PathLike[str]) -> list[Path]:
    """Return read candidates in priority order: central sidecar, then legacy sibling."""
    candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in (central_json_sidecar_path_for(image_path), sibling_json_sidecar_path_for(image_path)):
        if candidate is None:
            continue
        key = _path_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def find_json_sidecar(image_path: str | os.PathLike[str]) -> str | None:
    """Return the existing JSON sidecar path for *image_path*, if present."""
    if not image_path:
        return None
    for candidate in json_sidecar_candidate_paths_for(image_path):
        if candidate.is_file():
            return str(candidate)
    return None


def _normalise_text_values(values: Iterable[Any], *, split_strings: bool = False) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        parts = text.split(";") if split_strings else [text]
        for part in parts:
            clean = part.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            result.append(clean)
    return result


def normalise_json_subject_value(value: Any, *, split_strings: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return _normalise_text_values([value], split_strings=split_strings)
    return _normalise_text_values(value, split_strings=split_strings)


def _normalise_rating(value: Any) -> int:
    try:
        return max(0, min(5, int(float(str(value or 0)))))
    except Exception:
        return 0


def _normalise_pick(value: Any) -> int:
    try:
        text = str(value if value is not None else "").strip().lower()
        if text in ("true", "yes"):
            return 1
        if text in ("false", "no", ""):
            return 0
        if text == "reject":
            return -1
        return max(-1, min(1, int(float(text))))
    except Exception:
        return 0


def empty_json_sidecar_payload(image_path: str | os.PathLike[str]) -> dict[str, Any]:
    source_path = _normalise_path(image_path)
    return {
        "format": JSON_SIDECAR_FORMAT,
        "version": JSON_SIDECAR_VERSION,
        "source": {
            "file": os.path.basename(source_path),
        },
        "metadata": {},
    }


def json_sidecar_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    return {
        key: value
        for key, value in payload.items()
        if key not in {"format", "version", "source"}
    }


def _remove_matching_metadata_aliases(metadata: dict[str, Any], predicate, keep_key: str) -> None:
    keep_lower = keep_key.lower()
    for existing_key in list(metadata.keys()):
        existing_text = str(existing_key or "").strip()
        if existing_text.lower() != keep_lower and predicate(existing_text):
            metadata.pop(existing_key, None)


def read_json_sidecar(image_path: str | os.PathLike[str]) -> dict[str, Any]:
    sidecar_path = find_json_sidecar(image_path)
    if not sidecar_path:
        return {}
    try:
        with open(sidecar_path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json_sidecar(image_path: str | os.PathLike[str], payload: dict[str, Any]) -> bool:
    sidecar_path = json_sidecar_path_for(image_path)
    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{sidecar_path.name}.",
        suffix=".tmp",
        dir=str(sidecar_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, sidecar_path)
        return True
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        return False
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def json_sidecar_to_flat_dict(
    image_path: str | os.PathLike[str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a JSON sidecar payload to an exiftool-style flat dict."""
    source_path = _normalise_path(image_path)
    payload = payload if isinstance(payload, dict) else read_json_sidecar(source_path)
    metadata = json_sidecar_metadata(payload)
    if not metadata:
        return {"SourceFile": source_path}

    rec: dict[str, Any] = {"SourceFile": source_path}
    for key, value in metadata.items():
        key_text = str(key or "").strip()
        key_lower = key_text.lower()
        if key_lower in {"xmp-dc:subject", "xmp-dc:subjects", "subject", "subjects", "keywords"}:
            subjects = normalise_json_subject_value(value, split_strings=True)
            subject_text = "; ".join(subjects)
            if "XMP-dc:Subject" not in rec or key_lower in {"xmp-dc:subject", "xmp-dc:subjects"}:
                rec["XMP-dc:Subject"] = subject_text
                rec["XMP-dc:subject"] = subject_text
        elif key_lower in {"xmp-dc:description", "xmp:description", "description"}:
            text = "" if value is None else str(value)
            if "XMP-dc:Description" not in rec or key_lower in {"xmp-dc:description", "xmp:description"}:
                rec["XMP-dc:Description"] = text
                rec["XMP:Description"] = text
                rec["Description"] = text
        elif key_lower in {"xmp-xmp:rating", "xmp:rating", "rating"}:
            rating = _normalise_rating(value)
            if "XMP-xmp:Rating" not in rec or key_lower in {"xmp-xmp:rating", "xmp:rating"}:
                rec["XMP-xmp:Rating"] = str(rating)
                rec["rating"] = rating
        elif key_lower in {
            "xmp-xmpdm:pick",
            "xmp-xmp:pick",
            "xmp-xmp:picklabel",
            "xmp:pick",
            "xmp:picklabel",
            "pick",
        }:
            pick = _normalise_pick(value)
            if "XMP-xmpDM:pick" not in rec or key_lower in {"xmp-xmpdm:pick", "xmp-xmp:pick", "xmp:pick"}:
                rec["XMP-xmpDM:pick"] = str(pick)
                rec["pick"] = pick
        else:
            rec[key_text] = value
    return rec
