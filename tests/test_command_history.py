# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from app_common.command_history import (
    BatchCommand,
    CommandHistory,
    CommandHistoryError,
    PartialCommandError,
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


class _SetValuesCommand:
    def __init__(self, values, desired, failures, attempts=None) -> None:
        self.values = values
        self.desired = dict(desired)
        self.failures = failures
        self.attempts = attempts if attempts is not None else []

    def execute(self):
        inverse = {}
        remaining = {}
        for key, desired in self.desired.items():
            self.attempts.append(key)
            if key in self.failures:
                remaining[key] = desired
            elif self.values[key] != desired:
                inverse[key] = self.values[key]
                self.values[key] = desired
        undo = self._with_values(inverse) if inverse else None
        if remaining:
            raise PartialCommandError(
                "write failed", inverse=undo, remaining=self._with_values(remaining)
            )
        return undo

    def _with_values(self, desired):
        return _SetValuesCommand(self.values, desired, self.failures, self.attempts)


def test_noop_and_fully_failed_add_preserve_redo_and_observers() -> None:
    values = {"a": False}
    failures = set()
    history = CommandHistory()
    calls = []
    history.add_observer(lambda: calls.append(True))
    history.add_command(_SetValuesCommand(values, {"a": True}, failures))
    history.undo()
    assert len(calls) == 2

    history.add_command(_SetValuesCommand(values, {"a": False}, failures))
    failures.add("a")
    with pytest.raises(PartialCommandError):
        history.add_command(_SetValuesCommand(values, {"a": True}, failures))
    assert len(calls) == 2
    assert not history.can_undo and history.can_redo
    failures.clear()
    history.redo()
    assert values == {"a": True}


def test_partial_add_records_only_completed_changes() -> None:
    values = dict.fromkeys("abc", False)
    failures = {"b"}
    history = CommandHistory()
    with pytest.raises(PartialCommandError):
        history.add_command(_SetValuesCommand(values, dict.fromkeys("abc", True), failures))
    assert values == {"a": True, "b": False, "c": True}
    assert history.can_undo

    history.undo()
    assert values == dict.fromkeys("abc", False)
    assert not history.can_undo
    history.redo()
    assert values == {"a": True, "b": False, "c": True}


@pytest.mark.parametrize("direction", ["undo", "redo"])
def test_partial_undo_redo_keeps_failed_subset_for_retry(direction) -> None:
    values = dict.fromkeys("abc", False)
    failures = set()
    attempts = []
    history = CommandHistory()
    history.add_command(_SetValuesCommand(values, dict.fromkeys("abc", True), failures, attempts))
    if direction == "redo":
        history.undo()
    failures.add("b")
    attempts.clear()
    operation = getattr(history, direction)
    with pytest.raises(PartialCommandError):
        operation()
    desired = direction == "redo"
    assert values == {"a": desired, "b": not desired, "c": desired}
    assert history.can_undo and history.can_redo
    assert attempts == ["a", "b", "c"]

    attempts.clear()
    with pytest.raises(PartialCommandError):
        operation()
    assert attempts == ["b"]
    assert values == {"a": desired, "b": not desired, "c": desired}
    failures.clear()
    operation()
    assert values == dict.fromkeys("abc", desired)

    reverse = history.undo if direction == "redo" else history.redo
    reverse()
    assert values == {"a": desired, "b": not desired, "c": desired}
    reverse()
    assert values == dict.fromkeys("abc", not desired)


def test_plain_undo_exception_keeps_original_command_for_retry() -> None:
    values = ["a"]
    gate = {"fail": True}

    class _FailOnce:
        def execute(self):
            if gate["fail"]:
                raise OSError("temporarily unavailable")
            return _RemoveLastCommand(values, "a").execute()

    history = CommandHistory()
    history.add_command(_FailOnce(), execute=False)
    with pytest.raises(OSError, match="temporarily unavailable"):
        history.undo()
    assert values == ["a"]
    assert history.can_undo and not history.can_redo
    gate["fail"] = False
    history.undo()
    assert values == []
    history.redo()
    assert values == ["a"]


def test_noop_undo_and_empty_batch_do_not_create_inverse_entries() -> None:
    values = {"a": False}
    history = CommandHistory()
    noop = _SetValuesCommand(values, {"a": False}, set())
    history.add_command(noop, execute=False)
    history.undo()
    assert not history.can_undo and not history.can_redo
    assert BatchCommand([noop]).execute() is None
    history.add_command(BatchCommand())
    assert not history.can_undo


def test_noop_batch_preserves_redo() -> None:
    values = {"a": False}
    history = CommandHistory()
    history.add_command(_SetValuesCommand(values, {"a": True}, set()))
    history.undo()
    history.begin_batch()
    history.add_command(_SetValuesCommand(values, {"a": False}, set()))
    history.end_batch()
    assert not history.can_undo and history.can_redo


def test_default_capacity_keeps_most_recent_100_commands() -> None:
    values = []
    history = CommandHistory()
    for index in range(105):
        history.add_command(_AppendCommand(values, str(index)))
    for _ in range(100):
        history.undo()
    assert values == [str(index) for index in range(5)]
    assert not history.can_undo
    for _ in range(100):
        history.redo()
    assert values == [str(index) for index in range(105)]
    assert not history.can_redo


@pytest.mark.parametrize("capacity", [0, -1, 1.5])
def test_invalid_history_capacity_is_rejected(capacity) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        CommandHistory(max_commands=capacity)


def test_custom_capacity_retains_newest_commands() -> None:
    values = []
    history = CommandHistory(max_commands=2)
    for item in "abc":
        history.add_command(_AppendCommand(values, item))
    history.undo()
    history.undo()
    assert values == ["a"]
    assert not history.can_undo
    history.redo()
    history.redo()
    assert values == ["a", "b", "c"]


def test_batch_plain_failure_preserves_completed_inverse_and_retry_order() -> None:
    values = []
    gate = {"fail": True}

    class _MaybeAppendB:
        def execute(self):
            if gate["fail"]:
                raise OSError("b unavailable")
            return _AppendCommand(values, "b").execute()

    history = CommandHistory()
    batch = BatchCommand([
        _AppendCommand(values, "a"), _MaybeAppendB(), _AppendCommand(values, "c")
    ])
    with pytest.raises(PartialCommandError) as caught:
        history.add_command(batch)
    assert values == ["c"]
    assert history.can_undo
    gate["fail"] = False
    history.add_command(caught.value.remaining)
    assert values == ["c", "b", "a"]
    history.undo()
    assert values == ["c"]
    history.undo()
    assert values == []


def test_nested_partial_batch_preserves_current_and_unexecuted_work() -> None:
    values = dict.fromkeys("abcd", False)
    failures = {"c"}
    attempts = []
    batch = BatchCommand([
        _SetValuesCommand(values, {"a": True}, failures, attempts),
        BatchCommand([
            _SetValuesCommand(values, {"b": True, "c": True}, failures, attempts),
            _SetValuesCommand(values, {"d": True}, failures, attempts),
        ]),
    ])
    with pytest.raises(PartialCommandError) as caught:
        batch.execute()
    assert attempts == ["d", "b", "c"]
    assert values == {"a": False, "b": True, "c": False, "d": True}
    caught.value.inverse.execute()
    assert attempts == ["d", "b", "c", "b", "d"]
    assert values == dict.fromkeys("abcd", False)
    failures.clear()
    caught.value.remaining.execute()
    assert attempts == ["d", "b", "c", "b", "d", "c", "a"]
    assert values == {"a": True, "b": False, "c": True, "d": False}
