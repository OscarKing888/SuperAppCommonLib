# -*- coding: utf-8 -*-
"""Background workers for app_common.file_browser."""
from __future__ import annotations

import concurrent.futures as _futures
import threading

from app_common.file_browser._browser_core import *
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log, perf_probes_enabled
from app_common.raw_focus_metadata import is_raw_image_path, read_raw_embedded_focus_metadata
from app_common.exif_io.writer import _batch_read_xmp_sidecar


_RAW_FOCUS_CHECKED_KEY = "_superviewer_raw_focus_checked"
_REPORT_FAST_PATH_CAPTURE_COLUMNS = (
    "iso",
    "shutter_speed",
    "aperture",
    "focal_length",
    "camera_model",
    "lens_model",
    "date_time_original",
)
_REPORT_FAST_PATH_BROWSER_COLUMNS = (
    "adj_sharpness",
    "adj_topiq",
    "focus_status",
    "burst_id",
    "burst_position",
    "bird_species_cn",
    "rating",
    "pick",
    "caption",
    "title",
)


def _metadata_has_value(value) -> bool:
    return value is not None and (not isinstance(value, str) or value.strip() != "")


def _report_row_has_browser_fast_path(row: dict | None) -> bool:
    """Return True when report.db already has enough browser metadata to skip exiftool."""
    if not isinstance(row, dict) or not row:
        return False
    capture_hits = sum(1 for key in _REPORT_FAST_PATH_CAPTURE_COLUMNS if _metadata_has_value(row.get(key)))
    if capture_hits >= 5:
        return True
    browser_hits = sum(1 for key in _REPORT_FAST_PATH_BROWSER_COLUMNS if _metadata_has_value(row.get(key)))
    return capture_hits >= 3 and browser_hits >= 2

