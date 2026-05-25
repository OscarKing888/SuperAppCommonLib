# -*- coding: utf-8 -*-
"""Append-only edit records for concurrently edited XMP sidecars."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any


EDIT_DIR_SUFFIX = ".superpicky-edits"
EDIT_ROOT_DIRNAME = "sidecar_edits"
EDIT_FILE_SUFFIX = ".json"
EDIT_LOCK_FILENAME = ".compact.lock"
EDIT_LOCK_STALE_SECONDS = 120.0
_EDIT_DIR_TOKEN_MAX_LENGTH = 120


def sidecar_sha256(sidecar_path: str | os.PathLike[str]) -> str:
    path = Path(sidecar_path)
    try:
        if not path.is_file():
            return ""
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _find_existing_superpicky_dir(start_dir: Path) -> Path | None:
    try:
        candidate = Path(os.path.abspath(os.path.normpath(os.fspath(start_dir))))
    except Exception:
        candidate = start_dir
    while True:
        if candidate.name == ".superpicky" and candidate.is_dir():
            return candidate
        superpicky = candidate / ".superpicky"
        if superpicky.is_dir():
            return superpicky
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


def _superpicky_dir_for_sidecar(sidecar_path: str | os.PathLike[str]) -> Path:
    path = Path(sidecar_path)
    existing = _find_existing_superpicky_dir(path.parent)
    if existing is not None:
        return existing
    return path.parent / ".superpicky"


def _relative_sidecar_key(sidecar_path: Path, superpicky_dir: Path) -> str:
    try:
        sidecar_abs = os.path.normpath(os.path.abspath(os.fspath(sidecar_path)))
        root_abs = os.path.normpath(os.path.abspath(os.fspath(superpicky_dir.parent)))
        rel = os.path.relpath(sidecar_abs, root_abs)
        if rel and rel != os.curdir and not rel.startswith(os.pardir + os.sep) and not os.path.isabs(rel):
            return os.path.normpath(rel)
    except Exception:
        pass
    try:
        return os.path.normpath(os.path.abspath(os.fspath(sidecar_path)))
    except Exception:
        return os.fspath(sidecar_path)


def _safe_edit_dir_token(key: str, sidecar_path: Path) -> str:
    parts = [part for part in Path(key).parts if part not in ("", os.sep, os.altsep)]
    token = "__".join(parts) or sidecar_path.name or "sidecar"
    for ch in '<>:"/\\|?*':
        token = token.replace(ch, "_")
    token = "".join("_" if ord(ch) < 32 else ch for ch in token)
    token = token.strip(" ._") or "sidecar"
    if len(token) > _EDIT_DIR_TOKEN_MAX_LENGTH:
        token = token[:_EDIT_DIR_TOKEN_MAX_LENGTH].rstrip(" ._") or "sidecar"
    if token.upper().split(".", 1)[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        token = f"_{token}"
    return token


def legacy_edit_dir_for(sidecar_path: str | os.PathLike[str]) -> Path:
    path = Path(sidecar_path)
    return path.with_name(f"{path.name}{EDIT_DIR_SUFFIX}")


def edit_dir_for(sidecar_path: str | os.PathLike[str]) -> Path:
    sidecar = Path(sidecar_path)
    superpicky_dir = _superpicky_dir_for_sidecar(sidecar)
    key = _relative_sidecar_key(sidecar, superpicky_dir)
    digest = hashlib.sha256(os.path.normcase(key).encode("utf-8")).hexdigest()
    token = _safe_edit_dir_token(key, sidecar)
    return superpicky_dir / EDIT_ROOT_DIRNAME / digest[:2] / f"{digest[:16]}__{token}"


def edit_dirs_for(sidecar_path: str | os.PathLike[str]) -> list[Path]:
    primary = edit_dir_for(sidecar_path)
    legacy = legacy_edit_dir_for(sidecar_path)
    if os.path.normcase(os.path.normpath(os.fspath(primary))) == os.path.normcase(os.path.normpath(os.fspath(legacy))):
        return [primary]
    return [primary, legacy]


def has_pending_edits(sidecar_path: str | os.PathLike[str]) -> bool:
    for edit_dir in edit_dirs_for(sidecar_path):
        try:
            if any(entry.name.endswith(EDIT_FILE_SUFFIX) and entry.is_file() for entry in edit_dir.iterdir()):
                return True
        except Exception:
            continue
    return False


def _normalise_text_values(values: Iterable[Any] | Any) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        for part in text.split(";"):
            clean = part.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            result.append(clean)
    return result


def _normalise_rating(value: Any) -> str:
    try:
        return str(max(0, min(5, int(float(str(value or 0))))))
    except Exception:
        return "0"


def _normalise_pick(value: Any) -> str:
    try:
        text = str(value if value is not None else "").strip().lower()
        if text in ("", "0", "false", "no"):
            return ""
        if text == "reject":
            return "-1"
        if text in ("true", "yes"):
            return "1"
        pick = max(-1, min(1, int(float(text))))
        return "" if pick == 0 else str(pick)
    except Exception:
        return ""


def _actor() -> dict[str, Any]:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    try:
        host = socket.gethostname()
    except Exception:
        host = ""
    return {
        "user": user,
        "host": host,
        "pid": os.getpid(),
    }


def write_edit_file(
    sidecar_path: str | os.PathLike[str],
    source_path: str | os.PathLike[str],
    operations: list[dict[str, Any]],
    *,
    base_hash: str = "",
) -> Path | None:
    clean_ops = [op for op in operations if isinstance(op, dict)]
    if not clean_ops:
        return None
    sidecar = Path(sidecar_path)
    edit_dir = edit_dir_for(sidecar)
    try:
        edit_dir.mkdir(parents=True, exist_ok=True)
        created_ns = time.time_ns()
        suffix = uuid.uuid4().hex
        payload = {
            "version": 1,
            "created_ns": created_ns,
            "created_at": time.time(),
            "actor": _actor(),
            "source": os.path.normpath(os.fspath(source_path)),
            "sidecar": os.path.normpath(os.fspath(sidecar)),
            "base_hash": base_hash,
            "operations": clean_ops,
        }
        final_path = edit_dir / f"{created_ns}_{os.getpid()}_{suffix}{EDIT_FILE_SUFFIX}"
        tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, final_path)
        return final_path
    except Exception:
        try:
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return None


def load_pending_edits(sidecar_path: str | os.PathLike[str]) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    seen_paths: set[str] = set()
    for edit_dir in edit_dirs_for(sidecar_path):
        try:
            files = sorted(
                (
                    entry
                    for entry in edit_dir.iterdir()
                    if entry.name.endswith(EDIT_FILE_SUFFIX) and entry.is_file()
                ),
                key=lambda path: path.name,
            )
        except Exception:
            continue
        for edit_path in files:
            path_key = os.path.normcase(os.path.normpath(os.fspath(edit_path)))
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            try:
                data = json.loads(edit_path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(data, dict):
                records.append((edit_path, data))
    records.sort(key=lambda item: (int(item[1].get("created_ns") or 0), item[0].name))
    return records


def delete_edit_files(records: list[tuple[Path, dict[str, Any]]]) -> None:
    for edit_path, _ in records:
        try:
            edit_path.unlink()
        except OSError:
            pass
        try:
            edit_path.parent.rmdir()
        except OSError:
            pass


def acquire_compact_lock(sidecar_path: str | os.PathLike[str]) -> Path | None:
    edit_dir = edit_dir_for(sidecar_path)
    try:
        edit_dir.mkdir(parents=True, exist_ok=True)
        lock_path = edit_dir / EDIT_LOCK_FILENAME
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > EDIT_LOCK_STALE_SECONDS:
                lock_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            payload = {
                "created_at": time.time(),
                "actor": _actor(),
            }
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        return lock_path
    except Exception:
        return None


def release_compact_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def _row_matches(row: tuple[str, str, str], group: str, name: str) -> bool:
    return row[0].lower() == group.lower() and row[1].lower() == name.lower()


def _row_value(rows: list[tuple[str, str, str]], group: str, name: str) -> tuple[bool, str]:
    for row_group, row_name, row_value in reversed(rows):
        if row_group.lower() == group.lower() and row_name.lower() == name.lower():
            return True, row_value
    return False, ""


def _replace_row(
    rows: list[tuple[str, str, str]],
    group: str,
    name: str,
    value: str,
    *,
    present: bool = True,
) -> list[tuple[str, str, str]]:
    out = [row for row in rows if not _row_matches(row, group, name)]
    if present and value != "":
        out.append((group, name, value))
    return out


def _subjects_from_rows(rows: list[tuple[str, str, str]]) -> list[str]:
    values: list[str] = []
    for group, name, value in rows:
        if group.lower() == "xmp-dc" and name.lower() == "subject":
            values.extend(_normalise_text_values(value))
    return _normalise_text_values(values)


def _apply_subject_op(subjects: list[str], op_name: str, values: list[str]) -> list[str]:
    if op_name == "set":
        return _normalise_text_values(values)
    if op_name == "add":
        merged = list(subjects)
        seen = set(merged)
        for value in values:
            if value not in seen:
                seen.add(value)
                merged.append(value)
        return merged
    if op_name == "remove":
        remove = set(values)
        return [value for value in subjects if value not in remove]
    return subjects


def _operations_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    ops = record.get("operations", record.get("ops", []))
    return [op for op in ops if isinstance(op, dict)] if isinstance(ops, list) else []


def merge_xmp_rows_with_pending_edits(
    sidecar_path: str | os.PathLike[str],
    rows: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    records = load_pending_edits(sidecar_path)
    if not records:
        return rows

    merged_rows = list(rows)
    subjects = _subjects_from_rows(merged_rows)
    description_present, description_value = _row_value(merged_rows, "XMP-dc", "description")
    rating_present, rating_value = _row_value(merged_rows, "XMP-xmp", "Rating")
    pick_present, pick_value = _row_value(merged_rows, "XMP-xmpDM", "pick")

    for _, record in records:
        for op in _operations_from_record(record):
            field = str(op.get("field") or "").strip().lower()
            op_name = str(op.get("op") or "set").strip().lower()
            if field == "subject":
                subjects = _apply_subject_op(subjects, op_name, _normalise_text_values(op.get("values", [])))
            elif field == "description":
                description_present = True
                description_value = "" if op.get("value") is None else str(op.get("value"))
            elif field == "rating":
                rating_present = True
                rating_value = _normalise_rating(op.get("value"))
            elif field == "pick":
                pick_present = True
                pick_value = _normalise_pick(op.get("value"))

    merged_rows = _replace_row(
        merged_rows,
        "XMP-dc",
        "subject",
        "; ".join(subjects),
        present=bool(subjects),
    )
    merged_rows = _replace_row(
        merged_rows,
        "XMP-dc",
        "description",
        description_value,
        present=description_present,
    )
    merged_rows = _replace_row(
        merged_rows,
        "XMP-xmp",
        "Rating",
        rating_value,
        present=rating_present,
    )
    merged_rows = _replace_row(
        merged_rows,
        "XMP-xmpDM",
        "pick",
        pick_value,
        present=pick_present,
    )
    return merged_rows
