"""Portable content-addressed LUT resources. XMP holds references, never LUT pixels."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .native import backend

ASSET_DIRECTORY = ".superbird-assets"
MAX_ASSET_BYTES = 64 * 1024 * 1024


def _read(path):
    path = Path(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_ASSET_BYTES+1)
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError("LUT 文件不能超过 64 MiB")
    return data


def _publish(source: Path, reference: dict, data: bytes):
    target = asset_path(source,reference)
    if hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError("LUT 资源校验失败")
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if _read(target) != data:
            raise ValueError(f"已有 LUT 资源内容不匹配：{target}")
        return
    fd,name = tempfile.mkstemp(prefix=".lut-",suffix=".tmp",dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary,target)
        except FileExistsError:
            if _read(target) != data:
                raise ValueError(f"已有 LUT 资源内容不匹配：{target}")
        except OSError:
            # Filesystems without hard links: create exclusively, retaining no
            # partial resource after our own write fails.
            identity = None
            try:
                with target.open("xb") as output:
                    st = os.fstat(output.fileno())
                    identity = (st.st_dev,st.st_ino)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                if identity and target.exists():
                    st = target.stat()
                    if (st.st_dev,st.st_ino)==identity:
                        target.unlink()
                raise
    finally:
        temporary.unlink(missing_ok=True)


def asset_path(source: str | Path, reference: dict) -> Path:
    digest = reference.get("sha256","")
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("LUT 资源校验码不正确")
    relative = f"{ASSET_DIRECTORY}/{digest}.cube"
    if reference.get("path") != relative:
        raise ValueError("LUT 引用必须使用照片目录内的内容寻址资源")
    directory = Path(source).absolute().parent
    target = directory/relative
    if not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError("LUT 资源链接越出了照片目录")
    return target


def import_lut(source: str | Path, filename: str | Path) -> dict:
    if not Path(source).is_file():
        raise FileNotFoundError(source)
    parsed = json.loads(backend().load_lut_json(str(filename),_read(filename)))
    data = parsed["cube"].encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    reference = {"sha256":digest,"path":f"{ASSET_DIRECTORY}/{digest}.cube"}
    _publish(Path(source),reference,data)
    return reference


def load_asset(source: str | Path, reference: dict):
    path = asset_path(source,reference)
    data = _read(path)
    if hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError(f"LUT 资源已变化，未应用不匹配的效果：{path}")
    return path.name,data


def recipe_assets(recipe):
    found = {}
    for step in recipe.steps:
        if step.kind == "colorLookup" and step.params.get("asset"):
            ref = step.params["asset"]
            found[ref["sha256"]] = ref
    return list(found.values())


def copy_recipe_assets(source: str | Path, destination: str | Path, recipe):
    """Copy (never delete) resources shared by other photos in the source directory."""
    for reference in recipe_assets(recipe):
        _,data = load_asset(source,reference)
        _publish(Path(destination),reference,data)
