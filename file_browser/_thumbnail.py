# -*- coding: utf-8 -*-
"""Thumbnail cache and background loading for app_common.file_browser."""
from __future__ import annotations

from app_common.file_browser._browser_core import *

def _compute_thumb_cache_max_bytes() -> int:
    """Budget for the thumbnail QImage memory cache.

    Hard cap: 16 GB.  On machines where physical RAM is detectable we also
    limit to 25 % of total RAM so the app doesn't starve the OS on small
    machines (e.g. 16 GB system → 4 GB cache; 64 GB system → 16 GB cache).
    """
    hard_cap = 48 * 1024 * 1024 * 1024  # 16 GB
    total_ram = 0
    try:
        import psutil  # optional dependency
        total_ram = psutil.virtual_memory().total
    except Exception:
        pass
    if total_ram <= 0:
        try:
            # POSIX fallback (macOS / Linux)
            total_ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            pass
    if total_ram > 0:
        return min(hard_cap, int(total_ram * 0.25))
    return hard_cap


_THUMB_CACHE_MAX_BYTES_DEFAULT = _compute_thumb_cache_max_bytes()
_THUMB_MODEL_APPEND_BATCH_SIZE = 160
_THUMB_MODEL_APPEND_BUDGET_S = 0.008


class ThumbnailMemoryCache:
    """Thread-safe thumbnail cache with JPEG mip levels, max-size fallback for others, and LRU eviction."""

    def __init__(self, max_bytes: int | None = None) -> None:
        self._lock = threading.RLock()
        self._jpeg_mips: dict[tuple[str, int], QImage] = {}
        self._base_images: dict[str, QImage] = {}
        self._bytes: int = 0
        self._max_bytes = int(max_bytes or _THUMB_CACHE_MAX_BYTES_DEFAULT)
        self._lru_keys: list[tuple[str, object]] = []  # ("jpeg", (ckey, size)) | ("base", ckey)

    def _lru_key_jpeg(self, cache_key: str, requested_size: int) -> tuple[str, tuple[str, int]]:
        return ("jpeg", (cache_key, int(requested_size)))

    def _lru_key_base(self, cache_key: str) -> tuple[str, str]:
        return ("base", cache_key)

    def _evict_until_under_limit(self) -> None:
        while self._bytes > self._max_bytes and self._lru_keys:
            key = self._lru_keys.pop(0)
            if key[0] == "jpeg":
                img = self._jpeg_mips.pop(key[1], None)
            else:
                img = self._base_images.pop(key[1], None)
            if img is not None and not img.isNull():
                self._bytes -= _qimage_num_bytes(img)

    def _store_image(self, bucket: dict, key, image: QImage) -> None:
        old = bucket.get(key)
        if old is not None:
            self._bytes -= _qimage_num_bytes(old)
        stored = image.copy()
        bucket[key] = stored
        self._bytes += _qimage_num_bytes(stored)

    def _is_jpeg_like(self, path: str) -> bool:
        return Path(path).suffix.lower() in _JPEG_MIP_EXTENSIONS

    def get(self, source_path: str, requested_size: int) -> QImage | None:
        cache_key = _thumb_cache_key(source_path)
        with self._lock:
            if self._is_jpeg_like(source_path):
                k = self._lru_key_jpeg(cache_key, requested_size)
                if k in self._lru_keys:
                    self._lru_keys.remove(k)
                    self._lru_keys.append(k)
                cached = self._jpeg_mips.get((cache_key, int(requested_size)))
                return cached.copy() if cached is not None else None
            k = self._lru_key_base(cache_key)
            if k in self._lru_keys:
                self._lru_keys.remove(k)
                self._lru_keys.append(k)
            base = self._base_images.get(cache_key)
        if base is None:
            return None
        return _scale_qimage_for_thumb(base, requested_size)

    def put(self, source_path: str, requested_size: int, image: QImage) -> None:
        if image is None or image.isNull():
            return
        cache_key = _thumb_cache_key(source_path)
        with self._lock:
            if self._is_jpeg_like(source_path):
                jkey = (cache_key, int(requested_size))
                lru_k = self._lru_key_jpeg(cache_key, requested_size)
                if lru_k in self._lru_keys:
                    self._lru_keys.remove(lru_k)
                self._store_image(self._jpeg_mips, jkey, image)
                self._lru_keys.append(lru_k)
            else:
                lru_k = self._lru_key_base(cache_key)
                if lru_k in self._lru_keys:
                    self._lru_keys.remove(lru_k)
                self._store_image(self._base_images, cache_key, image)
                self._lru_keys.append(lru_k)
            self._evict_until_under_limit()

    def evict_other_dirs(self, current_dir_norm: str) -> int:
        """Evict all cached QImage entries whose file path does NOT belong to
        current_dir_norm (or any of its subdirectories).

        Called on every directory switch to implement folder-level FIFO eviction:
        the moment the user navigates away from a folder its cached thumbnails
        are freed, regardless of LRU age.  Within the new/current folder the
        existing byte-limit LRU eviction continues normally.

        current_dir_norm must be the same normalised absolute path that
        _thumb_cache_key() / _path_key() would produce for the directory.

        Returns the number of bytes freed.
        """
        prefix = current_dir_norm + os.sep  # e.g. "/photos/2024/"
        freed = 0
        with self._lock:
            # Collect stale LRU keys in one pass before mutating the dicts.
            stale = [
                lru_k for lru_k in self._lru_keys
                if not (lru_k[1][0] if lru_k[0] == "jpeg" else lru_k[1]).startswith(prefix)
            ]
            for lru_k in stale:
                if lru_k[0] == "jpeg":
                    img = self._jpeg_mips.pop(lru_k[1], None)
                else:
                    img = self._base_images.pop(lru_k[1], None)
                try:
                    self._lru_keys.remove(lru_k)
                except ValueError:
                    pass
                if img is not None and not img.isNull():
                    nb = _qimage_num_bytes(img)
                    self._bytes -= nb
                    freed += nb
        return freed

    def clear(self) -> dict[str, int]:
        with self._lock:
            stats = self.stats()
            self._jpeg_mips.clear()
            self._base_images.clear()
            self._lru_keys.clear()
            self._bytes = 0
        return stats

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "jpeg_levels": len(self._jpeg_mips),
                "base_images": len(self._base_images),
                "entries": len(self._jpeg_mips) + len(self._base_images),
                "bytes": int(self._bytes),
                "max_bytes": self._max_bytes,
            }


