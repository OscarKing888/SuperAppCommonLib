import io
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest

exifread = pytest.importorskip("exifread")

from app_common.exif_io import fast_reader, tiff_reader
from app_common import raw_focus_metadata
from app_common.focus_calc import extract_focus_box_for_display


def _sony_tiff(endian="<", note_size=40000):
    """Small synthetic ARW header with a realistically large MakerNote."""
    data = bytearray(256 + note_size)
    data[:8] = (b"II*\0" if endian == "<" else b"MM\0*") + struct.pack(endian + "I", 8)

    def ifd(offset, entries):
        struct.pack_into(endian + "H", data, offset, len(entries))
        for i, (tag, kind, count, value) in enumerate(entries):
            entry = offset + 2 + 12 * i
            struct.pack_into(endian + "HHI", data, entry, tag, kind, count)
            struct.pack_into(endian + ("H" if kind == 3 and count == 1 else "I"), data, entry + 8, value)

    ifd(8, [(0x010f, 2, 5, 100), (0x0110, 2, 9, 110),
            (0x0112, 3, 1, 8), (0x8769, 4, 1, 128)])
    data[100:105] = b"SONY\0"
    data[110:119] = b"ILCE-1M2\0"
    ifd(128, [(0x8827, 3, 1, 640), (0x927c, 7, note_size, 256),
              (0xa002, 4, 1, 8640), (0xa003, 4, 1, 5760)])
    ifd(256, [(0x2027, 3, 4, 320), (0x204a, 3, 4, 328),
              (0x2037, 7, 4, 0x01e00280)])
    struct.pack_into(endian + "8H", data, 320, 8640, 5760, 4954, 3324, 8640, 5760, 4955, 3324)
    return bytes(data)


def _legacy(data):
    return exifread.process_file(io.BytesIO(data), details=True, extract_thumbnail=False)


def _values(tags):
    return {key: (tag.values, tag.printable, tag.field_offset, tag.field_length)
            for key, tag in tags.items()}


@pytest.mark.parametrize("endian", ["<", ">"])
def test_bulk_reader_preserves_all_tags_and_avoids_per_byte_io(endian):
    class CountedFile(io.BytesIO):
        reads = 0

        def read(self, count=-1):
            self.reads += 1
            return super().read(count)

    data = _sony_tiff(endian)
    handle = CountedFile(data)
    actual = tiff_reader.read_tiff_exif_tags(handle)
    assert _values(actual) == _values(_legacy(data))
    assert actual["MakerNote Tag 0x2027"].values == [8640, 5760, 4954, 3324]
    assert handle.reads < 200  # Regression: a 40 KB MakerNote took >40,000 reads.


def test_parallel_readers_do_not_patch_exifread_globals():
    header_type = exifread.ExifHeader
    data = _sony_tiff()
    expected = _values(_legacy(data))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _values(tiff_reader.read_tiff_exif_tags(io.BytesIO(data))), range(12)))
    assert all(result == expected for result in results)
    assert exifread.ExifHeader is header_type


def test_browser_and_raw_focus_keep_existing_results(tmp_path, monkeypatch):
    path = tmp_path / "中文样例.ARW"
    path.write_bytes(_sony_tiff())
    actual_focus = raw_focus_metadata.read_raw_embedded_focus_metadata(path)
    actual_capture = fast_reader.fast_read_one(str(path))
    monkeypatch.setattr(raw_focus_metadata, "read_tiff_exif_tags", lambda handle: None)
    monkeypatch.setattr(fast_reader, "read_tiff_exif_tags", lambda handle: None)
    assert actual_focus == raw_focus_metadata.read_raw_embedded_focus_metadata(path)
    assert actual_capture == fast_reader.fast_read_one(str(path))
    assert actual_capture["ISO"] == "640"
    assert extract_focus_box_for_display(actual_focus, 8640, 5760, camera_type="ilce_a1m2") is not None


@pytest.mark.parametrize("data", [b"not a TIFF", b"II+\0\x08\0\0\0"])
def test_unsupported_container_uses_existing_fallback(data):
    assert tiff_reader.read_tiff_exif_tags(io.BytesIO(data)) is None


def test_incompatible_exifread_uses_existing_fallback(monkeypatch):
    monkeypatch.setattr(tiff_reader, "_BulkByteExifHeader", lambda *a, **kw: (_ for _ in ()).throw(TypeError("old API")))
    assert tiff_reader.read_tiff_exif_tags(io.BytesIO(_sony_tiff())) is None


@pytest.mark.parametrize("count", [40000, 0xffffffff])
def test_damaged_makernote_does_not_allocate_or_loop_over_claimed_size(tmp_path, count):
    data = bytearray(_sony_tiff()[:1024])
    struct.pack_into("<I", data, 128 + 2 + 12 + 4, count)
    with pytest.raises(ValueError):
        tiff_reader.read_tiff_exif_tags(io.BytesIO(data))
    path = tmp_path / "损坏.ARW"
    path.write_bytes(data)
    assert raw_focus_metadata.read_raw_embedded_focus_metadata(path) == {}
    assert fast_reader.fast_read_one(str(path)) is None