class DirectoryScanWorker(QThread):
    """在后台执行目录扫描与 report.db 加载，完成后通过信号回传结果。"""

    scan_finished = pyqtSignal(str, object, object, object, object)  # (path, files_list, selected_report_cache, full_report_cache_or_none, report_row_by_path)
    scan_progress = pyqtSignal(str, int, int, str)  # (path, found_files, scanned_dirs, current_dir)

    def __init__(
        self,
        path: str,
        recursive: bool,
        report_root: str | None = None,
        report_cache_full: dict | None = None,
        use_report_db: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._recursive = recursive
        self._report_root = report_root
        self._report_cache_full = report_cache_full
        self._use_report_db = bool(use_report_db)

    def run(self) -> None:
        _log.info(
            "[DirectoryScanWorker.run] START path=%r recursive=%s report_root=%r use_report_db=%s has_cached_full_report=%s",
            self._path,
            self._recursive,
            self._report_root,
            self._use_report_db,
            self._report_cache_full is not None,
        )
        report_cache: dict = {}
        full_report_cache: dict | None = self._report_cache_full if self._use_report_db else None
        report_source_available = self._use_report_db and self._report_cache_full is not None
        try:
            if not self._use_report_db:
                _log.info("[DirectoryScanWorker.run] report load disabled")
            elif self._report_cache_full is not None:
                report_cache = self._report_cache_full
                _log.info("[DirectoryScanWorker.run] reuse cached full report_cache %s entries", len(report_cache))
            else:
                db_dir = self._report_root or self._path
                db = ReportDB.open_if_exists(db_dir)
                if db:
                    report_source_available = True
                    full_report_cache = {}
                    try:
                        for row in db.get_all_photos():
                            r = _normalize_report_row_paths(dict(row))
                            stem = r.get("filename")
                            if stem is not None:
                                full_report_cache[stem] = r
                    finally:
                        db.close()
                    report_cache = full_report_cache
                _log.info("[DirectoryScanWorker.run] report_cache loaded %s entries", len(report_cache))
        except Exception as e:
            _log.warning("[DirectoryScanWorker.run] report load failed: %s", e)
        if self.isInterruptionRequested():
            _log.info("[DirectoryScanWorker.run] interrupted after report")
            return
        files: list = []
        scanned_dirs = 0
        last_progress_at = 0.0
        last_progress_files = 0
        last_progress_dirs = 0
        last_worker_probe_at = 0.0

        def maybe_emit_progress(current_dir: str = "", *, force: bool = False) -> None:
            nonlocal last_progress_at, last_progress_files, last_progress_dirs, last_worker_probe_at
            now = _time.perf_counter()
            if not force and last_progress_at > 0.0 and (now - last_progress_at) < 0.25:
                return
            last_progress_at = now
            last_progress_files = len(files)
            last_progress_dirs = scanned_dirs
            self.scan_progress.emit(
                self._path,
                len(files),
                scanned_dirs,
                os.path.normpath(current_dir) if current_dir else "",
            )
            if perf_probes_enabled() and (force or (now - last_worker_probe_at) >= 2.0):
                last_worker_probe_at = now
                _log.info(
                    "[FILE_BROWSER_PROBE] event=scan_worker_progress path=%r found_files=%s scanned_dirs=%s current_dir=%r",
                    self._path,
                    len(files),
                    scanned_dirs,
                    os.path.normpath(current_dir) if current_dir else "",
                )

        if report_source_available and self._report_root:
            # 当 report.db 有记录时，用 DB 中 current_path（相对选中目录）拼出完整路径，扩展名用 original_path 的（如 .ARW）
            selected_dir = os.path.normpath(self._path)
            report_root = os.path.normpath(self._report_root)
            files, report_cache = _select_report_scope_files(
                selected_dir=selected_dir,
                report_root=report_root,
                full_report_cache=report_cache,
            )
            selected_rel = ""
            if _is_same_or_child_path(report_root, selected_dir):
                try:
                    selected_rel = os.path.relpath(selected_dir, report_root)
                except Exception:
                    selected_rel = ""
            selected_rel_norm = _norm_rel_path_for_match(selected_rel)
            _log.info(
                "[DirectoryScanWorker.run] selected scope files=%s selected_report_cache=%s selected_dir=%r selected_rel=%r report_root=%r",
                len(files), len(report_cache), selected_dir, selected_rel_norm or ".", report_root,
            )
            _log.info(
                "[DirectoryScanWorker.run] 使用 DB current_path 拼出完整路径构建文件列表 files=%s（跳过文件系统扫描）",
                len(files),
            )
            try:
                # In report mode the DB view is subtree-based even without UI filters,
                # so actual file supplementation must recurse under the selected dir.
                actual_files = _collect_image_files_impl(self._path, True)
                full_cache = full_report_cache or report_cache or {}
                existing = {_path_key(p) for p in files if p}
                file_index_by_stem = {Path(p).stem: i for i, p in enumerate(files) if p}
                supplemented = 0
                replaced = 0
                for actual_path in actual_files:
                    stem = Path(actual_path).stem
                    row = full_cache.get(stem)
                    if not isinstance(row, dict):
                        continue
                    actual_norm = os.path.normpath(actual_path)
                    actual_key = _path_key(actual_norm)
                    if actual_key in existing:
                        continue
                    existing_idx = file_index_by_stem.get(stem)
                    if existing_idx is not None:
                        old_path = files[existing_idx]
                        if old_path and not os.path.isfile(old_path):
                            old_key = _path_key(old_path)
                            files[existing_idx] = actual_norm
                            existing.discard(old_key)
                            existing.add(actual_key)
                            replaced += 1
                        continue
                    files.append(actual_norm)
                    existing.add(actual_key)
                    file_index_by_stem[stem] = len(files) - 1
                    supplemented += 1
                    report_cache[stem] = row
                _log.info(
                    "[DirectoryScanWorker.run] supplement actual files matched_by_stem=%s replaced_missing=%s total_files=%s selected_report_cache=%s",
                    supplemented,
                    replaced,
                    len(files),
                    len(report_cache),
                )
            except Exception as e:
                _log.warning("[DirectoryScanWorker.run] supplement actual files failed: %s", e)
            # Fallback: if DB-based approach produced no files, scan filesystem directly.
            # This handles empty/uninitialized report.db or mismatched paths.
            if not files:
                _log.warning(
                    "[DirectoryScanWorker.run] DB-based scan yielded 0 files, falling back to filesystem scan path=%r",
                    self._path,
                )
                try:
                    for root, dirs, names in os.walk(self._path, topdown=True):
                        if self.isInterruptionRequested():
                            return
                        scanned_dirs += 1
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        for name in sorted(names, key=str.lower):
                            if (
                                not is_apple_double_metadata_file(name)
                                and name.lower().endswith(IMAGE_EXTENSIONS)
                            ):
                                files.append(os.path.join(root, name))
                        maybe_emit_progress(root)
                except (PermissionError, OSError) as e:
                    _log.warning("[DirectoryScanWorker.run] fallback scan error: %s", e)
        else:
            try:
                if self._recursive:
                    for root, dirs, names in os.walk(self._path, topdown=True):
                        if self.isInterruptionRequested():
                            _log.info("[DirectoryScanWorker.run] interrupted during walk")
                            return
                        scanned_dirs += 1
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        for name in sorted(names, key=str.lower):
                            if (
                                not is_apple_double_metadata_file(name)
                                and name.lower().endswith(IMAGE_EXTENSIONS)
                            ):
                                files.append(os.path.join(root, name))
                        maybe_emit_progress(root)
                else:
                    for entry in sorted(os.scandir(self._path), key=lambda e: e.name.lower()):
                        if self.isInterruptionRequested():
                            return
                        if (
                            entry.is_file()
                            and not is_apple_double_metadata_file(entry.name)
                            and entry.name.lower().endswith(IMAGE_EXTENSIONS)
                        ):
                            files.append(entry.path)
                    scanned_dirs = 1
                    maybe_emit_progress(self._path, force=True)
            except (PermissionError, OSError) as e:
                _log.warning("[DirectoryScanWorker.run] scan error: %s", e)
        report_row_by_path: dict = {}
        if self._use_report_db:
            try:
                scoped_cache, scoped_full_cache, scoped_rows_by_path = _build_report_scope_maps_for_files(
                    files,
                    self._path,
                )
                if scoped_rows_by_path:
                    report_cache = scoped_cache
                    full_report_cache = scoped_full_cache
                    report_row_by_path = scoped_rows_by_path
                    report_source_available = True
                _log.info(
                    "[DirectoryScanWorker.run] report scopes selected=%s full=%s path_rows=%s",
                    len(report_cache or {}),
                    len(full_report_cache or {}),
                    len(report_row_by_path or {}),
                )
            except Exception as exc:
                _log.warning("[DirectoryScanWorker.run] build report scopes failed: %s", exc)
        maybe_emit_progress(self._path, force=True)
        _log.info("[DirectoryScanWorker.run] 目录扫描完成：列出 %s 个图像文件，report_cache %s 条，即将通知主线程加载 EXIF", len(files), len(report_cache))
        _log.info("[DirectoryScanWorker.run] scan done files=%s", len(files))
        if not self.isInterruptionRequested():
            self.scan_finished.emit(self._path, files, report_cache, full_report_cache, report_row_by_path)
            _log.info("[DirectoryScanWorker.run] emit scan_finished END")


# ── 后台元数据加载线程 ─────────────────────────────────────────────────────────

def _score_path_lookup_candidate(source_path: str, candidate_path: str, root_dir: str) -> tuple[int, int, int]:
    try:
        source_rel = os.path.relpath(source_path, root_dir)
    except Exception:
        source_rel = source_path
    try:
        cand_rel = os.path.relpath(candidate_path, root_dir)
    except Exception:
        cand_rel = candidate_path
    source_parts = [p.lower() for p in Path(os.path.dirname(source_rel)).parts if p not in ("", ".")]
    cand_parts = [p.lower() for p in Path(os.path.dirname(cand_rel)).parts if p not in ("", ".")]
    common_suffix = 0
    while common_suffix < min(len(source_parts), len(cand_parts)):
        if source_parts[-1 - common_suffix] != cand_parts[-1 - common_suffix]:
            break
        common_suffix += 1
    same_parent = 1 if source_parts and cand_parts and source_parts[-1] == cand_parts[-1] else 0
    return (common_suffix, same_parent, -len(cand_parts))


class PathLookupWorker(QThread):
    resolved = pyqtSignal(str, object)  # (source_path, actual_path_or_none)

    def __init__(self, source_path: str, root_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._source_path = os.path.normpath(source_path) if source_path else ""
        self._root_dir = os.path.normpath(root_dir) if root_dir else ""

    def run(self) -> None:
        source_path = self._source_path
        root_dir = self._root_dir
        actual_path = None
        _log.info("[PathLookupWorker.run] START source=%r root=%r", source_path, root_dir)
        if source_path and os.path.isfile(source_path):
            actual_path = source_path
        elif root_dir and os.path.isdir(root_dir) and source_path:
            target_name = Path(source_path).name.lower()
            best_score = None
            best_path = None
            scanned_dirs = 0
            candidates = 0
            try:
                for walk_root, dirs, names in os.walk(root_dir, topdown=True):
                    if self.isInterruptionRequested():
                        _log.info("[PathLookupWorker.run] interrupted source=%r", source_path)
                        return
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    scanned_dirs += 1
                    for name in names:
                        if name.lower() != target_name:
                            continue
                        candidate = os.path.normpath(os.path.join(walk_root, name))
                        score = _score_path_lookup_candidate(source_path, candidate, root_dir)
                        candidates += 1
                        if best_score is None or score > best_score:
                            best_score = score
                            best_path = candidate
                actual_path = best_path
                _log.info(
                    "[PathLookupWorker.run] END source=%r root=%r scanned_dirs=%s candidates=%s actual=%r",
                    source_path,
                    root_dir,
                    scanned_dirs,
                    candidates,
                    actual_path,
                )
            except Exception as e:
                _log.warning("[PathLookupWorker.run] failed source=%r root=%r: %s", source_path, root_dir, e)
        self.resolved.emit(source_path, actual_path)


def _metadata_chunk_size_for_worker_count(total: int, worker_count: int) -> int:
    total_count = max(1, int(total or 0))
    requested_workers = max(1, int(worker_count or 1))
    max_chunk_size = max(1, _METADATA_CHUNK_SIZE)
    if total_count <= requested_workers:
        return 1
    target_chunk_size = (total_count + requested_workers - 1) // requested_workers
    return max(1, min(max_chunk_size, target_chunk_size))


class MetadataLoader(QThread):
    """
    批量读取图像文件的列表列元数据。
    通过 PhotoMetaDataProxy 做分块查询；每个 chunk 完成后立即把结果回推到主线程。
    """

    metadata_batch_ready = pyqtSignal(object)  # dict {norm_path: metadata_dict}
    focus_cache_batch_ready = pyqtSignal(object)  # dict {source_path: {"focus_box": tuple, "used_path": str}}
    progress_updated = pyqtSignal(int, int)  # (current_count, total_count)

    def __init__(
        self,
        paths: list,
        meta_proxy: PhotoMetaDataProxy,
        focus_source_paths: dict[str, str] | None = None,
        metadata_tags: list[str] | None = None,
        report_rows_by_path: dict[str, dict] | None = None,
        worker_count: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._meta_proxy = meta_proxy
        self._focus_source_paths = {
            os.path.normpath(display_path): os.path.normpath(source_path)
            for display_path, source_path in (focus_source_paths or {}).items()
            if display_path and source_path
        }
        self._metadata_tags = list(metadata_tags or [])
        self._report_rows_by_path = {
            os.path.normpath(path): dict(row)
            for path, row in (report_rows_by_path or {}).items()
            if path and isinstance(row, dict)
        }
        try:
            self._worker_count = max(1, int(worker_count or 1))
        except Exception:
            self._worker_count = 1
        self._stop_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()

    def _stopped(self) -> bool:
        return self._stop_flag or self.isInterruptionRequested()

    def run(self) -> None:
        if not self._paths or self._stop_flag:
            _log.debug("[MetadataLoader.run] no paths or stopped")
            return
        run_t0 = perf_counter()
        processed = 0
        chunk_count = 0
        chunk_size = 0
        active_worker_count = 0
        completed = False
        _log.info("[MetadataLoader.run] START paths=%s worker_count=%s", len(self._paths), self._worker_count)
        try:
            paths = self._paths
            total = len(paths)
            chunk_size = _metadata_chunk_size_for_worker_count(total, self._worker_count)
            chunks = [paths[i : i + chunk_size] for i in range(0, total, chunk_size)]
            chunk_count = len(chunks)
            worker_count = max(1, min(self._worker_count, len(chunks)))
            active_worker_count = worker_count
            perf_log(
                _log,
                "[metadata.run] start paths=%s chunks=%s chunk_size=%s requested_workers=%s active_workers=%s tags=%s report_rows=%s",
                total,
                chunk_count,
                chunk_size,
                self._worker_count,
                active_worker_count,
                len(self._metadata_tags),
                len(self._report_rows_by_path),
            )
            if worker_count <= 1 or len(chunks) <= 1:
                for chunk in chunks:
                    if self._stopped():
                        _log.info("[MetadataLoader.run] interrupted")
                        return
                    parsed_batch, focus_batch, processed_count = self._read_parse_chunk(chunk)
                    if self._stopped():
                        return
                    self._emit_metadata_chunk(parsed_batch, focus_batch)
                    processed += processed_count
                    self.progress_updated.emit(min(processed, total), total)
                completed = True
                return

            with _futures.ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="file-metadata",
            ) as executor:
                futures = [
                    (executor.submit(self._read_parse_chunk, chunk), len(chunk))
                    for chunk in chunks
                ]
                for future, chunk_len in futures:
                    if self._stopped():
                        for pending, _pending_len in futures:
                            pending.cancel()
                        _log.info("[MetadataLoader.run] interrupted")
                        return
                    processed_count = chunk_len
                    try:
                        parsed_batch, focus_batch, parsed_count = future.result()
                        processed_count = parsed_count
                    except Exception as exc:
                        _log.warning("[MetadataLoader.run] chunk failed: %s", exc)
                        parsed_batch = {}
                        focus_batch = {}
                    if self._stopped():
                        return
                    self._emit_metadata_chunk(parsed_batch, focus_batch)
                    processed += processed_count
                    self.progress_updated.emit(min(processed, total), total)
            completed = True
        except Exception as e:
            _log.warning("[MetadataLoader.run] exception: %s", e)
        finally:
            perf_log(
                _log,
                "[metadata.run] end status=%s processed=%s/%s chunks=%s chunk_size=%s active_workers=%s elapsed_ms=%.1f",
                "completed" if completed else ("stopped" if self._stopped() else "failed"),
                min(processed, len(self._paths)),
                len(self._paths),
                chunk_count,
                chunk_size,
                active_worker_count,
                elapsed_ms(run_t0),
            )
            _log.info("[MetadataLoader.run] END")

    def _read_parse_chunk(self, chunk: list[str]) -> tuple[dict, dict, int]:
        if self._stopped():
            return {}, {}, 0
        chunk_t0 = perf_counter()
        batch = self._read_metadata_batch(chunk)
        read_ms = elapsed_ms(chunk_t0)
        focus_t0 = perf_counter()
        focus_batch = self._build_focus_cache_batch(chunk) if self._should_prefetch_focus_cache() else {}
        focus_ms = elapsed_ms(focus_t0)
        parse_t0 = perf_counter()
        parsed_batch: dict = {}
        focus_box_count = 0
        checked_focus_count = 0
        for norm_path, flat in batch.items():
            if self._stopped():
                return parsed_batch, focus_batch, len(chunk)
            meta = self._parse_rec(flat)
            if meta.get("focus_box") is not None:
                focus_box_count += 1
            if meta.get("focus_box_checked"):
                checked_focus_count += 1
            species_cn = str(flat.get("bird_species_cn") or "").strip()
            if species_cn:
                meta["bird_species_cn"] = species_cn
            parsed_batch[norm_path] = meta
            _log.debug(
                "[MetadataLoader.run] path=%r title=%r rating=%s pick=%s",
                norm_path, meta.get("title", ""), meta.get("rating"), meta.get("pick"),
            )
        parse_ms = elapsed_ms(parse_t0)
        perf_log(
            _log,
            "[metadata.chunk] thread=%s paths=%s parsed=%s focus_checked=%s focus_box=%s read_ms=%.1f focus_cache_ms=%.1f parse_ms=%.1f total_ms=%.1f",
            threading.current_thread().name,
            len(chunk),
            len(parsed_batch),
            checked_focus_count,
            focus_box_count,
            read_ms,
            focus_ms,
            parse_ms,
            elapsed_ms(chunk_t0),
        )
        return parsed_batch, focus_batch, len(chunk)

    def _emit_metadata_chunk(self, parsed_batch: dict, focus_batch: dict) -> None:
        if focus_batch and not self._stopped():
            _log.info("[MetadataLoader.run] emit focus_cache_batch_ready batch=%s", len(focus_batch))
            self.focus_cache_batch_ready.emit(focus_batch)
        if parsed_batch and not self._stopped():
            _log.info("[MetadataLoader.run] emit metadata_batch_ready batch=%s", len(parsed_batch))
            self.metadata_batch_ready.emit(parsed_batch)

    def _should_prefetch_focus_cache(self) -> bool:
        """
        文件列表 metadata 加载阶段不要再同步触发第二次 metadata 扫描。

        焦点框预热应走独立 worker；这里若继续顺手批量读一遍 RAW metadata，
        会直接拖慢列表 metadata 完成时间，导致用户感觉“排序一直不可用”。
        """
        return False

    def _read_metadata_batch(self, paths: list[str]) -> dict[str, dict]:
        batch_t0 = perf_counter()
        norm_paths = [os.path.normpath(p) for p in paths]
        original_by_norm = {os.path.normpath(p): p for p in paths}
        result: dict[str, dict] = {norm: {"SourceFile": norm} for norm in norm_paths}
        raw_batch: dict[str, dict] = {}
        raw_focus_t0 = perf_counter()
        raw_focus_batch = self._read_raw_embedded_focus_batch(paths)
        raw_focus_ms = elapsed_ms(raw_focus_t0)
        for norm_path, focus_meta in raw_focus_batch.items():
            if norm_path in result and focus_meta:
                result[norm_path].update(focus_meta)

        report_fast_paths: list[str] = []
        exiftool_paths: list[str] = []
        for norm_path in norm_paths:
            report_row = self._report_rows_by_path.get(norm_path)
            original_path = original_by_norm.get(norm_path, norm_path)
            if _report_row_has_browser_fast_path(report_row):
                report_fast_paths.append(original_path)
            else:
                exiftool_paths.append(original_path)

        sidecar_fast_batch: dict[str, dict] = {}
        sidecar_fast_t0 = perf_counter()
        if report_fast_paths:
            try:
                sidecar_fast_batch = _batch_read_xmp_sidecar(report_fast_paths)
                for norm_path, flat in sidecar_fast_batch.items():
                    if norm_path in result and flat:
                        result[norm_path].update(flat)
            except Exception as exc:
                _log.warning("[MetadataLoader._read_metadata_batch] XMP fast path failed: %s", exc)
                sidecar_fast_batch = {}
                exiftool_paths.extend(report_fast_paths)
                report_fast_paths = []
        sidecar_fast_ms = elapsed_ms(sidecar_fast_t0)

        read_batch_t0 = perf_counter()
        if exiftool_paths:
            try:
                raw_batch = read_batch_metadata(
                    exiftool_paths,
                    tags=self._metadata_tags or None,
                    use_cache=not bool(self._metadata_tags),
                )
                for norm_path, flat in raw_batch.items():
                    if norm_path in result and flat:
                        result[norm_path].update(flat)
            except Exception as exc:
                _log.warning("[MetadataLoader._read_metadata_batch] read_batch_metadata failed: %s", exc)
        read_batch_ms = elapsed_ms(read_batch_t0)
        merge_t0 = perf_counter()
        raw_focus_merge_count = len(raw_focus_batch)
        report_merge_count = 0
        report_xmp_restore_count = 0
        report_raw_key_restore_count = 0
        for norm_path in norm_paths:
            report_row = self._report_rows_by_path.get(norm_path)
            if not isinstance(report_row, dict):
                continue
            report_merge_count += 1
            try:
                flat = report_row_to_exiftool_style(report_row, norm_path)
            except Exception:
                flat = {"SourceFile": norm_path}
            raw_flat = raw_batch.get(norm_path, {}) if isinstance(raw_batch, dict) else {}
            if not raw_flat and isinstance(sidecar_fast_batch, dict):
                raw_flat = sidecar_fast_batch.get(norm_path, {})
            for key, value in flat.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                if key_text.startswith("XMP"):
                    result[norm_path].setdefault(key_text, value)
                    report_xmp_restore_count += 1
                else:
                    result[norm_path][key_text] = value
                    report_raw_key_restore_count += 1
            for key, value in report_row.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                result[norm_path].setdefault(f"report.{key_text}", value)
                if f"XMP-superpicky:{key_text}" not in result[norm_path]:
                    result[norm_path].setdefault(key_text, value)
            for key, value in raw_flat.items():
                key_text = str(key or "").strip()
                if key_text.startswith("XMP") and value is not None and (not isinstance(value, str) or value.strip()):
                    result[norm_path][key_text] = value
            focus_flat = raw_focus_batch.get(norm_path, {})
            if isinstance(focus_flat, dict) and focus_flat:
                result[norm_path].update(focus_flat)
        merge_ms = elapsed_ms(merge_t0)
        perf_log(
            _log,
            "[metadata.read_batch] thread=%s paths=%s raw_focus_paths=%s report_fast_paths=%s exiftool_paths=%s raw_batch_entries=%s report_rows=%s raw_focus_ms=%.1f sidecar_fast_ms=%.1f read_batch_ms=%.1f merge_ms=%.1f total_ms=%.1f tags=%s xmp_restore=%s raw_restore=%s",
            threading.current_thread().name,
            len(norm_paths),
            raw_focus_merge_count,
            len(report_fast_paths),
            len(exiftool_paths),
            len(raw_batch) if isinstance(raw_batch, dict) else 0,
            report_merge_count,
            raw_focus_ms,
            sidecar_fast_ms,
            read_batch_ms,
            merge_ms,
            elapsed_ms(batch_t0),
            len(self._metadata_tags),
            report_xmp_restore_count,
            report_raw_key_restore_count,
        )
        return result

    @staticmethod
    def _read_raw_embedded_focus_batch(paths: list[str]) -> dict[str, dict]:
        probe_enabled = perf_probes_enabled()
        batch_t0 = perf_counter()
        result: dict[str, dict] = {}
        raw_candidates = 0
        metadata_hits = 0
        slowest_path = ""
        slowest_ms = 0.0
        for path in paths:
            if not is_raw_image_path(path):
                continue
            raw_candidates += 1
            norm_path = os.path.normpath(path)
            result[norm_path] = {"SourceFile": norm_path, _RAW_FOCUS_CHECKED_KEY: True}
            file_t0 = perf_counter()
            focus_meta = read_raw_embedded_focus_metadata(path)
            file_ms = elapsed_ms(file_t0)
            if file_ms > slowest_ms:
                slowest_ms = file_ms
                slowest_path = norm_path
            if focus_meta:
                metadata_hits += 1
                result[norm_path].update(focus_meta)
        if probe_enabled and raw_candidates:
            perf_log(
                _log,
                "[metadata.raw_focus] thread=%s raw_candidates=%s hits=%s checked=%s slowest_ms=%.1f slowest=%r total_ms=%.1f",
                threading.current_thread().name,
                raw_candidates,
                metadata_hits,
                len(result),
                slowest_ms,
                slowest_path,
                elapsed_ms(batch_t0),
            )
        return result

    @staticmethod
    def _parse_focus_float(raw) -> float | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clamp_focus_value(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @classmethod
    def _focus_box_from_center(
        cls,
        center_x: float,
        center_y: float,
        span_x: float,
        span_y: float,
    ) -> tuple[float, float, float, float]:
        cx = cls._clamp_focus_value(center_x)
        cy = cls._clamp_focus_value(center_y)
        sx = max(0.001, min(1.0, float(span_x)))
        sy = max(0.001, min(1.0, float(span_y)))
        return (
            cls._clamp_focus_value(cx - sx / 2.0),
            cls._clamp_focus_value(cy - sy / 2.0),
            cls._clamp_focus_value(cx + sx / 2.0),
            cls._clamp_focus_value(cy + sy / 2.0),
        )

    @classmethod
    def _build_report_focus_box(cls, rec: dict) -> tuple[float, float, float, float] | None:
        focus_x = cls._parse_focus_float(_metadata_value_from_candidates(rec, "focus_x"))
        focus_y = cls._parse_focus_float(_metadata_value_from_candidates(rec, "focus_y"))
        if focus_x is None or focus_y is None:
            return None
        if focus_x <= 1.0 and focus_y <= 1.0:
            return cls._focus_box_from_center(focus_x, focus_y, 0.045, 0.045)
        width = cls._parse_focus_float(
            _metadata_value_from_candidates(
                rec,
                "ExifImageWidth",
                "EXIF:ExifImageWidth",
                "ImageWidth",
                "File:ImageWidth",
                "RawImageWidth",
            )
        )
        height = cls._parse_focus_float(
            _metadata_value_from_candidates(
                rec,
                "ExifImageHeight",
                "EXIF:ExifImageHeight",
                "ImageHeight",
                "File:ImageHeight",
                "RawImageHeight",
            )
        )
        if not width or not height or width <= 0 or height <= 0:
            return None
        return cls._focus_box_from_center(
            focus_x / width,
            focus_y / height,
            min(0.12, max(0.02, 128.0 / width)),
            min(0.12, max(0.02, 128.0 / height)),
        )

    @classmethod
    def _build_focus_box_from_metadata(cls, rec: dict) -> tuple[float, float, float, float] | None:
        if not isinstance(rec, dict) or not rec:
            return None
        try:
            camera_type = resolve_focus_camera_type_from_metadata(rec)
            focus_box = extract_focus_box_for_display(rec, 1, 1, camera_type=camera_type)
        except Exception:
            focus_box = None
        if focus_box:
            return focus_box
        if rec.get(_RAW_FOCUS_CHECKED_KEY):
            return None
        return cls._build_report_focus_box(rec)

    def _parse_rec(self, rec: dict) -> dict:
        # 注释、标签、星级等支持 XMP sidecar（由 read_batch_metadata 合并），勿删以下键名。
        comment = _metadata_comment_from_meta(rec)
        tags = _metadata_tags_from_meta(rec)
        # 标题仍保留在 meta 里，供旧代码和缩略图兼容使用。
        title = (
            rec.get("XMP-dc:Title") or rec.get("XMP-dc:title")
            or rec.get("IFD0:XPTitle") or rec.get("IPTC:ObjectName") or ""
        )
        color = rec.get("XMP-xmp:Label") or ""
        rating_raw = _first_non_empty(
            rec.get("XMP-xmp:Rating"),
            rec.get("XMP:Rating"),
            rec.get("XMP-xmp:rating"),
            rec.get("rating"),
        )
        try:
            rating_num = int(float(str(rating_raw or 0)))
        except Exception:
            rating_num = 0
        rating = max(0, min(5, rating_num))
        # Pick/Reject 旗标（1=精选🏆, 0=无旗标, -1=排除🚫）
        # 实际 XMP 多为 <xmpDM:pick>1</xmpDM:pick>（Dynamic Media 命名空间），其次 xmp:Pick 等
        pick_raw = (
            rec.get("XMP-xmpDM:pick") or rec.get("XMP-xmpDM:Pick")
            or rec.get("XMP-xmp:Pick") or rec.get("XMP-xmp:PickLabel")
            or rec.get("XMP-1.0:Pick") or rec.get("XMP-1.0:PickLabel")
            or rec.get("XMP-lr:Pick") or rec.get("XMP-lr:PickLabel")
            or rec.get("XMP:Pick") or rec.get("XMP:PickLabel")
            or rec.get("pick")
            or ""
        )
        try:
            s = str(pick_raw).strip().lower()
            if s in ("true", "1", "yes"):
                pick = 1
            elif s in ("false", "0", "no", ""):
                pick = 0
            elif s in ("-1", "reject"):
                pick = -1
            else:
                pick = max(-1, min(1, int(float(s))))
        except Exception:
            pick = 0
        if pick == 0 and rating_num < 0:
            pick = -1

        # 城市 = 锐度（XMP:City 数值），省/直辖市/自治区 = 美学评分（XMP:State 数值），国家/地区 = 对焦状态（XMP:Country）
        city_raw = (
            rec.get("XMP:City") or rec.get("XMP-photoshop:City")
            or rec.get("IPTC:City") or ""
        )
        state_raw = (
            rec.get("XMP:State") or rec.get("XMP-photoshop:State")
            or rec.get("IPTC:Province-State") or ""
        )
        country_raw = (
            rec.get("XMP:Country")
            or rec.get("XMP-photoshop:Country")
            or rec.get("XMP-photoshop:Country-PrimaryLocationName")
            or rec.get("IPTC:Country-PrimaryLocationName") or ""
        )
        city = _format_optional_number(city_raw, "%06.2f")    # 锐度
        state = _format_optional_number(state_raw, "%05.2f") # 美学
        country = _focus_status_to_display(country_raw)      # 对焦状态 → 精焦/合焦/偏移/失焦
        shutter = _metadata_shutter_text(rec)
        aperture = _metadata_aperture_text(rec)
        iso = _metadata_iso_text(rec)
        focal_length = _metadata_focal_length_text(rec)
        camera_model = _metadata_camera_model_text(rec)
        lens_model = _metadata_lens_model_text(rec)
        capture_time = _metadata_capture_time_text(rec)
        sharpness = _metadata_sharpness_text(rec)
        aesthetic = _metadata_aesthetic_text(rec)
        focus_status = _metadata_focus_status_text(rec)
        focus_box = self._build_focus_box_from_metadata(rec)
        burst_id = _parse_optional_int(_metadata_value_from_candidates(rec, "burst_id"))
        burst_position = _parse_optional_int(_metadata_value_from_candidates(rec, "burst_position"))

        meta = {
            "title":   str(title).strip(),
            "comment": comment,
            "tags":    tags,
            "color":   str(color).strip(),
            "rating":  rating,
            "pick":    pick,
            "city":    city,
            "state":   state,
            "country": country,
            "shutter": shutter,
            "iso":     iso,
            "aperture": aperture,
            "focal_length": focal_length,
            "camera_model": camera_model,
            "lens_model": lens_model,
            "date_time_original": capture_time,
            "sharpness": sharpness,
            "aesthetic": aesthetic,
            "focus_status": focus_status,
            "focus_box_checked": True,
        }
        if focus_box is not None:
            meta["focus_box"] = focus_box
        if burst_id is not None:
            meta["burst_id"] = burst_id
        if burst_position is not None:
            meta["burst_position"] = burst_position
        return meta

    def _resolve_focus_source_path(self, display_path: str) -> str:
        norm_display = os.path.normpath(display_path) if display_path else ""
        if not norm_display:
            return ""
        return self._focus_source_paths.get(norm_display) or norm_display

    def _build_focus_cache_batch(self, chunk: list[str]) -> dict[str, dict]:
        """
        在批量元信息读取线程里顺手产出“文件内焦点缓存”。

        这里只处理可直接由文件 metadata 算出的尺寸无关焦点框；
        report.db 保底逻辑仍留给预览时按需处理，避免这里把 miss 提前写死。
        """
        ordered_source_paths: list[str] = []
        seen_paths: set[str] = set()
        for display_path in chunk or []:
            if self._stop_flag or self.isInterruptionRequested():
                return {}
            source_path = self._resolve_focus_source_path(display_path)
            if not source_path or not os.path.isfile(source_path):
                continue
            dedup_key = os.path.normcase(os.path.normpath(source_path))
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)
            ordered_source_paths.append(os.path.normpath(source_path))
        if not ordered_source_paths:
            return {}
        raw_focus_map = self._read_raw_embedded_focus_batch(ordered_source_paths)
        try:
            remaining_paths = [
                path
                for path in ordered_source_paths
                if os.path.normpath(path) not in raw_focus_map
            ]
            raw_map = read_batch_metadata(remaining_paths) if remaining_paths else {}
        except Exception as exc:
            _log.warning("[MetadataLoader._build_focus_cache_batch] read_batch_metadata failed: %s", exc)
            raw_map = {}
        focus_batch: dict[str, dict] = {}
        for source_path in ordered_source_paths:
            if self._stop_flag or self.isInterruptionRequested():
                return {}
            norm_source = os.path.normpath(source_path)
            raw = raw_focus_map.get(norm_source) or raw_map.get(norm_source) or raw_map.get(source_path)
            payload = self._build_focus_cache_payload(norm_source, raw)
            if payload is None:
                continue
            focus_batch[norm_source] = payload
        return focus_batch

    @staticmethod
    def _build_focus_cache_payload(source_path: str, raw: dict | None) -> dict | None:
        if not source_path or not isinstance(raw, dict) or not raw:
            return None
        try:
            camera_type = resolve_focus_camera_type_from_metadata(raw)
            # 这里的 1x1 只是兜底；真正的焦点坐标系尺寸优先从 metadata 宽高字段解析。
            focus_box = extract_focus_box_for_display(raw, 1, 1, camera_type=camera_type)
        except Exception:
            _log.exception("[MetadataLoader._build_focus_cache_payload] path=%r", source_path)
            return None
        if not focus_box:
            return None
        used_path = str(raw.get("SourceFile") or source_path).strip() or source_path
        return {
            "focus_box": focus_box,
            "used_path": os.path.normpath(used_path),
        }


# ── 图像文件列表面板 ───────────────────────────────────────────────────────────


__all__ = [name for name in globals() if not name.startswith('__')]