class ThumbnailLoader(QThread):
    """Background thumbnail loader with a priority queue and internal worker pool.

    Priority levels:
      PRIORITY_VISIBLE  (0) – currently visible items; processed first.
      PRIORITY_PREFETCH (1) – nearby but not yet visible; processed when idle.

    Thread-safety contract
    ----------------------
    ``enqueue()`` and ``promote()`` may be called from the main thread at any
    time, including while ``run()`` is executing.  The internal lock serialises
    mutations to the queued/loaded sets.  ``run()`` polls the priority queue
    with a short timeout so newly injected high-priority items are picked up
    within one batch cycle (≤ max_workers completions).
    """

    thumbnail_ready = pyqtSignal(int, str, object)  # (request_token, path, QImage)

    PRIORITY_VISIBLE  = 0  # noqa: E221
    PRIORITY_PREFETCH = 1

    def __init__(
        self,
        size: int,
        request_token: int,
        report_cache: dict | None = None,
        current_dir: str | None = None,
        thumb_cache: ThumbnailMemoryCache | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._size = int(size)
        self._request_token = int(request_token)
        self._report_cache = report_cache or {}
        self._current_dir = current_dir or ""
        self._thumb_cache = thumb_cache
        self._stop_flag = False
        self._executor: _futures.ThreadPoolExecutor | None = None
        self._max_workers = _thumbnail_loader_worker_count()
        self._batch_size = _thumbnail_loader_batch_size(self._max_workers)

        # Priority queue: items are (priority, seq, path)
        self._task_queue: _queue.PriorityQueue = _queue.PriorityQueue()
        self._queued:  set[str] = set()   # paths currently sitting in the queue
        self._loaded:  set[str] = set()   # paths already submitted to executor
        self._desired_paths: set[str] = set()
        self._seq = 0                      # monotonic counter for stable FIFO within same priority
        self._queue_lock = threading.Lock()
        self._profile_lock = threading.Lock()
        self._profile_enabled = _thumb_profile_enabled()
        self._profile_started_at = _time.perf_counter()
        self._profile_enqueued_visible = 0
        self._profile_enqueued_prefetch = 0
        self._profile_promoted = 0
        self._profile_batches = 0
        self._profile_submitted = 0
        self._profile_completed = 0
        self._profile_memory_hits = 0
        self._profile_disk_hits = 0
        self._profile_progressive_paths = 0
        self._profile_single_shot_paths = 0
        self._profile_frames_emitted = 0
        self._profile_decode_total_s = 0.0
        self._profile_decode_max_s = 0.0
        self._profile_decode_max_path = ""

    # ── Public API (thread-safe) ─────────────────────────────────────────────

    def _profile_record_decode(
        self,
        path: str,
        *,
        elapsed_s: float,
        frames_emitted: int,
        memory_hit: bool = False,
        disk_hit: bool = False,
        progressive: bool = False,
        single_shot: bool = False,
    ) -> None:
        if not self._profile_enabled:
            return
        _record_thumb_bottleneck_sample("decode_ms", elapsed_s * 1000.0)
        with self._profile_lock:
            self._profile_completed += 1
            self._profile_frames_emitted += max(0, int(frames_emitted))
            self._profile_decode_total_s += max(0.0, float(elapsed_s))
            if elapsed_s > self._profile_decode_max_s:
                self._profile_decode_max_s = float(elapsed_s)
                self._profile_decode_max_path = path
            if memory_hit:
                self._profile_memory_hits += 1
            if disk_hit:
                self._profile_disk_hits += 1
            if progressive:
                self._profile_progressive_paths += 1
            if single_shot:
                self._profile_single_shot_paths += 1

    def profile_snapshot(self) -> dict[str, object]:
        with self._queue_lock:
            queue_size = int(self._task_queue.qsize())
            queued_count = len(self._queued)
            loaded_count = len(self._loaded)
        with self._profile_lock:
            return {
                "started_at": self._profile_started_at,
                "queue_size": queue_size,
                "queued_count": queued_count,
                "loaded_count": loaded_count,
                "enqueued_visible": self._profile_enqueued_visible,
                "enqueued_prefetch": self._profile_enqueued_prefetch,
                "promoted": self._profile_promoted,
                "batches": self._profile_batches,
                "submitted": self._profile_submitted,
                "completed": self._profile_completed,
                "memory_hits": self._profile_memory_hits,
                "disk_hits": self._profile_disk_hits,
                "progressive_paths": self._profile_progressive_paths,
                "single_shot_paths": self._profile_single_shot_paths,
                "frames_emitted": self._profile_frames_emitted,
                "decode_total_s": self._profile_decode_total_s,
                "decode_max_s": self._profile_decode_max_s,
                "decode_max_path": self._profile_decode_max_path,
            }

    @staticmethod
    def _normalize_unique_paths(paths: list[str] | None) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for path in paths or []:
            if not path:
                continue
            norm = os.path.normpath(path)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(norm)
        return result

    def set_desired_paths(
        self,
        visible_paths: list[str] | None = None,
        prefetch_paths: list[str] | None = None,
    ) -> None:
        desired = set(self._normalize_unique_paths(visible_paths))
        desired.update(self._normalize_unique_paths(prefetch_paths))
        with self._queue_lock:
            self._desired_paths = desired

    def replace_pending(
        self,
        visible_paths: list[str] | None = None,
        prefetch_paths: list[str] | None = None,
    ) -> int:
        visible_norms = self._normalize_unique_paths(visible_paths)
        visible_set = set(visible_norms)
        prefetch_norms = [
            norm
            for norm in self._normalize_unique_paths(prefetch_paths)
            if norm not in visible_set
        ]
        desired = set(visible_norms)
        desired.update(prefetch_norms)

        replaced = 0
        with self._queue_lock:
            self._task_queue = _queue.PriorityQueue()
            self._queued.clear()
            self._desired_paths = desired
            for norm in visible_norms:
                if norm in self._loaded:
                    continue
                self._seq += 1
                self._task_queue.put_nowait((self.PRIORITY_VISIBLE, self._seq, norm))
                self._queued.add(norm)
                replaced += 1
            for norm in prefetch_norms:
                if norm in self._loaded or norm in self._queued:
                    continue
                self._seq += 1
                self._task_queue.put_nowait((self.PRIORITY_PREFETCH, self._seq, norm))
                self._queued.add(norm)
                replaced += 1
        return replaced

    def wants_path(self, path: str) -> bool:
        norm = os.path.normpath(path)
        with self._queue_lock:
            return norm in self._desired_paths

    def _resolve_load_target_path(self, path: str) -> str:
        norm_path = os.path.normpath(path)
        source_path = _resolve_thumb_source_path(norm_path, self._report_cache, self._current_dir)
        source_stamp = _thumb_source_stamp(norm_path, source_path)
        persistent_path = _existing_persistent_thumb_cache_path_for_file(
            norm_path,
            self._current_dir,
            requested_size=self._size,
            source_stamp=source_stamp,
            candidate_sizes=_effective_persistent_thumb_cache_sizes(self._size),
        )
        if persistent_path:
            return persistent_path
        return source_path

    def enqueue(self, paths: list[str], priority: int = PRIORITY_VISIBLE) -> int:
        """Add *paths* to the priority queue at *priority*.

        Paths that are already loaded or already sitting in the queue are
        skipped (no duplicates).  Returns the number of newly enqueued paths.
        """
        added = 0
        with self._queue_lock:
            for path in paths:
                norm = os.path.normpath(path)
                if norm in self._loaded or norm in self._queued:
                    continue
                self._seq += 1
                self._task_queue.put_nowait((priority, self._seq, norm))
                self._queued.add(norm)
                self._desired_paths.add(norm)
                added += 1
        if self._profile_enabled and added > 0:
            with self._profile_lock:
                if int(priority) == int(self.PRIORITY_VISIBLE):
                    self._profile_enqueued_visible += added
                else:
                    self._profile_enqueued_prefetch += added
        return added

    def promote(self, paths: list[str]) -> int:
        """Re-queue *paths* at ``PRIORITY_VISIBLE`` regardless of current state.

        If a path is already loaded it is skipped.  If it is already in the
        queue at a lower priority a second entry at priority 0 is inserted;
        the original lower-priority entry will be discarded when dequeued
        (detected via the ``_loaded`` set).  Returns the number of entries
        inserted.
        """
        promoted = 0
        with self._queue_lock:
            for path in paths:
                norm = os.path.normpath(path)
                if norm in self._loaded:
                    continue
                self._desired_paths.add(norm)
                self._seq += 1
                self._task_queue.put_nowait((self.PRIORITY_VISIBLE, self._seq, norm))
                self._queued.add(norm)  # idempotent; may already be present
                promoted += 1
        if self._profile_enabled and promoted > 0:
            with self._profile_lock:
                self._profile_promoted += promoted
        return promoted

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()
        with self._queue_lock:
            self._desired_paths.clear()

    def _load_single(self, path: str, emit_fn, *, allow_progressive: bool) -> None:
        """Decode one image progressively, calling emit_fn(path, QImage) for every
        available frame — coarse frames first, final high-quality frame last.

        emit_fn is called from the thread-pool worker thread.  Qt cross-thread
        signal delivery (queued connection) makes this safe: each call posts a
        QMetaCallEvent to the main thread's event loop instead of invoking the
        slot directly.

        Progressive pipeline for JPEG / RAW:
          1. Memory cache hit  → emit once, done (fastest path).
          2. Disk cache hit    → emit once, populate memory cache, done.
          3. Progressive feed  → emit BILINEAR intermediate frames as libjpeg
                                  decodes successive JPEG scans, then emit the
                                  final LANCZOS frame; cache that final frame.
        Non-JPEG / non-RAW falls back to a single-shot load.
        """
        path_to_load = os.path.normpath(path)
        load_started_at = _time.perf_counter()
        emitted_frames = 0

        def stopped() -> bool:
            return self._stop_flag or self.isInterruptionRequested()

        def safe_emit(qimg: QImage) -> None:
            nonlocal emitted_frames
            if not stopped() and not qimg.isNull() and self.wants_path(path_to_load):
                emitted_frames += 1
                emit_fn(self._request_token, path_to_load, qimg)

        if stopped():
            return

        cache = self._thumb_cache
        load_size = self._size
        load_target_path = self._resolve_load_target_path(path_to_load)

        # ── 1. Memory cache ──────────────────────────────────────────────────
        if cache is not None:
            cached = cache.get(path_to_load, load_size)
            if cached is not None and not cached.isNull():
                safe_emit(cached)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    memory_hit=True,
                )
                return

        ext = Path(load_target_path).suffix.lower()

        # ── 2. JPEG / RAW: disk cache then progressive pipeline ──────────────
        if ext in thumb_stream._JPEG_EXTENSIONS or ext in thumb_stream._RAW_EXTENSIONS:
            try:
                mtime = os.path.getmtime(load_target_path)
            except Exception:
                mtime = 0.0

            disk_img = _read_thumb_from_disk_cache(load_target_path, mtime, load_size)
            if disk_img is not None and not disk_img.isNull():
                if cache is not None:
                    cache.put(path_to_load, load_size, disk_img)
                safe_emit(disk_img)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    disk_hit=True,
                )
                return

            # Progressive decode – emit each frame as it arrives
            if not allow_progressive:
                qimg = _load_thumbnail_image(load_target_path, load_size)
                if qimg is None or qimg.isNull() or stopped():
                    return
                if cache is not None:
                    cache.put(path_to_load, load_size, qimg)
                    cached = cache.get(path_to_load, load_size)
                    if cached is not None and not cached.isNull():
                        qimg = cached
                safe_emit(qimg)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    single_shot=True,
                )
                return

            final_qimg: QImage | None = None
            for rgb_result in thumb_stream.iter_thumbnail_rgb_progressive(
                load_target_path, load_size, stopped
            ):
                if stopped():
                    return
                data, w, h = rgb_result
                qimg = _rgb_bytes_to_qimage(data, w, h)
                safe_emit(qimg)
                final_qimg = qimg

            if final_qimg is not None and not final_qimg.isNull():
                if cache is not None:
                    cache.put(path_to_load, load_size, final_qimg)
                cache_path = _thumb_disk_cache_path(load_target_path, mtime, load_size)
                _schedule_thumb_disk_cache_write(cache_path, final_qimg)
                self._profile_record_decode(
                    path_to_load,
                    elapsed_s=_time.perf_counter() - load_started_at,
                    frames_emitted=emitted_frames,
                    progressive=True,
                )

        # ── 3. Other formats: single-shot load (handles disk cache internally) ─
        else:
            qimg = _load_thumbnail_image(load_target_path, load_size)
            if qimg is None or qimg.isNull() or stopped():
                return
            if cache is not None:
                cache.put(path_to_load, load_size, qimg)
                cached = cache.get(path_to_load, load_size)
                if cached is not None and not cached.isNull():
                    qimg = cached
            safe_emit(qimg)
            self._profile_record_decode(
                path_to_load,
                elapsed_s=_time.perf_counter() - load_started_at,
                frames_emitted=emitted_frames,
                single_shot=True,
            )

    def run(self) -> None:
        if self._task_queue.empty():
            return
        if self._profile_enabled:
            _log.info(
                "[THUMB_PROFILE][loader.start] token=%s size=%s workers=%s batch=%s initial_queue=%s",
                self._request_token,
                self._size,
                self._max_workers,
                self._batch_size,
                int(self._task_queue.qsize()),
            )
        else:
            _log.debug(
                "[ThumbnailLoader.run] START size=%s workers=%s batch=%s",
                self._size,
                self._max_workers,
                self._batch_size,
            )
        executor = _futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="thumb",
        )
        self._executor = executor
        # emit_fn is called from pool-worker threads; Qt queued-connection
        # delivery is thread-safe and routes each call to the main event loop.
        emit_fn = self.thumbnail_ready.emit

        try:
            while not self._stop_flag and not self.isInterruptionRequested():
                # ── Drain priority queue into one batch ──────────────────────
                # We submit max_workers items at a time so that new high-priority
                # items injected via promote() can jump ahead after each batch.
                batch: list[tuple[int, str]] = []
                while len(batch) < self._max_workers:
                    with self._queue_lock:
                        if len(batch) >= self._batch_size:
                            break
                        try:
                            priority, _, path = self._task_queue.get_nowait()
                        except _queue.Empty:
                            break
                        self._queued.discard(path)
                        if path in self._loaded:
                            continue  # duplicate from promote(); skip
                        self._loaded.add(path)
                    batch.append((priority, path))

                if not batch:
                    # Queue is empty; wait briefly for newly injected items.
                    _time.sleep(0.05)
                    if self._task_queue.empty():
                        break
                    continue
                if self._profile_enabled:
                    with self._profile_lock:
                        self._profile_batches += 1

                # ── Submit batch to thread-pool workers ──────────────────────
                future_map: dict[_futures.Future, str] = {}
                for priority, path in batch:
                    if self._stop_flag or self.isInterruptionRequested():
                        break
                    try:
                        f = executor.submit(
                            self._load_single,
                            path,
                            emit_fn,
                            allow_progressive=(priority == self.PRIORITY_VISIBLE),
                        )
                        future_map[f] = path
                        if self._profile_enabled:
                            with self._profile_lock:
                                self._profile_submitted += 1
                    except RuntimeError as e:
                        _log.info("[ThumbnailLoader.run] submit stopped path=%r: %s", path, e)
                        break

                # ── Wait for this batch before taking the next ───────────────
                # Waiting (rather than fire-and-forget) lets the priority queue
                # be checked again after each batch, so newly-visible items
                # injected via promote() are processed in the next iteration.
                for f in _futures.as_completed(future_map):
                    if self._stop_flag or self.isInterruptionRequested():
                        break
                    try:
                        f.result()
                    except Exception as e:
                        _log.warning(
                            "[ThumbnailLoader.run] failed path=%r: %s",
                            future_map[f], e,
                        )

        finally:
            if self._profile_enabled:
                snap = self.profile_snapshot()
                elapsed_s = max(0.001, _time.perf_counter() - self._profile_started_at)
                avg_decode_ms = 1000.0 * float(snap.get("decode_total_s", 0.0)) / max(1, int(snap.get("completed", 0)))
                _log.info(
                    "[THUMB_PROFILE][loader.end] token=%s elapsed=%.2fs visible=%s prefetch=%s promoted=%s batches=%s submitted=%s completed=%s queue=%s mem_hit=%s disk_hit=%s progressive=%s single=%s frames=%s avg_decode=%.1fms max_decode=%.1fms max_path=%r",
                    self._request_token,
                    elapsed_s,
                    snap.get("enqueued_visible", 0),
                    snap.get("enqueued_prefetch", 0),
                    snap.get("promoted", 0),
                    snap.get("batches", 0),
                    snap.get("submitted", 0),
                    snap.get("completed", 0),
                    snap.get("queue_size", 0),
                    snap.get("memory_hits", 0),
                    snap.get("disk_hits", 0),
                    snap.get("progressive_paths", 0),
                    snap.get("single_shot_paths", 0),
                    snap.get("frames_emitted", 0),
                    avg_decode_ms,
                    1000.0 * float(snap.get("decode_max_s", 0.0)),
                    snap.get("decode_max_path", ""),
                )
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
            self._executor = None
            _log.debug("[ThumbnailLoader.run] END")


