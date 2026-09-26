# -*- coding: utf-8 -*-
"""TIFF/RAW metadata adapter with bulk reads for ExifRead byte fields.

Keep ExifRead's tag and MakerNote decoders, but avoid one Python seek/read per
byte of a large MakerNote. No dependency globals are patched: each call owns
its header and file handle, including when browser workers run concurrently.
"""
from __future__ import annotations

try:
    from exifread.core.exif_header import ExifHeader
except ImportError:  # Older/absent ExifRead uses the caller's existing fallback.
    ExifHeader = None


if ExifHeader is not None:
    class _BulkByteExifHeader(ExifHeader):
        def _process_field(self, tag_name, count, field_type, type_length, offset):
            # 与 ExifRead 的长度策略相同：普通长字段不展开；只加速无符号字节。
            # MakerNote 是大多数 Sony RAW 的热点（每张数万次 seek/read）。
            if (int(field_type) in (1, 7) and type_length == 1
                    and (0 <= count < 1000 or tag_name == "MakerNote")):
                # 损坏/恶意长度不能触发无限分配，也不能退回逐字节大循环。
                if not 0 <= count <= 16 * 1024 * 1024:
                    raise ValueError("ExifRead byte field exceeds metadata limit")
                self.file_handle.seek(self.offset + offset)
                data = self.file_handle.read(count)
                if len(data) != count:
                    raise ValueError("Truncated ExifRead byte field")
                return list(data)
            return super()._process_field(tag_name, count, field_type, type_length, offset)


def read_tiff_exif_tags(handle):
    """Return ExifRead tags for classic TIFF, or None for the public fallback.

    This adapter is restricted to TIFF headers (including ARW/NEF/DNG). JPEG,
    HEIF and other containers continue through their existing reader. ExifRead
    versions with a different internal API also use that existing reader.
    """
    if ExifHeader is None:
        return None
    handle.seek(0)
    signature = handle.read(4)
    if signature not in (b"II*\0", b"MM\0*"):
        return None
    try:
        header = _BulkByteExifHeader(
            handle, "I" if signature[:2] == b"II" else "M", 0, 0,
            strict=False, detailed=True, truncate_tags=True,
        )
        for index, offset in enumerate(header.list_ifd()):
            name = "Image" if index == 0 else ("Thumbnail" if index == 1 else f"IFD {index}")
            header.dump_ifd(ifd=offset, ifd_name=name)
        exif = header.tags.get("Image ExifOffset")
        if exif:
            header.dump_ifd(ifd=exif.values[0], ifd_name="EXIF")
        sub_ifds = header.tags.get("Image SubIFDs")
        if sub_ifds:
            for index, offset in enumerate(sub_ifds.values):
                header.dump_ifd(ifd=offset, ifd_name=f"EXIF SubIFD{index}")
        if "EXIF MakerNote" in header.tags and "Image Make" in header.tags:
            try:
                header.decode_maker_note()
            except ValueError:
                # ExifRead strict=False 保留已读到的标准字段。
                pass
        return header.tags
    except (AttributeError, TypeError):
        return None
