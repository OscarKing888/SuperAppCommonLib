"""Shared local metadata snapshots and durable edits awaiting XMP publication.

Only the SQL owner thread touches the database. Source-file work runs in a
SidecarSyncAction on the application's pool. An accepted edit survives cache
eviction, directory refresh, process exit and a failed sidecar write.
"""
from __future__ import annotations

import atexit
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import sqlite3
import threading
import time
import uuid

from app_common.log import get_logger
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log
from .sidecar_lock import data_directory
from .metadata_identity import path_key, lexical_key, resolve_path, register_alias

_log = get_logger("metadata.store")
_write_context = threading.local()


class _SourceBusy(RuntimeError):
    pass


def resource_key(path):
    # Lexical only: submitting an edit must never stat a slow photo volume.
    return os.path.splitext(path_key(path))[0].casefold()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _snapshot_content(record):
    return {key: value for key, value in record.items() if key != "_metadata_revision"}


def _subjects(value):
    if isinstance(value, str):
        value = [part.strip() for part in value.split(";")]
    return list(dict.fromkeys(str(item) for item in (value or []) if str(item)))


def _normalise_fields(fields):
    from .photo_meta import (_xmp_sidecar_write_key, _is_xmp_title_key, _is_xmp_description_key,
                             _is_xmp_rating_key, _is_xmp_pick_key, _is_xmp_subject_key,
                             _normalise_rating_value, _normalise_pick_value)
    result = {}
    for key, value in fields.items():
        key = _xmp_sidecar_write_key(key)
        if _is_xmp_title_key(key):
            key, value = "XMP-dc:Title", "" if value is None else str(value)
        elif _is_xmp_description_key(key):
            key, value = "XMP-dc:Description", "" if value is None else str(value)
        elif _is_xmp_rating_key(key):
            key, value = "XMP-xmp:Rating", _normalise_rating_value(value)
        elif _is_xmp_pick_key(key):
            key, value = "XMP-xmpDM:pick", _normalise_pick_value(value)
        elif _is_xmp_subject_key(key):
            key, value = "XMP-dc:Subject", _subjects(value)
        if not key.lower().startswith(("xmp-", "xmp:")):
            raise ValueError(f"Not a writable XMP metadata field: {key}")
        if value is None:
            value = ""
        result[key] = value
    _json(result)  # Reject invalid payloads before acknowledging an edit.
    return result


def apply_fields(record, fields, subject_states=None):
    """Return one flat snapshot, including UI aliases and explicit clears."""
    result = deepcopy(record or {})
    aliases = {
        "xmp-dc:title": ("Title", "title", "bird_species_cn", "XMP:Title", "IFD0:XPTitle", "IFD0:DocumentName", "IPTC:ObjectName"),
        "xmp-dc:description": ("Description", "description", "comment", "caption", "XMP:Description",
                               "IFD0:XPComment", "IFD0:ImageDescription", "EXIF:UserComment", "ExifIFD:UserComment",
                               "EXIF:ImageDescription", "UserComment", "IPTC:Caption-Abstract"),
        "xmp-xmp:rating": ("rating",), "xmp-xmpdm:pick": ("pick",),
        "xmp-tiff:model": ("Model", "camera_model", "EXIF:Model", "IFD0:Model"),
        "xmp-aux:lens": ("Lens", "LensModel", "lens_model", "XMP-aux:LensModel", "ExifIFD:LensModel"),
        "xmp-exif:isospeedratings": ("iso", "ISO", "ExifIFD:ISO", "XMP-exif:PhotographicSensitivity"),
        "xmp-exif:exposuretime": ("ExposureTime", "shutter", "ExifIFD:ExposureTime"),
        "xmp-exif:fnumber": ("FNumber", "aperture", "ExifIFD:FNumber"),
        "xmp-exif:focallength": ("FocalLength", "focal_length", "ExifIFD:FocalLength"),
        "xmp-exif:datetimeoriginal": ("DateTimeOriginal", "date_time_original", "ExifIFD:DateTimeOriginal"),
    }
    for key, value in fields.items():
        for previous in list(result):
            if previous.lower() == key.lower():
                result.pop(previous)
        result[key] = deepcopy(value)
        for alias in aliases.get(key.lower(), ()):
            result[alias] = deepcopy(value)
        if key.lower().startswith("xmp-superpicky:"):
            name = key.split(":", 1)[1]
            result[name] = deepcopy(value)
            result["report." + name] = deepcopy(value)
    if "XMP-dc:Subject" in fields or subject_states:
        subjects = _subjects(result.get("XMP-dc:Subject", result.get("XMP-dc:subject", result.get("tags", []))))
        for subject, present in (subject_states or {}).items():
            subjects = [item for item in subjects if item != subject]
            if present:
                subjects.append(subject)
        result["XMP-dc:Subject"] = subjects
        result["XMP-dc:subject"] = subjects
        result["tags"] = subjects
    return result


@dataclass(frozen=True)
class EditReceipt:
    path: str
    fields: dict
    revision: int
    record: dict
    before: dict


