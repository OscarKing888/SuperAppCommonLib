# -*- coding: utf-8 -*-
"""连拍信息计算：拍摄时间解析、分组编号、sidecar 写入决策与 XMP 读回。"""
import os

from app_common import burst_info as bi
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._models import _metadata_burst_values


def _t(seconds: float, subsec: bool = True) -> bi.CaptureTime:
    return bi.CaptureTime(1_700_000_000.0 + seconds, subsec)


def test_capture_time_prefers_composite_subsecond_text() -> None:
    rec = {
        "Composite:SubSecDateTimeOriginal": "2026:10:02 15:55:00.045+08:00",
        "ExifIFD:DateTimeOriginal": "2026:10:02 15:55:00",
        # exiftool -n 把 "045" 读成整数 45，不能拿来拼亚秒
        "ExifIFD:SubSecTimeOriginal": 45,
    }
    parsed = bi.capture_time_from_metadata(rec)
    base = bi.capture_time_from_metadata({"ExifIFD:DateTimeOriginal": "2026:10:02 15:55:00"})
    assert parsed.has_subsec and not base.has_subsec
    assert abs(parsed.seconds - base.seconds - 0.045) < 1e-6


def test_capture_time_fallbacks() -> None:
    text_subsec = bi.capture_time_from_metadata(
        {"ExifIFD:DateTimeOriginal": "2026:10:02 15:55:00", "ExifIFD:SubSecTimeOriginal": "045"}
    )
    int_subsec = bi.capture_time_from_metadata(
        {"ExifIFD:DateTimeOriginal": "2026:10:02 15:55:00", "ExifIFD:SubSecTimeOriginal": 45}
    )
    xmp = bi.capture_time_from_metadata({"XMP-exif:DateTimeOriginal": "2026-10-02T15:55:00.5"})
    assert text_subsec.has_subsec and not int_subsec.has_subsec
    assert abs(text_subsec.seconds - int_subsec.seconds - 0.045) < 1e-6
    assert xmp.has_subsec and abs(xmp.seconds - int_subsec.seconds - 0.5) < 1e-6
    assert bi.capture_time_from_metadata({"CreateDate": "2026:10:02 15:55:01"}) is not None
    assert bi.capture_time_from_metadata({"DateTimeOriginal": "0000:00:00 00:00:00"}) is None
    assert bi.capture_time_from_metadata(None) is None


def test_plan_groups_by_gap_and_min_count_per_directory() -> None:
    a = os.path.normpath("/photos/day1")
    b = os.path.normpath("/photos/day2")
    times = {}
    # day1：4 张 50ms 连拍 + 间隔 2s 的 2 张（不足 4 张）+ 再 5 张连拍
    offsets = [0, 0.05, 0.10, 0.15, 2.0, 2.05, 5.0, 5.05, 5.10, 5.15, 5.20]
    for index, offset in enumerate(offsets):
        times[os.path.join(a, f"IMG_{index:02d}.ARW")] = _t(offset)
    for index in range(4):
        times[os.path.join(b, f"DSC_{index}.JPG")] = _t(index * 0.2)
    times[os.path.join(b, "NO_TIME.JPG")] = None
    plan, stats = bi.plan_bursts(list(times), times, max_gap_ms=250, min_count=4)

    assert [plan[os.path.join(a, f"IMG_{i:02d}.ARW")] for i in range(4)] == [(1, 1), (1, 2), (1, 3), (1, 4)]
    assert plan[os.path.join(a, "IMG_04.ARW")] is None and plan[os.path.join(a, "IMG_05.ARW")] is None
    assert plan[os.path.join(a, "IMG_10.ARW")] == (2, 5)
    # 每个目录独立从 1 编号
    assert plan[os.path.join(b, "DSC_0.JPG")] == (1, 1)
    assert plan[os.path.join(b, "NO_TIME.JPG")] is None
    assert (stats.directories, stats.shots, stats.timed_shots, stats.groups, stats.burst_shots) == (2, 16, 15, 3, 13)


def test_plan_orders_by_time_not_name_and_keeps_raw_jpeg_pairs_together() -> None:
    d = os.path.normpath("/photos")
    times = {}
    for index, name in enumerate(["C", "A", "D", "B"]):
        times[os.path.join(d, f"{name}.ARW")] = _t(index * 0.1)
        times[os.path.join(d, f"{name}.JPG")] = _t(index * 0.1)
    plan, stats = bi.plan_bursts(list(times), times, min_count=4)
    assert stats.shots == 4
    assert [plan[os.path.join(d, f"{n}.ARW")] for n in "CADB"] == [(1, 1), (1, 2), (1, 3), (1, 4)]
    assert all(plan[os.path.join(d, f"{n}.JPG")] == plan[os.path.join(d, f"{n}.ARW")] for n in "CADB")


