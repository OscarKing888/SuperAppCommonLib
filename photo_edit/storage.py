"""Photo identity, strict XMP transactions and local recovery journals. No Qt."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from functools import wraps
import unicodedata
import uuid

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from .model import Recipe, canonical_json

EDIT_FIELD = "photo_edit_state"
BAKED_FIELD = "photo_edit_baked"


class EditConflict(RuntimeError):
    pass


def fingerprint(path: str | Path) -> dict:
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}


def photo_key(path: str | Path) -> str:
    # The same .xmp can accompany 001.ARW and 001.JPG; keep their full names.
    return unicodedata.normalize("NFC",Path(path).name).casefold()


def validate_fingerprint(value):
    if not isinstance(value,dict) or value.keys() != {"size","mtimeNs"}:
        raise ValueError("源文件指纹结构不正确")
    if any(isinstance(v,bool) or not isinstance(v,int) or v < 0 for v in value.values()):
        raise ValueError("源文件指纹数值不正确")
    return deepcopy(value)


@dataclass
class EditInput:
    mode: str = "original"
    path: str = ""
    fingerprint: dict | None = None
    recipe: Recipe = field(default_factory=Recipe)

    @property
    def key(self):
        if self.mode == "original":
            return "original"
        return "denoised:" + hashlib.sha256(canonical_json([self.path,self.fingerprint]).encode("utf-8")).hexdigest()[:24]

    def to_dict(self):
        return dict(mode=self.mode,path=self.path,fingerprint=deepcopy(self.fingerprint),recipe=self.recipe.to_dict())

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,dict) or value.keys() != {"mode","path","fingerprint","recipe"}:
            raise ValueError("编辑底图结构不正确")
        if value["mode"] not in ("original","denoised"):
            raise ValueError("未知编辑底图类型")
        path = value["path"]
        if not isinstance(path,str) or "\0" in path or len(path)>32768:
            raise ValueError("编辑底图路径不正确")
        fp = value["fingerprint"]
        if value["mode"] == "denoised":
            if not path:
                raise ValueError("降噪底图缺少实际文件")
            fp = validate_fingerprint(fp)
        elif path or fp is not None:
            raise ValueError("原图底图无需额外路径")
        return cls(value["mode"],path,fp,Recipe.from_dict(value["recipe"]))


@dataclass
class PhotoEditState:
    source: str
    fingerprint: dict
    inputs: dict[str,EditInput] = field(default_factory=lambda:{"original":EditInput()})
    active: str = "original"
    revision: str | None = None

    @property
    def current(self):
        return self.inputs[self.active]

    def to_dict(self):
        return dict(fingerprint=deepcopy(self.fingerprint),active=self.active,revision=self.revision,
                    inputs={key:value.to_dict() for key,value in self.inputs.items()})

    @classmethod
    def from_dict(cls, source: str, value):
        if not isinstance(value,dict) or value.keys() != {"fingerprint","active","revision","inputs"}:
            raise ValueError("照片编辑记录结构不正确")
        fp = validate_fingerprint(value["fingerprint"])
        if not isinstance(value["inputs"],dict) or not 1 <= len(value["inputs"]) <= 64:
            raise ValueError("编辑底图记录数量不正确")
        inputs = {key:EditInput.from_dict(item) for key,item in value["inputs"].items()}
        if any(key != item.key for key,item in inputs.items()) or "original" not in inputs:
            raise ValueError("编辑底图编号不匹配")
        if not isinstance(value["active"],str) or value["active"] not in inputs:
            raise ValueError("当前编辑底图不存在")
        rev = value["revision"]
        if rev is not None and (not isinstance(rev,str) or len(rev)>80):
            raise ValueError("编辑记录版本不正确")
        return cls(str(source),fp,inputs,value["active"],rev)

    def use_denoised(self, path: str | Path):
        path = Path(path).resolve()
        try:
            stored_path = os.path.relpath(path,Path(self.source).resolve().parent)
        except ValueError:  # Different Windows volume.
            stored_path = str(path)
        entry = EditInput("denoised",stored_path.replace("\\","/"),fingerprint(path))
        self.inputs.setdefault(entry.key,entry)
        self.active = entry.key


class RecipeStore:
    def __init__(self, metadata=None):
        self.metadata = metadata or PhotoMetaDataXMP()

    @staticmethod
    def _envelope(value):
        if value is None:
            return {"schema":1,"photos":{}}
        if not isinstance(value,dict) or value.keys() != {"schema","photos"} or value["schema"] != 1 or not isinstance(value["photos"],dict):
            raise ValueError("不支持的 XMP 编辑记录；原记录保留")
        return deepcopy(value)

    @staticmethod
    def _entry(envelope, source, source_fingerprint):
        key = photo_key(source)
        if key in envelope["photos"]:
            return key,envelope["photos"][key]
        # Finder/Explorer and the shared photo bundle operations preserve file
        # size/mtime but may rename a copied photo and its XMP. Adopt only one
        # unambiguous record of the same extension and exact fingerprint.
        candidates = [(name,value) for name,value in envelope["photos"].items()
            if isinstance(name,str) and isinstance(value,dict)
            and Path(name).suffix.casefold()==Path(source).suffix.casefold()
            and value.get("fingerprint")==source_fingerprint]
        if len(candidates)>1:
            raise EditConflict("XMP 中有多个匹配的旧文件记录，未猜测要使用的编辑配方")
        return candidates[0] if candidates else (key,None)

    def load(self, source: str | Path) -> PhotoEditState:
        source = str(Path(source).absolute())
        fp = fingerprint(source)
        envelope = self._envelope(self.metadata.read_json_field(source,EDIT_FIELD))
        _,value = self._entry(envelope,source,fp)
        if value is None:
            return PhotoEditState(source,fp)
        state = PhotoEditState.from_dict(source,value)
        if state.fingerprint != fp:
            raise EditConflict("源照片已变化，旧配方未自动套用；请保留旧 XMP 并重新建立编辑记录")
        return state

    def save(self, state: PhotoEditState) -> str:
        state = PhotoEditState.from_dict(state.source,state.to_dict())
        if fingerprint(state.source) != state.fingerprint:
            raise EditConflict("源照片在编辑期间已变化，未保存过期参数")
        revision = uuid.uuid4().hex
        def merge(value):
            envelope = self._envelope(value)
            old_key,old = self._entry(envelope,state.source,state.fingerprint)
            expected = old.get("revision") if isinstance(old,dict) else None
            if expected != state.revision:
                raise EditConflict("编辑参数已被其他会话修改；当前草稿已保留，请重新载入或复制参数")
            entry = state.to_dict()
            entry["revision"] = revision
            if old is not None and old_key!=photo_key(state.source):
                del envelope["photos"][old_key]
            envelope["photos"][photo_key(state.source)] = entry
            return envelope
        self.metadata.update_json_field(state.source,EDIT_FIELD,merge)
        return revision


_RECOVERY_LOCK = threading.RLock()


def _recovery_locked(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        with _RECOVERY_LOCK:
            return function(*args,**kwargs)
    return wrapped


class RecoveryStore:
    """Durable unsaved drafts in user state, never a metadata sidecar."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def path_for(self, source):
        key = os.path.normcase(os.path.abspath(source))
        return self.directory / (hashlib.sha256(key.encode("utf-8")).hexdigest()+".json")

    @_recovery_locked
    def save(self, state: PhotoEditState):
        self.directory.mkdir(parents=True,exist_ok=True)
        target = self.path_for(state.source)
        fd,name = tempfile.mkstemp(prefix=".draft-",suffix=".tmp",dir=self.directory)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as stream:
                stream.write(canonical_json(dict(source=state.source,state=state.to_dict())))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name,target)
        finally:
            Path(name).unlink(missing_ok=True)

    @_recovery_locked
    def load(self, source):
        path = self.path_for(source)
        if not path.exists():
            return None
        if path.stat().st_size > 2*1024*1024:
            raise ValueError("编辑恢复记录过大")
        data = json.loads(path.read_text("utf-8"))
        if os.path.normcase(os.path.abspath(data["source"])) != os.path.normcase(os.path.abspath(source)):
            raise ValueError("恢复记录来源不匹配")
        return PhotoEditState.from_dict(str(source),data["state"])

    @_recovery_locked
    def remove(self, source):
        self.path_for(source).unlink(missing_ok=True)

    @_recovery_locked
    def remove_if_matches(self, snapshot):
        current = self.load(snapshot.source)
        if current is not None and current.to_dict()==snapshot.to_dict():
            self.remove(snapshot.source)
