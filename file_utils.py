"""
跨平台文件/目录隐藏工具
"""
import os
import shutil
import subprocess
import sys

SUPERPICKY_DIRNAME = ".superpicky"
SUPERPICKY_TRASH_DIRNAME = "deleted"
SUPERPICKY_TRASH_ENV_VAR = "SUPERPICKY_TRASH_ENABLED"
MOVE_TO_SUPERPICKY_TRASH_BY_DEFAULT = True


def hide_path(path):
    """
    跨平台隐藏文件或目录
    
    Args:
        path: 要隐藏的文件或目录的绝对路径
        
    Returns:
        bool: 是否成功设置隐藏属性
    """
    if not os.path.exists(path):
        return False
    
    # Windows: 设置 Hidden 属性
    if sys.platform == 'win32':
        try:
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x02
            ret = ctypes.windll.kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_HIDDEN)
            return ret != 0
        except Exception as e:
            # 如果 ctypes 失败，尝试使用 attrib 命令
            try:
                import subprocess
                result = subprocess.run(
                    ['attrib', '+H', path],
                    capture_output=True,
                    shell=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False
    
    # macOS/Linux: 文件名以 . 开头已经隐藏，无需额外操作
    return True


def ensure_hidden_directory(directory_path):
    """
    确保目录存在并设置为隐藏（仅 Windows 需要）
    
    Args:
        directory_path: 目录路径
        
    Returns:
        bool: 目录是否存在且已隐藏
    """
    # 创建目录（如果不存在）
    os.makedirs(directory_path, exist_ok=True)
    
    # 设置隐藏属性
    return hide_path(directory_path)


def unhide_path(path):
    """
    取消隐藏文件或目录（主要用于 Windows）
    
    Args:
        path: 要取消隐藏的文件或目录路径
        
    Returns:
        bool: 是否成功取消隐藏属性
    """
    if not os.path.exists(path):
        return False
    
    # Windows: 移除 Hidden 属性
    if sys.platform == 'win32':
        try:
            import ctypes
            FILE_ATTRIBUTE_NORMAL = 0x80
            ret = ctypes.windll.kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_NORMAL)
            return ret != 0
        except Exception:
            try:
                import subprocess
                result = subprocess.run(
                    ['attrib', '-H', path],
                    capture_output=True,
                    shell=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False
    
    # macOS/Linux: 无需操作
    return True


def _path_key(path):
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except Exception:
        return ""


def _is_same_or_child_path(parent, child):
    parent_key = _path_key(parent)
    child_key = _path_key(child)
    if not parent_key or not child_key:
        return False
    try:
        return os.path.commonpath([parent_key, child_key]) == parent_key
    except Exception:
        return False


def _superpicky_trash_enabled(use_superpicky_trash):
    if use_superpicky_trash is not None:
        return bool(use_superpicky_trash)
    env_value = os.environ.get(SUPERPICKY_TRASH_ENV_VAR, "")
    if env_value.strip():
        return env_value.strip().lower() not in {"0", "false", "no", "off"}
    return MOVE_TO_SUPERPICKY_TRASH_BY_DEFAULT


def _find_superpicky_dir_for_path(path):
    if not path:
        return ""
    try:
        target = os.path.normpath(os.path.abspath(path))
    except Exception:
        return ""
    candidate = target if os.path.isdir(target) else os.path.dirname(target)
    if os.path.basename(candidate) == SUPERPICKY_DIRNAME and os.path.isdir(candidate):
        return candidate
    while candidate:
        superpicky_dir = os.path.join(candidate, SUPERPICKY_DIRNAME)
        if os.path.isdir(superpicky_dir):
            return os.path.normpath(superpicky_dir)
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return ""


def _unique_destination_path(dest_path, source_is_dir=False):
    if not os.path.lexists(dest_path):
        return dest_path
    parent = os.path.dirname(dest_path)
    name = os.path.basename(dest_path)
    if source_is_dir:
        stem = name
        suffix = ""
    else:
        stem, suffix = os.path.splitext(name)
    for index in range(1, 10000):
        candidate = os.path.join(parent, f"{stem} ({index}){suffix}")
        if not os.path.lexists(candidate):
            return candidate
    return ""


def _superpicky_trash_destination_for_path(path):
    try:
        source_abs = os.path.normpath(os.path.abspath(path))
    except Exception:
        return ""
    superpicky_dir = _find_superpicky_dir_for_path(source_abs)
    if not superpicky_dir:
        return ""

    superpicky_abs = os.path.normpath(os.path.abspath(superpicky_dir))
    root_abs = os.path.dirname(superpicky_abs)
    if _is_same_or_child_path(superpicky_abs, source_abs):
        return ""
    if _is_same_or_child_path(source_abs, superpicky_abs):
        return ""
    if not _is_same_or_child_path(root_abs, source_abs):
        return ""

    try:
        rel_path = os.path.relpath(source_abs, root_abs)
    except Exception:
        return ""
    if (
        not rel_path
        or rel_path == os.curdir
        or rel_path == os.pardir
        or rel_path.startswith(os.pardir + os.sep)
    ):
        return ""

    dest_path = os.path.join(superpicky_abs, SUPERPICKY_TRASH_DIRNAME, rel_path)
    if _is_same_or_child_path(source_abs, dest_path):
        return ""
    return _unique_destination_path(dest_path, source_is_dir=os.path.isdir(source_abs))


def _move_to_superpicky_trash(path):
    dest_path = _superpicky_trash_destination_for_path(path)
    if not dest_path:
        return None
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        moved_path = shutil.move(os.path.normpath(os.path.abspath(path)), dest_path)
        return bool(moved_path and os.path.lexists(moved_path))
    except Exception:
        return False


def move_to_trash(path, *, use_superpicky_trash=None):
    """
    By default, first moves files under an existing .superpicky root to
    .superpicky/deleted/<relative original path>. Pass use_superpicky_trash=False,
    or set SUPERPICKY_TRASH_ENABLED=0, to use the previous system-trash behavior.

    将文件或目录移动到系统垃圾桶（回收站），可恢复。

    优先使用 Send2Trash；若未安装则在 macOS 回退到 osascript / Finder，
    在 Windows 回退到 SHFileOperation。

    Args:
        path: 要删除的文件或目录路径

    Returns:
        bool: 是否成功送入垃圾桶；路径不存在或送 trash 失败为 False
    """
    if not path or not os.path.exists(path):
        return False
    if _superpicky_trash_enabled(use_superpicky_trash):
        superpicky_result = _move_to_superpicky_trash(path)
        if superpicky_result is not None:
            return bool(superpicky_result)
    try:
        import send2trash
        send2trash.send2trash(path)
        return True
    except ImportError:
        pass  # fall through to OS-native fallback
    except Exception:
        return False

    # ── OS-native fallback (no send2trash) ───────────────────────────────────
    try:
        if sys.platform == "darwin":
            escaped = path.replace("\\", "\\\\").replace('"', '\\"')
            result = subprocess.run(
                ["osascript", "-e",
                 f'tell application "Finder" to delete POSIX file "{escaped}"'],
                capture_output=True,
            )
            return result.returncode == 0
        elif sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd",                  wintypes.HWND),
                    ("wFunc",                 wintypes.UINT),
                    ("pFrom",                 wintypes.LPCWSTR),
                    ("pTo",                   wintypes.LPCWSTR),
                    ("fFlags",                wintypes.WORD),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings",         ctypes.c_void_p),
                    ("lpszProgressTitle",     wintypes.LPCWSTR),
                ]

            op = _SHFILEOPSTRUCTW()
            op.wFunc  = 3            # FO_DELETE
            op.pFrom  = path + "\0\0"
            op.fFlags = 0x0040 | 0x0010 | 0x0004  # ALLOWUNDO | NOCONFIRMATION | SILENT
            return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
    except Exception:
        pass
    return False