def test_plan_without_subseconds_tolerates_whole_second_steps() -> None:
    d = os.path.normpath("/photos")
    times = {os.path.join(d, f"{i}.JPG"): _t(float(i // 3), subsec=False) for i in range(6)}
    times[os.path.join(d, "late.JPG")] = _t(4.0, subsec=False)
    plan, stats = bi.plan_bursts(list(times), times, max_gap_ms=100, min_count=4)
    assert stats.groups == 1 and stats.burst_shots == 6
    assert plan[os.path.join(d, "late.JPG")] is None


def test_sidecar_fields_decisions() -> None:
    assert bi.sidecar_burst_fields((3, 2), ("", ""), False) == {bi.BURST_ID_FIELD: "3", bi.BURST_POSITION_FIELD: "2"}
    assert bi.sidecar_burst_fields((3, 2), ("3", "2"), True) is None
    # 不在连拍：report.db 有旧分组 → 写 0 覆盖；只有 sidecar 旧值 → 删除；都没有 → 不建 sidecar
    assert bi.sidecar_burst_fields(None, ("", ""), True) == {bi.BURST_ID_FIELD: "0", bi.BURST_POSITION_FIELD: "0"}
    assert bi.sidecar_burst_fields(None, ("0", "0"), True) is None
    assert bi.sidecar_burst_fields(None, ("5", "1"), False) == {bi.BURST_ID_FIELD: "", bi.BURST_POSITION_FIELD: ""}
    assert bi.sidecar_burst_fields(None, ("", ""), False) is None


def test_zero_burst_values_display_as_not_burst() -> None:
    assert _metadata_burst_values({"burst_id": 0, "burst_position": "0"}) == (None, None)
    assert _metadata_burst_values({"burst_id": "12", "burst_position": 3}) == (3, 12)


class _FakeReport:
    def __init__(self, rows):
        self.rows = {os.path.normpath(k): v for k, v in rows.items()}

    def row_for(self, path):
        return self.rows.get(os.path.normpath(path))


def test_write_plan_round_trips_xmp_and_is_idempotent(tmp_path, monkeypatch) -> None:
    from app_common.exif_io.photo_meta import PhotoMetaDataReportDB

    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    folder = tmp_path / "连拍目录"
    folder.mkdir()
    names = ["鹰鹃_1.jpg", "鹰鹃_2.jpg", "旧连拍.jpg", "报告连拍.jpg", "单张.jpg"]
    for name in names:
        (folder / name).write_bytes(b"")
    p = {name: os.path.normpath(str(folder / name)) for name in names}
    xmp = PhotoMetaDataXMP()
    assert xmp.write(p["旧连拍.jpg"], {"XMP-dc:Title": "保留标题", bi.BURST_ID_FIELD: "9", bi.BURST_POSITION_FIELD: "1"})
    plan = {p["鹰鹃_1.jpg"]: (1, 1), p["鹰鹃_2.jpg"]: (1, 2), p["旧连拍.jpg"]: None,
            p["报告连拍.jpg"]: None, p["单张.jpg"]: None}
    report = _FakeReport({p["报告连拍.jpg"]: {"burst_id": 4, "burst_position": 2}})
    outcomes = []

    written = bi.write_burst_plan(plan, xmp=xmp, report=report, on_outcome=lambda o, d, t: outcomes.append(o))

    assert written == 4 and len(outcomes) == 5
    assert xmp.read(p["鹰鹃_2.jpg"])[bi.BURST_ID_FIELD] == "1"
    assert xmp.read(p["鹰鹃_2.jpg"])[bi.BURST_POSITION_FIELD] == "2"
    old = xmp.read(p["旧连拍.jpg"])
    assert bi.BURST_ID_FIELD not in old and old["XMP-dc:Title"] == "保留标题"
    assert xmp.read(p["报告连拍.jpg"])[bi.BURST_ID_FIELD] == "0"
    assert not os.path.exists(os.path.splitext(p["单张.jpg"])[0] + ".xmp")
    assert bi.write_burst_plan(plan, xmp=xmp, report=report) == 0


def test_browser_meta_updates_override_report_keys() -> None:
    updates = bi.browser_meta_updates({bi.BURST_ID_FIELD: "0", bi.BURST_POSITION_FIELD: "0"})
    assert updates["burst_id"] == updates["report.burst_id"] == updates[bi.BURST_ID_FIELD] == "0"
    assert _metadata_burst_values({"report.burst_id": 5, **updates}) == (None, None)


def test_collect_directory_images_skips_hidden_and_respects_recursion(tmp_path) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "a.xmp").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden.jpg").write_bytes(b"")
    sub = tmp_path / "子目录"
    sub.mkdir()
    (sub / "b.jpg").write_bytes(b"")
    flat = bi.collect_directory_images(str(tmp_path))
    deep = bi.collect_directory_images(str(tmp_path), recursive=True)
    assert [os.path.basename(p) for p in flat] == ["a.ARW"]
    assert [os.path.basename(p) for p in deep] == ["a.ARW", "b.jpg"]
