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
from collections.abc import Iterable
from pathlib import Path
from typing import Any


JSON_SIDECAR_FORMAT = "superviewer-json-sidecar"
JSON_SIDECAR_VERSION = 1
JSON_SIDECAR_SUFFIX = ".superviewer.json"


def _normalise_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.fspath(path))


def json_sidecar_path_for(image_path: str | os.PathLike[str]) -> Path:
    """Return the canonical JSON sidecar path for an image path."""
    return Path(_normalise_path(image_path) + JSON_SIDECAR_SUFFIX)


def find_json_sidecar(image_path: str | os.PathLike[str]) -> str | None:
    """Return the existing JSON sidecar path for *image_path*, if present."""
    if not image_path:
        return None
    candidate = json_sidecar_path_for(image_path)
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
            rec["XMP-dc:Subject"] = subject_text
            rec["XMP-dc:subject"] = subject_text
        elif key_lower in {"xmp-dc:description", "xmp:description", "description"}:
            text = "" if value is None else str(value)
            rec["XMP-dc:Description"] = text
            rec["XMP:Description"] = text
            rec["Description"] = text
        elif key_lower in {"xmp-xmp:rating", "xmp:rating", "rating"}:
            rating = _normalise_rating(value)
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
            rec["XMP-xmpDM:pick"] = str(pick)
            rec["pick"] = pick
        else:
            rec[key_text] = value
    return rec
