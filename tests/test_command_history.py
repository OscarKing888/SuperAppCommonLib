# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from app_common.command_history import (
    BatchCommand,
    CommandHistory,
    CommandHistoryError,
)


class _AppendCommand:
    def __init__(self, values: list[str], item: str) -> None:
        self._values = values
        self._item = item

    def execute(self):
        self._values.append(self._item)
        return _RemoveLastCommand(self._values, self._item)


class _RemoveLastCommand:
    def __init__(self, values: list[str], expected: str) -> None:
        self._values = values
        self._expected = expected

    def execute(self):
        assert self._values, "cannot remove from empty list"
        removed = self._values.pop()
        assert removed == self._expected
        return _AppendCommand(self._values, removed)


class _CountingObserver:
    def __init__(self) -> None:
        self.count = 0

    def on_command_executed(self) -> None:
        self.count += 1


def test_single_add_undo_redo_roundtrip() -> None:
    values: list[str] = []
    history = CommandHistory()
    history.add_command(_AppendCommand(values, "a"))
    assert values == ["a"]
    assert history.can_undo and not history.can_redo

    history.undo()
    assert values == []
    assert not history.can_undo and history.can_redo

    history.redo()
    assert values == ["a"]
    assert history.can_undo and not history.can_redo


def test_multi_step_undo_redo_order() -> None:
    values: list[str] = []
    history = CommandHistory()
    history.add_command(_AppendCommand(values, "a"))
    history.add_command(_AppendCommand(values, "b"))
    history.add_command(_AppendCommand(values, "c"))
    assert values == ["a", "b", "c"]

    history.undo()
    assert values == ["a", "b"]
    history.undo()
    assert values == ["a"]
    history.redo()
    assert values == ["a", "b"]
    history.redo()
    assert values == ["a", "b", "c"]


def test_add_command_clears_redo() -> None:
    values: list[str] = []
    history = CommandHistory()
    history.add_command(_AppendCommand(values, "a"))
    history.add_command(_AppendCommand(values, "b"))
    history.undo()
    assert values == ["a"]
    assert history.can_redo

    history.add_command(_AppendCommand(values, "c"))
    assert values == ["a", "c"]
    assert not history.can_redo


def test_batch_undo_redo_lifo_order() -> None:
    values: list[str] = []
    history = CommandHistory()
    history.begin_batch()
    history.add_command(_AppendCommand(values, "a"))
    history.add_command(_AppendCommand(values, "b"))
    history.add_command(_AppendCommand(values, "c"))
    assert values == ["a", "b", "c"]
    assert history.is_batch_mode
    history.end_batch()
    assert not history.is_batch_mode
    assert history.can_undo and not history.can_redo

    history.undo()
    assert values == []
    assert history.can_redo

    history.redo()
    assert values == ["a", "b", "c"]


def test_undo_during_open_batch_ends_batch_first() -> None:
    values: list[str] = []
    history = CommandHistory()
    history.begin_batch()
    history.add_command(_AppendCommand(values, "a"))
    history.add_command(_AppendCommand(values, "b"))
    assert history.is_batch_mode

    history.undo()
    assert not history.is_batch_mode
    assert values == []
    assert history.can_redo


def test_observer_notifications_and_registration_errors() -> None:
    values: list[str] = []
    history = CommandHistory()
    observer = _CountingObserver()
    history.add_observer(observer)

    history.add_command(_AppendCommand(values, "a"))
    history.undo()
    history.redo()
    history.clear()
    assert observer.count == 4

    with pytest.raises(CommandHistoryError, match="already registered"):
        history.add_observer(observer)

    history.remove_observer(observer)
    with pytest.raises(CommandHistoryError, match="not registered"):
        history.remove_observer(observer)

    # Callable observers are also accepted.
    calls: list[int] = []
    history.add_observer(lambda: calls.append(1))
    history.add_command(_AppendCommand(values, "x"))
    assert calls == [1]


def test_execute_false_pushes_without_running() -> None:
    values: list[str] = []
    history = CommandHistory()
    history.add_command(_AppendCommand(values, "a"), execute=False)
    assert values == []
    assert history.can_undo

    history.undo()
    assert values == ["a"]
    assert history.can_redo


def test_empty_undo_redo_raise() -> None:
    history = CommandHistory()
    with pytest.raises(CommandHistoryError, match="nothing to undo"):
        history.undo()
    with pytest.raises(CommandHistoryError, match="nothing to redo"):
        history.redo()


def test_batch_command_execute_returns_inverse_batch() -> None:
    values: list[str] = []
    batch = BatchCommand(
        [
            _AppendCommand(values, "a"),
            _AppendCommand(values, "b"),
        ]
    )
    inverse = batch.execute()
    # LIFO: last member runs first.
    assert values == ["b", "a"]
    assert isinstance(inverse, BatchCommand)

    redo_batch = inverse.execute()
    assert values == []
    assert isinstance(redo_batch, BatchCommand)
    redo_batch.execute()
    assert values == ["b", "a"]
