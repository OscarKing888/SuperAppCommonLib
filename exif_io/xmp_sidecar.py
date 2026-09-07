# -*- coding: utf-8 -*-
"""
读取图像旁边的 XMP sidecar 文件中的元数据。
XMP 是基于 XML 的元数据格式，sidecar 文件与图像同名，扩展名为 .xmp。
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
from uuid import UUID

from app_common.log import get_logger
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log

_log = get_logger("xmp_sidecar")

# 常见 XMP 命名空间 URL → 短前缀映射（含无尾斜杠变体，便于属性匹配）
_NS_PREFIXES: dict[str, str] = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0": "xmp",   # 无尾斜杠（部分 XMP 属性写法）
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://ns.adobe.com/lightroom/1.0/": "lr",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.adobe.com/xap/1.0/bj/": "xmpBJ",
    "http://ns.adobe.com/xap/1.0/t/pg/": "xmpTPg",
    "http://ns.adobe.com/xap/1.0/g/img/": "xmpGImg",
    "http://ns.adobe.com/xmp/1.0/DynamicMedia/": "xmpDM",  # xmpDM:pick = Pick 旗标（1=精选）
    "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
    "http://ns.useplus.org/ldf/xmp/1.0/": "plus",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://purl.org/dc/terms/": "dcterms",
    "https://superbirdtools.local/xmp/superpicky/1.0/": "superpicky",
}

_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

_XMP_SUFFIX_CANDIDATES = (".xmp", ".XMP", ".Xmp")
_DERIVED_EXPORT_DIR_NAMES = {
    "dxo",
    "dxo pureraw",
    "pureraw",
    "exports",
    "export",
}
_DERIVED_STEM_SPLIT_MARKERS = (
    "-DxO_",
    "_DxO_",
)


def _candidate_sidecar_stems(image_path: Path) -> list[str]:
    """生成 sidecar 匹配用的 stem 候选（含 DxO 导出文件名回溯原始 stem）。"""
    stem = str(image_path.stem or "").strip()
    if not stem:
        return []
    stems: list[str] = [stem]
    for marker in _DERIVED_STEM_SPLIT_MARKERS:
        pos = stem.find(marker)
        if pos <= 0:
            continue
        base = stem[:pos].rstrip(" _-")
        if base and base not in stems:
            stems.append(base)
    return stems


def _candidate_sidecar_dirs(image_path: Path, stems: list[str]) -> list[Path]:
    """生成 sidecar 查找目录候选：默认当前目录，必要时回查上一级导出源目录。"""
    dirs: list[Path] = [image_path.parent]
    parent = image_path.parent
    stem_changed = any(stem != image_path.stem for stem in stems)
    parent_name = str(parent.name or "").strip().lower()
    if stem_changed or parent_name in _DERIVED_EXPORT_DIR_NAMES:
        upper = parent.parent
        if upper != parent and upper not in dirs:
            dirs.append(upper)
    return dirs


def _find_xmp_by_stem_in_dir(dir_path: Path, stem: str) -> str | None:
    if not stem:
        return None
    for suffix in _XMP_SUFFIX_CANDIDATES:
        candidate = dir_path / f"{stem}{suffix}"
        try:
            if candidate.exists() and candidate.is_file():
                return str(candidate)
        except Exception:
            continue
    target_lower = f"{stem.lower()}.xmp"
    try:
        for entry in os.scandir(str(dir_path)):
            name = entry.name
            if name.lower() == target_lower and entry.is_file():
                return entry.path
    except (PermissionError, OSError):
        return None
    return None


def find_xmp_sidecar(image_path: str) -> str | None:
    """
    查找图片旁边的 XMP sidecar 文件，不区分大小写。
    返回 sidecar 文件路径，找不到返回 None。
    """
    p = Path(image_path)
    stems = _candidate_sidecar_stems(p)
    if not stems:
        return None
    for dir_path in _candidate_sidecar_dirs(p, stems):
        for stem in stems:
            found = _find_xmp_by_stem_in_dir(dir_path, stem)
            if found:
                return found
    return None


def find_same_stem_xmp_sidecar(image_path: str) -> str | None:
    """Return only a same-directory, same-stem XMP sidecar.

    This strict resolver is for file lifecycle operations such as copy, move,
    rename and trash.  Unlike :func:`find_xmp_sidecar`, it never searches a
    parent directory and never maps a derived export stem back to its source.
    The ``.xmp`` suffix and filename comparison are case-insensitive so it also
    works with sidecars created on Windows and moved to macOS.
    """
    if not image_path:
        return None
    path = Path(os.path.normpath(image_path))
    if path.suffix.lower() == ".xmp" or not path.stem:
        return None
    return _find_xmp_by_stem_in_dir(path.parent, path.stem)


def _build_xmp_dir_index(dir_path: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    try:
        for entry in os.scandir(str(dir_path)):
            name = entry.name
            if not name.lower().endswith(".xmp"):
                continue
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            index.setdefault(name.lower(), entry.path)
    except (PermissionError, OSError):
        pass
    return index


def find_xmp_sidecars(image_paths: list[str]) -> dict[str, str]:
    """
    批量查找 XMP sidecar，避免同一目录被每张 RAW 重复 scandir。

    返回 {norm_image_path: xmp_path}，找不到的图片不包含在结果中。
    """
    result: dict[str, str] = {}
    dir_indexes: dict[str, dict[str, str]] = {}
    batch_t0 = perf_counter()
    index_ms = 0.0
    lookup_ms = 0.0
    slowest_dir = ""
    slowest_dir_ms = 0.0
    candidate_dirs = 0
    for image_path in image_paths or []:
        lookup_t0 = perf_counter()
        norm = os.path.normpath(image_path)
        p = Path(image_path)
        stems = _candidate_sidecar_stems(p)
        if not stems:
            lookup_ms += elapsed_ms(lookup_t0)
            continue
        for dir_path in _candidate_sidecar_dirs(p, stems):
            dir_key = os.path.normpath(str(dir_path))
            index = dir_indexes.get(dir_key)
            if index is None:
                candidate_dirs += 1
                index_t0 = perf_counter()
                index = _build_xmp_dir_index(dir_path)
                dir_ms = elapsed_ms(index_t0)
                index_ms += dir_ms
                if dir_ms > slowest_dir_ms:
                    slowest_dir_ms = dir_ms
                    slowest_dir = dir_key
                dir_indexes[dir_key] = index
            for stem in stems:
                found = index.get(f"{stem.lower()}.xmp")
                if found:
                    result[norm] = found
                    break
            if norm in result:
                break
        lookup_ms += elapsed_ms(lookup_t0)
    perf_log(
        _log,
        "[metadata.xmp_sidecar.find_batch] paths=%s found=%s dirs=%s index_ms=%.1f lookup_ms=%.1f total_ms=%.1f slowest_dir_ms=%.1f slowest_dir=%r",
        len(image_paths or []),
        len(result),
        candidate_dirs,
        index_ms,
        lookup_ms,
        elapsed_ms(batch_t0),
        slowest_dir_ms,
        slowest_dir,
    )
    return result


def _ns_to_prefix(ns_url: str) -> str:
    """将命名空间 URL 转为短前缀，优先用已知映射。"""
    if ns_url in _NS_PREFIXES:
        return _NS_PREFIXES[ns_url]
    # 从 URL 末尾提取有意义的部分
    stripped = ns_url.rstrip("/").rstrip("#")
    parts = stripped.split("/")
    for part in reversed(parts):
        part = part.strip()
        if part and not part.startswith("http") and len(part) <= 30:
            return part
    return "xmp"


def _about_file_path(about: str, base_dir: str) -> str | None:
    """Resolve a file-valued rdf:about without touching the filesystem."""
    try:
        parts = urlsplit(about)
    except ValueError:
        return None
    if parts.query or parts.fragment:
        return None
    if parts.scheme.lower() == "file":
        path = url2pathname(parts.path)
        if parts.netloc and parts.netloc.lower() != "localhost":
            path = "//" + parts.netloc + path
    elif parts.scheme and not (len(parts.scheme) == 1 and about[1:2] == ":"):
        return None
    else:
        path = unquote(about)
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    return os.path.normcase(os.path.abspath(path))


def _photo_descriptions(
    root: ET.Element,
    image_path: str | None = None,
    *,
    sidecar_path: str | None = None,
) -> list[ET.Element]:
    """Select photo properties, excluding nested and unrelated RDF resources.

    Empty/omitted about is conventional photo XMP. Explicit file references
    must identify the photo. Read-only derived-export fallback may also use
    the original photo identified by the sidecar's directory and stem.
    """
    rdf_tag = f"{{{_RDF_NS}}}RDF"
    rdf = root if root.tag == rdf_tag else root.find(f".//{rdf_tag}")
    if rdf is None:
        return []
    descriptions = rdf.findall(f"{{{_RDF_NS}}}Description")
    if image_path is None:
        return descriptions
    image_norm = os.path.normcase(os.path.abspath(image_path))
    base_dir = os.path.dirname(image_norm)
    fallback_stem = None
    if sidecar_path:
        sidecar_norm = os.path.normcase(os.path.abspath(sidecar_path))
        if os.path.splitext(sidecar_norm)[0] != os.path.splitext(image_norm)[0]:
            fallback_stem = os.path.splitext(sidecar_norm)[0]
    result = []
    for desc in descriptions:
        if f"{{{_RDF_NS}}}nodeID" in desc.attrib:
            continue
        about = desc.get(f"{{{_RDF_NS}}}about", "")
        if not about or _about_file_path(about, base_dir) == image_norm:
            result.append(desc)
        elif fallback_stem:
            referenced = _about_file_path(about, os.path.dirname(fallback_stem))
            if referenced and os.path.splitext(referenced)[0] == fallback_stem:
                result.append(desc)
    if result:
        return result
    # Some photo writers identify the primary resource by UUID, not a file
    # path. Accept an unambiguous UUID subject and all its split descriptions.
    # Never merge a second UUID into an already identified photo resource.
    uuid_descriptions: dict[UUID, list[ET.Element]] = {}
    for desc in descriptions:
        if f"{{{_RDF_NS}}}nodeID" in desc.attrib:
            continue
        about = desc.get(f"{{{_RDF_NS}}}about", "")
        prefix = "urn:uuid:" if about.lower().startswith("urn:uuid:") else "uuid:"
        if not about.lower().startswith(prefix):
            continue
        try:
            resource_id = UUID(about[len(prefix):])
        except ValueError:
            continue
        uuid_descriptions.setdefault(resource_id, []).append(desc)
    if len(uuid_descriptions) == 1:
        return next(iter(uuid_descriptions.values()))
    return result


def _extract_text_value(element) -> str | None:
    """
    从 XMP 元素中提取文本值。
    支持 rdf:Alt / rdf:Seq / rdf:Bag 容器及直接文本、属性形式。
    """
    rdf_ns = _RDF_NS
    # 先尝试 rdf:Alt / rdf:Seq / rdf:Bag 容器
    for container_tag in ("Alt", "Seq", "Bag"):
        container = element.find(f"{{{rdf_ns}}}{container_tag}")
        if container is not None:
            items = container.findall(f"{{{rdf_ns}}}li")
            if items:
                if container_tag == "Alt":
                    for item in items:
                        if item.get(_XML_LANG) == "x-default":
                            # An explicit empty default suppresses old translations.
                            return (item.text or "").strip()
                texts = [(item.text or "").strip() for item in items if (item.text or "").strip()]
                return "; ".join(texts) if texts else None

    # 直接文本
    if element.text and element.text.strip():
        return element.text.strip()

    # 嵌套 rdf:Description（结构化值取属性）
    desc = element.find(f"{{{rdf_ns}}}Description")
    if desc is not None:
        parts = []
        for k, v in desc.attrib.items():
            if "}" in k:
                local = k.split("}", 1)[1]
                if local != "about" and v.strip():
                    parts.append(f"{local}={v.strip()}")
        if parts:
            return "; ".join(parts)

    return None


def read_xmp_file(xmp_path: str, *, image_path: str | None = None) -> list[tuple[str, str, str]]:
    """读取一个 XMP 文件，解析所有元数据标签。"""
    try:
        tree = ET.parse(xmp_path)
        root = tree.getroot()
    except Exception:
        return []

    results: list[tuple[str, str, str]] = []
    rdf_ns = _RDF_NS

    # 图片读取只取该资源的顶层属性，避免嵌套/其它资源覆盖照片字段。
    for desc in _photo_descriptions(root, image_path, sidecar_path=xmp_path):
        # 1. 处理内联属性形式（如 exif:FNumber="28/10"）
        for attr_key, attr_val in desc.attrib.items():
            if not attr_key.startswith("{"):
                continue
            ns_url, local = attr_key[1:].split("}", 1)
            if ns_url == rdf_ns:
                continue  # 跳过 rdf:about 等 RDF 内部属性
            val = (attr_val or "").strip()
            if not val:
                continue
            prefix = _ns_to_prefix(ns_url)
            group = f"XMP-{prefix}"
            results.append((group, local, val))

        # 2. 处理子元素形式（如 <dc:title><rdf:Alt>...</rdf:Alt></dc:title>）
        for child in desc:
            tag = child.tag
            if not tag.startswith("{"):
                continue
            ns_url, local = tag[1:].split("}", 1)
            if ns_url == rdf_ns:
                continue
            value = _extract_text_value(child)
            if value is not None:
                prefix = _ns_to_prefix(ns_url)
                group = f"XMP-{prefix}"
                results.append((group, local, value))

    return results


def read_xmp_sidecar(image_path: str) -> list[tuple[str, str, str]]:
    """
    读取图片旁的 XMP sidecar 文件，解析所有元数据标签。

    返回 [(group, tag_name, value), ...] 列表：
    - group  : 命名空间前缀，格式为 "XMP-{prefix}"（如 "XMP-dc"），
               与 exiftool 输出风格一致
    - tag_name: XML 局部名称（如 "Title"、"Rating"、"FNumber"）
    - value  : 字符串形式的值

    找不到 sidecar 文件或解析失败时返回空列表。
    """
    xmp_path = find_xmp_sidecar(image_path)
    if not xmp_path:
        return []
    return read_xmp_file(xmp_path, image_path=image_path)
