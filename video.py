# -*- coding: utf-8 -*-
"""Video formats, bounded FFmpeg probes and posters (no Qt dependency).

Frame extraction follows SuperVideo's FFmpeg + aspect-preserving scale design.
Call probes/decoders from workers; never from selection/paint handlers.
"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm',
                    '.m4v', '.mpeg', '.mpg', '.ts', '.mts', '.m2ts')
_VIDEO_SLOTS = threading.BoundedSemaphore(2)


def is_video(path) -> bool:
    return bool(path) and Path(path).suffix.lower() in VIDEO_EXTENSIONS


def format_duration(seconds) -> str:
    try:
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            return '—'
        total = int(value)
    except (TypeError, ValueError):
        return '—'
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes:02d}:{secs:02d}'


def find_ffmpeg() -> str:
    override = os.environ.get('SUPERVIEWER_FFMPEG', '')
    if override:
        return override
    # imageio-ffmpeg ships a platform-specific binary and has a PyInstaller hook.
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        pass
    executable = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
    platform_dir = 'windows' if sys.platform == 'win32' else 'macos'
    root = Path(__file__).resolve().parent.parent
    for base in (Path(getattr(sys, '_MEIPASS', root)), root / 'SuperBirdStamp'):
        candidate = base / 'tools' / 'ffmpeg' / platform_dir / executable
        if candidate.is_file():
            return str(candidate)
    found = shutil.which('ffmpeg')
    if found:
        return found
    raise RuntimeError('无法生成视频封面：请安装 imageio-ffmpeg 或设置 SUPERVIEWER_FFMPEG。')


def run_video_tool(args, *, cancelled=lambda: False, timeout=12.0):
    """Bound decoder concurrency, hide Windows consoles, reap on every exit."""
    deadline = time.monotonic() + timeout
    while not _VIDEO_SLOTS.acquire(timeout=0.05):
        if cancelled() or time.monotonic() >= deadline:
            raise RuntimeError('视频读取已取消或超时')
    try:
        if cancelled():
            raise RuntimeError('视频读取已取消')
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            try:
                while True:
                    if cancelled() or time.monotonic() >= deadline:
                        raise RuntimeError('视频读取已取消或超时')
                    try:
                        out, err = process.communicate(timeout=0.1)
                        return process.returncode, out, err
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
    finally:
        _VIDEO_SLOTS.release()


def parse_ffmpeg_info(text: str) -> dict:
    """Fallback for bundled FFmpeg distributions without ffprobe."""
    info = {}
    duration = re.search(r'Duration: (\d+):(\d+):([\d.]+)', text)
    if duration:
        h, m, s = map(float, duration.groups())
        info['duration'] = h * 3600 + m * 60 + s
    bitrate = re.search(r'Duration:.*?bitrate: (\d+) kb/s', text)
    if bitrate:
        info['bit_rate'] = int(bitrate[1]) * 1000
    video = next((line for line in text.splitlines() if 'Video:' in line and 'attached pic' not in line), '')
    if not video:
        raise RuntimeError('没有可播放的视频轨道，或文件已损坏')
    info['video_codec'] = video.split('Video:', 1)[1].split(',', 1)[0].strip()
    dimensions = re.search(r'\b(\d{2,6})x(\d{2,6})\b', video)
    if dimensions:
        info['width'], info['height'] = map(int, dimensions.groups())
    fps = re.search(r'([\d.]+) fps', video)
    if fps:
        info['fps'] = float(fps[1])
    rotation = re.search(r'rotation of (-?[\d.]+) degrees', text)
    if rotation and round(float(rotation[1])) % 180:
        info['width'], info['height'] = info.get('height'), info.get('width')
    audios = [line.split('Audio:', 1)[1].strip() for line in text.splitlines() if 'Audio:' in line]
    info['audio'] = '\n'.join(audios) if audios else '无音轨'
    info['audio_tracks'] = len(audios)
    return info


def probe_video(path: str, *, cancelled=lambda: False) -> dict:
    path = os.path.abspath(os.fspath(path))
    # -i without an output reads only headers, and intentionally returns code 1.
    _, _, stderr = run_video_tool([find_ffmpeg(), '-hide_banner', '-nostdin', '-i', path],
                                  cancelled=cancelled)
    info = parse_ffmpeg_info(stderr.decode('utf-8', errors='replace'))
    stat = os.stat(path)
    info.update(path=path, container=Path(path).suffix[1:].upper(), size=stat.st_size,
                modified=stat.st_mtime)
    return info


def video_thumbnail_rgb(path: str, size: int, *, cancelled=lambda: False):
    """Decode one early frame; size caps output, and short clips still work."""
    from PIL import Image
    size = max(1, min(2048, int(size)))
    scale = f"scale=w='if(gte(iw,ih),min(iw,{size}),-2)':h='if(gte(iw,ih),-2,min(ih,{size}))'"
    code, data, error = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-threads', '1', '-i', os.path.abspath(os.fspath(path)), '-map', '0:V:0',
        '-frames:v', '1', '-vf', scale, '-threads', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
    ], cancelled=cancelled)
    if code or not data:
        raise RuntimeError(error.decode('utf-8', errors='replace').strip()[:500] or '无法读取视频封面')
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert('RGB')
        return image.tobytes(), image.width, image.height


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='只读视频信息检查')
    parser.add_argument('path')
    args = parser.parse_args()
    print(json.dumps(probe_video(args.path), ensure_ascii=False, indent=2))
