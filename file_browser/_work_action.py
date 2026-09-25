# -*- coding: utf-8 -*-
"""Browser operations, independent of thread identity and scheduling priority."""
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class WorkerAction(ABC):
    def __init__(self, *, cancelled: Callable[[], bool] = lambda: False):
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return bool(self._cancelled())

    @abstractmethod
    def execute(self) -> Any:
        """Execute one bounded unit of work; return its result to the Future."""


class CallableAction(WorkerAction):
    """Adapter for existing callers and small auxiliary operations."""
    def __init__(self, callback, *args, cancelled=lambda: False, **kwargs):
        super().__init__(cancelled=cancelled)
        self.callback, self.args, self.kwargs = callback, args, kwargs

    def execute(self):
        return self.callback(*self.args, **self.kwargs)


class MetadataReadAction(WorkerAction):
    def __init__(self, reader, paths, *, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.reader, self.paths = reader, tuple(paths)

    def execute(self):
        return self.reader(list(self.paths))


class ThumbnailAction(WorkerAction):
    def __init__(self, decoder, path, emit, *, allow_progressive, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.decoder, self.path, self.emit = decoder, path, emit
        self.allow_progressive = allow_progressive

    def execute(self):
        return self.decoder(self.path, self.emit, allow_progressive=self.allow_progressive)


class PersistentThumbnailAction(WorkerAction):
    def __init__(self, generator, task, *, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.generator, self.task = generator, task

    def is_set(self):
        # 兼容现有解码器的协作取消 token，不把线程/Qt 对象传给调度策略。
        return self.is_cancelled()

    def execute(self):
        return self.generator(self.task, self)
