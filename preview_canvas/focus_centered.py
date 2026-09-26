"""Viewer 与 BirdStamp 共用的焦点居中预览视口。"""
from __future__ import annotations

import math

from .canvas import PreviewCanvas


class FocusCenteredPreviewCanvas(PreviewCanvas):
    """可选焦点锁定视口；只改变平移/缩放，不改变叠加或导出像素。"""

    _auto_focus_center = False

    def set_auto_focus_center(self, enabled: bool) -> None:
        self._auto_focus_center = bool(enabled)
        self.recenter_focus()
        self._update_cursor()

    def recenter_focus(self) -> None:
        self._clamp_offset()
        self.update()

    def _clamp_offset(self) -> None:
        if not self._auto_focus_center or self._source_pixmap is None:
            super()._clamp_offset()
            return
        center = (0.5, 0.5)
        try:
            left, top, right, bottom = map(float, self._focus_box)
            if (
                all(math.isfinite(v) for v in (left, top, right, bottom))
                and 0 <= left <= right <= 1 and 0 <= top <= bottom <= 1
            ):
                center = ((left + right) / 2, (top + bottom) / 2)
        except (TypeError, ValueError):
            pass
        # 允许边缘留白，否则靠近图像边缘的焦点无法真正居中。
        self._apply_view_center_ratio(center)

    def _can_pan(self) -> bool:
        return not self._auto_focus_center and super()._can_pan()

    def set_source_pixmap(self, pixmap, **kwargs) -> None:
        zoom = self._zoom
        if self._auto_focus_center:
            # 两个应用都以适应窗口为基准保持 zoom，像素分辨率升级不重置视野。
            kwargs.update(reset_view=False, preserve_view=False, preserve_scale=False)
        super().set_source_pixmap(pixmap, **kwargs)
        if self._auto_focus_center and self._source_pixmap is None:
            # RAW/HEIF 的加载占位不能丢失放大程度。
            self._zoom = zoom

    def apply_overlay_state(self, state) -> None:
        super().apply_overlay_state(state)
        if self._auto_focus_center:
            self.recenter_focus()

    def set_focus_box(self, focus_box) -> None:
        super().set_focus_box(focus_box)
        if self._auto_focus_center:
            self.recenter_focus()
