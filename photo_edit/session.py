"""One photograph's in-memory editing session, shared by tabs and viewports."""
from __future__ import annotations

from dataclasses import dataclass, field
from .model import EditHistory, Recipe
from .storage import PhotoEditState


@dataclass
class EditSession:
    state: PhotoEditState
    serial: int = 0
    saved_serial: int = 0
    recovery_serial: int = 0
    error: str = ""
    conflict: bool = False
    histories: dict = field(default_factory=dict)

    @property
    def history(self):
        key = self.state.active
        if key not in self.histories:
            self.histories[key] = EditHistory(self.state.current.recipe)
        return self.histories[key]

    @property
    def dirty(self):
        return self.serial!=self.saved_serial

    def snapshot(self):
        return PhotoEditState.from_dict(self.state.source,self.state.to_dict())

    def edit(self, recipe: Recipe, *, commit=True):
        changed = self.history.update(recipe,commit=commit)
        self.state.current.recipe = self.history.current
        if changed:
            self.serial += 1
            if not self.conflict:
                self.error = ""
        return changed

    def finish_gesture(self):
        self.history.commit()

    def undo(self):
        self.finish_gesture()
        if self.history.can_undo:
            self.state.current.recipe = self.history.undo()
            self.serial += 1
            return True
        return False

    def redo(self):
        if self.history.can_redo:
            self.state.current.recipe = self.history.redo()
            self.serial += 1
            return True
        return False

    def select_input(self, key):
        if key not in self.state.inputs:
            raise ValueError("编辑底图不存在")
        if key!=self.state.active:
            self.finish_gesture()
            self.state.active = key
            self.serial += 1
            return True
        return False

    def add_denoised(self, path):
        self.finish_gesture()
        previous = self.state.active
        self.state.use_denoised(path)
        if self.state.active!=previous:
            self.serial += 1
            return True
        return False

    def saved(self, serial, revision):
        # A save may finish after another slider edit. Its revision belongs to
        # the next transaction, but it must not mark newer changes as saved.
        self.state.revision = revision
        self.saved_serial = serial
        self.error = ""
        self.conflict = False
