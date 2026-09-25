from pathlib import Path
import sys
import time

import pytest
from app_common import video
from app_common.file_browser._workers import DirectoryScanWorker
from app_common.file_browser._browser_core import _resolve_thumb_source_path
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


def test_formats_duration_and_rotation():
    assert video.is_video('中文.MOV')
    assert not video.is_video('photo.jpg')
    assert video.format_duration(3661.9) == '1:01:01'
    assert video.format_duration(float('nan')) == '—'
    info = video.parse_ffmpeg_info('''Duration: 00:00:01.25, start: 0, bitrate: 128 kb/s
Stream #0:0: Video: h264 (High), yuv420p, 1920x1080, 29.97 fps
  displaymatrix: rotation of -90.00 degrees
Stream #0:1: Audio: aac, 48000 Hz, stereo, 96 kb/s
''')
    assert (info['width'], info['height']) == (1080, 1920)
    assert info['duration'] == 1.25
    assert info['fps'] == 29.97
    assert info['audio_tracks'] == 1
    with pytest.raises(RuntimeError, match='视频轨道'):
        video.parse_ffmpeg_info('Invalid data found when processing input')


def test_scanning_video_is_opt_in_and_scope_aware(tmp_path):
    for name in ('照片.JPG', '鸟.MP4', '._隐藏.mp4', 'note.txt'):
        (tmp_path / name).touch()
    (tmp_path / 'child').mkdir()
    (tmp_path / 'child' / '子目录.mov').touch()
    (tmp_path / '.cache').mkdir()
    (tmp_path / '.cache' / '隐藏.mp4').touch()
    def scan(recursive, video_enabled):
        results = []
        worker = DirectoryScanWorker(str(tmp_path), recursive, include_videos=video_enabled)
        worker.scan_finished.connect(lambda path, files, *_: results.extend(files))
        worker.run()
        return {Path(path).name for path in results}
    assert scan(False, False) == {'照片.JPG'}
    assert scan(False, True) == {'照片.JPG', '鸟.MP4'}
    assert scan(True, True) == {'照片.JPG', '鸟.MP4', '子目录.mov'}


def test_video_never_uses_same_stem_photo_preview(tmp_path):
    poster = tmp_path / 'photo.jpg'
    poster.touch()
    source = str(tmp_path / 'photo.mp4')
    assert _resolve_thumb_source_path(source, {'photo': {'temp_jpeg_path': str(poster)}}, str(tmp_path)) == source


def test_timeout_reaps_process_and_releases_slot():
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='超时'):
        video.run_video_tool([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.2)
    assert time.monotonic() - started < 2
    code, data, _ = video.run_video_tool([sys.executable, '-c', 'print("ready")'])
    assert code == 0 and data.strip() == b'ready'


def test_cancelled_request_does_not_launch(monkeypatch):
    monkeypatch.setattr(video.subprocess, 'Popen', lambda *a, **k: pytest.fail('cancelled subprocess launched'))
    with pytest.raises(RuntimeError, match='取消'):
        video.run_video_tool(['unused'], cancelled=lambda: True)


def test_video_columns_and_badge_survive_rebuild_and_sort(tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    from app_common.file_browser._models import FileTableModel, ThumbnailListModel, _VideoInfoRole
    from app_common.file_browser._browser_core import _FILE_TABLE_HEADERS, _SortRole
    parent = QWidget()
    parent.include_videos = True
    model = FileTableModel(parent)
    thumb = ThumbnailListModel(parent)
    path = str(tmp_path / '鸟.mp4')
    info = {'duration': 62, 'width': 1920, 'height': 1080, 'fps': 24, 'video_codec': 'h264'}
    for target in (model, thumb):
        target.rebuild([path], meta_cache={path: {'video_info': info}}, tooltip_fn=None, mismatch_fn=None)
    column = len(_FILE_TABLE_HEADERS)
    assert model.columnCount() == column + 4
    assert FileTableModel().columnCount() == column
    assert model.headerData(column, Qt.Orientation.Horizontal) == '时长'
    assert model.data(model.index(0, column)) == '01:02'
    assert model.data(model.index(0, column), _SortRole) == 62
    assert thumb.data(thumb.index(0, 0), _VideoInfoRole) == info
    assert model.data(model.index(0, 0)) == '▶ 鸟.mp4'


def test_report_scope_still_supplements_videos(tmp_path, monkeypatch):
    from app_common.file_browser import _workers
    photo = tmp_path / 'bird.jpg'
    photo.touch()
    child = tmp_path / 'child'
    child.mkdir()
    clip = child / 'bird.mp4'
    clip.touch()
    row = {'current_path': 'bird.jpg', 'original_path': 'bird.jpg'}
    monkeypatch.setattr(_workers, '_select_report_scope_files', lambda **k: ([str(photo)], {'bird': row}))
    results = []
    worker = DirectoryScanWorker(str(tmp_path), False, report_root=str(tmp_path),
                                 report_cache_full={'bird': row}, use_report_db=True, include_videos=True)
    worker.scan_finished.connect(lambda path, files, *_: results.extend(files))
    worker.run()
    assert set(results) == {str(photo), str(clip)}
