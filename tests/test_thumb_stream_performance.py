from io import BytesIO
from pathlib import Path
from PIL import Image
from app_common import thumb_stream


def _jpeg_bytes(size: tuple[int, int], color: tuple[int, int, int], *, progressive: bool = False) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=90, progressive=progressive)
    return buffer.getvalue()



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

    frames = list(thumb_stream.iter_thumbnail_rgb_progressive(str(image_path), 256))

    assert frames
    data, width, height = frames[-1]
    assert (width, height) == (256, 192)
    assert len(data) == width * height * 3


def test_raw_embedded_jpeg_uses_aspect_correct_draft(monkeypatch, tmp_path):
    path = tmp_path / "白鹭.ARW"
    path.write_bytes(b"raw placeholder")
    embedded = _jpeg_bytes((5616, 3744), (90, 120, 150))
    monkeypatch.setattr(thumb_stream, "_get_raw_thumbnail_bytes", lambda source: embedded)
    sizes = []
    original = thumb_stream._pil_to_rgb_thumb
    def convert(image, size):
        sizes.append(image.size)
        return original(image, size)
    monkeypatch.setattr(thumb_stream, "_pil_to_rgb_thumb", convert)
    result = thumb_stream.load_thumbnail_rgb(str(path), 2048)
    assert sizes == [(2808, 1872)]
    assert result is not None and result[1:] == (2048, 1365)
