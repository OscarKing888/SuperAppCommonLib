# -*- coding: utf-8 -*-
"""Scheduling policy only; no decoding, metadata I/O or worker identities."""
from enum import Enum


class WorkKind(str, Enum):
    METADATA = 'metadata'
    THUMBNAIL = 'thumbnail'
    # 长耗时后台分析（如鸟清晰度检测）：优先级最低，并发受 analysis_limit 限制。
    ANALYSIS = 'analysis'


class BrowserWorkPolicy:
    def __init__(self, total, metadata_reserved, analysis_limit=0):
        self.total = total
        self.metadata_reserved = metadata_reserved
        # 分析任务最多占用的线程数：始终给元数据保留额度并至少留 1 个线程给缩略图。
        self.analysis_limit = max(0, min(int(analysis_limit), total - metadata_reserved - 1))

    def choose(self, active, queued, *, thumbnail_demand, metadata_demand):
        # 保留的是并发额度，不是某两个固定线程。任意空闲线程都能补足元数据额度。
        if queued['metadata'] and active['metadata'] < self.metadata_reserved:
            return WorkKind.METADATA
        thumbnail_limit = self.total - (self.metadata_reserved if metadata_demand else 0)
        if queued['thumbnail'] and active['thumbnail'] < thumbnail_limit:
            return WorkKind.THUMBNAIL
        if queued['metadata'] and not thumbnail_demand:
            return WorkKind.METADATA
        # 分析只用元数据/缩略图此刻用不上的空闲线程；单个任务约 1 秒，可见缩略图最多等一个任务。
        if queued.get('analysis') and active.get('analysis', 0) < self.analysis_limit:
            return WorkKind.ANALYSIS
        return None
