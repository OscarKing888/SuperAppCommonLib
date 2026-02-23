# -*- coding: utf-8 -*-
"""
EXIF 写入：exiftool 与 piexif。依赖模块内 exiftool_path 与 piexif。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading

import piexif

from app_common.exif_io.exiftool_path import get_exiftool_executable_path

# 与 main 中一致的常量（写入用）
META_TITLE_TAG_ID = "Title"
META_DESCRIPTION_TAG_ID = "Description"
EXIFTOOL_IFD_GROUP_MAP = {
    "0th": "IFD0",
    "Exif": "EXIF",
    "GPS": "GPS",
    "1st": "IFD1",
    "Interop": "InteropIFD",
}


def _sanitize(s: str) -> str:
    if not s:
        return s
    result = []
    for c in s:
        code = ord(c)
        if code == 0:
            result.append(" ")
        elif code < 32 and c not in "\t\n\r":
            result.append(" ")
        else:
            result.append(c)
    return "".join(result).strip()


def _tuple_as_bytes(value: tuple) -> bytes | None:
    if not value:
        return None
    try:
        if all(isinstance(x, int) and 0 <= x <= 255 for x in value):
            return bytes(value)
    except (TypeError, ValueError):
        pass
    return None


def _format_process_message(stdout: str, stderr: str) -> str:
    out = _sanitize((stdout or "").strip())
    err = _sanitize((stderr or "").strip())
    if err and out:
        return f"{err}\n{out}"
    return err or out or "未返回详细信息。"


def _normalize_rational_input(s: str) -> tuple[int, int]:
    txt = str(s or "").strip()
    if "(" in txt and ")" in txt and "/" in txt:
        txt = txt.split("(", 1)[0].strip()
    if "/" in txt:
        a, _, b = txt.partition("/")
        num = int(a.strip())
        den = int(b.strip()) if b.strip() else 1
        if den == 0:
            raise ValueError("分母不能为 0。")
        return num, den
    from fractions import Fraction
    f = float(txt)
    fr = Fraction(f).limit_denominator(10000)
    if fr.denominator == 0:
        raise ValueError("分母不能为 0。")
    return fr.numerator, fr.denominator


def _ensure_utf8_for_exiftool(s: str) -> str:
    if not s:
        return s
    return s.encode("utf-8", errors="replace").decode("utf-8")


def _get_exiftool_tag_target(ifd_name: str, tag_id: int) -> str | None:
    info = piexif.TAGS.get(ifd_name, {}).get(tag_id)
    if not isinstance(info, dict):
        return None
    raw_name = _sanitize(str(info.get("name", "")).strip())
    if not raw_name:
        return None
    group = EXIFTOOL_IFD_GROUP_MAP.get(ifd_name)
    if not group:
        return raw_name
    return f"{group}:{raw_name}"


def _convert_value_for_exiftool(new_val: str, raw_value) -> str:
    txt = _sanitize(str(new_val or "").strip())
    txt = _ensure_utf8_for_exiftool(txt)
    if raw_value is None:
        return txt
    if isinstance(raw_value, int):
        return str(int(txt))
    if isinstance(raw_value, float):
        return str(float(txt))
    if isinstance(raw_value, tuple):
        if len(raw_value) == 2 and isinstance(raw_value[0], int) and isinstance(raw_value[1], int):
            num, den = _normalize_rational_input(txt)
            return f"{num}/{den}"
        b = _tuple_as_bytes(raw_value)
        if b is not None:
            return txt
        if all(isinstance(x, int) for x in raw_value):
            parts = txt.replace(",", " ").split()
            if not parts:
                raise ValueError("请输入整数数组。")
            return " ".join(str(int(x)) for x in parts)
        return txt
    return txt


def run_exiftool_json(path: str) -> list[dict]:
    """用 exiftool -j -G1 读取文件元数据，返回 JSON 数组；失败返回 []。"""
    exiftool_path = get_exiftool_executable_path()
    if not exiftool_path:
        return []
    path_norm = os.path.normpath(path)
    use_argfile = sys.platform.startswith("win") and any(ord(c) > 127 for c in path_norm)
    try:
        if use_argfile:
            fd, argfile_path = tempfile.mkstemp(suffix=".args", prefix="exiftool_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(path_norm + "\n")
                cmd = [exiftool_path, "-charset", "filename=UTF8", "-j", "-G1", "-@", argfile_path]
                cp = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
            finally:
                try:
                    os.unlink(argfile_path)
                except OSError:
                    pass
        else:
            cmd = [exiftool_path, "-j", "-G1", path_norm]
            cp = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if cp.returncode != 0 or not (cp.stdout or "").strip():
            return []
        out = json.loads(cp.stdout)
        return out if isinstance(out, list) else [out] if isinstance(out, dict) else []
    except Exception:
        return []


def run_exiftool_assignments(path: str, assignments: list[str]) -> None:
    """按给定赋值参数调用 exiftool。"""
    exiftool_path = get_exiftool_executable_path()
    if not exiftool_path:
        raise RuntimeError(
            "未找到 exiftool 可执行文件，请检查 exif_io 内 exiftools_mac/exiftools_win 是否完整，"
            "或将 exiftool 加入系统 PATH。"
        )
    path_norm = os.path.normpath(path)
    args = ["-overwrite_original", "-charset", "filename=UTF8", *assignments, path_norm]
    fd, argfile_path = tempfile.mkstemp(suffix=".args", prefix="exiftool_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for a in args:
                f.write(a + "\n")
        cmd = [exiftool_path, "-@", argfile_path]
        cp = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        try:
            os.unlink(argfile_path)
        except OSError:
            pass
    if cp.returncode != 0:
        detail = _format_process_message(cp.stdout or "", cp.stderr or "")
        raise RuntimeError(f"ExifTool 写入失败：{detail}")


def write_exif_with_exiftool(path: str, ifd_name: str, tag_id: int, new_val: str, raw_value) -> None:
    """使用 exiftool 写入单个标签。"""
    tag_target = _get_exiftool_tag_target(ifd_name, tag_id)
    if not tag_target:
        raise RuntimeError(f"不支持写入该标签：{ifd_name}:{tag_id}")
    value = _convert_value_for_exiftool(new_val, raw_value)
    run_exiftool_assignments(path, [f"-{tag_target}={value}"])


def write_exif_with_exiftool_by_key(path: str, tag_key: str, value: str) -> None:
    """使用 exiftool 按 Group:Tag 键写入单个标签。"""
    value = _ensure_utf8_for_exiftool(_sanitize(str(value or "")))
    run_exiftool_assignments(path, [f"-{tag_key}={value}"])


def write_meta_with_exiftool(path: str, meta_tag_id: str, value: str) -> None:
    """使用 exiftool 写入标题/描述元数据。"""
    value = _ensure_utf8_for_exiftool(_sanitize(str(value or "")))
    if meta_tag_id == META_TITLE_TAG_ID:
        assignments = [
            f"-XMP-dc:Title={value}",
            f"-IFD0:XPTitle={value}",
            f"-IFD0:DocumentName={value}",
        ]
    elif meta_tag_id == META_DESCRIPTION_TAG_ID:
        assignments = [
            f"-XMP-dc:Description={value}",
            f"-IFD0:XPComment={value}",
            f"-IFD0:ImageDescription={value}",
            f"-EXIF:UserComment={value}",
        ]
    else:
        raise RuntimeError(f"未知元数据标签：{meta_tag_id}")
    run_exiftool_assignments(path, assignments)


def _encode_xp_text_value(text: str) -> bytes:
    if not text:
        return b""
    return text.encode("utf-16-le") + b"\x00\x00"


def _set_or_clear_exif_tag(ifd_data: dict, tag_id: int, value) -> None:
    if not isinstance(ifd_data, dict):
        return
    if value is None:
        ifd_data.pop(tag_id, None)
    else:
        ifd_data[tag_id] = value


def write_meta_with_piexif(path: str, meta_tag_id: str, value: str) -> None:
    """使用 piexif 写入标题/描述元数据。"""
    data = piexif.load(path)
    ifd0 = data.get("0th")
    if not isinstance(ifd0, dict):
        ifd0 = {}
        data["0th"] = ifd0
    exif_ifd = data.get("Exif")
    if not isinstance(exif_ifd, dict):
        exif_ifd = {}
        data["Exif"] = exif_ifd
    if meta_tag_id == META_TITLE_TAG_ID:
        _set_or_clear_exif_tag(ifd0, 40091, _encode_xp_text_value(value) if value else None)
        _set_or_clear_exif_tag(ifd0, 269, value.encode("utf-8") if value else None)
    elif meta_tag_id == META_DESCRIPTION_TAG_ID:
        _set_or_clear_exif_tag(ifd0, 40092, _encode_xp_text_value(value) if value else None)
        _set_or_clear_exif_tag(ifd0, 270, value.encode("utf-8") if value else None)
        _set_or_clear_exif_tag(
            exif_ifd,
            37510,
            (b"ASCII\x00\x00\x00" + value.encode("utf-8")) if value else None,
        )
    else:
        raise RuntimeError(f"未知元数据标签：{meta_tag_id}")
    try:
        exif_bytes = piexif.dump(data)
        piexif.insert(exif_bytes, path)
    except Exception as e:
        if type(e).__name__ == "InvalidImageDataError" and get_exiftool_executable_path():
            write_meta_with_exiftool(path, meta_tag_id, value)
        else:
            raise


# ─────────────────────────────────────────────────────────────────────────────
# 批量元数据读取（外部 API：自动处理 exiftool 优先 + XMP sidecar 回退）
# ─────────────────────────────────────────────────────────────────────────────

# 已读取过的元数据内存缓存，避免重复调用 exiftool / 读 XMP
# 键: os.path.normpath(path)，值: exiftool 风格平坦 dict（副本）
_METADATA_CACHE: dict[str, dict] = {}
_METADATA_CACHE_MAX = 20000  # 超过后按 FIFO 淘汰
_METADATA_CACHE_LOCK = threading.Lock()  # 多线程读写缓存时加锁

#: 文件列表视图默认读取的标签列表（exiftool -G1 风格）
# 标题、对焦状态等依赖 XMP/侧载；与 _XMP_INDICATORS 一致，便于 exiftool 与 sidecar 合并。
DEFAULT_METADATA_TAGS: list[str] = [
    "-XMP-dc:Title", "-XMP-dc:title",  # 标题（sidecar 常用小写 dc:title）
    "-XMP-xmp:Label",
    "-XMP-xmp:Rating",
    "-XMP-xmpDM:pick",        # 实际 XMP 结构 <xmpDM:pick>1</xmpDM:pick>（Dynamic Media）
    "-XMP-xmp:Pick", "-XMP-xmp:PickLabel",
    "-XMP:Pick", "-XMP:PickLabel",
    "-XMP:City", "-XMP:State", "-XMP:Country",  # 锐度/美学/对焦（复用 LR 城市/省/国家字段）
    "-XMP-photoshop:City",
    "-XMP-photoshop:State",
    "-XMP-photoshop:Country",  # 对焦状态（部分流程直接写在 photoshop:Country）
    "-XMP-photoshop:Country-PrimaryLocationName",
    "-IPTC:ObjectName",
    "-IPTC:City",
    "-IPTC:Province-State",
    "-IPTC:Country-PrimaryLocationName",
    "-IFD0:XPTitle",
]


def _apply_browser_metadata_aliases(rec: dict) -> None:
    """
    补全文件浏览器依赖的规范键，兼容 exiftool/XMP sidecar 的不同命名。

    当前重点：
    - 对焦状态：XMP-photoshop:Country / Country-PrimaryLocationName -> XMP:Country
    - 标题：XMP-dc:title -> XMP-dc:Title
    """
    if not isinstance(rec, dict):
        return
    if rec.get("XMP-photoshop:Country") and not rec.get("XMP:Country"):
        rec["XMP:Country"] = rec["XMP-photoshop:Country"]
    if rec.get("XMP-photoshop:Country-PrimaryLocationName") and not rec.get("XMP:Country"):
        rec["XMP:Country"] = rec["XMP-photoshop:Country-PrimaryLocationName"]
    if rec.get("XMP-dc:title") and not rec.get("XMP-dc:Title"):
        rec["XMP-dc:Title"] = rec["XMP-dc:title"]


def _xmp_rows_to_flat_dict(path: str, xmp_rows: list) -> dict:
    """
    将 read_xmp_sidecar 返回的 [(group, name, value), ...] 转换为
    exiftool -G1 风格的平坦字典 {"XMP-dc:Title": "...", ...}。
    并补全文件列表「标题」「对焦状态」所需的规范键，便于浏览器统一读取。
    """
    rec: dict = {"SourceFile": path}
    for group, name, value in xmp_rows:
        key = f"{group}:{name}"
        rec[key] = value
    _apply_browser_metadata_aliases(rec)
    return rec


def _batch_read_exiftool(et_path: str, paths: list, extra_tags: list | None) -> dict:
    """
    单次 exiftool 调用批量读取多个文件的元数据。
    exiftool 默认会自动合并同名 XMP sidecar，无需额外处理。
    返回 {os.path.normpath(path): raw_rec_dict}。
    """
    tag_args = ["-j", "-G1", "-charset", "filename=UTF8"]
    tag_args += (extra_tags if extra_tags is not None else DEFAULT_METADATA_TAGS)
    all_args = tag_args + [os.path.normpath(p) for p in paths]

    fd, argfile = tempfile.mkstemp(suffix=".args", prefix="et_bm_")
    result: dict = {}
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for a in all_args:
                f.write(a + "\n")
        fd = -1
        cp = subprocess.run(
            [et_path, "-@", argfile],
            check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        if cp.returncode == 0 and (cp.stdout or "").strip():
            records = json.loads(cp.stdout)
            paths_norm = {os.path.normpath(p) for p in paths}
            for rec in records:
                src = os.path.normpath(rec.get("SourceFile", ""))
                if src in paths_norm:
                    _apply_browser_metadata_aliases(rec)
                    result[src] = rec
    except Exception:
        pass
    finally:
        try:
            if fd >= 0:
                os.close(fd)
            os.unlink(argfile)
        except Exception:
            pass
    return result


def _batch_read_xmp_sidecar(paths: list) -> dict:
    """
    逐文件读取 XMP sidecar，转换为 exiftool 风格平坦字典。
    返回 {os.path.normpath(path): flat_dict}（无 sidecar 的文件也有空条目）。
    """
    from app_common.exif_io.xmp_sidecar import read_xmp_sidecar  # 局部导入避免循环

    result: dict = {}
    for path in paths:
        norm = os.path.normpath(path)
        try:
            xmp_rows = read_xmp_sidecar(path)
            result[norm] = _xmp_rows_to_flat_dict(path, xmp_rows) if xmp_rows else {"SourceFile": path}
        except Exception:
            result[norm] = {"SourceFile": path}
    return result


def read_batch_metadata(paths: list, tags: list | None = None) -> dict:
    """
    批量读取多个图像文件的元数据（API 透明，调用方无需感知数据来源）。

    已读取过的文件会缓存在内存中（normpath -> 元数据副本），下次同一路径直接返回缓存，
    缓存条目上限为 _METADATA_CACHE_MAX，超出时按 FIFO 淘汰。

    读取策略（对未命中缓存的路径）：
    1. exiftool 可用 → 单次批量调用；
    2. 完全缺失的文件 → 读 XMP sidecar；
    3. 有记录但 XMP/IPTC 全空（如 ARW）→ 合并 sidecar 字段。

    参数：
        paths : 图像文件路径列表。
        tags  : 要提取的 exiftool 标签；None 表示使用 DEFAULT_METADATA_TAGS。

    返回：
        dict  : { os.path.normpath(path) : flat_dict }，flat_dict 为 exiftool -G1 风格。
    """
    if not paths:
        return {}

    result = {}
    uncached = []
    with _METADATA_CACHE_LOCK:
        seen = set()
        for p in paths:
            norm = os.path.normpath(p)
            if norm in seen:
                continue
            seen.add(norm)
            if norm in _METADATA_CACHE:
                result[norm] = _METADATA_CACHE[norm].copy()
            else:
                uncached.append(p)

    if not uncached:
        return result

    # 仅对未命中缓存的路径调用 exiftool / sidecar（不加锁，允许多线程并行 I/O）
    et = get_exiftool_executable_path()
    if et:
        new_result = _batch_read_exiftool(et, uncached, tags)
    else:
        new_result = {}

    missing = [p for p in uncached if os.path.normpath(p) not in new_result]
    if missing:
        sidecar_result = _batch_read_xmp_sidecar(missing)
        new_result.update(sidecar_result)

    # 用于判断「是否需合并 XMP sidecar」；含标题、对焦状态等，缺一不可，勿删。
    _XMP_INDICATORS = (
        "XMP-dc:Title", "XMP-dc:title",   # 标题
        "XMP-xmp:Label", "XMP-xmp:Rating",
        "XMP-xmpDM:pick", "XMP-xmpDM:Pick",
        "XMP-xmp:Pick", "XMP-xmp:PickLabel", "XMP:Pick", "XMP:PickLabel",
        "XMP:City", "XMP:State", "XMP:Country",                        # 锐度/美学/对焦状态
        "XMP-photoshop:City", "XMP-photoshop:State",
        "XMP-photoshop:Country",                                       # 对焦状态（photoshop:Country）
        "XMP-photoshop:Country-PrimaryLocationName",                   # 对焦状态（侧载常用）
        "IPTC:ObjectName", "IPTC:City", "IFD0:XPTitle",
    )
    need_merge = [
        p for p in uncached
        if os.path.normpath(p) in new_result
        and not any(new_result[os.path.normpath(p)].get(f) for f in _XMP_INDICATORS)
    ]
    if need_merge:
        from app_common.exif_io.xmp_sidecar import read_xmp_sidecar
        for path in need_merge:
            norm = os.path.normpath(path)
            try:
                xmp_rows = read_xmp_sidecar(path)
            except Exception:
                continue
            if not xmp_rows:
                continue
            rec = new_result[norm]
            for group, name, value in xmp_rows:
                key = f"{group}:{name}"
                if not rec.get(key):
                    rec[key] = value
            # 保证文件列表「标题」「对焦状态」能从 sidecar 显示：补全浏览器使用的键名
            _apply_browser_metadata_aliases(rec)

    for norm, rec in new_result.items():
        result[norm] = rec

    # 写入缓存（副本），超出上限时 FIFO 淘汰（加锁保证多线程安全）
    with _METADATA_CACHE_LOCK:
        while len(_METADATA_CACHE) + len(new_result) > _METADATA_CACHE_MAX:
            first = next(iter(_METADATA_CACHE))
            del _METADATA_CACHE[first]
        for norm, rec in new_result.items():
            _METADATA_CACHE[norm] = rec.copy()

    return result
