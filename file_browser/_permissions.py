# -*- coding: utf-8 -*-
"""Shared write-permission state for .superpicky libraries."""
from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from app_common.exif_io.json_sidecar import superpicky_sidecar_dir_for_root

SUPERPICKY_DIRNAME = ".superpicky"

CURRENT_SUPERPICKY_ROOT_PATH = ""
CURRENT_SUPERPICKY_ROOT_WRITABLE = True
CURRENT_SUPERPICKY_ROOT_WRITE_ERROR = ""
CURRENT_SUPERPICKY_SIDECAR_DIR_PATH = ""
CURRENT_SUPERPICKY_SIDECAR_WRITABLE = True
CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR = ""
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


def probe_directory_target_writable(directory: str | os.PathLike[str] | None) -> tuple[bool, str]:
    """Probe a directory that may not exist yet by testing its nearest existing parent."""
    if not directory:
        return True, ""
    target = os.path.normpath(os.path.abspath(os.fspath(directory)))
    if os.path.isdir(target):
        return probe_directory_writable(target)
    parent = target
    while parent and not os.path.isdir(parent):
        next_parent = os.path.dirname(parent)
        if not next_parent or _path_key(next_parent) == _path_key(parent):
            return False, f"\u76ee\u5f55\u4e0d\u5b58\u5728\uff1a{target}"
        parent = next_parent
    return probe_directory_writable(parent)


