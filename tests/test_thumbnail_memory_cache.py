from __future__ import annotations

import ntpath
import posixpath
import sys
from types import SimpleNamespace

import pytest
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


@pytest.mark.parametrize(
    ("path_module", "directory", "retained_paths", "evicted_paths"),
    [
        pytest.param(
            ntpath,
            "C:\\",
            [r"C:\photo.jpg", r"C:\child\photo.png"],
            [r"D:\photo.jpg", r"D:\child\photo.png"],
            id="windows-volume-root",
        ),
        pytest.param(
            ntpath,
            "\\\\server\\share\\",
            [r"\\server\share\photo.jpg", r"\\server\share\child\photo.png"],
            [r"\\server\share2\photo.jpg", r"\\server\share2\child\photo.png"],
            id="windows-unc-root",
        ),
        pytest.param(
            posixpath,
            "/",
            ["/photo.jpg", "/child/photo.png"],
            [],
            id="posix-root",
        ),
        pytest.param(
            ntpath,
            r"C:\photos",
            [r"C:\photos\photo.jpg", r"C:\photos\child\photo.png"],
            [r"C:\photos2\photo.jpg", r"C:\photo.png"],
            id="windows-directory-boundary",
        ),
        pytest.param(
            posixpath,
            "/photos",
            ["/photos/photo.jpg", "/photos/child/photo.png"],
            ["/photos2/photo.jpg", "/photo.png"],
            id="posix-directory-boundary",
        ),
    ],
)
def test_evict_other_dirs_preserves_scope_and_byte_counts(
    monkeypatch, path_module, directory, retained_paths, evicted_paths
) -> None:
    # Exercise both path conventions on every host without changing global os.
    def normalize(path: str) -> str:
        return path_module.normcase(path_module.normpath(path))

    monkeypatch.setattr(_thumbnail, "os", SimpleNamespace(sep=path_module.sep))
    monkeypatch.setattr(_thumbnail, "_thumb_cache_key", normalize)
    cache = ThumbnailMemoryCache(max_bytes=10_000_000)
    image = _image(32, 24)
    image_bytes = image.sizeInBytes()
    for path in retained_paths + evicted_paths:
        cache.put(path, 128, image)

    before = cache.stats()
    assert before["entries"] == len(retained_paths) + len(evicted_paths)
    assert before["bytes"] == before["entries"] * image_bytes
    freed = cache.evict_other_dirs(normalize(directory))

    assert freed == len(evicted_paths) * image_bytes
    after = cache.stats()
    assert after["entries"] == len(retained_paths)
    assert after["bytes"] == len(retained_paths) * image_bytes
    assert before["bytes"] - after["bytes"] == freed
    for path in retained_paths:
        cached = cache.get(path, 128)
        assert cached is not None and not cached.isNull()
    for path in evicted_paths:
        assert cache.get(path, 128) is None
    assert cache.evict_other_dirs(normalize(directory)) == 0
    assert cache.stats() == after
