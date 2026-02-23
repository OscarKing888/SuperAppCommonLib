# -*- coding: utf-8 -*-
"""
读取图像旁边的 XMP sidecar 文件中的元数据。
XMP 是基于 XML 的元数据格式，sidecar 文件与图像同名，扩展名为 .xmp。
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

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
}

_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

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

    try:
        tree = ET.parse(xmp_path)
        root = tree.getroot()
    except Exception:
        return []

    results: list[tuple[str, str, str]] = []
    rdf_ns = _RDF_NS

    # 遍历所有 rdf:Description 节点（XMP 元数据的载体）
    for desc in root.iter(f"{{{rdf_ns}}}Description"):
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
            if value:
                prefix = _ns_to_prefix(ns_url)
                group = f"XMP-{prefix}"
                results.append((group, local, value))

    return results
