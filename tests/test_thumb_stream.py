from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app_common import thumb_stream


def _jpeg_bytes(size: tuple[int, int], color: tuple[int, int, int], *, progressive: bool = False) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=90, progressive=progressive)
    return buffer.getvalue()


def test_baseline_jpeg_progressive_loader_yields_complete_frame(tmp_path: Path) -> None:
    image_path = tmp_path / "baseline.jpg"
    image_path.write_bytes(_jpeg_bytes((600, 400), (230, 220, 210), progressive=False))

    frames = list(thumb_stream.iter_thumbnail_rgb_progressive(str(image_path), 512))

    assert len(frames) == 1
    data, width, height = frames[0]
    assert width == 512
    assert height in (341, 342)
    assert min(data[-width * 3 :]) > 100


def test_raw_thumbnail_uses_embedded_jpeg_long_edge(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.arw"
    raw_path.write_bytes(b"raw placeholder")
    embedded = _jpeg_bytes((900, 600), (80, 130, 180), progressive=False)
    monkeypatch.setattr(thumb_stream, "get_raw_preview_jpeg", lambda path: embedded)

    result = thumb_stream.load_thumbnail_rgb(str(raw_path), 256)
    frames = list(thumb_stream.iter_thumbnail_rgb_progressive(str(raw_path), 256))

    assert result is not None
    assert result[1] == 256
    assert result[2] in (170, 171)
    assert len(frames) == 1
    assert frames[0][1:] == result[1:]


def test_raw_preview_prefers_rawpy_embedded_jpeg_over_piexif_thumbnail(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.arw"
    raw_path.write_bytes(b"raw placeholder")
    tiny = _jpeg_bytes((160, 120), (40, 40, 40), progressive=False)
    embedded = _jpeg_bytes((1200, 800), (80, 130, 180), progressive=False)

    class _Thumb:
        data = embedded
        format = object()

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_thumb(self):
            return _Thumb()

    class _Rawpy:
        ThumbFormat = type("ThumbFormat", (), {"JPEG": _Thumb.format})

        @staticmethod
        def imread(path):
            return _Raw()

    class _Piexif:
        @staticmethod
        def load(path):
            return {"thumbnail": tiny}

    monkeypatch.setattr(thumb_stream, "_run_exiftool_binary_tag", lambda path, tag: None)
    monkeypatch.setitem(__import__("sys").modules, "rawpy", _Rawpy)
    monkeypatch.setitem(__import__("sys").modules, "piexif", _Piexif)

    data = thumb_stream.get_raw_preview_jpeg(str(raw_path))

    assert data == embedded
