#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky ReportDB - SQLite 报告数据库封装
替代原有的 CSV 报告存储，提供更高效的查询和更新操作。

Usage:
    db = ReportDB("/path/to/photos")
    db.insert_photo({"filename": "IMG_1234", "has_bird": 1, ...})
    photo = db.get_photo("IMG_1234")
    db.close()
"""

import os
import sqlite3
import time
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from .file_utils import ensure_hidden_directory
from .log import get_logger

_log = get_logger("report_db")


# Schema 版本，用于未来升级
SCHEMA_VERSION = "4"

# 所有列定义（有序），用于 CREATE TABLE 和数据验证
PHOTO_COLUMNS = [
    # (列名, SQLite 类型, 默认值)
    ("filename",      "TEXT NOT NULL UNIQUE", None),
    ("has_bird",      "INTEGER", 0),          # 0=no, 1=yes
    ("confidence",    "REAL", 0.0),
    ("head_sharp",    "REAL", None),
    ("left_eye",      "REAL", None),
    ("right_eye",     "REAL", None),
    ("beak",          "REAL", None),
    ("nima_score",    "REAL", None),
    ("is_flying",     "INTEGER", 0),          # 0=no, 1=yes
    ("flight_conf",   "REAL", None),
    ("rating",        "INTEGER", 0),          # -1/0/1/2/3
    ("focus_status",  "TEXT", None),           # BEST/GOOD/BAD/WORST
    ("focus_x",       "REAL", None),
    ("focus_y",       "REAL", None),
    ("adj_sharpness", "REAL", None),
    ("adj_topiq",     "REAL", None),
    
    # V2: 相机设置
    ("iso",              "INTEGER", None),
    ("shutter_speed",    "TEXT", None),
    ("aperture",         "TEXT", None),
    ("focal_length",     "REAL", None),
    ("focal_length_35mm","INTEGER", None),
    ("camera_model",     "TEXT", None),
    ("lens_model",       "TEXT", None),
    
    # V2: GPS
    ("gps_latitude",     "REAL", None),
    ("gps_longitude",    "REAL", None),
    ("gps_altitude",     "REAL", None),
    
    # V2: IPTC 元数据
    ("title",            "TEXT", None),
    ("caption",          "TEXT", None),
    ("city",             "TEXT", None),
    ("state_province",   "TEXT", None),
    ("country",          "TEXT", None),
    
    # V2: 时间
    ("date_time_original", "TEXT", None),
    
    # V2: 鸟种识别
    ("bird_species_cn",  "TEXT", None),
    ("bird_species_en",  "TEXT", None),
    ("birdid_confidence","REAL", None),
    
    # V2: 曝光状态
    ("exposure_status",  "TEXT", None),
    
    # V3: 文件路径（相对路径）
    ("original_path",    "TEXT", None),
    ("current_path",     "TEXT", None),
    ("temp_jpeg_path",   "TEXT", None),
    ("debug_crop_path",  "TEXT", None),   # 裁切鸟+mask (crop_debug/)
    ("yolo_debug_path",  "TEXT", None),   # 全图+YOLO框 (yolo_debug/)
    
    ("created_at",    "TEXT", None),
    ("updated_at",    "TEXT", None),
]

# 列名集合，用于快速查找
COLUMN_NAMES = {col[0] for col in PHOTO_COLUMNS}

# 控制开关：True = 仅从 report.db 读取 EXIF，不读文件；False = report 优先，未命中再读文件
EXIF_ONLY_FROM_REPORT_DB = True


def find_report_root(directory: str) -> Optional[str]:
    """
    从给定目录开始向上查找，返回最近一个包含 .superpicky/report.db 的目录路径。
    若未找到则返回 None。
    """
    if not directory:
        return None
    try:
        cur = os.path.normpath(directory)
        last = None
        while cur and cur != last:
            db_path = os.path.join(cur, ".superpicky", ReportDB.DB_FILENAME)
            if os.path.isfile(db_path):
                _log.info("[find_report_root] 命中 root=%r db_path=%r", cur, db_path)
                return cur
            last = cur
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                break
            cur = parent
    except Exception as e:
        _log.warning("[find_report_root] 失败 directory=%r: %s", directory, e)
    _log.info("[find_report_root] 未找到 report.db directory=%r", directory)
    return None


def get_preview_path_for_file(path: str, current_dir: str, report_cache: Dict[str, Any]) -> str:
    """
    若 report 中有该文件（按文件名 stem 匹配）且存在 temp_jpeg_path（相对 current_dir，如 .superpicky\\cache\\temp_preview\\xxx.jpg），
    则返回拼出的完整路径用于预览/缩略图，避免重复解码；否则返回原 path。
    current_dir 为选中目录（含 .superpicky 的父目录），用于解析相对路径的 temp_jpeg_path。
    """
    _log.debug("[get_preview_path_for_file] path=%r current_dir=%r cache_keys=%s", path, current_dir, len(report_cache) if isinstance(report_cache, dict) else 0)
    if not path or not report_cache or not current_dir:
        _log.debug("[get_preview_path_for_file] 跳过 path=%r", path)
        return path
    stem = os.path.splitext(os.path.basename(path))[0]
    row = report_cache.get(stem) if isinstance(report_cache, dict) else None
    if not row or not isinstance(row, dict):
        return path
    temp_path = row.get("temp_jpeg_path")
    if not temp_path or not str(temp_path).strip():
        return path
    temp_path = str(temp_path).strip()
    resolved = os.path.normpath(os.path.join(current_dir, temp_path)) if not os.path.isabs(temp_path) else os.path.normpath(temp_path)
    if os.path.isfile(resolved):
        _log.debug("[get_preview_path_for_file] 使用 temp_jpeg_path path=%r resolved=%r", path, resolved)
        return resolved
    _log.debug("[get_preview_path_for_file] temp 文件不存在 使用原 path=%r", path)
    return path


def report_row_to_exiftool_style(row: Dict[str, Any], source_file: str) -> Dict[str, Any]:
    """
    将 ReportDB 的一行（photos 表记录）转为 exiftool -G1 风格的平坦字典，
    供文件列表 MetadataLoader._parse_rec 与主窗口 load_all_exif_exiftool 复用。

    Args:
        row: get_photo / get_all_photos 返回的字典（列名即键）
        source_file: 当前文件路径，用于 SourceFile 及供调用方 normpath

    Returns:
        exiftool 风格 dict，包含 SourceFile 及 XMP-dc:Title、XMP-xmp:Rating、
        XMP:City/State/Country、ExifIFD:ISO、Composite:ShutterSpeed 等键
    """
    _log.debug("[report_row_to_exiftool_style] source_file=%r row_keys=%s", source_file, len(row) if isinstance(row, dict) else 0)
    out: Dict[str, Any] = {"SourceFile": source_file}
    if not isinstance(row, dict):
        _log.debug("[report_row_to_exiftool_style] row 非 dict 返回仅 SourceFile")
        return out

    def _set(k: str, v: Any) -> None:
        if v is not None and (not isinstance(v, str) or v.strip() != ""):
            out[k] = v

    # 标题：优先鸟种中文名（列表“标题”列），无则用 title
    species = row.get("bird_species_cn")
    if species and str(species).strip():
        _set("XMP-dc:Title", species)
        _set("XMP-dc:title", species)
        _set("IPTC:ObjectName", species)
    else:
        _set("XMP-dc:Title", row.get("title"))
        _set("XMP-dc:title", row.get("title"))
        _set("IPTC:ObjectName", row.get("title"))
    _set("XMP-dc:Description", row.get("caption"))
    _set("IFD0:ImageDescription", row.get("caption"))

    # 对焦状态（列表/主窗口复用 XMP:Country 语义，优先 focus_status）
    focus = row.get("focus_status")
    _set("XMP:Country", focus or row.get("country"))
    _set("XMP-photoshop:Country", focus or row.get("country"))
    _set("XMP-photoshop:Country-PrimaryLocationName", focus or row.get("country"))

    # 锐度（列表“锐度值”列）← adj_sharpness；美学评分（列表“美学评分”列）← adj_topiq
    sharp = row.get("adj_sharpness")
    if sharp is not None:
        _set("XMP:City", sharp)
        _set("XMP-photoshop:City", sharp)
    else:
        _set("XMP:City", row.get("city"))
        _set("XMP-photoshop:City", row.get("city"))
    topiq = row.get("adj_topiq")
    if topiq is not None:
        _set("XMP:State", topiq)
        _set("XMP-photoshop:State", topiq)
    else:
        _set("XMP:State", row.get("state_province"))
        _set("XMP-photoshop:State", row.get("state_province"))

    # 颜色标签：红 = is_flying==1，绿 = 精焦（focus_status BEST/精焦）
    is_flying = row.get("is_flying")
    focus_str = (row.get("focus_status") or "").strip().upper()
    if is_flying == 1:
        _set("XMP-xmp:Label", "Red")
    elif focus_str in ("BEST", "精焦"):
        _set("XMP-xmp:Label", "Green")

    # 星级
    r = row.get("rating")
    if r is not None:
        try:
            rv = int(float(str(r)))
            if rv < 0:
                out["XMP-xmpDM:pick"] = -1
                out["XMP-xmpDM:Pick"] = -1
            else:
                out["XMP-xmp:Rating"] = max(0, min(5, rv))
        except (TypeError, ValueError):
            pass

    # 相机与镜头
    _set("IFD0:Model", row.get("camera_model"))
    _set("ExifIFD:LensModel", row.get("lens_model"))
    _set("EXIF:Model", row.get("camera_model"))
    _set("EXIF:LensModel", row.get("lens_model"))

    # 拍摄参数
    _set("ExifIFD:ISO", row.get("iso"))
    _set("EXIF:ISO", row.get("iso"))
    _set("Composite:ShutterSpeed", row.get("shutter_speed"))
    _set("ExifIFD:ExposureTime", row.get("shutter_speed"))
    _set("Composite:Aperture", row.get("aperture"))
    _set("ExifIFD:FNumber", row.get("aperture"))
    fl = row.get("focal_length")
    if fl is not None:
        _set("ExifIFD:FocalLength", fl)
        _set("EXIF:FocalLength", fl)
    fl35 = row.get("focal_length_35mm")
    if fl35 is not None:
        _set("ExifIFD:FocalLengthIn35mmFormat", fl35)
        _set("EXIF:FocalLengthIn35mmFormat", fl35)

    # 时间
    _set("ExifIFD:DateTimeOriginal", row.get("date_time_original"))
    _set("EXIF:DateTimeOriginal", row.get("date_time_original"))

    # GPS
    lat = row.get("gps_latitude")
    lon = row.get("gps_longitude")
    alt = row.get("gps_altitude")
    if lat is not None:
        _set("Composite:GPSLatitude", lat)
        _set("EXIF:GPSLatitude", lat)
    if lon is not None:
        _set("Composite:GPSLongitude", lon)
        _set("EXIF:GPSLongitude", lon)
    if alt is not None:
        _set("Composite:GPSAltitude", alt)
        _set("EXIF:GPSAltitude", alt)

    _log.debug("[report_row_to_exiftool_style] 完成 out_keys=%s", len(out))
    return out


class ReportDB:
    """SQLite 报告数据库封装。

    每个照片处理目录拥有一个独立的数据库文件：
        <directory>/.superpicky/report.db

    线程安全：设置 check_same_thread=False，支持工作线程写入。
    WAL 模式：支持读写并发。
    """

    DB_FILENAME = "report.db"

    def __init__(self, directory: str, create_if_missing: bool = True):
        """
        初始化数据库连接。

        Args:
            directory: 照片目录路径（数据库存储在 .superpicky/ 子目录下）
            create_if_missing: 若 True，确保 .superpicky 存在并创建库；若 False，仅当 report.db 已存在时打开，否则抛出 FileNotFoundError
        """
        _log.info("[ReportDB.__init__] directory=%r create_if_missing=%s", directory, create_if_missing)
        self.directory = directory
        self._superpicky_dir = os.path.join(directory, ".superpicky")
        self.db_path = os.path.join(self._superpicky_dir, self.DB_FILENAME)
        # 同一连接会被主线程和后台线程复用，需要串行化访问避免事务冲突
        self._lock = threading.RLock()

        if create_if_missing:
            # 确保 .superpicky 目录存在并隐藏（Windows 下设置 Hidden 属性）
            ensure_hidden_directory(self._superpicky_dir)
        else:
            if not os.path.isfile(self.db_path):
                raise FileNotFoundError(
                    f"ReportDB: database not found: {self.db_path!r} (create_if_missing=False)"
                )

        # 连接数据库
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row  # 支持按列名访问

        # 启用 WAL 模式和外键
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # 初始化 Schema
        self._init_schema()
        _log.info("[ReportDB.__init__] 完成 db_path=%r", self.db_path)

    @classmethod
    def open_if_exists(cls, directory: str) -> Optional["ReportDB"]:
        """
        仅当 directory/.superpicky/report.db 已存在时打开并返回 ReportDB，否则返回 None。
        用于只读加载缓存，不会创建目录或数据库文件。
        """
        db_path = os.path.join(directory, ".superpicky", cls.DB_FILENAME)
        _log.info("[ReportDB.open_if_exists] directory=%r db_path=%r", directory, db_path)
        if not os.path.isfile(db_path):
            _log.info("[ReportDB.open_if_exists] 文件不存在 返回 None")
            return None
        try:
            db = cls(directory, create_if_missing=False)
            _log.info("[ReportDB.open_if_exists] 打开成功")
            return db
        except Exception as e:
            _log.warning("[ReportDB.open_if_exists] 打开失败: %s", e)
            return None

    def _init_schema(self):
        """创建表和索引（如果不存在）。"""
        _log.info("[ReportDB._init_schema] START")
        # 构建 CREATE TABLE 语句
        col_defs = []
        for name, type_def, _ in PHOTO_COLUMNS:
            col_defs.append(f"    {name} {type_def}")

        create_sql = (
            "CREATE TABLE IF NOT EXISTS photos (\n"
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            + ",\n".join(col_defs)
            + "\n)"
        )

        with self._lock:
            with self._conn:
                self._conn.execute(create_sql)

                # 索引
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_filename "
                    "ON photos(filename)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photos_rating "
                    "ON photos(rating)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photos_has_bird "
                    "ON photos(has_bird)"
                )

                # 元数据表
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)

                # 初始化元数据
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                    ("schema_version", SCHEMA_VERSION)
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                    ("directory_path", self.directory)
                )

        # Schema 升级在独立事务中执行，避免嵌套 commit 冲突
        self._upgrade_schema_if_needed()
        _log.info("[ReportDB._init_schema] END")

    def _upgrade_schema_if_needed(self):
        """检查并升级数据库 Schema（支持连续升级 v1 -> v2 -> v3 -> v4）"""
        _log.debug("[ReportDB._upgrade_schema_if_needed] START")
        with self._lock:
            # 获取当前 schema 版本
            cursor = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            )
            row = cursor.fetchone()
            current_version = row[0] if row else "1"

            # ----------------------------------------------------------------------
            #  Upgrade: v1 -> v2 (EXIF metadata)
            # ----------------------------------------------------------------------
            if current_version == "1":
                _log.info("[ReportDB._upgrade_schema_if_needed] 升级 v1 -> v2")
                new_columns = [
                    ("iso", "INTEGER"),
                    ("shutter_speed", "TEXT"),
                    ("aperture", "TEXT"),
                    ("focal_length", "REAL"),
                    ("focal_length_35mm", "INTEGER"),
                    ("camera_model", "TEXT"),
                    ("lens_model", "TEXT"),
                    ("gps_latitude", "REAL"),
                    ("gps_longitude", "REAL"),
                    ("gps_altitude", "REAL"),
                    ("title", "TEXT"),
                    ("caption", "TEXT"),
                    ("city", "TEXT"),
                    ("state_province", "TEXT"),
                    ("country", "TEXT"),
                    ("date_time_original", "TEXT"),
                    ("bird_species_cn", "TEXT"),
                    ("bird_species_en", "TEXT"),
                    ("birdid_confidence", "REAL"),
                    ("exposure_status", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("2")
                current_version = "2"
                _log.info("[ReportDB._upgrade_schema_if_needed] 已升级到 v2")

            # ----------------------------------------------------------------------
            #  Upgrade: v2 -> v3 (File paths)
            # ----------------------------------------------------------------------
            if current_version == "2":
                _log.info("[ReportDB._upgrade_schema_if_needed] 升级 v2 -> v3")
                new_columns_v3 = [
                    ("original_path", "TEXT"),
                    ("current_path", "TEXT"),
                    ("temp_jpeg_path", "TEXT"),
                    ("debug_crop_path", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v3:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("3")
                current_version = "3"
                _log.info("[ReportDB._upgrade_schema_if_needed] 已升级到 v3")

            # ----------------------------------------------------------------------
            #  Upgrade: v3 -> v4 (Check debug images)
            # ----------------------------------------------------------------------
            if current_version == "3":
                _log.info("[ReportDB._upgrade_schema_if_needed] 升级 v3 -> v4")
                new_columns_v4 = [
                    ("yolo_debug_path", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v4:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("4")
                current_version = "4"
                _log.info("[ReportDB._upgrade_schema_if_needed] 已升级到 v4")
        _log.debug("[ReportDB._upgrade_schema_if_needed] END current_version=%s", current_version)

    def _update_schema_version(self, version):
        """更新数据库中的版本号（由调用方负责提交事务）"""
        _log.debug("[ReportDB._update_schema_version] version=%s", version)
        with self._lock:
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (version,)
            )

    # ==========================================================================
    #  写入操作
    # ==========================================================================

    def insert_photo(self, data: dict) -> None:
        """
        插入或更新一条照片记录。

        如果 filename 已存在则更新，否则插入新记录。
        自动处理 CSV 兼容的数据格式转换（如 "yes"/"no" → 1/0）。

        Args:
            data: 照片数据字典，键为列名
        """
        filename = data.get("filename", "")
        _log.info("[ReportDB.insert_photo] filename=%r", filename)
        cleaned = self._clean_data(data)
        now = _now_iso()
        cleaned.setdefault("created_at", now)
        cleaned["updated_at"] = now

        # 仅保留合法列
        columns = [k for k in cleaned if k in COLUMN_NAMES]
        values = [cleaned[k] for k in columns]

        placeholders = ", ".join(["?"] * len(columns))
        col_str = ", ".join(columns)

        # INSERT OR REPLACE
        update_clause = ", ".join(
            f"{c} = excluded.{c}" for c in columns if c != "filename"
        )

        sql = (
            f"INSERT INTO photos ({col_str}) VALUES ({placeholders}) "
            f"ON CONFLICT(filename) DO UPDATE SET {update_clause}"
        )

        with self._lock:
            self._conn.execute(sql, values)
            self._safe_commit()
        _log.info("[ReportDB.insert_photo] 完成 filename=%r", filename)

    def insert_photos_batch(self, photos: List[dict]) -> int:
        """
        批量插入或更新照片记录。

        使用事务包裹，性能优于逐条插入。

        Args:
            photos: 照片数据字典列表

        Returns:
            成功插入/更新的记录数
        """
        _log.info("[ReportDB.insert_photos_batch] photos_count=%s", len(photos))
        if not photos:
            _log.info("[ReportDB.insert_photos_batch] 空列表 返回 0")
            return 0

        now = _now_iso()
        count = 0

        with self._lock:
            with self._conn:
                for data in photos:
                    cleaned = self._clean_data(data)
                    cleaned.setdefault("created_at", now)
                    cleaned["updated_at"] = now

                    columns = [k for k in cleaned if k in COLUMN_NAMES]
                    values = [cleaned[k] for k in columns]

                    placeholders = ", ".join(["?"] * len(columns))
                    col_str = ", ".join(columns)

                    update_clause = ", ".join(
                        f"{c} = excluded.{c}" for c in columns if c != "filename"
                    )

                    sql = (
                        f"INSERT INTO photos ({col_str}) VALUES ({placeholders}) "
                        f"ON CONFLICT(filename) DO UPDATE SET {update_clause}"
                    )

                    self._conn.execute(sql, values)
                    count += 1

        _log.info("[ReportDB.insert_photos_batch] 完成 count=%s", count)
        return count

    # ==========================================================================
    #  查询操作
    # ==========================================================================

    def get_photo(self, filename: str) -> Optional[dict]:
        """
        按 filename 查询单条记录。

        Args:
            filename: 照片文件名（不含扩展名）

        Returns:
            照片数据字典，未找到返回 None
        """
        _log.debug("[ReportDB.get_photo] filename=%r", filename)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE filename = ?", (filename,)
            )
            row = cursor.fetchone()
            result = dict(row) if row else None
        _log.debug("[ReportDB.get_photo] 完成 found=%s", result is not None)
        return result

    def get_all_photos(self) -> List[dict]:
        """
        获取所有照片记录。

        Returns:
            照片数据字典列表
        """
        _log.debug("[ReportDB.get_all_photos]")
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM photos ORDER BY filename")
            rows = [dict(row) for row in cursor.fetchall()]
        _log.debug("[ReportDB.get_all_photos] 完成 count=%s", len(rows))
        return rows

    def get_bird_photos(self) -> List[dict]:
        """
        获取所有有鸟的照片记录（has_bird=1）。

        Returns:
            有鸟照片数据字典列表
        """
        _log.debug("[ReportDB.get_bird_photos]")
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE has_bird = 1 ORDER BY filename"
            )
            rows = [dict(row) for row in cursor.fetchall()]
        _log.debug("[ReportDB.get_bird_photos] 完成 count=%s", len(rows))
        return rows

    def get_photos_by_rating(self, rating: int) -> List[dict]:
        """
        按评分查询照片。

        Args:
            rating: 评分 (-1/0/1/2/3)

        Returns:
            照片数据字典列表
        """
        _log.debug("[ReportDB.get_photos_by_rating] rating=%s", rating)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE rating = ? ORDER BY filename",
                (rating,)
            )
            rows = [dict(row) for row in cursor.fetchall()]
        _log.debug("[ReportDB.get_photos_by_rating] 完成 count=%s", len(rows))
        return rows

    def get_distinct_species(self, use_en: bool = False) -> List[str]:
        """
        获取数据库中去重后的鸟种名称列表（用于结果浏览器筛选下拉框）。

        Args:
            use_en: True 使用英文鸟种列，False 使用中文鸟种列

        Returns:
            鸟种名称列表（已去重、去空值）
        """
        _log.debug("[ReportDB.get_distinct_species] use_en=%s", use_en)
        column = "bird_species_en" if use_en else "bird_species_cn"
        assert column in {"bird_species_en", "bird_species_cn"}, f"Invalid column: {column}"
        order_clause = f"{column} COLLATE NOCASE" if use_en else column

        with self._lock:
            cursor = self._conn.execute(
                f"""
                SELECT DISTINCT {column}
                FROM photos
                WHERE {column} IS NOT NULL
                  AND TRIM({column}) != ''
                ORDER BY {order_clause}
                """
            )
            species_list = [row[0] for row in cursor.fetchall()]
        _log.debug("[ReportDB.get_distinct_species] 完成 count=%s", len(species_list))
        return species_list

    def get_photos_by_filters(self, filters: Optional[dict] = None) -> List[dict]:
        """
        按结果浏览器筛选条件查询照片。

        支持的 filters 键：
            - ratings: List[int]
            - focus_statuses: List[str]
            - is_flying: List[int]
            - bird_species_cn / bird_species_en: str
            - sort_by: filename | sharpness_desc | aesthetic_desc
        """
        _log.debug("[ReportDB.get_photos_by_filters] filters=%s", filters)
        filters = filters or {}

        where_clauses = []
        params: List[Any] = []

        ratings = filters.get("ratings")
        if isinstance(ratings, list):
            if not ratings:
                return []
            placeholders = ", ".join(["?"] * len(ratings))
            where_clauses.append(f"rating IN ({placeholders})")
            params.extend(ratings)

        focus_statuses = filters.get("focus_statuses")
        if isinstance(focus_statuses, list):
            if not focus_statuses:
                return []
            placeholders = ", ".join(["?"] * len(focus_statuses))
            where_clauses.append(f"focus_status IN ({placeholders})")
            params.extend(focus_statuses)

        is_flying = filters.get("is_flying")
        if isinstance(is_flying, list):
            if not is_flying:
                return []
            placeholders = ", ".join(["?"] * len(is_flying))
            where_clauses.append(f"is_flying IN ({placeholders})")
            params.extend(is_flying)

        species_col = None
        species_val = None
        if "bird_species_en" in filters:
            species_col = "bird_species_en"
            species_val = filters.get("bird_species_en")
        elif "bird_species_cn" in filters:
            species_col = "bird_species_cn"
            species_val = filters.get("bird_species_cn")

        if isinstance(species_val, str) and species_val.strip():
            assert species_col in {"bird_species_en", "bird_species_cn"}, f"Invalid column: {species_col}"
            where_clauses.append(f"{species_col} = ?")
            params.append(species_val.strip())

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        sort_by = filters.get("sort_by") or "filename"
        if sort_by == "sharpness_desc":
            order_sql = "ORDER BY COALESCE(adj_sharpness, head_sharp, -1e99) DESC, filename ASC"
        elif sort_by == "aesthetic_desc":
            order_sql = "ORDER BY COALESCE(adj_topiq, nima_score, -1e99) DESC, filename ASC"
        else:
            order_sql = "ORDER BY filename ASC"

        sql = f"SELECT * FROM photos {where_sql} {order_sql}"

        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()]
        _log.debug("[ReportDB.get_photos_by_filters] 完成 count=%s", len(rows))
        return rows

    def get_statistics(self) -> dict:
        """
        获取评分统计信息。

        Returns:
            包含统计数据的字典，如:
            {
                "total": 217,
                "has_bird": 180,
                "flying": 15,
                "by_rating": {0: 50, 1: 60, 2: 45, 3: 25}
            }
        """
        _log.debug("[ReportDB.get_statistics]")
        stats = {}

        with self._lock:
            # 总数
            row = self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()
            stats["total"] = row[0]

            # 有鸟数
            row = self._conn.execute(
                "SELECT COUNT(*) FROM photos WHERE has_bird = 1"
            ).fetchone()
            stats["has_bird"] = row[0]

            # 飞行数
            row = self._conn.execute(
                "SELECT COUNT(*) FROM photos WHERE is_flying = 1"
            ).fetchone()
            stats["flying"] = row[0]

            # 按评分统计
            cursor = self._conn.execute(
                "SELECT rating, COUNT(*) as cnt FROM photos GROUP BY rating ORDER BY rating"
            )
            stats["by_rating"] = {row[0]: row[1] for row in cursor.fetchall()}

        _log.debug("[ReportDB.get_statistics] 完成 total=%s", stats.get("total"))
        return stats

    def count(self) -> int:
        """返回总记录数。"""
        _log.debug("[ReportDB.count]")
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()
            n = row[0]
        _log.debug("[ReportDB.count] 完成 n=%s", n)
        return n

    def exists(self) -> bool:
        """数据库文件是否存在。"""
        _log.debug("[ReportDB.exists] db_path=%r", self.db_path)
        return os.path.exists(self.db_path)

    # ==========================================================================
    #  更新操作
    # ==========================================================================

    def update_photo(self, filename: str, data: dict) -> bool:
        """
        按 filename 更新指定字段。

        Args:
            filename: 照片文件名
            data: 要更新的字段字典（仅包含需要更新的字段）

        Returns:
            是否成功更新
        """
        _log.info("[ReportDB.update_photo] filename=%r", filename)
        cleaned = self._clean_data(data)
        cleaned["updated_at"] = _now_iso()

        # 仅保留合法列，排除 filename 和 id
        columns = [k for k in cleaned if k in COLUMN_NAMES and k not in ("filename", "id")]
        if not columns:
            _log.info("[ReportDB.update_photo] 无有效列 返回 False")
            return False

        values = [cleaned[k] for k in columns]
        set_clause = ", ".join(f"{c} = ?" for c in columns)

        sql = f"UPDATE photos SET {set_clause} WHERE filename = ?"
        values.append(filename)

        with self._lock:
            cursor = self._conn.execute(sql, values)
            self._safe_commit()
            updated = cursor.rowcount > 0
        _log.info("[ReportDB.update_photo] 完成 filename=%r updated=%s", filename, updated)
        return updated

    def update_ratings_batch(self, updates: List[dict]) -> int:
        """
        批量更新评分及相关数据。

        用于重新评星场景（PostAdjustmentEngine）。

        Args:
            updates: 更新数据列表，每个字典必须包含 "filename" 键，
                     以及要更新的字段（如 rating, adj_sharpness, adj_topiq）

        Returns:
            成功更新的记录数
        """
        _log.info("[ReportDB.update_ratings_batch] updates_count=%s", len(updates))
        if not updates:
            return 0

        now = _now_iso()
        count = 0

        with self._lock:
            with self._conn:
                for upd in updates:
                    filename = upd.get("filename")
                    if not filename:
                        continue

                    cleaned = self._clean_data(upd)
                    cleaned["updated_at"] = now

                    columns = [k for k in cleaned if k in COLUMN_NAMES and k not in ("filename", "id")]
                    if not columns:
                        continue

                    values = [cleaned[k] for k in columns]
                    set_clause = ", ".join(f"{c} = ?" for c in columns)

                    sql = f"UPDATE photos SET {set_clause} WHERE filename = ?"
                    values.append(filename)

                    cursor = self._conn.execute(sql, values)
                    if cursor.rowcount > 0:
                        count += 1

        _log.info("[ReportDB.update_ratings_batch] 完成 count=%s", count)
        return count

    def clear_cache_paths(self) -> int:
        """清空缓存相关路径字段（临时 JPG、调试裁切、YOLO 调试图）。"""
        _log.info("[ReportDB.clear_cache_paths]")
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE photos SET debug_crop_path = NULL, temp_jpeg_path = NULL, yolo_debug_path = NULL"
            )
            self._safe_commit()
            n = cursor.rowcount
        _log.info("[ReportDB.clear_cache_paths] 完成 rowcount=%s", n)
        return n

    # ==========================================================================
    #  元数据操作
    # ==========================================================================

    def get_meta(self, key: str) -> Optional[str]:
        """获取元数据值。"""
        _log.debug("[ReportDB.get_meta] key=%r", key)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            result = row[0] if row else None
        _log.debug("[ReportDB.get_meta] 完成 key=%r has_value=%s", key, result is not None)
        return result

    def set_meta(self, key: str, value: str) -> None:
        """设置元数据值。"""
        _log.debug("[ReportDB.set_meta] key=%r", key)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value)
            )
            self._safe_commit()
        _log.debug("[ReportDB.set_meta] 完成")

    # ==========================================================================
    #  同步预留
    # ==========================================================================

    def get_updated_since(self, since: str) -> List[dict]:
        """
        获取指定时间之后更新的记录（增量同步用）。

        Args:
            since: ISO 8601 时间字符串

        Returns:
            更新记录列表
        """
        _log.debug("[ReportDB.get_updated_since] since=%r", since)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE updated_at > ? ORDER BY updated_at",
                (since,)
            )
            rows = [dict(row) for row in cursor.fetchall()]
        _log.debug("[ReportDB.get_updated_since] 完成 count=%s", len(rows))
        return rows

    # ==========================================================================
    #  连接管理
    # ==========================================================================

    def close(self) -> None:
        """关闭数据库连接。"""
        _log.info("[ReportDB.close] db_path=%r", getattr(self, "db_path", None))
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
        _log.info("[ReportDB.close] 完成")

    def __enter__(self):
        _log.debug("[ReportDB.__enter__]")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _log.debug("[ReportDB.__exit__] exc_type=%s", exc_type)
        self.close()
        return False

    # ==========================================================================
    #  内部方法
    # ==========================================================================

    def _safe_commit(self) -> None:
        """仅在存在活动事务时提交，兼容 autocommit 场景。"""
        _log.debug("[ReportDB._safe_commit]")
        if not self._conn:
            return
        try:
            if self._conn.in_transaction:
                self._conn.commit()
        except sqlite3.OperationalError as e:
            # 某些运行时在 autocommit 下会抛 "no transaction is active"
            if "no transaction is active" in str(e).lower():
                _log.debug("[ReportDB._safe_commit] 无活动事务 跳过")
                return
            raise

    @staticmethod
    def _clean_data(data: dict) -> dict:
        """
        清洗输入数据，处理 CSV 兼容格式转换。

        转换规则：
        - "yes"/"no" → 1/0（仅对 has_bird, is_flying 字段）
        - "-" 或空字符串 → None
        - 数值字符串 → 对应的 float/int
        """
        _log.debug("[ReportDB._clean_data] input_keys=%s", len(data) if data else 0)
        cleaned = {}
        for key, value in data.items():
            # 跳过非法列名
            if key not in COLUMN_NAMES:
                continue

            # 布尔/yes-no 字段（优先处理，"-"/None/空 → 0）
            if key in ("has_bird", "is_flying"):
                if value is None or value == "-" or value == "":
                    cleaned[key] = 0
                elif isinstance(value, str):
                    cleaned[key] = 1 if value.lower() in ("yes", "1", "true") else 0
                else:
                    cleaned[key] = 1 if value else 0
                continue

            # 处理 None 和占位符
            if value is None or value == "-" or value == "":
                cleaned[key] = None
                continue

            # 数值字段
            if key in ("confidence", "head_sharp", "left_eye", "right_eye",
                        "beak", "nima_score", "flight_conf", "focus_x",
                        "focus_y", "adj_sharpness", "adj_topiq",
                        # V2: 新增数值字段
                        "focal_length", "gps_latitude", "gps_longitude",
                        "gps_altitude", "birdid_confidence"):
                try:
                    cleaned[key] = float(value)
                except (ValueError, TypeError):
                    cleaned[key] = None
                continue

            # 整数字段
            if key in ("rating", "iso", "focal_length_35mm"):
                try:
                    cleaned[key] = int(float(value))
                except (ValueError, TypeError):
                    cleaned[key] = 0 if key == "rating" else None
                continue

            # 文本字段直接使用（包括 V2 新增的文本字段）
            # shutter_speed, aperture, camera_model, lens_model,
            # title, caption, city, state_province, country,
            # date_time_original, bird_species_cn, bird_species_en, exposure_status
            cleaned[key] = value

        _log.debug("[ReportDB._clean_data] 完成 output_keys=%s", len(cleaned))
        return cleaned


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _log.debug("[_now_iso] %s", s)
    return s
