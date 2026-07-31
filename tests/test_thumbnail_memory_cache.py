from __future__ import annotations

import sys
from types import SimpleNamespace

from PyQt6.QtGui import QColor, QImage

from app_common.file_browser import _thumbnail
from app_common.file_browser._thumbnail import ThumbnailMemoryCache


def _image(width: int, height: int, color: str = "#123456") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def test_thumbnail_cache_budget_is_quarter_ram_with_16_gib_cap(monkeypatch) -> None:
    gib = 1024**3
    fake_psutil = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(total=256 * gib),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert _thumbnail._compute_thumb_cache_max_bytes() == 16 * gib

    fake_psutil.virtual_memory = lambda: SimpleNamespace(total=16 * gib)
    assert _thumbnail._compute_thumb_cache_max_bytes() == 4 * gib


def test_non_jpeg_small_tier_does_not_satisfy_larger_request() -> None:
    cache = ThumbnailMemoryCache(max_bytes=10_000_000)
    cache.put("photo.png", 128, _image(128, 96))

    assert cache.get("photo.png", 512) is None
    small = cache.get("photo.png", 64)
    assert small is not None
    assert max(small.width(), small.height()) <= 64


def test_non_jpeg_later_small_put_cannot_downgrade_larger_base() -> None:
    cache = ThumbnailMemoryCache(max_bytes=10_000_000)
    cache.put("photo.heic", 512, _image(512, 384, "#102030"))
    cache.put("photo.heic", 128, _image(128, 96, "#abcdef"))

    large = cache.get("photo.heic", 512)
    assert large is not None
    assert (large.width(), large.height()) == (512, 384)
    assert large.pixelColor(0, 0) == QColor("#102030")


def test_non_jpeg_equal_or_larger_tier_upgrades_base() -> None:
    cache = ThumbnailMemoryCache(max_bytes=20_000_000)
    cache.put("photo.tiff", 128, _image(128, 96))
    cache.put("photo.tiff", 512, _image(512, 384))

    large = cache.get("photo.tiff", 512)
    assert large is not None
    assert (large.width(), large.height()) == (512, 384)
    assert cache.stats()["base_images"] == 1


def test_lru_eviction_keeps_most_recent_entry() -> None:
    one_image_bytes = _image(10, 10).sizeInBytes()
    cache = ThumbnailMemoryCache(max_bytes=one_image_bytes + 8)
    cache.put("first.png", 128, _image(10, 10, "#111111"))
    cache.put("second.png", 128, _image(10, 10, "#222222"))

    assert cache.get("first.png", 128) is None
    assert cache.get("second.png", 128) is not None
    assert cache.stats()["entries"] == 1
