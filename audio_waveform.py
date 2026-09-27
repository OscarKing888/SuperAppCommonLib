# -*- coding: utf-8 -*-
"""Bounded audio peak envelopes on the video timeline; no Qt dependency."""
from __future__ import annotations

from collections import OrderedDict
import math
import os
import re
import threading

from .video import find_ffmpeg, run_video_tool

_SAMPLE_RATE = 48000
_CACHE_LIMIT = 16
_cache = OrderedDict()
_cache_lock = threading.Lock()


def _parse_peaks(text, duration, bins, window_seconds):
    peaks = [0.0] * bins
    timestamp = None
    frames = 0
    for line in text.splitlines():
        match = re.search(r'pts_time:([\d.eE+\-]+)', line)
        if match:
            timestamp = float(match[1])
        elif line.startswith('lavfi.astats.Overall.Peak_level=') and timestamp is not None:
            level = float(line.split('=', 1)[1])
            peak = min(1.0, 10 ** (min(0.0, level) / 20)) if math.isfinite(level) else 0.0
            # 用音频时间戳定位，保留音轨开头/结尾的空白，不能拉伸成整段视频。
            start = max(0, math.floor(timestamp / duration * bins + 1e-6))
            end = min(bins, math.ceil((timestamp + window_seconds) / duration * bins - 1e-6))
            for index in range(start, end):
                peaks[index] = max(peaks[index], peak)
            frames += 1
    if not frames:
        raise RuntimeError('音轨没有可读取的波形数据')
    return tuple(peaks)


def audio_waveform(path, duration, *, bins=2048, cancelled=lambda: False, timeout=120.0):
    """Read first audio track as at most 4096 peak bins, retaining channel peaks.

    FFmpeg reduces each window before sending text over the pipe: neither full
    PCM nor video frames are buffered in Python. The 16-entry LRU is keyed by
    path/size/mtime/duration/resolution. Failed or cancelled work is not cached.
    Call from a worker; cancellation/timeout reaps the shared bounded process.
    """
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('生成音频波形需要有效视频时长')
    bins = max(16, min(4096, int(bins)))
    path = os.path.abspath(os.fspath(path))
    stat = os.stat(path)
    key = (os.path.normcase(path), stat.st_size, stat.st_mtime_ns, duration, bins)
    if cancelled():
        raise RuntimeError('音频波形读取已取消')
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    samples = max(1, math.ceil(duration * _SAMPLE_RATE / bins))
    # 不混合声道，避免左右声道反相相消；整体峰值覆盖所有声道。
    filters = (f'aresample={_SAMPLE_RATE},atrim=duration={duration:.9f},'
               f'asetnsamples=n={samples}:p=1,'
               'astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,'
               'ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file=-')
    code, data, error = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-threads', '1', '-i', path, '-map', '0:a:0', '-vn', '-sn', '-dn',
        '-af', filters, '-threads', '1', '-f', 'null', '-',
    ], cancelled=cancelled, timeout=timeout)
    if code:
        raise RuntimeError(error.decode('utf-8', errors='replace').strip()[:500] or '无法读取音频波形')
    if cancelled():
        raise RuntimeError('音频波形读取已取消')
    result = _parse_peaks(data.decode('utf-8', errors='replace'), duration, bins, samples / _SAMPLE_RATE)
    after = os.stat(path)
    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise RuntimeError('视频已改变，请重新选择以读取音频波形')
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return result


if __name__ == '__main__':
    import argparse
    import json
    from .video import probe_video
    parser = argparse.ArgumentParser(description='只读音频波形检查（首条音轨）')
    parser.add_argument('path')
    parser.add_argument('--bins', type=int, default=2048)
    args = parser.parse_args()
    info = probe_video(args.path)
    peaks = audio_waveform(args.path, info['duration'], bins=args.bins) if info['audio_tracks'] else ()
    print(json.dumps({'duration': info['duration'], 'peaks': peaks}, ensure_ascii=False))
