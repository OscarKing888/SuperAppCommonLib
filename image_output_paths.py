"""Shared portable image/XMP output allocation for editing and denoising."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

OUTPUT_MODES = (("source_subdir","原图目录的子目录"),("source_dir","与原图文件同目录"),
                ("sibling_dir","与原图目录平级的新目录"),("fixed","指定固定目录"),("ask","每次选择目录"))
EXTENSIONS = {"tiff":".tif","jpeg":".jpg","png":".png"}
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)",re.I)


@dataclass(frozen=True)
class ImageOutputOptions:
    output_mode: str = "source_subdir"
    subdir: str = "edited"
    output_directory: str = ""
    format: str = "tiff"


def portable_key(value):
    return unicodedata.normalize("NFC",str(value)).casefold()


def validate_component(name):
    if (not isinstance(name,str) or not name or len(name)>100 or name in {".",".."}
            or name!=name.strip() or name.endswith(".") or _RESERVED.match(name)
            or any(ord(c)<32 or c in '<>:"/\\|?*' for c in name)):
        raise ValueError("目录名必须是兼容 Windows/macOS 的单个名称")


def validate_output_options(options):
    if options.output_mode not in dict(OUTPUT_MODES):
        raise ValueError("未知输出位置模式")
    validate_component(options.subdir)
    if options.format not in EXTENSIONS:
        raise ValueError("输出格式必须为 TIFF、JPEG 或 PNG")
    if options.output_mode in {"fixed","ask"}:
        directory = options.output_directory
        if not isinstance(directory,str) or not directory.strip() or "\0" in directory:
            raise ValueError("请先选择输出目录")


def output_directory(source, options):
    validate_output_options(options)
    source = Path(source).absolute()
    if options.output_mode == "source_subdir":
        return source.parent/options.subdir
    if options.output_mode == "source_dir":
        return source.parent
    if options.output_mode == "sibling_dir":
        if not source.parent.name:
            raise ValueError("磁盘根目录没有可用的平级目录，请选择其他输出位置")
        return source.parent.parent/(source.parent.name+"_"+options.subdir)
    return Path(options.output_directory).expanduser().absolute()


def allocate_outputs(paths, options, *, suffix="edited"):
    validate_output_options(options)
    validate_component(suffix)
    occupied,allocated = {},[]
    for source in map(Path,paths):
        directory = output_directory(source,options).resolve()
        key = portable_key(directory)
        if key not in occupied:
            try:
                occupied[key] = {portable_key(p.name) for p in directory.iterdir()}
            except OSError:
                # Publication still uses exclusive create; a directory's error
                # must be reported for that item without failing other sources.
                occupied[key] = set()
        used = occupied[key]
        stem = f"{source.stem}_{suffix}"
        for number in range(1,1_000_001):
            name = stem if number==1 else f"{stem}_{number}"
            image,xmp = name+EXTENSIONS[options.format],name+".xmp"
            if portable_key(image) not in used and portable_key(xmp) not in used:
                used.update((portable_key(image),portable_key(xmp)))
                allocated.append(str(directory/image))
                break
        else:
            raise FileExistsError("无法分配不重名的输出文件")
    return allocated
