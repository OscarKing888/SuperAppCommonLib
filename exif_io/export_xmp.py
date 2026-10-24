"""成片 XMP 合并：侧车优先，保留未知命名空间及完整 RDF 属性。"""
from __future__ import annotations

from pathlib import Path
import re
from xml.dom import Node, minidom
from xml.etree import ElementTree as ET
from xml.parsers.expat import ExpatError

from app_common.exif_io.xmp_sidecar import _photo_descriptions

_RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
_XMLNS = 'http://www.w3.org/2000/xmlns/'
_XML = 'http://www.w3.org/XML/1998/namespace'
_EMPTY = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
          b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
          b'</x:xmpmeta>')


def _descriptions(document):
    rdfs = document.getElementsByTagNameNS(_RDF, 'RDF')
    if len(rdfs) != 1:
        raise ValueError('XMP 必须包含一个 RDF 数据集。')
    rdf = rdfs[0]
    return rdf, [node for node in rdf.childNodes
                 if node.nodeType == Node.ELEMENT_NODE
                 and node.namespaceURI == _RDF and node.localName == 'Description']


def _photo_indices(payload: bytes, source: Path) -> set[int]:
    # 与共享 XMP 读取器使用同一照片资源识别规则（路径、空 about、UUID）。
    root = ET.fromstring(payload)
    rdf = root if root.tag == f'{{{_RDF}}}RDF' else root.find(f'.//{{{_RDF}}}RDF')
    if rdf is None:
        raise ValueError('XMP 缺少 RDF 数据集。')
    selected = _photo_descriptions(root, str(source))
    return {index for index, node in enumerate(rdf.findall(f'{{{_RDF}}}Description'))
            if node in selected}


def _properties(description):
    for attribute in description.attributes.values():
        if attribute.namespaceURI not in {_RDF, _XMLNS, _XML, None}:
            yield attribute
    for child in description.childNodes:
        if child.nodeType == Node.ELEMENT_NODE:
            yield child


def _import_description(document, description):
    result = document.importNode(description, deep=True)
    # 属性值可能引用命名空间前缀；DOM 保留原前缀，并补齐祖先上的声明。
    inherited = {}
    node = description
    while node.nodeType == Node.ELEMENT_NODE:
        for attribute in node.attributes.values():
            if attribute.namespaceURI in {_XMLNS, _XML}:
                inherited.setdefault(attribute.name, attribute)
        node = node.parentNode
    for name, attribute in inherited.items():
        result.setAttributeNS(attribute.namespaceURI, name, attribute.value)
    return result


def _bind_to_export(description):
    about = description.getAttributeNodeNS(_RDF, 'about')
    if about is not None:
        # 保持原前缀；minidom.setAttributeNS 改已有属性前缀会破坏其内部索引。
        about.value = ''
    else:
        description.setAttributeNS(_XMLNS, 'xmlns:rdf', _RDF)
        description.setAttributeNS(_RDF, 'rdf:about', '')


def merge_export_xmp(embedded: bytes, sidecar: bytes, *, source: Path) -> bytes:
    """整属性覆盖（包括数组/多语言/空值），不经 ExifTool 已知标签白名单。"""
    embedded = embedded.strip() or _EMPTY
    sidecar = sidecar.strip() or _EMPTY
    try:
        with minidom.parseString(embedded) as base, minidom.parseString(sidecar) as edits:
            base_rdf, base_descriptions = _descriptions(base)
            _, edit_descriptions = _descriptions(edits)
            base_photos = [base_descriptions[i] for i in sorted(_photo_indices(embedded, source))]
            edit_indices = _photo_indices(sidecar, source)
            keys = {(prop.namespaceURI, prop.localName)
                    for index in edit_indices for prop in _properties(edit_descriptions[index])}
            for description in base_photos:
                for prop in list(_properties(description)):
                    if (prop.namespaceURI, prop.localName) in keys:
                        if prop.nodeType == Node.ATTRIBUTE_NODE:
                            description.removeAttributeNode(prop)
                        else:
                            description.removeChild(prop)
                _bind_to_export(description)
            for index, description in enumerate(edit_descriptions):
                copied = _import_description(base, description)
                if index in edit_indices:
                    _bind_to_export(copied)
                base_rdf.appendChild(copied)
            # 结果是独立 XML；主 JPEG 包指向扩展段的标识不能带到侧车中。
            for description in _descriptions(base)[1]:
                for prop in list(_properties(description)):
                    if (prop.namespaceURI, prop.localName) == ('http://ns.adobe.com/xmp/note/', 'HasExtendedXMP'):
                        if prop.nodeType == Node.ATTRIBUTE_NODE:
                            description.removeAttributeNode(prop)
                        else:
                            description.removeChild(prop)
            # 输出标准 XMP packet，保留命名空间和完整属性，不复制原包空白填充。
            return (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
                    + base.documentElement.toxml(encoding='utf-8')
                    + b'\n<?xpacket end="w"?>')
    except (ET.ParseError, ValueError, ExpatError) as exc:
        raise ValueError(f'无法合并原图 XMP：{source}：{exc}') from exc


