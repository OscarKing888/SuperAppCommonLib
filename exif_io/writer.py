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
from app_common.log import get_logger
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log

_log = get_logger("exif_io")

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


def _is_hidden_data_minor_copy_error(detail: str) -> bool:
    text = str(detail or "").lower()
    return ("error copying hidden data" in text) and ("minor" in text)


def _cleanup_exiftool_temp_output(path_norm: str) -> None:
    """
    exiftool 在失败时可能遗留 ``<file>_exiftool_tmp``，会阻塞同路径重试写入。
    仅在确认是可恢复的 minor hidden-data 错误时清理该临时文件。
    """
    temp_path = f"{path_norm}_exiftool_tmp"
    try:
        if os.path.isfile(temp_path):
            os.unlink(temp_path)
    except Exception:
        pass


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
    target_norm = path_norm
    if os.path.splitext(path_norm)[1].lower() == ".xmp":
        args = ["-overwrite_original", "-charset", "filename=UTF8", *assignments, path_norm]
    else:
        try:
            from app_common.exif_io.xmp_sidecar import find_xmp_sidecar
            found = find_xmp_sidecar(path_norm)
        except Exception:
            found = None
        target_norm = os.path.normpath(found or os.path.splitext(path_norm)[0] + ".xmp")
        args = [
            "-overwrite_original",
            "-charset",
            "filename=UTF8",
            *assignments,
            f"-o={target_norm}",
            path_norm,
        ]

    def _invoke(*, ignore_minor: bool) -> subprocess.CompletedProcess:
        fd, argfile_path = tempfile.mkstemp(suffix=".args", prefix="exiftool_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for a in args:
                    f.write(a + "\n")
            cmd = [exiftool_path]
            if ignore_minor:
                # 针对 DxO 导出的部分 TIFF，写入时可能出现 [minor] Error copying hidden data。
                # 加 -m 后会降级为 warning 并完成写入。
                cmd.append("-m")
            cmd.extend(["-@", argfile_path])
            return subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        finally:
            try:
                os.unlink(argfile_path)
            except OSError:
                pass

    cp = _invoke(ignore_minor=False)
    if cp.returncode == 0:
        return

    detail = _format_process_message(cp.stdout or "", cp.stderr or "")
    if _is_hidden_data_minor_copy_error(detail):
        _cleanup_exiftool_temp_output(target_norm)
        cp_retry = _invoke(ignore_minor=True)
        if cp_retry.returncode == 0:
            return
        retry_detail = _format_process_message(cp_retry.stdout or "", cp_retry.stderr or "")
        raise RuntimeError(f"ExifTool 写入失败：{retry_detail}")

    raise RuntimeError(f"ExifTool 写入失败：{detail}")


def write_exif_with_exiftool(path: str, ifd_name: str, tag_id: int, new_val: str, raw_value) -> None:
    """Compatibility wrapper: write one edited tag to the XMP sidecar."""
    tag_target = _get_exiftool_tag_target(ifd_name, tag_id)
    if not tag_target:
        raise RuntimeError(f"不支持写入该标签：{ifd_name}:{tag_id}")
    value = _convert_value_for_exiftool(new_val, raw_value)
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    if not PhotoMetaDataXMP().write(path, {tag_target: value}):
        raise RuntimeError("XMP sidecar write failed")


def write_exif_with_exiftool_by_key(path: str, tag_key: str, value: str) -> None:
    """Compatibility wrapper: write one edited tag to the XMP sidecar."""
    value = _ensure_utf8_for_exiftool(_sanitize(str(value or "")))
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    if not PhotoMetaDataXMP().write(path, {tag_key: value}):
        raise RuntimeError("XMP sidecar write failed")


def write_meta_with_exiftool(path: str, meta_tag_id: str, value: str) -> None:
    """Compatibility wrapper: write title/description to the XMP sidecar."""
    value = _ensure_utf8_for_exiftool(_sanitize(str(value or "")))
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    xmp = PhotoMetaDataXMP()
    if meta_tag_id == META_TITLE_TAG_ID:
        if not xmp.write_title(path, value):
            raise RuntimeError("XMP sidecar write failed")
        return
    if meta_tag_id == META_DESCRIPTION_TAG_ID:
        if not xmp.write_description(path, value):
            raise RuntimeError("XMP sidecar write failed")
        return
    raise RuntimeError(f"未知元数据标签：{meta_tag_id}")


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
    """Compatibility wrapper: write title/description to the XMP sidecar."""
    write_meta_with_exiftool(path, meta_tag_id, value)


# ─────────────────────────────────────────────────────────────────────────────
# 批量元数据读取（外部 API：自动处理 exiftool 优先 + XMP sidecar 回退）
# ─────────────────────────────────────────────────────────────────────────────

# 已读取过的元数据内存缓存，避免重复调用 exiftool / 读 XMP
# 键: os.path.normpath(path)，值: exiftool 风格平坦 dict（副本）
_METADATA_CACHE: dict[str, dict] = {}
_METADATA_CACHE_MAX = 20000  # 超过后按 FIFO 淘汰
_METADATA_CACHE_LOCK = threading.Lock()  # 多线程读写缓存时加锁

#: 文件列表视图基础标签（exiftool -G1 风格）。
#: 标题、对焦状态等依赖 XMP/侧载；与 _XMP_INDICATORS 一致，便于 exiftool 与 sidecar 合并。
_BROWSER_METADATA_TAGS: list[str] = [
    "-ExifIFD:DateTimeOriginal",
    "-EXIF:DateTimeOriginal",
    "-XMP-exif:DateTimeOriginal",
    "-DateTimeOriginal",
    "-ExifIFD:CreateDate",
    "-EXIF:CreateDate",
    "-XMP-xmp:CreateDate",
    "-CreateDate",
    "-DateTimeCreated",
    "-DateCreated",
    "-MediaCreateDate",
    "-XMP-dc:Title", "-XMP-dc:title",  # 标题（sidecar 常用小写 dc:title）
    "-XMP-dc:Description", "-XMP-dc:description",
    "-IFD0:ImageDescription", "-EXIF:ImageDescription",
    "-ExifIFD:UserComment", "-EXIF:UserComment",
    "-IFD0:XPComment",
    "-IPTC:Caption-Abstract",
    "-XMP-dc:Subject", "-XMP-dc:subject",
    "-IPTC:Keywords",
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

#: 焦点/横竖/尺寸批量预热所需标签。
#:
#: 设计约束：
#: 1. 文件列表加载元信息时，会顺手把“显示对焦点”所需的文件内 metadata 也读出来；
#: 2. 这里必须只放“可批量、可复用”的标签，避免把 GUI 预览里的焦点逻辑散落到调用方；
#: 3. 后续新增机型时，优先在 focus_calc 里扩展解析器，再把对应原始标签补到这里；
#: 4. 不要轻易删除这些标签，否则会退化成“只有选中过的文件才有焦点缓存”。
FOCUS_METADATA_TAGS: list[str] = [
    "-Make",
    "-Model",
    "-CameraModelName",
    "-Manufacturer",
    "-ExifImageWidth",
    "-ExifImageHeight",
    "-ImageWidth",
    "-ImageHeight",
    "-RawImageWidth",
    "-RawImageHeight",
    "-File:ImageWidth",
    "-File:ImageHeight",
    "-Composite:ImageSize",
    "-Orientation",
    "-Sony:CameraOrientation",
    "-SubjectArea",
    "-SubjectLocation",
    "-FocusLocation",
    "-FocusLocation2",
    "-AFPoint",
    "-FocusFrameSize",
    "-FocusFrameSize2",
    "-Composite:FocusX",
    "-Composite:FocusY",
    "-Composite:FocusW",
    "-Composite:FocusH",
    "-FocusX",
    "-FocusY",
    "-FocusW",
    "-FocusH",
    "-RegionInfo:RegionsRegionListRegionAreaX",
    "-RegionInfo:RegionsRegionListRegionAreaY",
    "-RegionInfo:RegionsRegionListRegionAreaW",
    "-RegionInfo:RegionsRegionListRegionAreaH",
    "-RegionAreaX",
    "-RegionAreaY",
    "-RegionAreaW",
    "-RegionAreaH",
    "-MakernoteTag0x2027",
    "-MakernoteTag0x204a",
]


def _merge_metadata_tag_groups(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for tag in group or []:
            text = str(tag or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


# 默认批量读取同时覆盖列表列元信息 + 焦点预热标签。
DEFAULT_METADATA_TAGS: list[str] = _merge_metadata_tag_groups(
    _BROWSER_METADATA_TAGS,
    FOCUS_METADATA_TAGS,
)


def _apply_browser_metadata_aliases(rec: dict) -> None:
    """
    补全文件浏览器依赖的规范键，兼容 exiftool/XMP sidecar 的不同命名。

    当前重点：
    - 对焦状态：XMP-photoshop:Country / Country-PrimaryLocationName -> XMP:Country
    - 标题：XMP-dc:title -> XMP-dc:Title
    - 注释：XMP-dc:description -> XMP-dc:Description
    - 标签：XMP-dc:subject -> XMP-dc:Subject
    """
    if not isinstance(rec, dict):
        return
    def has_value(value) -> bool:
        return value is not None and str(value).strip() != ""

    def first(*keys: str):
        for key in keys:
            value = rec.get(key)
            if has_value(value):
                return value
        return None

    country = first("XMP:Country", "XMP-photoshop:Country", "XMP-photoshop:Country-PrimaryLocationName")
    if country is not None and not has_value(rec.get("XMP:Country")):
        rec["XMP:Country"] = country

    title = first("XMP-dc:Title", "XMP-dc:title")
    if title is not None and not has_value(rec.get("XMP-dc:Title")):
        rec["XMP-dc:Title"] = title

    description = first("XMP-dc:Description", "XMP-dc:description")
    if description is not None and not has_value(rec.get("XMP-dc:Description")):
        rec["XMP-dc:Description"] = description
    if description is not None and not has_value(rec.get("XMP:Description")):
        rec["XMP:Description"] = description
    if description is not None and not has_value(rec.get("Description")):
        rec["Description"] = description

    subject = first("XMP-dc:Subject", "XMP-dc:subject")
    if subject is not None and not has_value(rec.get("XMP-dc:Subject")):
        rec["XMP-dc:Subject"] = subject

    rating_raw = first("XMP-xmp:Rating", "XMP:Rating", "XMP-xmp:rating", "rating")
    if rating_raw is not None:
        if not has_value(rec.get("XMP-xmp:Rating")):
            rec["XMP-xmp:Rating"] = rating_raw
        try:
            rec["rating"] = max(0, min(5, int(float(str(rating_raw)))))
        except Exception:
            rec["rating"] = 0

    pick_raw = first(
        "XMP-xmpDM:pick",
        "XMP-xmpDM:Pick",
        "XMP-xmp:Pick",
        "XMP-xmp:PickLabel",
        "XMP:Pick",
        "XMP:PickLabel",
        "pick",
    )
    if pick_raw is not None:
        if not has_value(rec.get("XMP-xmpDM:pick")):
            rec["XMP-xmpDM:pick"] = pick_raw
        try:
            text = str(pick_raw).strip().lower()
            if text in ("true", "yes"):
                rec["pick"] = 1
            elif text in ("false", "no", ""):
                rec["pick"] = 0
            else:
                rec["pick"] = max(-1, min(1, int(float(text))))
        except Exception:
            rec["pick"] = 0


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
        if group == "XMP-superpicky":
            rec[str(name)] = value
            rec[f"report.{name}"] = value
    _apply_browser_metadata_aliases(rec)
    return rec


def _batch_read_exiftool(et_path: str, paths: list, extra_tags: list | None) -> dict:
    """
    单次 exiftool 调用批量读取多个文件的元数据。
    exiftool 默认会自动合并同名 XMP sidecar，无需额外处理。
    返回 {os.path.normpath(path): raw_rec_dict}。
    """
    tag_args = [
        # -fast：跳过文件尾部的耗时扫描（不读 MakerNote 之后的 trailer/预览），
        # 实测对浏览器列字段与 Sony 对焦块结果零差异，但在机械/外置盘上显著降低单次读取耗时。
        # 注意不要升级为 -fast2：那会跳过 MakerNote，导致对焦块丢失。
        "-fast",
        "-j",
        "-G1",
        "-n",
        "-u",
        "-charset",
        "filename=UTF8",
        "-api",
        "largefilesupport=1",
    ]
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
    from app_common.exif_io.xmp_sidecar import find_xmp_sidecars, read_xmp_file  # 局部导入避免循环

    batch_t0 = perf_counter()
    result: dict = {}
    find_t0 = perf_counter()
    sidecars_by_norm = find_xmp_sidecars([os.path.normpath(path) for path in paths])
    find_ms = elapsed_ms(find_t0)
    parse_ms = 0.0
    flatten_ms = 0.0
    parsed_files = 0
    parsed_rows = 0
    slowest_parse_path = ""
    slowest_parse_ms = 0.0
    for path in paths:
        norm = os.path.normpath(path)
        try:
            xmp_path = sidecars_by_norm.get(norm)
            if xmp_path:
                parse_t0 = perf_counter()
                xmp_rows = read_xmp_file(xmp_path)
                file_parse_ms = elapsed_ms(parse_t0)
                parse_ms += file_parse_ms
                parsed_files += 1
                parsed_rows += len(xmp_rows)
                if file_parse_ms > slowest_parse_ms:
                    slowest_parse_ms = file_parse_ms
                    slowest_parse_path = xmp_path
            else:
                xmp_rows = []
            flatten_t0 = perf_counter()
            result[norm] = _xmp_rows_to_flat_dict(path, xmp_rows) if xmp_rows else {"SourceFile": path}
            flatten_ms += elapsed_ms(flatten_t0)
        except Exception:
            result[norm] = {"SourceFile": path}
    perf_log(
        _log,
        "[metadata.xmp_sidecar.read_batch] paths=%s found=%s parsed_files=%s parsed_rows=%s find_ms=%.1f parse_ms=%.1f flatten_ms=%.1f total_ms=%.1f slowest_parse_ms=%.1f slowest_parse=%r",
        len(paths or []),
        len(sidecars_by_norm),
        parsed_files,
        parsed_rows,
        find_ms,
        parse_ms,
        flatten_ms,
        elapsed_ms(batch_t0),
        slowest_parse_ms,
        slowest_parse_path,
    )
    return result


def _summarize_rec_for_log(rec: dict) -> str:
    """用于日志：从 flat_dict 提取标题、Rating、Pick 等简要信息。"""
    title = rec.get("XMP-dc:Title") or rec.get("XMP-dc:title") or rec.get("IFD0:XPTitle") or rec.get("IPTC:ObjectName") or ""
    rating = rec.get("XMP-xmp:Rating") or ""
    pick = rec.get("XMP-xmpDM:pick") or rec.get("XMP-xmpDM:Pick") or rec.get("XMP-xmp:Pick") or ""
    key_count = len([k for k in rec if isinstance(k, str) and ":" in k and k != "SourceFile"])
    return "键数=%s 标题=%r Rating=%s Pick=%s" % (key_count, (title[:40] + "..." if title and len(str(title)) > 40 else title), rating, pick)


def read_batch_metadata(paths: list, tags: list | None = None, use_cache: bool = True) -> dict:
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
        use_cache : 是否使用 path 级内存缓存；自定义 tag 集建议关闭，避免与默认缓存互串。

    返回：
        dict  : { os.path.normpath(path) : flat_dict }，flat_dict 为 exiftool -G1 风格。
    """
    if not paths:
        return {}

    probe_t0 = perf_counter()
    cache_t0 = perf_counter()
    result = {}
    uncached = []
    seen = set()
    if use_cache:
        with _METADATA_CACHE_LOCK:
            for p in paths:
                norm = os.path.normpath(p)
                if norm in seen:
                    continue
                seen.add(norm)
                if norm in _METADATA_CACHE:
                    result[norm] = _METADATA_CACHE[norm].copy()
                else:
                    uncached.append(p)
    else:
        for p in paths:
            norm = os.path.normpath(p)
            if norm in seen:
                continue
            seen.add(norm)
            uncached.append(p)
    cache_ms = elapsed_ms(cache_t0)

    cached_norms = set(result.keys())
    _log.info("[read_batch_metadata] 批量查询 paths=%s 缓存命中=%s 未命中=%s", len(paths), len(cached_norms), len(uncached))
    for norm in cached_norms:
        rec = result.get(norm)
        if rec:
            _log.debug("[read_batch_metadata] path=%r 来源=缓存 %s", norm, _summarize_rec_for_log(rec))

    if not uncached:
        perf_log(
            _log,
            "[metadata.exif_io.read_batch] paths=%s cached=%s uncached=0 cache_ms=%.1f total_ms=%.1f use_cache=%s tags=%s",
            len(paths),
            len(cached_norms),
            cache_ms,
            elapsed_ms(probe_t0),
            int(bool(use_cache)),
            len(tags or DEFAULT_METADATA_TAGS),
        )
        return result

    # 仅对未命中缓存的路径调用 exiftool / sidecar（不加锁，允许多线程并行 I/O）
    exiftool_t0 = perf_counter()
    et = get_exiftool_executable_path()
    if et:
        new_result = _batch_read_exiftool(et, uncached, tags)
        _log.debug("[read_batch_metadata] exiftool 返回 path数=%s", len(new_result))
    else:
        new_result = {}
        _log.debug("[read_batch_metadata] 无 exiftool，跳过文件内读取")

    exiftool_ms = elapsed_ms(exiftool_t0)

    exiftool_norms = set(new_result.keys())
    missing = [p for p in uncached if os.path.normpath(p) not in new_result]
    sidecar_fallback_ms = 0.0
    if missing:
        _log.debug("[read_batch_metadata] exiftool 未返回 改用 XMP sidecar paths=%s", [os.path.normpath(p) for p in missing])
        sidecar_t0 = perf_counter()
        sidecar_result = _batch_read_xmp_sidecar(missing)
        new_result.update(sidecar_result)
        sidecar_fallback_ms = elapsed_ms(sidecar_t0)

    # 用于判断「是否需合并 XMP sidecar」；含标题、对焦状态等，缺一不可，勿删。
    _XMP_INDICATORS = (
        "XMP-dc:Title", "XMP-dc:title",   # 标题
        "XMP-dc:Description", "XMP-dc:description",
        "XMP-dc:Subject", "XMP-dc:subject",
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
    sidecar_merge_ms = 0.0
    sidecar_merge_rows = 0
    if need_merge:
        _log.debug("[read_batch_metadata] 合并 XMP sidecar 补全 paths=%s", [os.path.normpath(p) for p in need_merge])
        sidecar_merge_t0 = perf_counter()
        sidecar_batch = _batch_read_xmp_sidecar(need_merge)
        for norm, xmp_rec in sidecar_batch.items():
            if not isinstance(xmp_rec, dict) or len(xmp_rec) <= 1:
                continue
            rec = new_result.get(norm)
            if not isinstance(rec, dict):
                continue
            for key, value in xmp_rec.items():
                if key == "SourceFile":
                    continue
                if value is not None and not rec.get(key):
                    rec[key] = value
            _apply_browser_metadata_aliases(rec)
            sidecar_merge_rows += 1
        sidecar_merge_ms = elapsed_ms(sidecar_merge_t0)

    need_merge_norms = {os.path.normpath(p) for p in need_merge}
    for norm, rec in new_result.items():
        result[norm] = rec
        if norm in exiftool_norms:
            source = "文件内+XMP合并" if norm in need_merge_norms else "文件内(exiftool)"
        else:
            source = "XMP"
        _log.debug("[read_batch_metadata] path=%r 来源=%s %s", norm, source, _summarize_rec_for_log(rec))

    # 写入缓存（副本），超出上限时 FIFO 淘汰（加锁保证多线程安全）
    cache_store_ms = 0.0
    if use_cache:
        cache_store_t0 = perf_counter()
        with _METADATA_CACHE_LOCK:
            while len(_METADATA_CACHE) + len(new_result) > _METADATA_CACHE_MAX:
                first = next(iter(_METADATA_CACHE))
                del _METADATA_CACHE[first]
            for norm, rec in new_result.items():
                _METADATA_CACHE[norm] = rec.copy()
        cache_store_ms = elapsed_ms(cache_store_t0)
    perf_log(
        _log,
        "[metadata.exif_io.read_batch] paths=%s cached=%s uncached=%s exiftool_hits=%s missing=%s sidecar_merge=%s result=%s cache_ms=%.1f exiftool_ms=%.1f sidecar_fallback_ms=%.1f sidecar_merge_ms=%.1f cache_store_ms=%.1f total_ms=%.1f use_cache=%s tags=%s",
        len(paths),
        len(cached_norms),
        len(uncached),
        len(exiftool_norms),
        len(missing),
        sidecar_merge_rows,
        len(result),
        cache_ms,
        exiftool_ms,
        sidecar_fallback_ms,
        sidecar_merge_ms,
        cache_store_ms,
        elapsed_ms(probe_t0),
        int(bool(use_cache)),
        len(tags or DEFAULT_METADATA_TAGS),
    )

    _log.debug("[read_batch_metadata] 批量查询完成 结果总数=%s", len(result))
    return result


def inject_metadata_cache(path: str, rec: dict) -> None:
    """
    将单条元数据写入全局 _METADATA_CACHE（供 report.db 等外部数据源注入，与 read_batch_metadata 行为一致）。
    """
    norm = os.path.normpath(path)
    with _METADATA_CACHE_LOCK:
        while len(_METADATA_CACHE) + 1 > _METADATA_CACHE_MAX:
            first = next(iter(_METADATA_CACHE))
            del _METADATA_CACHE[first]
        _METADATA_CACHE[norm] = rec.copy()


def invalidate_metadata_cache(paths) -> None:
    """Remove one or more paths from the batch metadata cache."""
    if paths is None:
        return
    if isinstance(paths, (str, os.PathLike)):
        iterable = [paths]
    else:
        iterable = list(paths)
    norms = {os.path.normpath(os.fspath(p)) for p in iterable if p}
    if not norms:
        return
    with _METADATA_CACHE_LOCK:
        for norm in norms:
            _METADATA_CACHE.pop(norm, None)
