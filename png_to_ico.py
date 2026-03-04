#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 icons/{app_name}.png 生成图标文件：
- .ico：Windows 图标（多分辨率）
- .icns：macOS 原生图标（仅在本机为 macOS 且存在 sips/iconutil 时生成）

作为模块使用时，可调用 `generate_icons(app_name)`。
作为脚本运行时，第一个命令行参数为 app 名，默认为 "SuperViewer"。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent
IMAGE_DIR = ROOT_DIR / "icons"

# Windows ICO 常用多分辨率
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# macOS iconset 所需尺寸：(基准, 可选 @2x)
ICNS_SIZES = [(16, 32), (32, 64), (128, 256), (256, 512), (512, 1024)]


def _paths_for_app(app_name: str) -> tuple[Path, Path, Path, Path]:
    png_path = IMAGE_DIR / f"app_icon.png"
    ico_path = IMAGE_DIR / f"app_icon.ico"
    icns_path = IMAGE_DIR / f"app_icon.icns"
    iconset_dir = IMAGE_DIR / f"app_icon.iconset"
    return png_path, ico_path, icns_path, iconset_dir


def save_ico(app_name: str) -> None:
    png_path, ico_path, _, _ = _paths_for_app(app_name)
    if not png_path.is_file():
        raise FileNotFoundError(f"找不到 PNG 源文件: {png_path}")
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(png_path).convert("RGBA")
    img.save(ico_path, format="ICO", sizes=ICO_SIZES)
    print(f"已生成: {ico_path}")


def save_icns(app_name: str) -> bool:
    """在 macOS 下用 sips + iconutil 从 PNG 生成 .icns。"""
    if sys.platform != "darwin":
        print("[跳过] .icns 仅在 macOS 下生成")
        return False
    if not shutil.which("iconutil") or not shutil.which("sips"):
        print("[跳过] 未找到 iconutil 或 sips，无法生成 .icns")
        return False

    png_path, _, icns_path, iconset = _paths_for_app(app_name)
    if not png_path.is_file():
        raise FileNotFoundError(f"找不到 PNG 源文件: {png_path}")

    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)

    try:
        for base, double in ICNS_SIZES:
            for size, suffix in [(base, ""), (double, "@2x")]:
                out = iconset / f"icon_{base}x{base}{suffix}.png"
                subprocess.run(
                    ["sips", "-z", str(size), str(size), str(png_path), "--out", str(out)],
                    check=True,
                    capture_output=True,
                )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
            check=True,
            capture_output=True,
        )
        print(f"已生成: {icns_path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[失败] 生成 .icns 时出错: {e}")
        return False
    finally:
        if iconset.exists():
            shutil.rmtree(iconset)


def generate_icons(app_name: str) -> None:
    """
    从 icons/{app_name}.png 生成对应 .ico 和（在 macOS 下）.icns。
    """
    png_path, _, _, _ = _paths_for_app(app_name)
    if not png_path.is_file():
        raise FileNotFoundError(f"找不到 PNG 源文件: {png_path}")

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    save_ico(app_name)
    save_icns(app_name)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    app_name = argv[0] if argv else "SuperViewer"
    try:
        generate_icons(app_name)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

