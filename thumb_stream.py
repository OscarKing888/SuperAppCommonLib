# -*- coding: utf-8 -*-
"""
thumb_stream
============
两阶段缩略图加载 API（先低精度快速显示，再高精度替换）。
返回 RGB 原始字节 (bytes, width, height)，无 Qt 依赖，便于日后用 C 扩展替换实现。

接口约定（替换为 C 时保持以下签名与语义）：
- load_thumbnail_rgb_fast(path, max_size) -> (bytes, int, int) | None
- load_thumbnail_rgb(path, size) -> (bytes, int, int) | None
- iter_thumbnail_rgb_progressive(path, size, stop_fn) -> Iterator[(bytes, int, int)]
"""
from __future__ import annotations

import io as _io
import os
import subprocess
import sys
import tempfile
import time as _time
from pathlib import Path
from typing import Callable, Generator

from app_common.image_formats import (
    JPEG_IMAGE_EXTENSIONS as _JPEG_EXTENSIONS,
    PHOTOSHOP_IMAGE_EXTENSIONS as _PHOTOSHOP_EXTENSIONS,
    RAW_IMAGE_EXTENSIONS as _RAW_EXTENSIONS,
)
from app_common.psd_composite import load_psd_composite_rgb

THUMB_FAST_DEFAULT_SIZE = 64
RAW_PREVIEW_JPEG_TAGS = ("JpgFromRaw", "PreviewImage", "ThumbnailImage")

# Chunk size for progressive JPEG feeding.  64 KB gives ~10–80 feed iterations
# for typical camera JPEGs (2–5 MB), providing 2–5 visible intermediate frames
# for progressive-encoded files.
_PROGRESSIVE_CHUNK = 65536  # 64 KB

# Allow PIL to decode truncated / partially-received JPEG data without raising.
# This is the standard setting for streaming / progressive decode scenarios.
try:
    from PIL import ImageFile as _ImageFile
    _ImageFile.LOAD_TRUNCATED_IMAGES = True
except Exception:
    pass


def _get_raw_thumbnail_bytes(path: str) -> bytes | None:
    """从 RAW 文件提取嵌入 JPEG 缩略图字节。"""
    if Path(path).suffix.lower() not in _RAW_EXTENSIONS:
        return None
    # Prefer rawpy's camera embedded JPEG.  piexif often exposes only a tiny
    # 160x120 EXIF thumbnail for ARW files, which is too small for preview.
    try:
        import rawpy
        with rawpy.imread(path) as rp:
            thumb = rp.extract_thumb()
        if thumb is not None and hasattr(rawpy, "ThumbFormat") and thumb.format == rawpy.ThumbFormat.JPEG:
            if isinstance(thumb.data, bytes):
                return thumb.data
    except Exception:
        pass
    try:
        import piexif
        data = piexif.load(path)
        thumb = data.get("thumbnail")
        if isinstance(thumb, bytes) and len(thumb) > 100:
            return thumb
    except Exception:
        pass
    return None