# OS-generated metadata files that should not prevent a directory from being
# considered "empty".  Comparison is case-insensitive.
_IGNORABLE_NAMES = frozenset({
    ".ds_store",           # macOS Finder metadata
    ".localized",          # macOS localization marker
    ".apdisk",             # macOS AFP disk flag
    "thumbs.db",           # Windows thumbnail cache
    "desktop.ini",         # Windows folder configuration
    ".bridgecache",        # Adobe Bridge
    ".bridgecachesettings",
})


def _dir_is_effectively_empty(dir_path: str) -> bool:
    """Return True if *dir_path* contains only ignorable OS/app metadata files.

    Directories that appear empty to the user but contain .DS_Store, Thumbs.db,
    etc. are treated as empty so they can be trashed.
    """
    try:
        entries = os.listdir(dir_path)
    except FileNotFoundError:
        return False  # already gone
    except Exception:
        return False
    return all(e.lower() in _IGNORABLE_NAMES for e in entries)


def move_empty_dirs_to_trash(root_path, include_root=False, *, use_superpicky_trash=None):
    """
    Move empty directories under ``root_path`` to the system trash.

    A directory is considered "empty" if it contains no files or subdirectories
    other than OS-generated metadata (e.g. .DS_Store on macOS, Thumbs.db on
    Windows).

    Returns:
        tuple[list[str], list[str]]: (moved_paths, failed_paths)
    """
    if not root_path:
        return [], []
    try:
        root_abs = os.path.normpath(os.path.abspath(root_path))
    except Exception:
        return [], []
    if not os.path.isdir(root_abs):
        return [], []

    root_key = os.path.normcase(root_abs)
    candidates = []
    try:
        for current_root, _, _ in os.walk(root_abs, topdown=False):
            candidates.append(current_root)
    except Exception:
        return [], []

    moved = []
    failed = []
    for current_root in candidates:
        try:
            current_abs = os.path.normpath(os.path.abspath(current_root))
        except Exception:
            continue
        if not include_root and os.path.normcase(current_abs) == root_key:
            continue
        if os.path.islink(current_abs):
            continue
        if not _dir_is_effectively_empty(current_abs):
            continue
        if move_to_trash(current_abs, use_superpicky_trash=use_superpicky_trash):
            moved.append(current_abs)
        else:
            failed.append(current_abs)
    return moved, failed


def reveal_in_file_manager(path):
    """
    在系统文件管理器中定位并显示目标路径。

    - macOS: `open -R <path>`（Finder 中选中）
    - Windows: `explorer /select,<path>`（资源管理器中选中）
    - Linux: `xdg-open <dir>`（打开所在目录）

    Args:
        path: 要显示的文件或目录路径

    Returns:
        bool: 是否成功启动系统文件管理器命令
    """
    if not path:
        return False
    try:
        norm_path = os.path.normpath(os.path.abspath(path))
        if sys.platform == "darwin":
            args = ["open", "-R", norm_path]
        elif os.name == "nt":
            if os.path.isfile(norm_path):
                args = ["explorer.exe", f"/select,{norm_path}"]
            else:
                args = ["explorer.exe", norm_path]
        else:
            target = os.path.dirname(norm_path) if os.path.isfile(norm_path) else norm_path
            args = ["xdg-open", target]
        subprocess.Popen(args)
        return True
    except Exception:
        return False
