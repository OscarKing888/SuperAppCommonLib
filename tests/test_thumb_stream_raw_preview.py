from __future__ import annotations

import sys
from pathlib import Path

from app_common import thumb_stream


def test_raw_preview_prefers_rawpy_camera_jpeg(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "preview.arw"
    raw_path.write_bytes(b"raw-placeholder")
    rawpy_jpeg = b"\xff\xd8" + (b"r" * 512)
    piexif_jpeg = b"\xff\xd8" + (b"p" * 128)
    piexif_calls: list[str] = []

    class _Thumb:
        format = "jpeg"
        data = rawpy_jpeg

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def extract_thumb():
            return _Thumb()

    class _RawpyProbe:
        class ThumbFormat:
            JPEG = "jpeg"

        @staticmethod
        def imread(_path):
            return _Raw()

    class _PiexifProbe:
        @staticmethod
        def load(path):
            piexif_calls.append(path)
            return {"thumbnail": piexif_jpeg}

    monkeypatch.setitem(sys.modules, "rawpy", _RawpyProbe)
    monkeypatch.setitem(sys.modules, "piexif", _PiexifProbe)

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == rawpy_jpeg
    assert piexif_calls == []


def test_raw_preview_falls_back_to_piexif(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "fallback.arw"
    raw_path.write_bytes(b"raw-placeholder")
    piexif_jpeg = b"\xff\xd8" + (b"p" * 128)

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def extract_thumb():
            return None

    class _RawpyProbe:
        class ThumbFormat:
            JPEG = "jpeg"

        @staticmethod
        def imread(_path):
            return _Raw()

    class _PiexifProbe:
        @staticmethod
        def load(_path):
            return {"thumbnail": piexif_jpeg}

    monkeypatch.setitem(sys.modules, "rawpy", _RawpyProbe)
    monkeypatch.setitem(sys.modules, "piexif", _PiexifProbe)

    assert thumb_stream.get_raw_preview_jpeg(str(raw_path)) == piexif_jpeg
