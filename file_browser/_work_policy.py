# -*- coding: utf-8 -*-
"""Scheduling policy only; no decoding, metadata I/O or worker identities."""
from enum import Enum


class WorkKind(str, Enum):
    METADATA = 'metadata'
    THUMBNAIL = 'thumbnail'


class BrowserWorkPolicy:
    def __init__(self, total, metadata_reserved):
        self.total = total
        self.metadata_reserved = metadata_reserved

    def choose(self, active, queued, *, thumbnail_demand, metadata_demand):
        # 保留的是并发额度，不是某两个固定线程。任意空闲线程都能补足元数据额度。
        if queued['metadata'] and active['metadata'] < self.metadata_reserved:
            return WorkKind.METADATA
        thumbnail_limit = self.total - (self.metadata_reserved if metadata_demand else 0)
        if queued['thumbnail'] and active['thumbnail'] < thumbnail_limit:
            return WorkKind.THUMBNAIL
        if queued['metadata'] and not thumbnail_demand:
            return WorkKind.METADATA
        return None
