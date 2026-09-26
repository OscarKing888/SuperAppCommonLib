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


def _install_fake_rawpy(monkeypatch, embedded: bytes | None, opened: list | None = None) -> None:
    class _Thumb:
        data = embedded
        format = "jpeg"

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_thumb(self):
            if embedded is None:
                raise RuntimeError("no thumbnail")
            return _Thumb()

    class _Rawpy:
        ThumbFormat = type("ThumbFormat", (), {"JPEG": "jpeg"})

        @staticmethod
        def imread(source):
            if opened is not None:
                opened.append(source)
            return _Raw()

    monkeypatch.setitem(__import__("sys").modules, "rawpy", _Rawpy)


def test_raw_preview_uses_inprocess_libraw_without_exiftool_process(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.arw"
    raw_path.write_bytes(b"raw placeholder")
    embedded = _jpeg_bytes((2400, 1600), (80, 130, 180))
    exiftool_calls: list[str] = []
    _install_fake_rawpy(monkeypatch, embedded)
    monkeypatch.setattr(thumb_stream, "_run_exiftool_binary_tag", lambda path, tag: exiftool_calls.append(tag))

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == embedded
    assert exiftool_calls == []


def test_raw_preview_small_inprocess_preview_falls_back_to_larger_exiftool_preview(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.arw"
    raw_path.write_bytes(b"raw placeholder")
    small = _jpeg_bytes((640, 424), (40, 40, 40))
    large = _jpeg_bytes((1616, 1080), (80, 130, 180))
    exiftool_calls: list[str] = []
    _install_fake_rawpy(monkeypatch, small)

    def _exiftool(path, tag):
        exiftool_calls.append(tag)
        return large if tag == "PreviewImage" else None

    monkeypatch.setattr(thumb_stream, "_run_exiftool_binary_tag", _exiftool)

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == large
    assert exiftool_calls == ["JpgFromRaw", "PreviewImage"]


def test_raw_preview_keeps_larger_inprocess_preview_over_smaller_exiftool_tag(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.arw"
    raw_path.write_bytes(b"raw placeholder")
    inprocess = _jpeg_bytes((1200, 800), (80, 130, 180))
    thumbnail = _jpeg_bytes((160, 120), (40, 40, 40))
    _install_fake_rawpy(monkeypatch, inprocess)
    monkeypatch.setattr(
        thumb_stream,
        "_run_exiftool_binary_tag",
        lambda path, tag: thumbnail if tag == "ThumbnailImage" else None,
    )

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == inprocess


def test_raw_preview_without_rawpy_keeps_exiftool_order(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.arw"
    raw_path.write_bytes(b"raw placeholder")
    jpg_from_raw = _jpeg_bytes((1200, 800), (80, 130, 180))
    _install_fake_rawpy(monkeypatch, None)
    calls: list[str] = []
    monkeypatch.setattr(
        thumb_stream,
        "_run_exiftool_binary_tag",
        lambda path, tag: calls.append(tag) or (jpg_from_raw if tag == "JpgFromRaw" else None),
    )

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == jpg_from_raw
    assert calls == ["JpgFromRaw"]


def test_raw_preview_non_ascii_path_uses_file_object_on_windows(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "中文目录" / "鸟.arw"
    raw_path.parent.mkdir()
    raw_path.write_bytes(b"raw placeholder")
    embedded = _jpeg_bytes((2400, 1600), (80, 130, 180))
    opened: list = []
    _install_fake_rawpy(monkeypatch, embedded, opened)
    monkeypatch.setattr(thumb_stream.sys, "platform", "win32")
    monkeypatch.setattr(thumb_stream, "_run_exiftool_binary_tag", lambda path, tag: None)

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == embedded
    assert len(opened) == 1 and hasattr(opened[0], "read")
    assert opened[0].closed


def test_draft_box_keeps_aspect_ratio_so_large_tiers_still_scale() -> None:
    assert thumb_stream.draft_box_for_long_edge(5616, 3744, 2048) == (2048, 1365)
    assert thumb_stream.draft_box_for_long_edge(3744, 5616, 2048) == (1365, 2048)
    assert thumb_stream.draft_box_for_long_edge(800, 600, 2048) == (800, 600)
    data = _jpeg_bytes((5616, 3744), (90, 120, 150))
    with Image.open(BytesIO(data)) as square:
        square.draft("RGB", (2048, 2048))
        assert square.size == (5616, 3744)  # the old square box never scaled this landscape image
    with Image.open(BytesIO(data)) as img:
        thumb_stream._apply_jpeg_draft(img, 2048)
        assert img.size == (2808, 1872)


def _oriented_jpeg(path: Path, orientation: int) -> None:
    image = Image.new("RGB", (600, 400), (200, 30, 30))
    image.paste((30, 30, 200), (300, 0, 600, 400))
    exif = Image.Exif()
    exif[0x0112] = orientation
    image.save(path, format="JPEG", quality=95, exif=exif)


def test_thumbnail_applies_exif_orientation_after_shrinking(tmp_path: Path) -> None:
    from PIL import ImageOps

    for orientation in range(1, 9):
        path = tmp_path / f"o{orientation}.jpg"
        _oriented_jpeg(path, orientation)
        data, width, height = thumb_stream.load_thumbnail_rgb(str(path), 128)
        with Image.open(path) as reference:
            reference.draft("RGB", (128, 85))
            expected = ImageOps.exif_transpose(reference)
            expected.thumbnail((128, 128), Image.LANCZOS)
            expected = expected.convert("RGB")
        assert (width, height) == expected.size
        actual = Image.frombytes("RGB", (width, height), data)
        diff = max(abs(a - b) for a, b in zip(actual.tobytes(), expected.tobytes()))
        assert diff <= 1, (orientation, diff)


def test_progressive_final_frame_reuses_parser_image_without_second_decode(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "progressive.jpg"
    image_path.write_bytes(_jpeg_bytes((1600, 1200), (120, 160, 200), progressive=True))

    def _no_second_decode(*_args, **_kwargs):
        raise AssertionError("final progressive frame must reuse the parser image")

    monkeypatch.setattr(thumb_stream, "load_thumbnail_rgb", _no_second_decode)
    monkeypatch.setattr(thumb_stream, "_load_thumbnail_rgb_from_jpeg_bytes", _no_second_decode)

    frames = list(thumb_stream.iter_thumbnail_rgb_progressive(str(image_path), 256))

    assert frames
    data, width, height = frames[-1]
    assert (width, height) == (256, 192)
    assert len(data) == width * height * 3
