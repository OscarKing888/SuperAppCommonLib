"""Serializable recipes and bounded, gesture-aware undo; independent of Qt/native code."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import uuid

from .catalog import camera_defaults, normalize_camera, normalize_adjustment

SCHEMA_VERSION = 1
ALGORITHM_VERSION = "photocraft-369704ac-bridge1"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass
class AdjustmentStep:
    kind: str
    params: dict = field(default_factory=dict)
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self):
        return dict(id=self.id, kind=self.kind, enabled=self.enabled, params=deepcopy(self.params))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,dict) or value.keys()-{"id","kind","enabled","params"}:
            raise ValueError("调色步骤结构不正确")
        kind = value.get("kind")
        if not isinstance(kind,str):
            raise ValueError("缺少调色步骤类型")
        ident = value.get("id")
        if not isinstance(ident,str) or not ident or len(ident)>80:
            raise ValueError("调色步骤编号不正确")
        if not isinstance(value.get("enabled",True),bool):
            raise ValueError("调色步骤启用状态不正确")
        return cls(kind,normalize_adjustment(kind,value.get("params",{})),value.get("enabled",True),ident)


@dataclass
class Recipe:
    camera: dict = field(default_factory=camera_defaults)
    steps: list[AdjustmentStep] = field(default_factory=list)
    camera_enabled: bool = True
    algorithm: str = ALGORITHM_VERSION

    def to_dict(self):
        return dict(schema=SCHEMA_VERSION,algorithm=self.algorithm,cameraEnabled=self.camera_enabled,
                    cameraRaw=deepcopy(self.camera),steps=[s.to_dict() for s in self.steps])

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,dict) or value.keys()-{"schema","algorithm","cameraEnabled","cameraRaw","steps"}:
            raise ValueError("编辑配方结构不正确")
        if value.get("schema") != SCHEMA_VERSION:
            raise ValueError("不支持的编辑配方版本；原记录保留")
        if value.get("algorithm") != ALGORITHM_VERSION:
            raise ValueError("编辑配方的算法版本不兼容；原记录保留")
        if not isinstance(value.get("cameraEnabled",True),bool):
            raise ValueError("Camera RAW 启用状态不正确")
        steps = value.get("steps",[])
        if not isinstance(steps,list) or len(steps)>64:
            raise ValueError("最多支持 64 个调色步骤")
        steps = [AdjustmentStep.from_dict(s) for s in steps]
        if len({s.id for s in steps}) != len(steps):
            raise ValueError("调色步骤编号重复")
        return cls(normalize_camera(value.get("cameraRaw",{})),steps,value.get("cameraEnabled",True),value["algorithm"])

    def validated(self):
        return self.from_dict(self.to_dict())

    def signature(self):
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def is_identity(self):
        return (not self.camera_enabled or self.camera == camera_defaults()) and not any(s.enabled for s in self.steps)


class EditHistory:
    """One gesture = one history entry. No-op edits preserve the redo stack."""

    def __init__(self, recipe: Recipe | None = None, *, limit=100):
        self.current = (recipe or Recipe()).validated()
        self.limit = max(1,int(limit))
        self._undo: list[Recipe] = []
        self._redo: list[Recipe] = []
        self._gesture: Recipe | None = None

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def begin(self):
        if self._gesture is None:
            self._gesture = deepcopy(self.current)

    def update(self, recipe: Recipe, *, commit=True) -> bool:
        recipe = recipe.validated()
        changed = recipe != self.current
        if self._gesture is None:
            self.begin()
        self.current = recipe
        if commit:
            self.commit()
        return changed

    def commit(self):
        before,self._gesture = self._gesture,None
        if before is not None and before != self.current:
            self._undo.append(before)
            del self._undo[:-self.limit]
            self._redo.clear()

    def undo(self):
        self.commit()
        if self._undo:
            self._redo.append(deepcopy(self.current))
            self.current = self._undo.pop()
        return deepcopy(self.current)

    def redo(self):
        self.commit()
        if self._redo:
            self._undo.append(deepcopy(self.current))
            self.current = self._redo.pop()
        return deepcopy(self.current)