class PersistentThumbCacheWorker(QThread):
    """Build restart-persistent small thumbnail JPEGs for the current directory."""

    progress_updated = pyqtSignal(int, int, int, int, int, str)
    finished_summary = pyqtSignal(int, int, int, int, int)

    def __init__(
        self,
        paths: list[str],
        current_dir: str,
        *,
        report_cache: dict | None = None,
        sizes: list[int] | tuple[int, ...] | None = None,
        worker_count: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = [os.path.normpath(p) for p in paths if p]
        self._current_dir = os.path.normpath(current_dir) if current_dir else ""
        self._report_cache = report_cache or {}
        normalized_sizes = sorted(
            {
                int(size)
                for size in (sizes or _persistent_thumb_cache_sizes())
                if int(size) in _THUMB_SIZE_STEPS
            }
        )
        self._sizes = tuple(normalized_sizes or _persistent_thumb_cache_sizes())
        self._worker_count = max(1, int(worker_count or _persistent_thumb_cache_worker_count()))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self.requestInterruption()

    def _process_path(self, source_path: str) -> tuple[str, int, int, int]:
        if self._stop_event.is_set():
            return source_path, 0, 0, 1
        load_target_path = _resolve_thumb_source_path(
            source_path,
            self._report_cache,
            self._current_dir,
        )
        source_stamp = _thumb_source_stamp(source_path, load_target_path)
        missing_sizes = [
            size
            for size in self._sizes
            if not _existing_persistent_thumb_cache_path_for_exact_size(
                source_path,
                self._current_dir,
                size,
                source_stamp=source_stamp,
            )
        ]
        if not missing_sizes:
            return source_path, 0, 1, 0
        if self._stop_event.is_set():
            return source_path, 0, 0, 1
        base_image = _load_thumbnail_image(load_target_path, max(missing_sizes))
        if base_image is None or base_image.isNull():
            return source_path, 0, 0, 1
        wrote_any = False
        for size in missing_sizes:
            if self._stop_event.is_set():
                break
            target_path = _persistent_thumb_cache_path_for_file(
                source_path,
                self._current_dir,
                size,
            )
            output_image = base_image if size >= max(missing_sizes) else _scale_qimage_for_thumb(base_image, size)
            if (
                target_path
                and not output_image.isNull()
                and _write_persistent_thumb_cache_image(
                    target_path,
                    output_image,
                    source_stamp=source_stamp,
                )
            ):
                wrote_any = True
        if wrote_any:
            return source_path, 1, 0, 0
        return source_path, 0, 0, 1

    def run(self) -> None:
        total = len(self._paths)
        processed = 0
        generated = 0
        skipped = 0
        failed = 0
        current_path = ""
        started_at = _time.perf_counter()
        last_emit_at = 0.0

        def emit_progress(force: bool = False) -> None:
            nonlocal last_emit_at
            now = _time.perf_counter()
            if (
                not force
                and processed < total
                and processed != 1
                and processed % 8 != 0
                and (now - last_emit_at) < 0.15
            ):
                return
            last_emit_at = now
            self.progress_updated.emit(
                processed,
                total,
                generated,
                skipped,
                failed,
                current_path,
            )

        _log.info(
            "[PersistentThumbCacheWorker.run] START dir=%r total=%s sizes=%s workers=%s",
            self._current_dir,
            total,
            list(self._sizes),
            self._worker_count,
        )
        executor: _futures.ThreadPoolExecutor | None = None
        try:
            executor = _futures.ThreadPoolExecutor(
                max_workers=self._worker_count,
                thread_name_prefix="thumb_preview",
            )
            futures = {
                executor.submit(self._process_path, source_path): source_path
                for source_path in self._paths
            }
            for future in _futures.as_completed(futures):
                current_path = futures.get(future, "") or current_path
                try:
                    _, generated_inc, skipped_inc, failed_inc = future.result()
                except Exception:
                    generated_inc = 0
                    skipped_inc = 0
                    failed_inc = 1
                processed += 1
                generated += generated_inc
                skipped += skipped_inc
                failed += failed_inc
                emit_progress()
                if self.isInterruptionRequested() or self._stop_event.is_set():
                    break
        finally:
            self._stop_event.set()
            if executor is not None:
                try:
                    executor.shutdown(wait=True, cancel_futures=True)
                except Exception:
                    pass
            emit_progress(force=True)
            self.finished_summary.emit(processed, total, generated, skipped, failed)
            _log.info(
                "[PersistentThumbCacheWorker.run] END dir=%r processed=%s/%s generated=%s skipped=%s failed=%s elapsed=%.2fs",
                self._current_dir,
                processed,
                total,
                generated,
                skipped,
                failed,
                _time.perf_counter() - started_at,
            )


__all__ = [name for name in globals() if not name.startswith('__')]