def read_export_xmp(source: Path) -> bytes:
    """只读原图内嵌 XMP（含扩展段），以同名侧车覆盖，返回独立侧车内容。"""
    from .exiftool_path import get_exiftool_executable_path
    from .exiftool_runner import run_exiftool_once
    from .xmp_sidecar import find_same_stem_xmp_sidecar

    executable = get_exiftool_executable_path()
    if not executable:
        raise RuntimeError('无法使用 ExifTool，不能完整读取原图 XMP。')
    args = ['-charset', 'filename=UTF8', '-a', '-b', '-XMP', str(source)]
    if any('\n' in arg or '\r' in arg or '\0' in arg for arg in args):
        raise ValueError('ExifTool 文件路径不能包含换行或 NUL 字符。')
    # 二进制输出不能混用 stay_open 协议；UTF-8 参数流兼容 Windows 中文路径。
    result = run_exiftool_once([executable, '-@', '-'],
        input=('\n'.join(args) + '\n').encode('utf-8'), capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f'导出 XMP 读取失败：{result.stderr.decode("utf-8", errors="replace")}')
    merged = _EMPTY
    if result.stdout.strip():
        # -a 输出主包与扩展包；用临时 XML 容器解析，不能只保留第一个包。
        packets = re.sub(br'<\?xml\s+[^?]*\?>', b'', result.stdout)
        try:
            with minidom.parseString(b'<packets>' + packets + b'</packets>') as document:
                for node in document.documentElement.childNodes:
                    if node.nodeType == Node.ELEMENT_NODE:
                        merged = merge_export_xmp(merged, node.toxml(encoding='utf-8'), source=source)
        except ExpatError as exc:
            raise ValueError(f'无法读取原图 XMP：{source}：{exc}') from exc
    sidecar = find_same_stem_xmp_sidecar(str(source))
    if not result.stdout.strip() and sidecar is None:
        return _EMPTY.replace(b'/>', b'><rdf:Description rdf:about=""/></rdf:RDF>')
    edits = Path(sidecar).read_bytes() if sidecar else b''
    if sidecar and not edits.strip():
        raise ValueError(f'原图 XMP 侧车为空：{sidecar}')
    return merge_export_xmp(merged, edits, source=source)


def export_xmp_geometry(payload: bytes, *, source: Path, size: tuple[int, int]) -> bytes:
    """校正成片侧车几何；保留未知属性、命名空间前缀及无关 RDF 资源。"""
    tiff, exif = 'http://ns.adobe.com/tiff/1.0/', 'http://ns.adobe.com/exif/1.0/'
    values = {
        (tiff, 'Orientation'): ('tiff', 1),
        (tiff, 'ImageWidth'): ('tiff', size[0]),
        (tiff, 'ImageLength'): ('tiff', size[1]),
        (exif, 'PixelXDimension'): ('exif', size[0]),
        (exif, 'PixelYDimension'): ('exif', size[1]),
    }
    removed = set(values) | {
        ('http://ns.adobe.com/xmp/note/', 'HasExtendedXMP'),
        ('http://ns.adobe.com/xap/1.0/', 'Thumbnails'),
    }
    with minidom.parseString(payload) as document:
        rdf, descriptions = _descriptions(document)
        photos = [descriptions[i] for i in sorted(_photo_indices(payload, source))]
        if not photos:
            description = document.createElementNS(_RDF, 'rdf:Description')
            description.setAttributeNS(_XMLNS, 'xmlns:rdf', _RDF)
            rdf.appendChild(description)
            photos = [description]
        for description in photos:
            _bind_to_export(description)
            for prop in list(_properties(description)):
                if (prop.namespaceURI, prop.localName) in removed:
                    if prop.nodeType == Node.ATTRIBUTE_NODE:
                        description.removeAttributeNode(prop)
                    else:
                        description.removeChild(prop)
        for (uri, key), (prefix, value) in values.items():
            element = document.createElementNS(uri, f'{prefix}:{key}')
            element.setAttributeNS(_XMLNS, f'xmlns:{prefix}', uri)
            element.appendChild(document.createTextNode(str(value)))
            photos[0].appendChild(element)
        return document.toxml(encoding='utf-8')
