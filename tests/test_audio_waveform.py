import math
import os
import struct
import sys
import threading
import wave

import pytest

from app_common import audio_waveform as waveform
from app_common.video import find_ffmpeg, run_video_tool


def write_audio(path, *, silent=False):
    # 12 kHz 反相双声道；若低采样率解码或先混音，响声会消失。
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        samples = bytearray()
        for index in range(48000):
            amplitude = 0 if silent or not 12000 <= index < 36000 else 16000
            value = round(amplitude * math.sin(2 * math.pi * 12000 * index / 48000))
            samples.extend(struct.pack('<hh', value, -value))
        output.writeframes(samples)


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / '中文 反相音轨.wav'
    write_audio(path)
    return path


def test_real_peak_timing_high_frequency_and_antiphase(audio):
    peaks = waveform.audio_waveform(audio, 1, bins=100)
    assert len(peaks) == 100
    assert max(peaks[:24]) == 0
    assert min(peaks[26:74]) > .45
    assert max(peaks[76:]) == 0


def test_delayed_short_audio_is_aligned_to_video(audio, tmp_path):
    movie = tmp_path / '延迟音轨.mkv'
    code, _, error = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=10:d=2',
        '-itsoffset', '0.5', '-i', str(audio), '-c:v', 'libx264', '-c:a', 'pcm_s16le', str(movie),
    ])
    assert code == 0, error.decode()
    peaks = waveform.audio_waveform(movie, 2, bins=200)
    assert max(peaks[:73]) == 0
    assert min(peaks[77:123]) > .45
    assert max(peaks[128:]) == 0


def test_silence_cache_hit_and_file_invalidation(audio, monkeypatch):
    original = waveform.run_video_tool
    first = waveform.audio_waveform(audio, 1, bins=100)
    monkeypatch.setattr(waveform, 'run_video_tool', lambda *a, **kw: pytest.fail('cache miss'))
    assert waveform.audio_waveform(audio, 1, bins=100) is first
    with pytest.raises(RuntimeError, match='取消'):
        waveform.audio_waveform(audio, 1, bins=100, cancelled=lambda: True)
    monkeypatch.setattr(waveform, 'run_video_tool', original)
    before = audio.stat()
    write_audio(audio, silent=True)
    os.utime(audio, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert max(waveform.audio_waveform(audio, 1, bins=100)) == 0


def test_cache_is_bounded_and_failures_are_not_cached(audio, monkeypatch):
    monkeypatch.setattr(waveform, '_cache', waveform.OrderedDict())
    monkeypatch.setattr(waveform, 'run_video_tool', lambda *a, **kw:
                        (0, b'frame:0 pts:0 pts_time:0\nlavfi.astats.Overall.Peak_level=-6\n', b''))
    for count in range(32, 52):
        waveform.audio_waveform(audio, 1, bins=count)
    assert len(waveform._cache) == 16
    before = tuple(waveform._cache)
    monkeypatch.setattr(waveform, 'run_video_tool', lambda *a, **kw: (1, b'', b'bad audio'))
    with pytest.raises(RuntimeError, match='bad audio'):
        waveform.audio_waveform(audio, 1, bins=52)
    assert tuple(waveform._cache) == before


def test_inflight_cancel_reaps_decoder(audio, monkeypatch):
    from app_common import video
    processes = []
    popen = video.subprocess.Popen
    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(video.subprocess, 'Popen', capture)
    monkeypatch.setattr(waveform, 'run_video_tool', lambda args, **kw:
                        video.run_video_tool([sys.executable, '-c', 'import time; time.sleep(10)'], **kw))
    cancelled = threading.Event()
    timer = threading.Timer(.2, cancelled.set)
    timer.start()
    try:
        with pytest.raises(RuntimeError, match='取消'):
            waveform.audio_waveform(audio, 1, cancelled=cancelled.is_set)
    finally:
        timer.cancel()
        timer.join()
    assert processes and all(process.poll() is not None for process in processes)


@pytest.mark.parametrize('duration', [0, -1, float('nan'), float('inf')])
def test_unknown_duration_does_not_decode(audio, duration, monkeypatch):
    monkeypatch.setattr(waveform, 'run_video_tool', lambda *a, **kw: pytest.fail('invalid duration decoded'))
    with pytest.raises(ValueError):
        waveform.audio_waveform(audio, duration)
