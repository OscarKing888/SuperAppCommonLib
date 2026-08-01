from __future__ import annotations

import struct
from pathlib import Path

from app_common import thumb_stream
from app_common.psd_composite import (
    load_psd_composite_rgb,
    read_psd_composite_size,
)


def _psd_header(width: int, height: int, depth: int) -> bytes:
    return struct.pack(
        ">4sH6sHIIHH",
        b"8BPS",
        1,
        b"\x00" * 6,
        3,
        height,
        width,
        depth,
        3,
    ) + struct.pack(">III", 0, 0, 0)


def _write_raw_16_bit_psd(path: Path, width: int = 4, height: int = 2) -> None:
    red_row = (0, 16384, 32768, 65535)
    green_row = (32768,) * width
    blue_row = (65535, 32768, 16384, 0)
    planes = []
    for row in (red_row, green_row, blue_row):
        planes.append(struct.pack(f">{width * height}H", *(row * height)))
    path.write_bytes(_psd_header(width, height, 16) + b"\x00\x00" + b"".join(planes))


def _packbits_literal(row: bytes) -> bytes:
    assert 1 <= len(row) <= 128
    return bytes((len(row) - 1,)) + row


def _write_rle_8_bit_psd(path: Path, width: int = 3, height: int = 2) -> None:
    encoded_rows = []
    for value in (10, 20, 30):
        for _row in range(height):
            encoded_rows.append(_packbits_literal(bytes((value,)) * width))
    lengths = b"".join(struct.pack(">H", len(row)) for row in encoded_rows)
    path.write_bytes(
        _psd_header(width, height, 8)
        + b"\x00\x01"
        + lengths
        + b"".join(encoded_rows)
    )


def test_raw_16_bit_psd_composite_and_thumbnail_fallback(tmp_path: Path) -> None:
    path = tmp_path / "16-bit.psd"
    _write_raw_16_bit_psd(path)

    assert read_psd_composite_size(path) == (4, 2)
    full = load_psd_composite_rgb(path)
    assert full is not None
    data, width, height = full
    assert (width, height) == (4, 2)
    assert tuple(data[0:3]) == (0, 128, 255)
    assert tuple(data[9:12]) == (255, 128, 0)

    # Pillow 无法识别部分 16 位 PSD 时，公共缩略图入口必须使用合成图兜底。
    thumbnail = thumb_stream.load_thumbnail_rgb(str(path), 2)
    assert thumbnail is not None
    thumb_data, thumb_width, thumb_height = thumbnail
    assert (thumb_width, thumb_height) == (2, 1)
    assert tuple(thumb_data[0:3]) == (0, 128, 255)
    assert tuple(thumb_data[3:6]) == (255, 128, 0)


def test_rle_8_bit_psd_composite(tmp_path: Path) -> None:
    path = tmp_path / "rle.psd"
    _write_rle_8_bit_psd(path)

    result = load_psd_composite_rgb(path)
    assert result is not None
    data, width, height = result
    assert (width, height) == (3, 2)
    assert len(data) == width * height * 3
    assert set(tuple(data[offset:offset + 3]) for offset in range(0, len(data), 3)) == {
        (10, 20, 30)
    }


def test_truncated_psd_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "truncated.psd"
    _write_raw_16_bit_psd(path)
    path.write_bytes(path.read_bytes()[:-1])

    assert load_psd_composite_rgb(path) is None
