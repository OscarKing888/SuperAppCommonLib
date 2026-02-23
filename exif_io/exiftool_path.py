# -*- coding: utf-8 -*-
"""
按平台定位 exiftool 可执行文件。优先使用模块内 exiftools_mac / exiftools_win。
"""
from __future__ import annotations

import os
import shutil
import sys


def _module_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def get_exiftool_executable_path() -> str | None:
    """
    按平台定位 exiftool 可执行文件。
    优先：模块内 exiftools_mac / exiftools_win → 打包后 _MEIPASS 下同路径 → 系统 PATH。
    Windows 仅使用 .exe，不使用 .pl（避免依赖 Perl）。
    """
    rel_candidates = []
    if sys.platform == "darwin":
        rel_candidates.append(os.path.join("exiftools_mac", "exiftool"))
    elif sys.platform.startswith("win"):
        rel_candidates.extend([
            os.path.join("exiftools_win", "exiftool.exe"),
            os.path.join("exiftools_win", "exiftool(-k).exe"),
            os.path.join("exiftools_win", "exiftool_files", "exiftool.exe"),
            os.path.join("exiftools_win", "exiftool_files", "exiftool(-k).exe"),
        ])
    else:
        rel_candidates.extend([
            os.path.join("exiftools_mac", "exiftool"),
            os.path.join("exiftools_win", "exiftool.exe"),
        ])

    search_dirs = [_module_dir()]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            search_dirs.append(meipass)
        if sys.platform == "darwin":
            # .app 内可能在 Contents/Resources
            app_res = os.path.abspath(os.path.join(_module_dir(), "..", "..", "Resources"))
            search_dirs.append(app_res)

    for base in search_dirs:
        for rel in rel_candidates:
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return p

    p = shutil.which("exiftool")
    if p and os.path.isfile(p):
        if sys.platform.startswith("win") and (p or "").lower().endswith(".pl"):
            return None
        return p
    return None
