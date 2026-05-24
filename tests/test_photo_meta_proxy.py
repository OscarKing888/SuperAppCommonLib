import os
from pathlib import Path

from app_common.exif_io.photo_meta import PhotoMetaDataProxy


class _FakeExifMeta:
    def read(self, path: str) -> dict:
        return {"SourceFile": path, "XMP-xmp:Rating": "1"}

    def read_batch(self, paths: list[str]) -> dict:
        return {os.path.normpath(path): self.read(path) for path in paths}

    def write(self, path: str, fields: dict) -> bool:
        return True


class _FakeXmpMeta:
    def __init__(self, rating: str = "4") -> None:
        self.rating = rating

    def read(self, path: str) -> dict:
        return {"SourceFile": path, "XMP-xmp:Rating": self.rating}

    def read_batch(self, paths: list[str]) -> dict:
        return {os.path.normpath(path): self.read(path) for path in paths}

    def write(self, path: str, fields: dict) -> bool:
        return True


def test_proxy_merges_sidecar_over_exif_and_normalizes_rating() -> None:
    proxy = PhotoMetaDataProxy(exif=_FakeExifMeta(), xmp=_FakeXmpMeta("4"))

    assert proxy.read("/tmp/img.jpg")["rating"] == 4
    assert proxy.read_batch(["/tmp/img.jpg"])[os.path.normpath("/tmp/img.jpg")]["rating"] == 4


def test_proxy_writes_rating_pick_to_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    assert PhotoMetaDataProxy().write(str(photo_path), {"rating": 5, "pick": 1})
    assert (tmp_path / "img001.xmp").is_file()
    assert PhotoMetaDataProxy().read(str(photo_path)).get("rating") == 5
    assert PhotoMetaDataProxy().read(str(photo_path)).get("pick") == 1
