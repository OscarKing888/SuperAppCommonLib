# -*- coding: utf-8 -*-
"""Bounded undo/redo history using commands that return their inverse.

Commands return ``None`` when they make no change. A command that changes only
part of its target must raise ``PartialCommandError`` with an inverse for the
completed changes and a command for the remaining work. Other exceptions mean
the command made no change and can safely be retried.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable


class CommandHistoryError(Exception):
    """Raised for invalid command-history operations."""


@runtime_checkable
class Command(Protocol):
    """Apply an operation and return its inverse, or None for no change."""

    def execute(self) -> Command | None:
        ...


class PartialCommandError(CommandHistoryError):
    """Report completed changes separately from work that still needs a retry."""

    def __init__(
        self,
        message: str,
        *,
        inverse: Command | None = None,
        remaining: Command | None = None,
    ) -> None:
        super().__init__(message)
        self.inverse = inverse
        self.remaining = remaining


@runtime_checkable
class CommandExecuteObserver(Protocol):
    def on_command_executed(self) -> None:
        ...


ObserverLike = CommandExecuteObserver | Callable[[], None]


class BatchCommand:
    """Execute members in LIFO order, preserving completed work on failure."""

    def __init__(self, commands: Iterable[Command] | None = None) -> None:
        self._commands = tuple(commands or ())

    def __repr__(self) -> str:
        return f"{type(self).__name__}(count={len(self._commands)})"

    @property
    def commands(self) -> tuple[Command, ...]:
        return self._commands

    def execute(self) -> BatchCommand | None:
        inverses: list[Command] = []
        for index in range(len(self._commands) - 1, -1, -1):
            cmd = self._commands[index]
            try:
                inverse = cmd.execute()
            except PartialCommandError as exc:
                if exc.inverse is not None:
                    inverses.append(exc.inverse)
                remaining = list(self._commands[:index])
                if exc.remaining is not None:
                    remaining.append(exc.remaining)
                raise PartialCommandError(
                    str(exc),
                    inverse=BatchCommand(inverses) if inverses else None,
                    remaining=BatchCommand(remaining) if remaining else None,
                ) from exc
            except Exception as exc:
                raise PartialCommandError(
                    str(exc),
                    inverse=BatchCommand(inverses) if inverses else None,
                    remaining=BatchCommand(self._commands[:index + 1]),
                ) from exc
            if inverse is not None:
                inverses.append(inverse)
        return BatchCommand(inverses) if inverses else None


class CommandHistory:
    """Undo/redo history with batching and at most max_commands per stack.

    Partial undo/redo moves the completed subset to the other stack and keeps
    the unfinished subset at the current stack's top. The error is then raised
    to the caller for reporting; a subsequent undo/redo retries that subset.
    """

    def __init__(self, max_commands: int = 100) -> None:
        if not isinstance(max_commands, int) or max_commands < 1:
            raise ValueError("max_commands must be a positive integer")
        self._undo: deque[Command] = deque(maxlen=max_commands)
        self._redo: deque[Command] = deque(maxlen=max_commands)
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
        try:
            inverse = cmd.execute() if execute else cmd
        except PartialCommandError as exc:
            self._record_inverse(exc.inverse)
            raise
        self._record_inverse(inverse)

    def _record_inverse(self, inverse: Command | None) -> None:
        if inverse is None:
            return
        stack = self._batch if self._is_batch_mode else self._undo
        stack.append(inverse)
        self._redo.clear()
        self._notify()

    def undo(self) -> None:
        if self._is_batch_mode:
            self.end_batch()
        if not self._undo:
            raise CommandHistoryError("nothing to undo")
        self._move_command(self._undo, self._redo)

    def redo(self) -> None:
        if self._is_batch_mode:
            self.end_batch()
        if not self._redo:
            raise CommandHistoryError("nothing to redo")
        self._move_command(self._redo, self._undo)

    def _move_command(self, source: deque[Command], target: deque[Command]) -> None:
        cmd = source[-1]
        try:
            inverse = cmd.execute()
        except PartialCommandError as exc:
            if exc.remaining is None:
                source.pop()
            else:
                source[-1] = exc.remaining
            if exc.inverse is not None:
                target.append(exc.inverse)
            self._notify()
            raise
        source.pop()
        if inverse is not None:
            target.append(inverse)
        self._notify()

    def clear(self) -> None:
        do_notify = bool(self._undo or self._redo or self._batch)
        self._undo.clear()
        self._redo.clear()
        self._batch.clear()
        self._is_batch_mode = False
        if do_notify:
            self._notify()

    def begin_batch(self) -> None:
        self._is_batch_mode = True

    def end_batch(self) -> None:
        self._is_batch_mode = False
        if self._batch:
            self._undo.append(BatchCommand(self._batch))
            self._batch.clear()
            self._notify()

    def add_observer(self, observer: ObserverLike) -> None:
        if observer in self._observers:
            raise CommandHistoryError("observer already registered")
        self._observers.append(observer)

    def remove_observer(self, observer: ObserverLike) -> None:
        try:
            self._observers.remove(observer)
        except ValueError as exc:
            raise CommandHistoryError("observer not registered") from exc

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
    "PartialCommandError",
]
