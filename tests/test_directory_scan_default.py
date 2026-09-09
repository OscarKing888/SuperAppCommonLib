from pathlib import Path

from app_common.file_browser._workers import DirectoryScanWorker


def test_default_scan_ignores_report_database_and_cached_report_paths(tmp_path, monkeypatch):
    nested = tmp_path / "照片" / "优选"
    nested.mkdir(parents=True)
    photo = nested / "白鹭.HIF"
    photo.write_bytes(b"directory scan does not decode images")
    report_dir = tmp_path / ".superpicky"
    report_dir.mkdir()
    report_path = report_dir / "report.db"
    report_path.write_bytes(b"must not be opened by default listing")
    opened = []

    def forbid_report_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError("default listing must not open report.db")

    monkeypatch.setattr("app_common.file_browser._workers.ReportDB.open_if_exists", forbid_report_open)
    results = []
    worker = DirectoryScanWorker(
        str(tmp_path), True, report_root=str(tmp_path),
        report_cache_full={"白鹭": {"filename": "白鹭", "current_path": "旧目录/白鹭.HIF"}},
    )
    worker.scan_finished.connect(lambda *args: results.append(args))
    worker.run()

    assert not opened
    assert len(results) == 1
    _, files, cache, full_cache = results[0]
    assert [Path(path) for path in files] == [photo]
    assert cache == {}
    assert full_cache is None
    assert report_path.read_bytes() == b"must not be opened by default listing"