class MetadataStore:
    def __init__(self, path=None, *, max_entries=100_000, max_bytes=512 * 1024 * 1024,
                 memory_entries=20_000, memory_bytes=256 * 1024 * 1024, start_scheduler=True):
        self.path = Path(path or data_directory() / "metadata-store-v1.sqlite3").absolute()
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._memory_entries, self._memory_bytes = memory_entries, memory_bytes
        self._memory = OrderedDict()
        self._versions = OrderedDict()
        self._aliases = OrderedDict()
        self._memory_size = 0
        self._pending = {}
        self._lock = threading.RLock()
        self._jobs = queue.Queue(maxsize=2048)
        self._closed = False
        self._closing = False
        self._sql_ident = None
        self._pools = []
        self._running = {}
        self._operation_local = threading.local()
        self._direct_local = threading.local()
        self._source_condition = threading.Condition(self._lock)
        self._active_sources = {}
        self._active_operations = {}
        self._active_sync = 0
        self._prepared_aliases = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._sql = threading.Thread(target=self._sql_loop, name="metadata-store-sql", daemon=True)
        self._sql.start()
        self._identity = ThreadPoolExecutor(max_workers=1, thread_name_prefix="metadata-identity")
        self._identity_ready = self._identity.submit(self._prepare_existing)
        self._scheduler = None
        if start_scheduler:
            self._scheduler = threading.Thread(target=self._coordinate, name="metadata-store-sync", daemon=True)
            self._scheduler.start()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 5
        while True:
            conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
            try:
                return self._initialize_connection(conn)
            except sqlite3.OperationalError as exc:
                conn.close()
                if ("locked" not in str(exc).lower() and "busy" not in str(exc).lower()) or time.monotonic() >= deadline:
                    raise
                # Two applications may create the shared WAL/schema together.
                # journal_mode can fail immediately despite the busy timeout.
                time.sleep(.02)
            except BaseException:
                conn.close()
                raise

    def _initialize_connection(self, conn):
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            conn.close()
            raise RuntimeError("Metadata store was created by a newer application; pending edits were not modified")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS resources (
                resource TEXT PRIMARY KEY, path TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                synced_revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS snapshots (
                profile TEXT NOT NULL, path TEXT NOT NULL, resource TEXT NOT NULL, payload TEXT NOT NULL,
                signature TEXT, touched INTEGER NOT NULL, bytes INTEGER NOT NULL,
                PRIMARY KEY(profile,path));
            CREATE INDEX IF NOT EXISTS snapshot_resource ON snapshots(resource);
            CREATE INDEX IF NOT EXISTS snapshot_age ON snapshots(touched);
            CREATE TABLE IF NOT EXISTS read_aliases (
                path TEXT PRIMARY KEY, resource TEXT NOT NULL, touched INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshot_totals (id INTEGER PRIMARY KEY, count INTEGER NOT NULL, bytes INTEGER NOT NULL);
            INSERT OR IGNORE INTO snapshot_totals SELECT 1,count(*),coalesce(sum(bytes),0) FROM snapshots;
            CREATE TRIGGER IF NOT EXISTS snapshot_insert AFTER INSERT ON snapshots BEGIN
                UPDATE snapshot_totals SET count=count+1,bytes=bytes+new.bytes WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS snapshot_delete AFTER DELETE ON snapshots BEGIN
                UPDATE snapshot_totals SET count=count-1,bytes=bytes-old.bytes WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS snapshot_update AFTER UPDATE ON snapshots BEGIN
                UPDATE snapshot_totals SET bytes=bytes+new.bytes-old.bytes WHERE id=1; END;
            CREATE TABLE IF NOT EXISTS outbox (
                resource TEXT PRIMARY KEY, path TEXT NOT NULL, revision INTEGER NOT NULL,
                fields TEXT NOT NULL, subjects TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS changes (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, resource TEXT NOT NULL,
                kind TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_guards (
                owner TEXT NOT NULL, scope TEXT NOT NULL, resource TEXT NOT NULL, pid INTEGER NOT NULL,
                PRIMARY KEY(owner,scope));
            CREATE TABLE IF NOT EXISTS source_write_leases (
                owner TEXT PRIMARY KEY, path TEXT NOT NULL, resource TEXT NOT NULL,
                pid INTEGER NOT NULL, operation_owner TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS identity_aliases (path TEXT PRIMARY KEY, canonical TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS edit_intents (
                resource TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                seq INTEGER NOT NULL, PRIMARY KEY(resource,kind,key));
            CREATE TABLE IF NOT EXISTS identity_conflicts (
                resource TEXT PRIMARY KEY, conflicts TEXT NOT NULL, original_tasks TEXT NOT NULL);
            PRAGMA user_version=1;
        """)
        if "last_seq" not in {row[1] for row in conn.execute("PRAGMA table_info(outbox)")}:
            try:
                conn.execute("ALTER TABLE outbox ADD COLUMN last_seq INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                # Another process may have finished the same additive migration.
                if "last_seq" not in {row[1] for row in conn.execute("PRAGMA table_info(outbox)")}:
                    raise
        conn.execute("UPDATE outbox SET last_seq=coalesce((SELECT max(seq) FROM changes WHERE changes.resource=outbox.resource AND kind='edit'),0) WHERE last_seq=0")
        for original, canonical in conn.execute("SELECT path,canonical FROM identity_aliases"):
            register_alias(original, canonical)
        with self._lock:
            self._pending = {r: (json.loads(f), json.loads(s), revision)
                             for r, f, s, revision in conn.execute("SELECT resource,fields,subjects,revision FROM outbox")}
        return conn

    def _sql_loop(self):
        self._sql_ident = threading.get_ident()
        conn, error = None, None
        try:
            try:
                conn = self._connect()
            except Exception as exc:
                error = exc
                _log.exception("[metadata.store] cannot open durable store path=%r", str(self.path))
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                callback, future, write = job
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    job_started = perf_counter()
                    if error is not None:
                        raise RuntimeError(f"Cannot open metadata store {self.path}: {error}") from error
                    if write:
                        conn.execute("BEGIN IMMEDIATE")
                    value = callback(conn)
                    if write:
                        conn.commit()
                    future._metadata_sql_elapsed_ms = elapsed_ms(job_started)
                    future.set_result(value)
                except BaseException as exc:
                    if conn is not None and conn.in_transaction:
                        conn.rollback()
                    future.set_exception(exc)
        finally:
            if conn is not None:
                conn.close()

    def submit_sql(self, callback, *, write=True):
        future = Future()
        with self._lock:
            if self._closed:
                future.set_exception(RuntimeError("Metadata store is closed"))
                return future
            try:
                self._jobs.put_nowait((callback, future, write))
            except queue.Full:
                future.set_exception(RuntimeError("Metadata store is busy; edit was not accepted"))
        return future

    def _call(self, callback, *, write=False):
        if threading.get_ident() == self._sql_ident:
            raise RuntimeError("Do not wait for a metadata SQL job from the SQL owner thread")
        return self.submit_sql(callback, write=write).result()

    def _prepare_existing(self):
        # Resolve pending legacy paths before any replay. Filesystem work stays
        # off both Qt and the SQL transaction owner.
        paths = self._call(lambda conn: [row[0] for row in conn.execute("SELECT path FROM outbox")])
        self._prepare_paths_impl(paths)

    def prepare_paths(self, paths, *, refresh=False):
        """Worker-only identity barrier; returns canonical paths, without writes to photos."""
        paths = tuple(paths)
        return self._identity.submit(self._prepare_paths_impl, paths, refresh).result()

    def _after_identity(self, paths, operation, *, refresh=False):
        result = Future()
        def prepare():
            try:
                self._prepare_paths_impl(paths, refresh)
                pending = operation()
                def completed(future):
                    try:
                        result.set_result(future.result())
                    except BaseException as exc:
                        result.set_exception(exc)
                pending.add_done_callback(completed)
            except BaseException as exc:
                result.set_exception(exc)
        try:
            self._identity.submit(prepare)
        except RuntimeError as exc:
            result.set_exception(exc)
        return result

    @staticmethod
    def _merge_identity_tasks(conn, canonical, rows):
        candidates = {}
        originals = []
        for resource, path, revision, fields_text, subjects_text, last_seq in rows:
            fields, subjects = json.loads(fields_text), json.loads(subjects_text)
            originals.append(dict(resource=resource, path=path, revision=revision, fields=fields,
                                  subject_states=subjects, seq=last_seq))
            recorded = {(kind, key): (seq, json.loads(value)) for kind, key, value, seq in
                        conn.execute("SELECT kind,key,value,seq FROM edit_intents WHERE resource=?", (resource,))}
            for kind, values in (("field", fields), ("subject", subjects)):
                for key, value in values.items():
                    # A legacy aggregate containing several fields does not
                    # reveal when each field changed. Never invent that order.
                    seq = recorded.get((kind, key), (last_seq if len(fields) + len(subjects) == 1 else 0, value))[0]
                    candidates.setdefault((kind, key), []).append((seq, value))
        merged, conflicts = {}, {}
        for item, values in candidates.items():
            distinct = {_json(value) for _, value in values}
            if len(distinct) > 1 and (any(seq == 0 for seq, _ in values)
                                     or len({seq for seq, _ in values}) != len(values)):
                conflicts[":".join(item)] = values
            else:
                merged[item] = max(values, key=lambda entry: entry[0])
        subject_reset = merged.get(("field", "XMP-dc:Subject"), (-1, None))[0]
        fields, subjects = {}, {}
        for (kind, key), (seq, value) in merged.items():
            if kind == "subject" and seq < subject_reset:
                continue
            (fields if kind == "field" else subjects)[key] = value
        return fields, subjects, merged, conflicts, originals

    def _prepare_paths_impl(self, paths, refresh=False):
        pairs = [resolve_path(path, refresh=refresh) for path in paths]
        aliases = [(old, new) for old, new in pairs if old != new and self._prepared_aliases.get(old) != new]
        if not aliases:
            return [new for _, new in pairs]
        def migrate(conn):
            changed = set()
            for original, canonical in aliases:
                old_resource = os.path.splitext(original)[0].casefold()
                resource = os.path.splitext(canonical)[0].casefold()
                conn.execute("INSERT OR REPLACE INTO identity_aliases VALUES(?,?)", (original, canonical))
                if old_resource == resource:
                    continue
                old_exists = conn.execute("SELECT 1 FROM resources WHERE resource=? UNION SELECT 1 FROM snapshots WHERE resource=? UNION SELECT 1 FROM outbox WHERE resource=?",
                                          (old_resource, old_resource, old_resource)).fetchone()
                if not old_exists:
                    continue
                keys = (old_resource, resource)
                rows = list(conn.execute("SELECT resource,path,revision,fields,subjects,last_seq FROM outbox WHERE resource IN (?,?)", keys))
                revision = max([row[0] for row in conn.execute("SELECT revision FROM resources WHERE resource IN (?,?)", keys)] + [0]) + 1
                if rows:
                    fields, subjects, intents, conflicts, originals = self._merge_identity_tasks(conn, resource, rows)
                    for existing_conflicts, existing_originals in conn.execute(
                            "SELECT conflicts,original_tasks FROM identity_conflicts WHERE resource IN (?,?)", keys):
                        conflicts.update(json.loads(existing_conflicts))
                        originals.extend(json.loads(existing_originals))
                    for item in conflicts:
                        kind, key = item.split(":", 1)
                        (fields if kind == "field" else subjects).pop(key, None)
                        intents.pop((kind, key), None)
                    conn.execute("DELETE FROM identity_conflicts WHERE resource IN (?,?)", keys)
                    conn.execute("DELETE FROM outbox WHERE resource IN (?,?)", keys)
                    conn.execute("INSERT INTO outbox(resource,path,revision,fields,subjects,last_seq,error) VALUES(?,?,?,?,?,?,?)",
                                 (resource, canonical, revision, _json(fields), _json(subjects), max(row[5] for row in rows),
                                  "旧路径编辑存在字段冲突，请重新编辑冲突字段" if conflicts else ""))
                    conn.execute("DELETE FROM edit_intents WHERE resource IN (?,?)", keys)
                    for (kind, key), (seq, value) in intents.items():
                        conn.execute("INSERT INTO edit_intents VALUES(?,?,?,?,?)", (resource, kind, key, _json(value), seq))
                    if conflicts:
                        conn.execute("INSERT OR REPLACE INTO identity_conflicts VALUES(?,?,?)", (resource, _json(conflicts), _json(originals)))
                conn.execute("DELETE FROM snapshots WHERE resource IN (?,?)", keys)
                conn.execute("UPDATE read_aliases SET resource=? WHERE resource=?", (resource, old_resource))
                conn.execute("DELETE FROM resources WHERE resource=?", (old_resource,))
                conn.execute("INSERT INTO resources(resource,path,revision) VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET path=excluded.path,revision=excluded.revision",
                             (resource, canonical, revision))
                self._change(conn, canonical, resource, "external")
                changed.update(keys)
            return changed
        changed = self._call(migrate, write=True)
        self._prepared_aliases.update(aliases)
        if changed:
            def publish(conn):
                # Read and publish on the SQL owner so a newer commit callback
                # cannot be replaced by an earlier migration's pending map.
                pending = {r: (json.loads(f), json.loads(s), revision) for r, f, s, revision in
                           conn.execute("SELECT resource,fields,subjects,revision FROM outbox")}
                with self._lock:
                    self._pending = pending
                    for key in [key for key, (record, _) in self._memory.items() if record.get("_metadata_resource") in changed]:
                        self._memory_size -= self._memory.pop(key)[1]
                    for key in changed:
                        self._versions.pop(key, None)
            self._call(publish)
        return [new for _, new in pairs]

    @staticmethod
    def _change(conn, path, resource, kind):
        conn.execute("INSERT INTO changes(path,resource,kind) VALUES(?,?,?)", (path, resource, kind))
        # Gaps are explicitly signalled to polling consumers, which reload a snapshot.
        seq = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("DELETE FROM changes WHERE seq < (SELECT max(seq)-50000 FROM changes)")
        return seq

    def _remember_memory(self, profile, path, record):
        key = (profile, path_key(path))
        value = deepcopy(record)
        size = len(_json(value).encode("utf-8")) * 3 + 256
        with self._lock:
            resource = value.get("_metadata_resource") or resource_key(path)
            # The querying worker can resume after a newer SQL commit callback
            # already published its value. Never put that older read back.
            if int(value.get("_metadata_revision", 0)) < self._versions.get(resource, 0):
                return False
            self._aliases[path_key(path)] = resource
            self._aliases.move_to_end(path_key(path))
            while len(self._aliases) > self._memory_entries:
                self._aliases.popitem(last=False)
            self._versions[resource] = max(self._versions.get(resource, 0), int(value.get("_metadata_revision", 0)))
            self._versions.move_to_end(resource)
            while len(self._versions) > self._memory_entries:
                self._versions.popitem(last=False)
            previous = self._memory.pop(key, None)
            if previous is not None:
                self._memory_size -= previous[1]
            if size <= self._memory_bytes:
                self._memory[key] = (value, size)
                self._memory_size += size
            while self._memory and (len(self._memory) > self._memory_entries or self._memory_size > self._memory_bytes):
                _, (_, removed) = self._memory.popitem(last=False)
                self._memory_size -= removed
            return True

    def peek(self, path, profile="browser"):
        key = (profile, path_key(path))
        with self._lock:
            entry = self._memory.get(key)
            if entry is None:
                resource = self._aliases.get(key[1], resource_key(path))
                pending = self._pending.get(resource)
                if profile != "browser" or pending is None:
                    return None
                record = apply_fields({"SourceFile": os.path.normpath(path), "_metadata_complete": False}, pending[0], pending[1])
                record["_metadata_revision"], record["_metadata_resource"] = pending[2], resource
                return record
            self._memory.move_to_end(key)
            record = deepcopy(entry[0])
            pending = self._pending.get(record.get("_metadata_resource") or resource_key(path))
            if pending:
                record = apply_fields(record, pending[0], pending[1])
                record["_metadata_revision"] = pending[2]
            return record

    def current_revision(self, path):
        """Known local version for GUI stale-result rejection; no SQL or stat."""
        with self._lock:
            resource = self._aliases.get(path_key(path), resource_key(path))
            pending = self._pending.get(resource)
            return max(pending[2] if pending else 0, self._versions.get(resource, 0))

    def peek_revision(self, path, profile="browser"):
        """Version probe for a cached parsed row, without copying its payload."""
        with self._lock:
            entry = self._memory.get((profile, path_key(path)))
            resource = self._aliases.get(path_key(path), resource_key(path))
            pending = self._pending.get(resource)
            if entry is None and (profile != "browser" or pending is None):
                return None
            return max(int(entry[0].get("_metadata_revision", 0)) if entry else 0,
                       pending[2] if pending else 0)

    @staticmethod
    def _read_resource(path, signatures=None, *, canonical=None):
        signature = signatures.get(os.path.normpath(path)) if signatures is not None else None
        if isinstance(signature, (tuple, list)) and len(signature) > 1 and signature[1]:
            dependency = signature[1]
            if isinstance(dependency, (tuple, list)) and isinstance(dependency[0], str):
                return resource_key(dependency[0])
        return resource_key(canonical if canonical is not None else path)

    @staticmethod
    def _cached_path_keys(conn, paths):
        """Restore evicted durable aliases using only the local database."""
        lexical = {os.path.normpath(path): lexical_key(path) for path in paths}
        resolved = {key: path_key(key) for key in lexical.values()}
        missing = [key for key, canonical in resolved.items() if key == canonical]
        for start in range(0, len(missing), 300):
            chunk = missing[start:start + 300]
            for key, canonical in conn.execute(
                    f"SELECT path,canonical FROM identity_aliases WHERE path IN ({','.join('?' for _ in chunk)})", chunk):
                resolved[key] = canonical
                register_alias(key, canonical)
        # Keep the complete lookup local even when one batch exceeds the alias
        # LRU capacity; registering later rows must not undo earlier SQL hits.
        return {path: resolved[key] for path, key in lexical.items()}

    def revisions(self, paths, signatures=None):
        paths = tuple(paths)
        def read(conn):
            result = {}
            keys = self._cached_path_keys(conn, paths)
            for path in paths:
                key = keys[os.path.normpath(path)]
                resource = self._read_resource(path, signatures, canonical=key)
                if signatures is None:
                    alias = conn.execute("SELECT resource FROM read_aliases WHERE path=?", (key,)).fetchone()
                    resource = alias[0] if alias else resource
                result[os.path.normpath(path)] = (conn.execute("SELECT revision FROM resources WHERE resource=?", (resource,)).fetchone() or (0,))[0]
            return result
        return self._call(read)

    def read_many(self, paths, profile="browser", signatures=None):
        started = perf_counter()
        paths = tuple(paths)
        if not paths:
            return {}
        def read(conn):
            # Connection initialization loads persisted aliases before this
            # SQL-only lookup. Never wait for identity/source-disk validation.
            canonical = self._cached_path_keys(conn, paths)
            originals = {key: path for path, key in canonical.items()}
            result = {}
            keys = list(originals)
            for start in range(0, len(keys), 300):
                chunk = keys[start:start + 300]
                rows = conn.execute(f"""SELECT s.path,s.payload,s.signature,s.resource,
                    coalesce(r.revision,0),o.fields,o.subjects FROM snapshots s
                    LEFT JOIN resources r ON r.resource=s.resource LEFT JOIN outbox o ON o.resource=s.resource
                    WHERE s.profile=? AND s.path IN ({','.join('?' for _ in chunk)})""", [profile, *chunk])
                for key, payload, signature, resource, revision, fields, subjects in rows:
                    original = originals[key]
                    if signatures is not None and signature != _json(signatures.get(original)):
                        continue
                    record = json.loads(payload)
                    if signatures is not None and record.get("_metadata_complete") is False:
                        continue
                    if fields is not None:
                        record = apply_fields(record, json.loads(fields), json.loads(subjects))
                    record["_metadata_revision"] = revision
                    record["_metadata_resource"] = resource
                    record["SourceFile"] = original
                    result[original] = record
            if profile == "browser" and signatures is None:
                for key, original in originals.items():
                    if original in result:
                        continue
                    alias = conn.execute("SELECT resource FROM read_aliases WHERE path=?", (key,)).fetchone()
                    resource = alias[0] if alias else resource_key(key)
                    pending = conn.execute("SELECT revision,fields,subjects FROM outbox WHERE resource=?", (resource,)).fetchone()
                    if pending:
                        record = apply_fields({"SourceFile": original, "_metadata_complete": False},
                                              json.loads(pending[1]), json.loads(pending[2]))
                        record["_metadata_revision"], record["_metadata_resource"] = pending[0], resource
                        result[original] = record
            return result, canonical
        future = self.submit_sql(read, write=False)
        result, canonical = future.result()
        for path, record in list(result.items()):
            register_alias(path, canonical[path])
            if not self._remember_memory(profile, path, record):
                current = self.peek(path, profile)
                if current is not None:
                    result[path] = current
                else:
                    result.pop(path)
        if result:
            touched = time.time_ns()
            self.submit_sql(lambda conn: conn.executemany("UPDATE snapshots SET touched=? WHERE profile=? AND path=?",
                [(touched, profile, canonical[path]) for path in result]))
        perf_log(_log, "[metadata.store.read] profile=%s paths=%s hits=%s sql_ms=%.1f total_ms=%.1f validated=%s",
                 profile, len(paths), len(result), getattr(future, "_metadata_sql_elapsed_ms", 0),
                 elapsed_ms(started), int(signatures is not None))
        return result

    def _prune(self, conn):
        # Eviction applies exclusively to snapshots. Never remove resources or outbox.
        count, size = conn.execute("SELECT count,bytes FROM snapshot_totals WHERE id=1").fetchone()
        if count <= self.max_entries and size <= self.max_bytes:
            return
        victims = []
        for profile, path, amount in conn.execute("SELECT profile,path,bytes FROM snapshots ORDER BY touched"):
            if count <= self.max_entries and size <= self.max_bytes:
                break
            victims.append((profile, path))
            count -= 1
            size -= amount
        conn.executemany("DELETE FROM snapshots WHERE profile=? AND path=?", victims)

    def remember_many(self, records, profile="browser", signatures=None, expected_revisions=None):
        return self._after_identity(tuple(records), lambda: self._remember_many(
            records, profile, signatures, expected_revisions))

    def _remember_many(self, records, profile="browser", signatures=None, expected_revisions=None):
        items = [(os.path.normpath(p), deepcopy(record)) for p, record in records.items()]
        def write(conn):
            accepted, invalidated = {}, []
            baseline, advanced = {}, set()
            for path, record in items:
                resource = self._read_resource(path, signatures)
                revision = (conn.execute("SELECT revision FROM resources WHERE resource=?", (resource,)).fetchone() or (0,))[0]
                baseline.setdefault(resource, revision)
                if expected_revisions is not None and baseline[resource] != expected_revisions.get(path, 0):
                    continue
                pending = conn.execute("SELECT fields,subjects FROM outbox WHERE resource=?", (resource,)).fetchone()
                if pending:
                    record = apply_fields(record, json.loads(pending[0]), json.loads(pending[1]))
                record["SourceFile"] = path
                record["_metadata_revision"] = revision
                record["_metadata_resource"] = resource
                record.setdefault("_metadata_complete", True)
                signature = _json(signatures.get(path)) if signatures is not None else None
                previous = conn.execute("SELECT payload,signature FROM snapshots WHERE profile=? AND path=?", (profile, path_key(path))).fetchone()
                changed = previous is None or _snapshot_content(json.loads(previous[0])) != _snapshot_content(record)
                # Compare the common source/sidecar part, excluding requested
                # tags/profile names. Browser stamps use a historical ordering.
                def common_signature(value, name):
                    if not isinstance(value, (list, tuple)) or len(value) < 2:
                        return None
                    source, sidecar = value[:2]
                    if name == "browser" and isinstance(source, (list, tuple)) and len(source) == 5:
                        source = [source[4], source[3], source[2], source[0], source[1]]
                    return _json(source), _json(sidecar)
                incoming = common_signature(signatures.get(path), profile) if signatures is not None else None
                peers = conn.execute("SELECT profile,path,signature FROM snapshots WHERE resource=?", (resource,)).fetchall()
                stale = set()
                for peer_profile, peer_path, peer_signature in peers:
                    if peer_profile == profile and peer_path == path_key(path):
                        continue
                    known = common_signature(json.loads(peer_signature), peer_profile) if peer_signature else None
                    if incoming is not None and known is not None:
                        if incoming[1] != known[1] or (peer_path == path_key(path) and incoming[0] != known[0]):
                            stale.add((peer_profile, peer_path))
                    elif previous and changed and signatures is None and peer_path == path_key(path):
                        stale.add((peer_profile, peer_path))
                if stale:
                    invalidated.extend(stale)
                    conn.executemany("DELETE FROM snapshots WHERE profile=? AND path=?", stale)
                # A browser projection can remain identical while EXIF-only
                # fields change. Its invalidation must still reach subscribers.
                signature_changed = previous is not None and signature != previous[1]
                changed = changed or bool(stale) or signature_changed
                known_path = previous is not None or conn.execute("SELECT 1 FROM read_aliases WHERE path=?", (path_key(path),)).fetchone()
                if changed and known_path and resource not in advanced:
                    revision += 1
                    advanced.add(resource)
                    conn.execute("INSERT INTO resources(resource,path,revision) VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET revision=excluded.revision",
                                 (resource, path, revision))
                    conn.execute("UPDATE outbox SET revision=? WHERE resource=?", (revision, resource))
                    record["_metadata_revision"] = revision
                payload = _json(record)
                conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?) ON CONFLICT(profile,path) DO UPDATE SET resource=excluded.resource,payload=excluded.payload,signature=excluded.signature,touched=excluded.touched,bytes=excluded.bytes",
                             (profile, path_key(path), resource, payload, signature, time.time_ns(), len(payload.encode("utf-8")) + 256))
                if changed:
                    self._change(conn, path, resource, "import")
                accepted[path] = record
                conn.execute("INSERT INTO read_aliases VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET resource=excluded.resource,touched=excluded.touched",
                             (path_key(path), resource, time.time_ns()))
            self._prune(conn)
            conn.execute("DELETE FROM read_aliases WHERE path IN (SELECT path FROM read_aliases ORDER BY touched DESC LIMIT -1 OFFSET ?)", (self.max_entries,))
            overlays = {}
            for resource in advanced:
                revision = conn.execute("SELECT revision FROM resources WHERE resource=?", (resource,)).fetchone()[0]
                for record in accepted.values():
                    if record["_metadata_resource"] == resource:
                        record["_metadata_revision"] = revision
                pending = conn.execute("SELECT fields,subjects,revision FROM outbox WHERE resource=?", (resource,)).fetchone()
                if pending:
                    overlays[resource] = (json.loads(pending[0]), json.loads(pending[1]), pending[2])
            return accepted, overlays, invalidated
        future = self.submit_sql(write)
        result = Future()
        def done(completed):
            try:
                accepted, overlays, invalidated = completed.result()
                with self._lock:
                    self._pending.update(overlays)
                    for key in invalidated:
                        if key in self._memory:
                            self._memory_size -= self._memory.pop(key)[1]
                for path, record in accepted.items():
                    self._remember_memory(profile, path, record)
                result.set_result(accepted)
            except BaseException as exc:
                result.set_exception(exc)
        future.add_done_callback(done)
        return result

    def submit_edit(self, path, fields, subject_states=None):
        return self._after_identity([path], lambda: self._submit_edit(path, fields, subject_states), refresh=True)

    def _submit_edit(self, path, fields, subject_states=None):
        started = perf_counter()
        path = os.path.normpath(os.path.abspath(path))
        try:
            if self._closing:
                raise RuntimeError("Metadata store is closing; edit was not accepted")
            fields = _normalise_fields(fields)
            subjects = {str(k): bool(v) for k, v in (subject_states or {}).items()}
        except Exception as exc:
            failed = Future()
            failed.set_exception(exc)
            return failed
        resource = resource_key(path)
        def write(conn):
            self._clean_guards(conn)
            if any(self._overlap(path_key(path), resource, scope, key)
                   for scope, key in conn.execute("SELECT scope,resource FROM operation_guards")):
                raise RuntimeError("照片正在复制、移动或删除，元数据编辑未被接受；请在文件操作完成后重试。")
            row = conn.execute("SELECT revision FROM resources WHERE resource=?", (resource,)).fetchone()
            revision = (row[0] if row else 0) + 1
            pending = conn.execute("SELECT fields,subjects FROM outbox WHERE resource=?", (resource,)).fetchone()
            patch, states = (json.loads(pending[0]), json.loads(pending[1])) if pending else ({}, {})
            if "XMP-dc:Subject" in fields:
                states = {}
            patch.update(fields)
            states.update(subjects)
            rows = list(conn.execute("SELECT profile,path,payload,signature FROM snapshots WHERE resource=? OR path=? ORDER BY CASE profile WHEN 'browser' THEN 0 WHEN 'subjects' THEN 2 ELSE 1 END", (resource, path_key(path))))
            before = {"SourceFile": path}
            for _, _, payload, _ in rows:
                before.update(json.loads(payload))
            if pending:
                before = apply_fields(before, json.loads(pending[0]), json.loads(pending[1]))
            record = apply_fields(before, fields, subjects)
            record["SourceFile"], record["_metadata_revision"], record["_metadata_resource"] = path, revision, resource
            record["_metadata_complete"] = any(profile in ("browser", "full", "full-pillow") and json.loads(payload).get("_metadata_complete", True)
                                                for profile, _, payload, _ in rows)
            conn.execute("INSERT INTO resources(resource,path,revision) VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET path=excluded.path,revision=excluded.revision",
                         (resource, path_key(path), revision))
            conn.execute("INSERT INTO outbox(resource,path,revision,fields,subjects) VALUES(?,?,?,?,?) ON CONFLICT(resource) DO UPDATE SET path=excluded.path,revision=excluded.revision,fields=excluded.fields,subjects=excluded.subjects,attempts=0,next_attempt=0,error=''",
                         (resource, path_key(path), revision, _json(patch), _json(states)))
            conn.execute("INSERT INTO read_aliases VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET resource=excluded.resource,touched=excluded.touched",
                         (path_key(path), resource, time.time_ns()))
            if not rows:
                before["_metadata_complete"] = False
                rows = [("browser", path_key(path), _json(before), None)]
            for profile, cached_path, payload, signature in rows:
                updated = apply_fields(json.loads(payload), fields, subjects)
                updated["_metadata_revision"] = revision
                updated["_metadata_resource"] = resource
                text = _json(updated)
                conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?) ON CONFLICT(profile,path) DO UPDATE SET resource=excluded.resource,payload=excluded.payload,signature=excluded.signature,touched=excluded.touched,bytes=excluded.bytes",
                             (profile, cached_path, resource, text, signature, time.time_ns(), len(text.encode("utf-8")) + 256))
            seq = self._change(conn, path, resource, "edit")
            conn.execute("UPDATE outbox SET last_seq=? WHERE resource=?", (seq, resource))
            if "XMP-dc:Subject" in fields:
                conn.execute("DELETE FROM edit_intents WHERE resource=? AND kind='subject'", (resource,))
            for kind, values in (("field", fields), ("subject", subjects)):
                for key, value in values.items():
                    conn.execute("INSERT OR REPLACE INTO edit_intents VALUES(?,?,?,?,?)", (resource, kind, key, _json(value), seq))
            conflict = conn.execute("SELECT conflicts FROM identity_conflicts WHERE resource=?", (resource,)).fetchone()
            if conflict:
                remaining = json.loads(conflict[0])
                for key in fields: remaining.pop("field:" + key, None)
                for key in subjects: remaining.pop("subject:" + key, None)
                if remaining:
                    conn.execute("UPDATE identity_conflicts SET conflicts=? WHERE resource=?", (_json(remaining), resource))
                else:
                    conn.execute("DELETE FROM identity_conflicts WHERE resource=?", (resource,))
            return EditReceipt(path, fields, revision, record, before), patch, states
        result = Future()
        future = self.submit_sql(write)
        def done(completed):
            try:
                receipt, patch, states = completed.result()
                with self._lock:
                    self._pending[resource] = (patch, states, receipt.revision)
                    keys = [key for key in self._memory if self._aliases.get(key[1], resource_key(key[1])) == resource or key[1] == path_key(path)]
                    for profile, cached_path in keys:
                        current = self._memory[(profile, cached_path)][0]
                        updated = apply_fields(current, fields, subjects)
                        updated["_metadata_revision"] = receipt.revision
                        updated["_metadata_resource"] = resource
                        self._remember_memory(profile, cached_path, updated)
                self._remember_memory("browser", path, receipt.record)
                perf_log(_log, "[metadata.store.commit] paths=1 fields=%s subject_states=%s revision=%s sql_ms=%.1f total_ms=%.1f",
                         len(fields), len(subjects), receipt.revision,
                         getattr(completed, "_metadata_sql_elapsed_ms", 0), elapsed_ms(started))
                result.set_result(receipt)
                self._wake.set()
            except BaseException as exc:
                result.set_exception(exc)
        future.add_done_callback(done)
        return result

    def refresh_scope(self, directory):
        return self._after_identity([directory], lambda: self._refresh_scope(directory), refresh=True)

    def _refresh_scope(self, directory):
        prefix = path_key(directory).rstrip(os.sep) + os.sep
        with self._lock:
            for key in [key for key in self._memory if key[1].startswith(prefix)]:
                self._memory_size -= self._memory.pop(key)[1]
        def invalidate(conn):
            conn.execute("DELETE FROM snapshots WHERE substr(path,1,?)=?", (len(prefix), prefix))
            self._change(conn, prefix, "", "refresh")
        return self.submit_sql(invalidate)

    def invalidate_paths(self, paths):
        keys = {path_key(path) for path in paths}
        with self._lock:
            for key in [key for key in self._memory if key[1] in keys]:
                self._memory_size -= self._memory.pop(key)[1]
        def invalidate(conn):
            conn.executemany("DELETE FROM snapshots WHERE path=?", [(key,) for key in keys])
            for key in keys:
                self._change(conn, key, resource_key(key), "refresh")
        return self.submit_sql(invalidate)

    def note_sidecar_write(self, path):
        """Notify a successful low-level write; SQL work never rereads the XML."""
        self.prepare_paths([path])
        path, resource = os.path.normpath(path), resource_key(path)
        def invalidate(conn):
            revision = (conn.execute("SELECT revision FROM resources WHERE resource=?", (resource,)).fetchone() or (0,))[0] + 1
            conn.execute("INSERT INTO resources(resource,path,revision,synced_revision) VALUES(?,?,?,?) ON CONFLICT(resource) DO UPDATE SET revision=excluded.revision",
                         (resource, path, revision, revision))
            # A local pending patch remains authoritative for its edited fields.
            # Advancing its generation also rejects any older in-flight import.
            conn.execute("UPDATE outbox SET revision=? WHERE resource=?", (revision, resource))
            conn.execute("DELETE FROM snapshots WHERE resource=? OR path=?", (resource, path_key(path)))
            pending = conn.execute("SELECT fields,subjects,revision FROM outbox WHERE resource=?", (resource,)).fetchone()
            self._change(conn, path, resource, "external")
            return revision, pending
        future = self.submit_sql(invalidate)
        published = Future()
        def done(completed):
            try:
                revision, pending = completed.result()
                with self._lock:
                    self._versions[resource] = revision
                    self._versions.move_to_end(resource)
                    while len(self._versions) > self._memory_entries:
                        self._versions.popitem(last=False)
                    if pending:
                        self._pending[resource] = (json.loads(pending[0]), json.loads(pending[1]), pending[2])
                    else:
                        self._pending.pop(resource, None)
                    for key in [key for key in self._memory if self._aliases.get(key[1], resource_key(key[1])) == resource or key[1] == path_key(path)]:
                        self._memory_size -= self._memory.pop(key)[1]
                self._wake.set()
                published.set_result((revision, pending))
            except Exception as exc:
                _log.exception("[metadata.store] could not invalidate direct sidecar write path=%r", path)
                published.set_exception(exc)
        future.add_done_callback(done)
        return published

    @contextmanager
    def direct_write_guard(self, path, *, synchronization=False):
        """Serialize legacy source writes against a directory/file operation.

        The lease lives in a short SQL transaction, not in an open transaction
        during file I/O. A file operation waits for earlier writes before moving
        anything, while later writes fail before creating a sidecar.
        """
        self.prepare_paths([path])
        resource = resource_key(path)
        held = getattr(self._direct_local, "held", None)
        if held is None:
            held = self._direct_local.held = {}
        if resource in held:
            yield
            return
        owner = uuid.uuid4().hex
        operation_owner = getattr(self._operation_local, "owner", None) or ""
        with self._source_condition:
            if self._closing and not synchronization and not operation_owner:
                raise RuntimeError("Metadata store is closing")
            self._active_sources[owner] = threading.get_ident()
        acquired = False
        try:
            def acquire(conn):
                self._clean_guards(conn)
                for guarded_owner, scope, key in conn.execute("SELECT owner,scope,resource FROM operation_guards"):
                    if (guarded_owner != operation_owner and not synchronization
                            and self._overlap(path_key(path), resource, scope, key)):
                        raise RuntimeError("照片正在执行文件操作；已拒绝旧路径的元数据写入。")
                conn.execute("INSERT INTO source_write_leases VALUES(?,?,?,?,?)",
                             (owner, path_key(path), resource, os.getpid(), operation_owner))
            self._call(acquire, write=True)
            acquired = True
            held[resource] = owner
            if not Path(path).is_file():
                raise FileNotFoundError(f"找不到照片原文件，未创建 XMP：{path}")
            yield
        finally:
            held.pop(resource, None)
            try:
                if acquired:
                    self._call(lambda conn: conn.execute("DELETE FROM source_write_leases WHERE owner=?", (owner,)), write=True)
            finally:
                with self._source_condition:
                    self._active_sources.pop(owner, None)
                    self._source_condition.notify_all()

    def changes_since(self, seq):
        def read(conn):
            low, high = conn.execute("SELECT min(seq),max(seq) FROM changes").fetchone()
            rows = conn.execute("SELECT seq,path,resource,kind FROM changes WHERE seq>? ORDER BY seq LIMIT 1000", (seq,)).fetchall()
            return {"seq": rows[-1][0] if rows else (high or 0), "reset": bool(low and seq and seq < low - 1),
                    "changes": [dict(zip(("seq", "path", "resource", "kind"), row)) for row in rows]}
        return self._call(read)

    def affected_paths(self, paths, resources):
        """Match currently listed read aliases without accessing the photo volume."""
        originals = {path_key(path): os.path.normpath(path) for path in paths}
        resources = set(resources)
        def read(conn):
            matched = {original for key, original in originals.items() if resource_key(key) in resources}
            keys = list(originals)
            for start in range(0, len(keys), 300):
                chunk = keys[start:start + 300]
                for path, resource in conn.execute(f"SELECT path,resource FROM read_aliases WHERE path IN ({','.join('?' for _ in chunk)})", chunk):
                    if resource in resources:
                        matched.add(originals[path])
            return [path for path in originals.values() if path in matched]
        return self._call(read)

    def attach_pool(self, pool):
        with self._lock:
            if pool not in self._pools:
                self._pools.append(pool)
        self._wake.set()

    def detach_pool(self, pool):
        with self._lock:
            if pool in self._pools:
                self._pools.remove(pool)

    def _schedule_sync(self, path, pool, *, deadline=None):
        from app_common.file_browser._work_action import CallableAction
        from app_common.file_browser._work_policy import WorkKind
        resource = resource_key(path)
        with self._lock:
            running = self._running.get(resource)
            if running is not None and not running[0].done():
                return running[0]
            action = SidecarSyncAction(self, path, deadline=deadline)
            future = pool.submit_action(CallableAction(action.execute), kind=WorkKind.METADATA, priority=-5)
            self._running[resource] = (future, pool)
            return future

    def _coordinate(self):
        self._identity_ready.result()
        seq = 0
        while not self._stop.is_set():
            try:
                change = self.changes_since(seq)
                seq = change["seq"]
                if change["changes"] or change["reset"]:
                    # Polling is SQL-only. Refresh pending overlays across application processes.
                    def refresh_pending(conn):
                        pending = {r: (json.loads(f), json.loads(s), v)
                                   for r, f, s, v in conn.execute("SELECT resource,fields,subjects,revision FROM outbox")}
                        # This assignment shares the SQL owner's sequencing with
                        # commit callbacks; an old poll cannot replace a new edit.
                        with self._lock:
                            self._pending = pending
                    self._call(refresh_pending)
                    with self._lock:
                        changed = {item["resource"] for item in change["changes"]}
                        keys = list(self._memory) if change["reset"] else [key for key in self._memory if self._aliases.get(key[1], resource_key(key[1])) in changed]
                    # Reuse SQL snapshots so another process's ack cannot leave an old local value.
                    for profile in {key[0] for key in keys}:
                        paths = [path for p, path in keys if p == profile]
                        fresh = self.read_many(paths, profile=profile)
                        with self._lock:
                            for path in paths:
                                key = (profile, path_key(path))
                                if os.path.normpath(path) not in fresh and key in self._memory:
                                    self._memory_size -= self._memory.pop(key)[1]
                with self._lock:
                    self._running = {key: item for key, item in self._running.items() if not item[0].done()}
                    pool = self._pools[-1] if self._pools else None
                    active = set(self._running)
                if pool is not None and len(active) < 4:
                    jobs = self._call(lambda conn: conn.execute(
                        "SELECT resource,path FROM outbox WHERE next_attempt<=? ORDER BY next_attempt LIMIT 16", (time.time(),)).fetchall())
                    for resource, path in jobs:
                        if resource in active or len(active) >= 4 or self._stop.is_set():
                            continue
                        try:
                            self._schedule_sync(path, pool)
                        except Exception:
                            self.detach_pool(pool)
                            break
                        active.add(resource)
            except Exception:
                if not self._stop.is_set():
                    _log.exception("[metadata.store] synchronization coordinator")
            self._wake.wait(.5)
            self._wake.clear()

    def _sync_one(self, path, deadline=None):
        path = self.prepare_paths([path])[0]
        with self._source_condition:
            if self._closing:
                return False
            self._active_sync += 1
        previous = getattr(_write_context, "value", None)
        _write_context.value = (self, True)
        try:
            return self._sync_one_locked(path, deadline=deadline)
        finally:
            _write_context.value = previous
            with self._source_condition:
                self._active_sync -= 1
                self._source_condition.notify_all()

    def _sync_one_locked(self, path, deadline=None):
        from .photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
        resource = resource_key(path)
        lock = xmp_sidecar_write_lock(path)
        if not lock.acquire(timeout=max(0, deadline - time.monotonic()) if deadline is not None else -1):
            return False
        try:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            row = self._call(lambda conn: conn.execute(
                "SELECT path,revision,fields,subjects FROM outbox WHERE resource=?", (resource,)).fetchone())
            if row is None:
                return True
            source, revision, text, subject_text = row
            fields, states = json.loads(text), json.loads(subject_text)
            try:
                conflict = self._call(lambda conn: conn.execute("SELECT conflicts FROM identity_conflicts WHERE resource=?", (resource,)).fetchone())
                if conflict:
                    raise ValueError("同一照片存在无法确定先后顺序的旧路径编辑，请重新编辑这些字段：" + ", ".join(json.loads(conflict[0])))
                if not Path(source).is_file():
                    raise FileNotFoundError(f"找不到照片原文件：{source}")
                writer = PhotoMetaDataXMP()
                if states:
                    current = fields["XMP-dc:Subject"] if "XMP-dc:Subject" in fields else writer.read_subjects(source, strict=True)
                    fields["XMP-dc:Subject"] = apply_fields({"XMP-dc:Subject": current}, {}, states)["XMP-dc:Subject"]
                if not writer.write(source, fields):
                    raise OSError(f"XMP 同步失败，编辑仍保存在本地：{source}")
                def acknowledge(conn):
                    conn.execute("UPDATE resources SET synced_revision=max(synced_revision,?) WHERE resource=?", (revision, resource))
                    conn.execute("DELETE FROM outbox WHERE resource=? AND revision=?", (resource, revision))
                    if not conn.execute("SELECT 1 FROM outbox WHERE resource=?", (resource,)).fetchone():
                        conn.execute("DELETE FROM edit_intents WHERE resource=?", (resource,))
                    updated_records = []
                    # The strict subject read may have discovered unknown tags
                    # absent from the cached base. Keep those in every snapshot.
                    if "XMP-dc:Subject" in fields:
                        pending = conn.execute("SELECT fields,subjects FROM outbox WHERE resource=?", (resource,)).fetchone()
                        for profile, cached_path, payload in conn.execute("SELECT profile,path,payload FROM snapshots WHERE resource=?", (resource,)).fetchall():
                            updated = apply_fields(json.loads(payload), {"XMP-dc:Subject": fields["XMP-dc:Subject"]})
                            if pending:
                                updated = apply_fields(updated, json.loads(pending[0]), json.loads(pending[1]))
                            if _snapshot_content(updated) != _snapshot_content(json.loads(payload)):
                                updated_records.append((profile, cached_path, updated))
                    current_revision = conn.execute("SELECT revision FROM resources WHERE resource=?", (resource,)).fetchone()[0]
                    if updated_records:
                        current_revision += 1
                        conn.execute("UPDATE resources SET revision=? WHERE resource=?", (current_revision, resource))
                        conn.execute("UPDATE outbox SET revision=? WHERE resource=?", (current_revision, resource))
                        for profile, cached_path, updated in updated_records:
                            updated["_metadata_revision"] = current_revision
                            text = _json(updated)
                            conn.execute("UPDATE snapshots SET payload=?,bytes=? WHERE profile=? AND path=?", (text, len(text.encode("utf-8")) + 256, profile, cached_path))
                    self._change(conn, source, resource, "synced")
                    pending = conn.execute("SELECT fields,subjects,revision FROM outbox WHERE resource=?", (resource,)).fetchone()
                    return current_revision, pending, updated_records
                acknowledged, pending, updated_records = self._call(acknowledge, write=True)
                with self._lock:
                    if self._pending.get(resource, ({}, {}, -1))[2] <= acknowledged:
                        if pending:
                            self._pending[resource] = (json.loads(pending[0]), json.loads(pending[1]), pending[2])
                        else:
                            self._pending.pop(resource, None)
                    for profile, cached_path, updated in updated_records:
                        self._remember_memory(profile, cached_path, updated)
                return True
            except Exception as exc:
                message = str(exc)
                def failed(conn):
                    attempts = (conn.execute("SELECT attempts FROM outbox WHERE resource=? AND revision=?", (resource, revision)).fetchone() or (-1,))[0]
                    if attempts >= 0:
                        conn.execute("UPDATE outbox SET attempts=attempts+1,next_attempt=?,error=? WHERE resource=? AND revision=?",
                                     (time.time() + min(60, 2 ** min(attempts, 6)), message, resource, revision))
                        self._change(conn, source, resource, "failed")
                self._call(failed, write=True)
                _log.warning("[metadata.store] sidecar pending path=%r revision=%s error=%s", source, revision, message)
                return False

        finally:
            lock.release()

    def flush_paths(self, paths, timeout=10):
        # Worker/CLI barrier only; never call this from a GUI event handler.
        paths = list(paths)
        self.prepare_paths(paths)
        scopes = [(path_key(path), resource_key(path)) for path in paths]
        pending = self._call(lambda conn: conn.execute("SELECT resource,path FROM outbox").fetchall())
        resources = {resource: path for resource, path in pending
                     if any(self._overlap(path_key(path), resource, scope, key) for scope, key in scopes)}
        deadline = time.monotonic() + timeout
        while resources and time.monotonic() < deadline:
            for resource, path in list(resources.items()):
                if time.monotonic() >= deadline:
                    return False
                with self._lock:
                    pool = self._pools[-1] if self._pools else None
                    in_owned_worker = any(getattr(candidate, "is_worker_thread", lambda: False)()
                                          for candidate in self._pools)
                if pool is None or in_owned_worker:
                    # Standalone CLI callers already execute in their source
                    # worker. A file-operation action already in an attached
                    # pool must not wait for another job in a fully occupied
                    # pool; the sidecar lock still makes duplicate syncs safe.
                    if not self._sync_one(path, deadline=deadline):
                        return False
                else:
                    try:
                        if not self._schedule_sync(path, pool, deadline=deadline).result(timeout=max(0, deadline - time.monotonic())):
                            return False
                    except TimeoutError:
                        return False
                pending = self._call(lambda conn, key=resource: conn.execute("SELECT 1 FROM outbox WHERE resource=?", (key,)).fetchone())
                if pending is None:
                    resources.pop(resource)
        return not resources

    @staticmethod
    def _overlap(path, resource, scope, key):
        return (resource == key or path == scope or path.startswith(scope.rstrip(os.sep) + os.sep)
                or scope.startswith(path.rstrip(os.sep) + os.sep))

    @staticmethod
    def _process_alive(pid):
        if pid == os.getpid():
            return True
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return ctypes.get_last_error() != 87  # Access denied is not proof of death.
            try:
                code = wintypes.DWORD()
                return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @classmethod
    def _clean_guards(cls, conn):
        for (pid,) in conn.execute("SELECT pid FROM operation_guards UNION SELECT pid FROM source_write_leases"):
            if not cls._process_alive(pid):
                conn.execute("DELETE FROM operation_guards WHERE pid=?", (pid,))
                conn.execute("DELETE FROM source_write_leases WHERE pid=?", (pid,))

    @contextmanager
    def file_operation(self, paths, timeout=10):
        paths = list(dict.fromkeys(os.path.normpath(os.path.abspath(p)) for p in paths))
        self.prepare_paths(paths, refresh=True)
        owner = getattr(self._operation_local, "owner", None)
        outer = owner is None
        if outer:
            owner = uuid.uuid4().hex
            with self._source_condition:
                if self._closing:
                    raise RuntimeError("Metadata store is closing; file operation was not started")
                self._active_operations[owner] = threading.get_ident()
        scopes = [(path_key(path), resource_key(path)) for path in paths]
        def acquire(conn):
            self._clean_guards(conn)
            existing = list(conn.execute("SELECT owner,scope,resource FROM operation_guards"))
            for scope, key in scopes:
                if any(other != owner and self._overlap(scope, key, guarded, resource)
                       for other, guarded, resource in existing):
                    raise RuntimeError("此照片范围已有文件操作正在执行，请稍后重试。")
                if any(operation_owner != owner and self._overlap(scope, key, writing_path, writing_resource)
                       for writing_path, writing_resource, operation_owner in conn.execute(
                           "SELECT path,resource,operation_owner FROM source_write_leases")):
                    raise _SourceBusy("等待已有元数据写入结束，尚未开始文件操作。")
            conn.executemany("INSERT OR IGNORE INTO operation_guards VALUES(?,?,?,?)",
                             [(owner, scope, key, os.getpid()) for scope, key in scopes])
        acquired = False
        previous_context = getattr(_write_context, "value", None)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    self._call(acquire, write=True)
                    acquired = True
                    break
                except _SourceBusy:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.02)
            self._operation_local.owner = owner
            _write_context.value = (self, False)
            if not self.flush_paths(paths, timeout=max(0, deadline - time.monotonic())):
                raise OSError("尚有元数据未同步到 XMP；已保留本地编辑，文件操作已停止。")
            yield
        finally:
            # Nested operations keep the union protected until the outermost
            # copy/move/delete has finished publishing its complete file pair.
            _write_context.value = previous_context
            if outer:
                self._operation_local.owner = None
                try:
                    if acquired:
                        self._call(lambda conn: conn.execute("DELETE FROM operation_guards WHERE owner=?", (owner,)), write=True)
                finally:
                    with self._source_condition:
                        self._active_operations.pop(owner, None)
                        self._source_condition.notify_all()

    def pending_edits(self, path):
        self.prepare_paths([path])
        row = self._call(lambda conn: conn.execute("SELECT fields,subjects,revision FROM outbox WHERE resource=?",
                                                  (resource_key(path),)).fetchone())
        return None if row is None else {"fields": json.loads(row[0]), "subject_states": json.loads(row[1]), "revision": row[2]}

    def export_fields(self, path):
        pending = self.pending_edits(path)
        return pending["fields"] if pending else {}

    def pending_status(self, paths=None):
        self._identity_ready.result()
        if paths is not None:
            paths = tuple(paths)
            self.prepare_paths(paths)
        resources = {resource_key(path) for path in paths} if paths is not None else None
        return self._call(lambda conn: [dict(zip(("resource", "path", "revision", "attempts", "next_attempt", "error"), row))
            for row in conn.execute("SELECT resource,path,revision,attempts,next_attempt,error FROM outbox")
            if resources is None or row[0] in resources])

    def close(self):
        with self._source_condition:
            if self._closed:
                return
            if (threading.get_ident() in self._active_sources.values()
                    or threading.get_ident() in self._active_operations.values()):
                raise RuntimeError("Close the metadata store from its owner, not from inside a source write")
            self._closing = True
        self._stop.set()
        self._wake.set()
        if self._scheduler is not None and threading.current_thread() is not self._scheduler:
            self._scheduler.join(timeout=6)
        with self._lock:
            if self._closed:
                return
            running = list(self._running.values())
            self._pools.clear()
        for future, pool in running:
            if not future.done():
                pool.cancel(future)
        # Running source transactions still need SQL to acknowledge their actual
        # result and release leases. Keep the owner alive through real completion.
        with self._source_condition:
            while self._active_sources or self._active_sync or self._active_operations:
                self._source_condition.wait(.1)
        self._identity.shutdown(wait=True)
        # Accepted SQL jobs always drain; queued synchronization remains durable.
        with self._lock:
            self._closed = True
        self._jobs.put(None)
        if threading.current_thread() is not self._sql:
            self._sql.join(timeout=10)


# The coordinator wraps this operation in the pool's CallableAction lazily;
# importing the durable store must not import the Qt file-browser package.
class SidecarSyncAction:
    def __init__(self, store, path, *, cancelled=lambda: False, deadline=None):
        self._cancelled = cancelled
        self.store, self.path = store, path
        self.deadline = deadline

    def execute(self):
        # Background contention must eventually release this pool worker and
        # allow shutdown; pending edits are retried by the coordinator.
        deadline = self.deadline if self.deadline is not None else time.monotonic() + 10
        return None if self._cancelled() else self.store._sync_one(self.path, deadline=deadline)


_default = None
_default_lock = threading.Lock()


def get_store():
    global _default
    with _default_lock:
        if _default is None:
            _default = MetadataStore()
        return _default


def close_store():
    global _default
    with _default_lock:
        store, _default = _default, None
    if store is not None:
        store.close()


@contextmanager
def sidecar_write_guard(path):
    context = getattr(_write_context, "value", None)
    store, synchronization = context if context is not None else (get_store(), False)
    _write_context.value = (store, synchronization)
    try:
        with store.direct_write_guard(path, synchronization=synchronization):
            yield
    finally:
        _write_context.value = context


def notify_sidecar_write(path):
    context = getattr(_write_context, "value", None)
    if context is not None and context[1]:
        return  # Synchronization acknowledges its version and emits one event.
    store = context[0] if context is not None else get_store()
    future = store.note_sidecar_write(path)
    capture = getattr(_write_context, "capture", None)
    if capture is not None and capture[0] == resource_key(path):
        capture[1].append(future)
    return future


def write_with_published_revision(writer, path, fields):
    """Worker-only direct write returning this write's published revision.

    Capture the notification from this call rather than reading a resource's
    latest revision, which may already belong to a later user edit.
    """
    notifications = []
    previous = getattr(_write_context, "capture", None)
    _write_context.capture = (resource_key(path), notifications)
    try:
        written = writer.write(path, fields)
    finally:
        _write_context.capture = previous
    if not written:
        return None
    if not notifications:
        raise RuntimeError("XMP 已保存，但未收到本地元数据版本通知")
    revision, _pending = notifications[-1].result()
    return revision


atexit.register(close_store)
