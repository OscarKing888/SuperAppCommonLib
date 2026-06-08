# -*- coding: utf-8 -*-
"""Shared write-permission state for .superpicky libraries."""
from __future__ import annotations

import os
import threading
from pathlib import Path

SUPERPICKY_DIRNAME = ".superpicky"

CURRENT_SUPERPICKY_ROOT_PATH = ""
CURRENT_SUPERPICKY_ROOT_WRITABLE = True
CURRENT_SUPERPICKY_ROOT_WRITE_ERROR = ""
READONLY_LABEL_SUFFIX = "(\u53ea\u8bfb)"


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def find_nearest_superpicky_root(path: str | os.PathLike[str] | None) -> str:
    """Return the nearest directory that owns an existing .superpicky folder."""
    if not path:
        return ""
    try:
        candidate = os.path.normpath(os.path.abspath(os.fspath(path)))
    except Exception:
        return ""
    if os.path.isfile(candidate):
        candidate = os.path.dirname(candidate)
    if os.path.basename(candidate) == SUPERPICKY_DIRNAME and os.path.isdir(candidate):
        candidate = os.path.dirname(candidate)

    while candidate:
        superpicky_dir = os.path.join(candidate, SUPERPICKY_DIRNAME)
        if os.path.isdir(superpicky_dir):
            return candidate
        parent = os.path.dirname(candidate)
        if not parent or _path_key(parent) == _path_key(candidate):
            break
        candidate = parent
    return ""


def probe_directory_writable(root_dir: str | os.PathLike[str] | None) -> tuple[bool, str]:
    """Probe writability by creating and removing a temp file in *root_dir*."""
    if not root_dir:
        return True, ""
    root = os.path.normpath(os.path.abspath(os.fspath(root_dir)))
    if not os.path.isdir(root):
        return False, f"目录不存在：{root}"
    tmp_path = os.path.join(
        root,
        f".superpicky-write-test-{os.getpid()}-{threading.get_ident()}.tmp",
    )
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(tmp_path, flags, 0o600)
        try:
            os.write(fd, b"write-test\n")
        finally:
            os.close(fd)
        os.remove(tmp_path)
        return True, ""
    except Exception as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False, str(exc)


def superpicky_root_write_state_for_path(path: str | os.PathLike[str] | None) -> tuple[str, bool, str]:
    root = find_nearest_superpicky_root(path)
    if not root:
        return "", True, ""
    writable, error = probe_directory_writable(root)
    return root, bool(writable), str(error or "")


def refresh_superpicky_root_write_permission(path: str | os.PathLike[str] | None) -> bool:
    """Refresh global .superpicky root write state for *path*.

    Returns True when the global state changed.
    """
    global CURRENT_SUPERPICKY_ROOT_PATH
    global CURRENT_SUPERPICKY_ROOT_WRITABLE
    global CURRENT_SUPERPICKY_ROOT_WRITE_ERROR

    root, writable, error = superpicky_root_write_state_for_path(path)
    changed = (
        root != CURRENT_SUPERPICKY_ROOT_PATH
        or writable != CURRENT_SUPERPICKY_ROOT_WRITABLE
        or error != CURRENT_SUPERPICKY_ROOT_WRITE_ERROR
    )
    CURRENT_SUPERPICKY_ROOT_PATH = root
    CURRENT_SUPERPICKY_ROOT_WRITABLE = writable
    CURRENT_SUPERPICKY_ROOT_WRITE_ERROR = error
    return changed


def set_superpicky_root_write_permission_state(
    *,
    root_path: str = "",
    writable: bool = True,
    error: str = "",
) -> None:
    """Set global state directly; intended for focused tests."""
    global CURRENT_SUPERPICKY_ROOT_PATH
    global CURRENT_SUPERPICKY_ROOT_WRITABLE
    global CURRENT_SUPERPICKY_ROOT_WRITE_ERROR

    CURRENT_SUPERPICKY_ROOT_PATH = os.path.normpath(root_path) if root_path else ""
    CURRENT_SUPERPICKY_ROOT_WRITABLE = bool(writable)
    CURRENT_SUPERPICKY_ROOT_WRITE_ERROR = str(error or "")


def superpicky_root_writable() -> bool:
    return bool(CURRENT_SUPERPICKY_ROOT_WRITABLE)


def superpicky_root_write_state() -> tuple[str, bool, str]:
    return (
        CURRENT_SUPERPICKY_ROOT_PATH,
        CURRENT_SUPERPICKY_ROOT_WRITABLE,
        CURRENT_SUPERPICKY_ROOT_WRITE_ERROR,
    )


def superpicky_root_write_disabled_tooltip(action: str = "写入操作") -> str:
    root = CURRENT_SUPERPICKY_ROOT_PATH or "当前目录"
    reason = CURRENT_SUPERPICKY_ROOT_WRITE_ERROR or "没有写入权限"
    return f"{action}已禁用：{root} 无写入权限。{reason}"


def superpicky_path_write_disabled_tooltip(path: str | os.PathLike[str] | None, action: str = "写入操作") -> str:
    root, writable, error = superpicky_root_write_state_for_path(path)
    if writable:
        return ""
    reason = error or "没有写入权限"
    return f"{action}已禁用：{root or Path(os.fspath(path or '')).parent} 无写入权限。{reason}"


def readonly_label(text: str, *, suffix: str = READONLY_LABEL_SUFFIX) -> str:
    """Append a read-only marker before any Qt menu shortcut suffix."""
    value = str(text or "")
    label, separator, shortcut = value.partition("\t")
    if label.endswith(suffix):
        return value
    return f"{label}{suffix}{separator}{shortcut}"


def clear_readonly_label(text: str, *, suffix: str = READONLY_LABEL_SUFFIX) -> str:
    """Remove the read-only marker added by :func:`readonly_label`."""
    value = str(text or "")
    label, separator, shortcut = value.partition("\t")
    if label.endswith(suffix):
        label = label[: -len(suffix)]
    return f"{label}{separator}{shortcut}"


def mark_write_action_disabled(target, tooltip: str = "") -> None:
    """Disable a QAction/button-like object and visibly mark its label read-only."""
    try:
        target.setEnabled(False)
    except Exception:
        pass
    try:
        target.setText(readonly_label(target.text()))
    except Exception:
        pass
    if tooltip:
        try:
            target.setToolTip(tooltip)
        except Exception:
            pass
        try:
            target.setStatusTip(tooltip)
        except Exception:
            pass


__all__ = [
    "CURRENT_SUPERPICKY_ROOT_PATH",
    "CURRENT_SUPERPICKY_ROOT_WRITABLE",
    "CURRENT_SUPERPICKY_ROOT_WRITE_ERROR",
    "READONLY_LABEL_SUFFIX",
    "clear_readonly_label",
    "find_nearest_superpicky_root",
    "mark_write_action_disabled",
    "probe_directory_writable",
    "readonly_label",
    "refresh_superpicky_root_write_permission",
    "set_superpicky_root_write_permission_state",
    "superpicky_path_write_disabled_tooltip",
    "superpicky_root_writable",
    "superpicky_root_write_disabled_tooltip",
    "superpicky_root_write_state",
    "superpicky_root_write_state_for_path",
]
