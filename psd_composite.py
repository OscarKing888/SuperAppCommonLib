# -*- coding: utf-8 -*-
"""PSD 合成图的轻量读取兜底。

Pillow/Qt 对部分 16 位 PSD 无法解码。本模块只读取 PSD 文件末尾已经合成好的
RGB 通道，不解析图层；因此既可生成小尺寸缩略图，也可为预览/导出读取原尺寸。
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path


_PSD_SIGNATURE = b"8BPS"
_PSD_VERSION = 1
_PSD_RGB_COLOR_MODE = 3
_PSD_MAX_CHANNELS = 56
_PSD_MAX_DIMENSION = 30_000
_PSD_RAW_COMPRESSION = 0
_PSD_RLE_COMPRESSION = 1
_FULL_IMAGE_ROW_CHUNK = 256


@dataclass(frozen=True)
class PsdCompositeInfo:
    """解码 PSD 合成图所需的最小文件头信息。"""

    width: int
    height: int
    channels: int
    depth: int
    color_mode: int
    compression: int
    data_offset: int
    file_size: int


def _read_exact(stream, size: int) -> bytes | None:
    try:
        data = stream.read(int(size))
    except Exception:
        return None
    return data if len(data) == int(size) else None


def _parse_psd_composite_info(path: str | os.PathLike[str]) -> PsdCompositeInfo | None:
    """解析 PSD v1 文件头及合成图位置，所有长度均先做边界校验。"""
    try:
        file_path = os.fspath(path)
        file_size = int(os.path.getsize(file_path))
        with open(file_path, "rb") as stream:
            header = _read_exact(stream, 26)
            if header is None:
                return None
            (
                signature,
                version,
                reserved,
                channels,
                height,
                width,
                depth,
                color_mode,
            ) = struct.unpack(">4sH6sHIIHH", header)
            if signature != _PSD_SIGNATURE or version != _PSD_VERSION:
                return None
            if reserved != b"\x00" * 6:
                return None
            if not (1 <= channels <= _PSD_MAX_CHANNELS):
                return None
            if not (1 <= width <= _PSD_MAX_DIMENSION and 1 <= height <= _PSD_MAX_DIMENSION):
                return None
            if depth not in (1, 8, 16, 32):
                return None

            # Color mode data、Image resources、Layer and mask 三段都使用
            # 4 字节大端长度；只跳过内容，不把大段数据读入内存。
            for _section_index in range(3):
                length_data = _read_exact(stream, 4)
                if length_data is None:
                    return None
                section_length = struct.unpack(">I", length_data)[0]
                next_offset = int(stream.tell()) + int(section_length)
                if next_offset > file_size:
                    return None
                stream.seek(section_length, os.SEEK_CUR)

            compression_data = _read_exact(stream, 2)
            if compression_data is None:
                return None
            compression = struct.unpack(">H", compression_data)[0]
            return PsdCompositeInfo(
                width=int(width),
                height=int(height),
                channels=int(channels),
                depth=int(depth),
                color_mode=int(color_mode),
                compression=int(compression),
                data_offset=int(stream.tell()),
                file_size=file_size,
            )
    except (OSError, ValueError, struct.error):
        return None


def read_psd_composite_size(path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """仅读取 PSD 文件头中的 ``(width, height)``，不解码像素。"""
    if not path or Path(path).suffix.lower() != ".psd":
        return None
    info = _parse_psd_composite_info(path)
    if info is None:
        return None
    return info.width, info.height


def _scaled_dimensions(width: int, height: int, max_size: int | None) -> tuple[int, int]:
    try:
        limit = int(max_size or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0 or max(width, height) <= limit:
        return int(width), int(height)
    scale = float(limit) / float(max(width, height))
    return (
        max(1, int(round(float(width) * scale))),
        max(1, int(round(float(height) * scale))),
    )


def _channel_values_to_u8(np, values, depth: int):
    if depth == 8:
        return np.asarray(values, dtype=np.uint8)
    # PSD 的 16 位通道使用完整 0..65535 范围。分块转换既保留正确端点和
    # 四舍五入，也不会为一张大图同时创建整幅 uint32 临时数组。
    converted = values.astype(np.uint32)
    np.multiply(converted, 255, out=converted)
    np.add(converted, 32767, out=converted)
    np.floor_divide(converted, 65535, out=converted)
    return converted.astype(np.uint8)


def _source_indices(np, source_size: int, output_size: int):
    if source_size == output_size:
        return None
    return np.linspace(0, source_size - 1, output_size).astype(np.intp)


def _load_raw_rgb(path: str, info: PsdCompositeInfo, np, output_width: int, output_height: int):
    bytes_per_sample = info.depth // 8
    expected_size = info.channels * info.height * info.width * bytes_per_sample
    if info.data_offset + expected_size > info.file_size:
        return None
    dtype = np.uint8 if info.depth == 8 else np.dtype(">u2")
    mapped = None
    try:
        mapped = np.memmap(
            path,
            dtype=dtype,
            mode="r",
            offset=info.data_offset,
            shape=(info.channels, info.height, info.width),
            order="C",
        )
        output = np.empty((output_height, output_width, 3), dtype=np.uint8)
        x_indices = _source_indices(np, info.width, output_width)
        y_indices = _source_indices(np, info.height, output_height)
        if x_indices is not None or y_indices is not None:
            if x_indices is None:
                x_indices = np.arange(info.width, dtype=np.intp)
            if y_indices is None:
                y_indices = np.arange(info.height, dtype=np.intp)
            # 逐输出行采样，避免为了 512px 缩略图读取完整的大尺寸通道。
            for channel in range(3):
                for output_y, source_y in enumerate(y_indices):
                    values = mapped[channel, int(source_y), x_indices]
                    output[output_y, :, channel] = _channel_values_to_u8(
                        np, values, info.depth
                    )
        else:
            # 原尺寸解码按行分块，限制 16 位转换时的瞬时内存。
            for channel in range(3):
                for row_start in range(0, info.height, _FULL_IMAGE_ROW_CHUNK):
                    row_end = min(info.height, row_start + _FULL_IMAGE_ROW_CHUNK)
                    output[row_start:row_end, :, channel] = _channel_values_to_u8(
                        np,
                        mapped[channel, row_start:row_end, :],
                        info.depth,
                    )
        return output
    except (OSError, ValueError, OverflowError, MemoryError):
        return None
    finally:
        if mapped is not None:
            # Windows 上显式关闭 memmap，避免预览后文件仍被占用。
            mapping = getattr(mapped, "_mmap", None)
            try:
                if mapping is not None:
                    mapping.close()
            except Exception:
                pass


def _decode_packbits_row(data: bytes, expected_size: int) -> bytes | None:
    """解码 PSD RLE 使用的 PackBits 单行数据。"""
    output = bytearray()
    offset = 0
    data_size = len(data)
    while offset < data_size:
        control = data[offset]
        offset += 1
        if control <= 127:
            count = control + 1
            if offset + count > data_size or len(output) + count > expected_size:
                return None
            output.extend(data[offset:offset + count])
            offset += count
        elif control >= 129:
            count = 257 - control
            if offset >= data_size or len(output) + count > expected_size:
                return None
            output.extend(data[offset:offset + 1] * count)
            offset += 1
        # 128 是 PackBits no-op。
    if len(output) != expected_size:
        return None
    return bytes(output)


def _load_rle_rgb(path: str, info: PsdCompositeInfo, np, output_width: int, output_height: int):
    bytes_per_sample = info.depth // 8
    row_size = info.width * bytes_per_sample
    row_count = info.channels * info.height
    table_size = row_count * 2  # PSD v1 的每行压缩长度为 2 字节。
    if info.data_offset + table_size > info.file_size:
        return None
    try:
        with open(path, "rb") as stream:
            stream.seek(info.data_offset)
            table_data = _read_exact(stream, table_size)
            if table_data is None:
                return None
            row_lengths = np.frombuffer(table_data, dtype=">u2", count=row_count)
            row_offsets = np.empty(row_count + 1, dtype=np.uint64)
            row_offsets[0] = info.data_offset + table_size
            np.cumsum(row_lengths, dtype=np.uint64, out=row_offsets[1:])
            row_offsets[1:] += row_offsets[0]
            if int(row_offsets[-1]) > info.file_size:
                return None

            output = np.empty((output_height, output_width, 3), dtype=np.uint8)
            x_indices = _source_indices(np, info.width, output_width)
            y_indices = _source_indices(np, info.height, output_height)
            if y_indices is None:
                y_indices = np.arange(info.height, dtype=np.intp)
            dtype = np.uint8 if info.depth == 8 else np.dtype(">u2")
            for channel in range(3):
                for output_y, source_y in enumerate(y_indices):
                    row_index = channel * info.height + int(source_y)
                    row_offset = int(row_offsets[row_index])
                    compressed_size = int(row_lengths[row_index])
                    stream.seek(row_offset)
                    compressed = _read_exact(stream, compressed_size)
                    if compressed is None:
                        return None
                    row = _decode_packbits_row(compressed, row_size)
                    if row is None:
                        return None
                    values = np.frombuffer(row, dtype=dtype, count=info.width)
                    if x_indices is not None:
                        values = values[x_indices]
                    output[output_y, :, channel] = _channel_values_to_u8(
                        np, values, info.depth
                    )
            return output
    except (OSError, ValueError, OverflowError, MemoryError):
        return None


def load_psd_composite_rgb(
    path: str | os.PathLike[str],
    max_size: int | None = None,
) -> tuple[bytes, int, int] | None:
    """读取 PSD 合成图，返回 ``(RGB bytes, width, height)``。

    当前兜底支持 RGB、8/16 位、Raw 与 PackBits RLE 压缩。Pillow/Qt 仍是
    上层首选解码器；本函数专门覆盖它们无法识别的合法 PSD。
    """
    if not path or Path(path).suffix.lower() != ".psd":
        return None
    info = _parse_psd_composite_info(path)
    if (
        info is None
        or info.color_mode != _PSD_RGB_COLOR_MODE
        or info.channels < 3
        or info.depth not in (8, 16)
        or info.compression not in (_PSD_RAW_COMPRESSION, _PSD_RLE_COMPRESSION)
    ):
        return None
    try:
        import numpy as np
    except Exception:
        return None

    output_width, output_height = _scaled_dimensions(
        info.width, info.height, max_size
    )
    file_path = os.fspath(path)
    if info.compression == _PSD_RAW_COMPRESSION:
        output = _load_raw_rgb(
            file_path, info, np, output_width, output_height
        )
    else:
        output = _load_rle_rgb(
            file_path, info, np, output_width, output_height
        )
    if output is None:
        return None
    try:
        return output.tobytes(order="C"), output_width, output_height
    except (ValueError, MemoryError):
        return None


__all__ = [
    "PsdCompositeInfo",
    "load_psd_composite_rgb",
    "read_psd_composite_size",
]
