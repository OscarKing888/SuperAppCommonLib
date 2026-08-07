# -*- coding: utf-8 -*-
"""Pythonic undo/redo command history (ICommandX-style inverse commands).

Usage::

    from app_common.command_history import Command, CommandHistory

    class AddOne:
        def __init__(self, values: list[int]) -> None:
            self._values = values

        def execute(self) -> "AddOne":
            self._values.append(1)
            return RemoveLast(self._values)

    history = CommandHistory()
    history.add_command(AddOne(values))
    history.undo()
    history.redo()

Semantics match Ogre ``ICommandX``: ``execute()`` applies the command and
returns the inverse command used for undo/redo.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable


class CommandHistoryError(Exception):
    """Raised for invalid command-history operations."""


@runtime_checkable
class Command(Protocol):
    """A command that applies itself and returns its inverse."""

    def execute(self) -> Command:
        """Apply this command; return the inverse command."""


@runtime_checkable
class CommandExecuteObserver(Protocol):
    """Notified when command history changes (add / undo / redo / clear / end_batch)."""

    def on_command_executed(self) -> None:
        """Called after a history-mutating operation."""


ObserverLike = CommandExecuteObserver | Callable[[], None]


class BatchCommand:
    """Composite command: execute members in LIFO order, return inverse batch."""

    def __init__(self, commands: Iterable[Command] | None = None) -> None:
        self._commands: deque[Command] = deque(commands or ())

    def __repr__(self) -> str:
        return f"{type(self).__name__}(count={len(self._commands)})"

    @property
    def commands(self) -> tuple[Command, ...]:
        return tuple(self._commands)

    def execute(self) -> BatchCommand:
        inverses: deque[Command] = deque()
        for cmd in reversed(self._commands):
            inverses.append(cmd.execute())
        return BatchCommand(inverses)


class CommandHistory:
    """Undo/redo history with optional batching and change observers."""

    def __init__(self) -> None:
        self._undo: deque[Command] = deque()
        self._redo: deque[Command] = deque()
        self._batch: deque[Command] = deque()
        self._is_batch_mode = False
        self._observers: list[ObserverLike] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def is_batch_mode(self) -> bool:
        return self._is_batch_mode

    def add_command(self, cmd: Command, execute: bool = True) -> None:
        if self._is_batch_mode:
            self._push(self._batch, cmd, execute=execute)
        else:
            self._push(self._undo, cmd, execute=execute)
        self._redo.clear()
        self._notify()

    def undo(self) -> None:
        if self._is_batch_mode:
            self.end_batch()
            self.undo()
            return
        if not self._undo:
            raise CommandHistoryError("nothing to undo")
        cmd = self._undo[-1]
        self._push(self._redo, cmd, execute=True)
        self._undo.pop()
        self._notify()

    def redo(self) -> None:
        if self._is_batch_mode:
            # Finish an open batch before redo (do not also undo).
            self.end_batch()
        if not self._redo:
            raise CommandHistoryError("nothing to redo")
        cmd = self._redo[-1]
        self._push(self._undo, cmd, execute=True)
        self._redo.pop()
        self._notify()

    def clear(self) -> None:
        self.end_batch()
        do_notify = bool(self._undo or self._redo)
        self._undo.clear()
        self._redo.clear()
        if do_notify:
            self._notify()

    def begin_batch(self) -> None:
        self._is_batch_mode = True

    def end_batch(self) -> None:
        if self._batch:
            batch = BatchCommand(self._batch)
            self._push(self._undo, batch, execute=False)
            self._redo.clear()
            self._batch.clear()
            self._is_batch_mode = False
            self._notify()
            return
        self._is_batch_mode = False

    def add_observer(self, observer: ObserverLike) -> None:
        if observer in self._observers:
            raise CommandHistoryError("observer already registered")
        self._observers.append(observer)

    def remove_observer(self, observer: ObserverLike) -> None:
        try:
            self._observers.remove(observer)
        except ValueError as exc:
            raise CommandHistoryError("observer not registered") from exc

    @staticmethod
    def _push(stack: deque[Command], cmd: Command, execute: bool = True) -> None:
        if execute:
            stack.append(cmd.execute())
        else:
            stack.append(cmd)

    def _notify(self) -> None:
        for observer in list(self._observers):
            on_executed = getattr(observer, "on_command_executed", None)
            if callable(on_executed):
                on_executed()
            else:
                observer()  # type: ignore[operator]


__all__ = [
    "BatchCommand",
    "Command",
    "CommandExecuteObserver",
    "CommandHistory",
    "CommandHistoryError",
]
