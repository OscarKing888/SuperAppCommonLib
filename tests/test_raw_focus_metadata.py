"""RAW focus readers route Canon containers without changing Sony's fast path."""
import json
from types import SimpleNamespace

import pytest

from app_common import raw_focus_metadata as reader
from app_common.canon_focus import CANON_FOCUS_TAGS
from app_common.exif_io.writer import FOCUS_METADATA_TAGS, DEFAULT_METADATA_TAGS


def test_batch_profiles_include_canon_arrays_and_masks():
    assert set(CANON_FOCUS_TAGS) <= set(FOCUS_METADATA_TAGS)
    assert set(CANON_FOCUS_TAGS) <= set(DEFAULT_METADATA_TAGS)


@pytest.mark.parametrize("extension", ["CR3", "cr2", "CRW"])
def test_canon_reads_source_metadata_with_owned_exiftool_runner(tmp_path, monkeypatch, extension):
    path = tmp_path / f"未命名照片(1).{extension}"
    path.write_bytes(b"source must stay unchanged")
    metadata = {"SourceFile": str(path), "IFD0:Make": "Canon", "Canon:ValidAFPoints": 1}
    calls = []
    monkeypatch.setattr(reader, "exifread", None)
    monkeypatch.setattr(reader, "get_exiftool_executable_path", lambda: "bundled-exiftool")

    def run(executable, args, **kwargs):
        calls.append((executable, args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps([metadata]), stderr="")

    monkeypatch.setattr(reader, "run_exiftool", run)
    assert reader.read_raw_embedded_focus_metadata(path) == metadata
    executable, args, kwargs = calls[0]
    assert executable == "bundled-exiftool" and args[-1] == str(path)
    assert set(CANON_FOCUS_TAGS) <= set(args)
    assert "-n" in args and "filename=UTF8" in args
    assert kwargs["encoding"] == "utf-8" and kwargs["timeout"] == 20
    assert path.read_bytes() == b"source must stay unchanged"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("returncode,payload", [(1, ""), (0, "broken"), (0, "[]"), (0, "{}")])
def test_failed_canon_read_returns_no_metadata(tmp_path, monkeypatch, returncode, payload):
    monkeypatch.setattr(reader, "get_exiftool_executable_path", lambda: "exiftool")
    monkeypatch.setattr(reader, "run_exiftool", lambda *args, **kwargs:
                        SimpleNamespace(returncode=returncode, stdout=payload, stderr="read error"))
    assert reader.read_raw_embedded_focus_metadata(tmp_path / "sample.CR3") == {}


def test_sony_embedded_reader_does_not_start_exiftool(tmp_path, monkeypatch):
    path = tmp_path / "sample.ARW"
    path.write_bytes(b"raw")
    monkeypatch.setattr(reader, "read_tiff_exif_tags", lambda handle: {
        "Image Make": SimpleNamespace(values="SONY"),
        "MakerNote Tag 0x2027": SimpleNamespace(values=[6000, 4000, 1000, 1500]),
    })
    monkeypatch.setattr(reader, "run_exiftool", lambda *args, **kwargs: pytest.fail("Sony must stay in-process"))
    assert reader.read_raw_embedded_focus_metadata(path)["Make"] == "SONY"


def test_canon_tiff_basic_tags_do_not_hide_missing_af_arrays(tmp_path, monkeypatch):
    path = tmp_path / "sample.DNG"
    path.write_bytes(b"raw")
    monkeypatch.setattr(reader, "read_tiff_exif_tags", lambda handle: {
        "Image Make": SimpleNamespace(values="Canon"),
    })
    monkeypatch.setattr(reader, "_read_canon_focus_metadata", lambda path: {"Canon:AFImageWidth": 6000})
    assert reader.read_raw_embedded_focus_metadata(path)["Canon:AFImageWidth"] == 6000