def _run_exiftool_binary_tag(path: str, tag: str) -> bytes | None:
    """读取 exiftool 二进制标签，返回 JPEG 字节。"""
    try:
        from app_common.exif_io.exiftool_path import get_exiftool_executable_path
        from app_common.exif_io.exiftool_runner import run_exiftool_once
    except Exception:
        return None
    exiftool_path = get_exiftool_executable_path()
    if not exiftool_path:
        return None
    path_norm = os.path.normpath(path)
    cmd_common = [
        exiftool_path,
        "-b",
        "-charset",
        "filename=UTF8",
        "-api",
        "largefilesupport=1",
    ]
    use_argfile = sys.platform.startswith("win") and any(ord(c) > 127 for c in path_norm)
    argfile_path = ""
    try:
        if use_argfile:
            fd, argfile_path = tempfile.mkstemp(suffix=".args", prefix="exiftool_preview_")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"-{tag}\n")
                f.write(path_norm + "\n")
            cmd = [*cmd_common, "-@", argfile_path]
        else:
            cmd = [*cmd_common, f"-{tag}", path_norm]
        proc = run_exiftool_once(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        data = proc.stdout or b""
        if len(data) > 100 and data.startswith(b"\xff\xd8"):
            return data
    except Exception:
        return None
    finally:
        if argfile_path:
            try:
                os.unlink(argfile_path)
            except Exception:
                pass
    return None


def get_raw_preview_jpeg(path: str) -> bytes | None:
    """从 RAW 文件提取适合预览/缩略图的内嵌 JPEG，优先使用高清 JpgFromRaw。"""
    if Path(path).suffix.lower() not in _RAW_EXTENSIONS:
        return None
    for tag in RAW_PREVIEW_JPEG_TAGS:
        data = _run_exiftool_binary_tag(path, tag)
        if data:
            return data
    return _get_raw_thumbnail_bytes(path)


def _pil_to_rgb_thumb(img, size: int) -> tuple[bytes, int, int] | None:
    """PIL Image 缩放到不超过 size，转为 RGB 字节 (data, w, h)。使用 LANCZOS 以获得最终高质量。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img.thumbnail((size, size), Image.LANCZOS)
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (45, 45, 45))
            try:
                alpha = img.split()[-1]
                bg.paste(img.convert("RGB"), mask=alpha)
            except Exception:
                bg.paste(img.convert("RGB"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        data = img.tobytes("raw", "RGB")
        return (data, w, h)
    except Exception:
        return None


def _pil_to_rgb_thumb_bilinear(img, size: int) -> tuple[bytes, int, int] | None:
    """Like _pil_to_rgb_thumb but uses BILINEAR resampling — faster for intermediate
    progressive frames where perfect quality is not yet needed."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img.thumbnail((size, size), Image.BILINEAR)
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (45, 45, 45))
            try:
                alpha = img.split()[-1]
                bg.paste(img.convert("RGB"), mask=alpha)
            except Exception:
                bg.paste(img.convert("RGB"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        data = img.tobytes("raw", "RGB")
        return (data, w, h)
    except Exception:
        return None


def _jpeg_image_is_progressive(img) -> bool:
    """判断 PIL JPEG 是否为 progressive；失败时按非 progressive 处理。"""
    try:
        info = getattr(img, "info", {}) or {}
        return bool(info.get("progressive") or info.get("progression"))
    except Exception:
        return False


def _jpeg_file_is_progressive(path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return _jpeg_image_is_progressive(img)
    except Exception:
        return False


def _jpeg_bytes_are_progressive(data: bytes | None) -> bool:
    if not data:
        return False
    try:
        from PIL import Image
        with Image.open(_io.BytesIO(data)) as img:
            return _jpeg_image_is_progressive(img)
    except Exception:
        return False


def _load_thumbnail_rgb_from_jpeg_bytes(data: bytes | None, size: int) -> tuple[bytes, int, int] | None:
    if not data:
        return None
    try:
        from PIL import Image
        with Image.open(_io.BytesIO(data)) as img:
            return _pil_to_rgb_thumb(img, size)
    except Exception:
        return None


def load_thumbnail_rgb_fast(path: str, max_size: int = THUMB_FAST_DEFAULT_SIZE) -> tuple[bytes, int, int] | None:
    """
    仅针对 JPEG 做小尺寸快速解码（draft），用于首帧快速显示。
    返回 (rgb_bytes, width, height)，24-bit RGB 行优先；非 JPEG 或失败返回 None。
    不读磁盘缓存，保证低延迟。可被 C 实现替换。
    """
    if not path or not os.path.isfile(path):
        return None
    ext = Path(path).suffix.lower()
    if ext not in _JPEG_EXTENSIONS:
        return None
    try:
        from PIL import Image
        img = Image.open(path)
        try:
            img.draft("RGB", (max_size, max_size))
        except Exception:
            pass
        return _pil_to_rgb_thumb(img, max_size)
    except Exception:
        return None


def load_thumbnail_rgb(path: str, size: int) -> tuple[bytes, int, int] | None:
    """
    解码为指定尺寸内的 RGB 缩略图，支持 JPEG（draft）、RAW（嵌入缩略图）及常见位图。
    返回 (rgb_bytes, width, height)，24-bit RGB 行优先；失败返回 None。
    不包含磁盘缓存，由调用方负责。可被 C 实现替换。
    """
    if not path or not os.path.isfile(path):
        return None
    ext = Path(path).suffix.lower()
    try:
        from PIL import Image
    except ImportError:
        if ext in _PHOTOSHOP_EXTENSIONS:
            return load_psd_composite_rgb(path, size)
        return None
    try:
        img = None
        if ext in _RAW_EXTENSIONS:
            raw_data = get_raw_preview_jpeg(path)
            if raw_data:
                result = _load_thumbnail_rgb_from_jpeg_bytes(raw_data, size)
                if result is not None:
                    return result
        if img is None:
            img = Image.open(path)
            if ext in _JPEG_EXTENSIONS:
                try:
                    img.draft("RGB", (size, size))
                except Exception:
                    pass
        return _pil_to_rgb_thumb(img, size)
    except Exception:
        if ext in _PHOTOSHOP_EXTENSIONS:
            return load_psd_composite_rgb(path, size)
        return None


def iter_thumbnail_rgb_progressive(
    path: str,
    size: int,
    stop_fn: Callable[[], bool] | None = None,
) -> Generator[tuple[bytes, int, int], None, None]:
    """
    Progressive JPEG texture-streaming generator.

    Yields (rgb_bytes, w, h) tuples from coarse to fine, mimicking the
    "progressive texture streaming" pattern used in game engines:

    - For JPEG files:  reads the file in 64 KB chunks through PIL's
      ImageFile.Parser.  Progressive-encoded JPEGs yield a blurry full-frame
      after the first scan and successively sharper frames as more scans arrive.
      Baseline JPEGs are decoded in one complete pass to avoid top-N-row frames
      with black lower/right regions.  Each progressive intermediate frame uses
      BILINEAR scaling (fast); the final frame uses a complete LANCZOS decode.
    - For RAW files:   extracts the best embedded preview JPEG first
      (JpgFromRaw → PreviewImage → ThumbnailImage) and applies the same logic.
    - For all other formats (PNG, WebP, TIFF …): yields the single result
      from load_thumbnail_rgb (same as before).

    stop_fn, if provided, is polled between chunks; the generator returns
    immediately when it returns True.

    Callers should REPLACE the displayed thumbnail with each new yield —
    later frames are always better quality.
    """
    if not path or not os.path.isfile(path):
        return

    ext = Path(path).suffix.lower()

    # ── Non-JPEG/RAW: single-shot fallback ──────────────────────────────────
    if ext not in _JPEG_EXTENSIONS and ext not in _RAW_EXTENSIONS:
        result = load_thumbnail_rgb(path, size)
        if result:
            yield result
        return

    # ── Obtain the JPEG stream source ───────────────────────────────────────
    jpeg_bytes: bytes | None = None
    if ext in _RAW_EXTENSIONS:
        jpeg_bytes = get_raw_preview_jpeg(path)
        if jpeg_bytes is None:
            # Embedded JPEG unavailable → plain load
            result = load_thumbnail_rgb(path, size)
            if result:
                yield result
            return
        if not _jpeg_bytes_are_progressive(jpeg_bytes):
            result = _load_thumbnail_rgb_from_jpeg_bytes(jpeg_bytes, size)
            if result:
                yield result
            return
    elif not _jpeg_file_is_progressive(path):
        result = load_thumbnail_rgb(path, size)
        if result:
            yield result
        return

    # ── Progressive feed loop ────────────────────────────────────────────────
    try:
        from PIL import ImageFile
    except ImportError:
        result = load_thumbnail_rgb(path, size)
        if result:
            yield result
        return

    parser = ImageFile.Parser()
    last_emit_t = 0.0
    has_intermediate = False  # True once we have emitted at least one partial frame

    try:
        if jpeg_bytes is not None:
            chunk_iter = (
                jpeg_bytes[offset: offset + _PROGRESSIVE_CHUNK]
                for offset in range(0, len(jpeg_bytes), _PROGRESSIVE_CHUNK)
            )
        else:
            try:
                fh = open(path, "rb")
            except Exception:
                return

            def _file_chunks():
                try:
                    while True:
                        chunk = fh.read(_PROGRESSIVE_CHUNK)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    try:
                        fh.close()
                    except Exception:
                        pass

            chunk_iter = _file_chunks()

        for chunk in chunk_iter:
            if stop_fn is not None and stop_fn():
                return

            try:
                parser.feed(chunk)
            except Exception:
                break  # truncated or corrupt — proceed to finalise below

            img = parser.image
            if img is None:
                continue  # header not yet fully parsed

            now = _time.monotonic()
            # Emit an intermediate frame at most every 50 ms so we don't
            # spend more time scaling than decoding.
            if not has_intermediate or (now - last_emit_t) >= 0.05:
                has_intermediate = True
                last_emit_t = now
                try:
                    # copy() reads whatever pixels libjpeg has decoded so far;
                    # LOAD_TRUNCATED_IMAGES=True prevents exceptions on partial data.
                    result = _pil_to_rgb_thumb_bilinear(img.copy(), size)
                except Exception:
                    result = None
                if result:
                    yield result
                    if stop_fn is not None and stop_fn():
                        return

    except Exception:
        pass  # fall through to finalise

    # ── Finalise: decode the full JPEG again for the cacheable final frame ───
    try:
        parser.close()
    except Exception:
        pass

    if jpeg_bytes is not None:
        result = _load_thumbnail_rgb_from_jpeg_bytes(jpeg_bytes, size)
    else:
        result = load_thumbnail_rgb(path, size)
    if result:
        yield result
    elif not has_intermediate:
        # Parser gave nothing useful → plain fallback
        fallback = load_thumbnail_rgb(path, size)
        if fallback:
            yield fallback