def _existing_file_readonly_reason(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        mode = os.stat(path).st_mode
    except Exception as exc:
        return f"无法检查文件权限：{path}。{exc}"
    if os.name == "nt" and not (mode & stat.S_IWRITE):
        return f"文件为只读：{path}"
    try:
        if not os.access(path, os.W_OK):
            return f"文件无写入权限：{path}"
    except Exception as exc:
        return f"无法检查文件权限：{path}。{exc}"
    return ""


def file_operation_paths_write_state(paths: list[str] | tuple[str, ...] | set[str]) -> tuple[bool, str]:
    """Return whether selected filesystem paths may be moved, renamed, or deleted."""
    parent_results: dict[str, tuple[bool, str]] = {}
    seen: set[str] = set()
    for raw_path in paths or []:
        if not raw_path:
            continue
        try:
            path = os.path.normpath(os.path.abspath(os.fspath(raw_path)))
        except Exception as exc:
            return False, f"路径无效：{raw_path}。{exc}"
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        if not os.path.exists(path):
            return False, f"文件不存在：{path}"
        readonly_reason = _existing_file_readonly_reason(path)
        if readonly_reason:
            return False, readonly_reason
        parent = os.path.dirname(path)
        parent_key = _path_key(parent)
        if parent_key not in parent_results:
            parent_results[parent_key] = probe_directory_target_writable(parent)
        parent_writable, parent_error = parent_results[parent_key]
        if not parent_writable:
            return False, parent_error or f"目录无写入权限：{parent}"
    return True, ""


def file_operation_paths_writable(paths: list[str] | tuple[str, ...] | set[str]) -> bool:
    writable, _error = file_operation_paths_write_state(paths)
    return writable


def file_operation_paths_disabled_tooltip(
    paths: list[str] | tuple[str, ...] | set[str],
    action: str = "写入操作",
) -> str:
    _writable, error = file_operation_paths_write_state(paths)
    reason = error or "没有写入权限"
    return f"{action}已禁用：{reason}"


def superpicky_root_write_state_for_path(path: str | os.PathLike[str] | None) -> tuple[str, bool, str]:
    root = find_nearest_superpicky_root(path)
    if not root:
        return "", True, ""
    probe_target = ""
    if path:
        try:
            norm_path = os.path.normpath(os.path.abspath(os.fspath(path)))
            probe_target = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
        except Exception:
            probe_target = ""
    writable, error = probe_directory_target_writable(probe_target or root)
    return root, bool(writable), str(error or "")


def superpicky_sidecar_write_state_for_path(path: str | os.PathLike[str] | None) -> tuple[str, bool, str]:
    root = find_nearest_superpicky_root(path)
    if not root:
        return "", True, ""
    sidecar_dir = superpicky_sidecar_dir_for_root(root)
    if sidecar_dir is None:
        return "", True, ""
    writable, error = probe_directory_target_writable(sidecar_dir)
    return os.path.normpath(os.fspath(sidecar_dir)), bool(writable), str(error or "")


def refresh_superpicky_root_write_permission(path: str | os.PathLike[str] | None) -> bool:
    """Refresh global .superpicky root write state for *path*.

    Returns True when the global state changed.
    """
    global CURRENT_SUPERPICKY_ROOT_PATH
    global CURRENT_SUPERPICKY_ROOT_WRITABLE
    global CURRENT_SUPERPICKY_ROOT_WRITE_ERROR
    global CURRENT_SUPERPICKY_SIDECAR_DIR_PATH
    global CURRENT_SUPERPICKY_SIDECAR_WRITABLE
    global CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR

    root, writable, error = superpicky_root_write_state_for_path(path)
    sidecar_dir, sidecar_writable, sidecar_error = superpicky_sidecar_write_state_for_path(path)
    changed = (
        root != CURRENT_SUPERPICKY_ROOT_PATH
        or writable != CURRENT_SUPERPICKY_ROOT_WRITABLE
        or error != CURRENT_SUPERPICKY_ROOT_WRITE_ERROR
        or sidecar_dir != CURRENT_SUPERPICKY_SIDECAR_DIR_PATH
        or sidecar_writable != CURRENT_SUPERPICKY_SIDECAR_WRITABLE
        or sidecar_error != CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR
    )
    CURRENT_SUPERPICKY_ROOT_PATH = root
    CURRENT_SUPERPICKY_ROOT_WRITABLE = writable
    CURRENT_SUPERPICKY_ROOT_WRITE_ERROR = error
    CURRENT_SUPERPICKY_SIDECAR_DIR_PATH = sidecar_dir
    CURRENT_SUPERPICKY_SIDECAR_WRITABLE = sidecar_writable
    CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR = sidecar_error
    return changed


def set_superpicky_root_write_permission_state(
    *,
    root_path: str = "",
    writable: bool = True,
    error: str = "",
    sidecar_dir_path: str = "",
    sidecar_writable: bool | None = None,
    sidecar_error: str = "",
) -> None:
    """Set global state directly; intended for focused tests."""
    global CURRENT_SUPERPICKY_ROOT_PATH
    global CURRENT_SUPERPICKY_ROOT_WRITABLE
    global CURRENT_SUPERPICKY_ROOT_WRITE_ERROR
    global CURRENT_SUPERPICKY_SIDECAR_DIR_PATH
    global CURRENT_SUPERPICKY_SIDECAR_WRITABLE
    global CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR

    CURRENT_SUPERPICKY_ROOT_PATH = os.path.normpath(root_path) if root_path else ""
    CURRENT_SUPERPICKY_ROOT_WRITABLE = bool(writable)
    CURRENT_SUPERPICKY_ROOT_WRITE_ERROR = str(error or "")
    CURRENT_SUPERPICKY_SIDECAR_DIR_PATH = os.path.normpath(sidecar_dir_path) if sidecar_dir_path else ""
    CURRENT_SUPERPICKY_SIDECAR_WRITABLE = bool(writable if sidecar_writable is None else sidecar_writable)
    CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR = str(sidecar_error or "")


def superpicky_root_writable() -> bool:
    return bool(CURRENT_SUPERPICKY_ROOT_WRITABLE)


def superpicky_sidecar_writable() -> bool:
    return bool(CURRENT_SUPERPICKY_SIDECAR_WRITABLE)


def superpicky_root_write_state() -> tuple[str, bool, str]:
    return (
        CURRENT_SUPERPICKY_ROOT_PATH,
        CURRENT_SUPERPICKY_ROOT_WRITABLE,
        CURRENT_SUPERPICKY_ROOT_WRITE_ERROR,
    )


def superpicky_sidecar_write_state() -> tuple[str, bool, str]:
    return (
        CURRENT_SUPERPICKY_SIDECAR_DIR_PATH,
        CURRENT_SUPERPICKY_SIDECAR_WRITABLE,
        CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR,
    )


def superpicky_root_write_disabled_tooltip(action: str = "写入操作") -> str:
    root = CURRENT_SUPERPICKY_ROOT_PATH or "当前目录"
    reason = CURRENT_SUPERPICKY_ROOT_WRITE_ERROR or "没有写入权限"
    return f"{action}已禁用：{root} 无写入权限。{reason}"


def superpicky_sidecar_write_disabled_tooltip(action: str = "\u5199\u5165\u64cd\u4f5c") -> str:
    sidecar_dir = CURRENT_SUPERPICKY_SIDECAR_DIR_PATH or "\u5f53\u524d sidecar \u76ee\u5f55"
    reason = CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR or "\u6ca1\u6709\u5199\u5165\u6743\u9650"
    return f"{action}\u5df2\u7981\u7528\uff1a{sidecar_dir} \u65e0 sidecar \u5199\u5165\u6743\u9650\u3002{reason}"


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
    "CURRENT_SUPERPICKY_SIDECAR_DIR_PATH",
    "CURRENT_SUPERPICKY_SIDECAR_WRITABLE",
    "CURRENT_SUPERPICKY_SIDECAR_WRITE_ERROR",
    "READONLY_LABEL_SUFFIX",
    "clear_readonly_label",
    "file_operation_paths_disabled_tooltip",
    "file_operation_paths_writable",
    "file_operation_paths_write_state",
    "find_nearest_superpicky_root",
    "mark_write_action_disabled",
    "probe_directory_target_writable",
    "probe_directory_writable",
    "readonly_label",
    "refresh_superpicky_root_write_permission",
    "set_superpicky_root_write_permission_state",
    "superpicky_path_write_disabled_tooltip",
    "superpicky_sidecar_writable",
    "superpicky_sidecar_write_disabled_tooltip",
    "superpicky_sidecar_write_state",
    "superpicky_sidecar_write_state_for_path",
    "superpicky_root_writable",
    "superpicky_root_write_disabled_tooltip",
    "superpicky_root_write_state",
    "superpicky_root_write_state_for_path",
]
