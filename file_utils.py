"""
跨平台文件/目录隐藏工具
"""
import os
import subprocess
import sys


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


def move_to_trash(path):
    """
    将文件或目录移动到系统垃圾桶（回收站），可恢复。

    使用 Send2Trash，跨平台（macOS / Windows / Linux）。

    Args:
        path: 要删除的文件或目录路径

    Returns:
        bool: 是否成功送入垃圾桶；路径不存在或送 trash 失败为 False
    """
    if not path or not os.path.exists(path):
        return False
    try:
        import send2trash
        send2trash.send2trash(path)
        return True
    except Exception:
        return False


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
