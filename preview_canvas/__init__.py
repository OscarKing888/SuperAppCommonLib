# -*- coding: utf-8 -*-
"""app_common.preview_canvas – reusable zoomable image preview widget.

Exports
-------
PreviewCanvas
    Base widget with checker background, focus-box overlay, original-size
    mode, and two extension points:

    * Override ``_paint_overlays(painter, draw_rect, content_rect)`` in a
      subclass to add static, application-specific overlays.
    * Call ``register_overlay_layer(fn)`` to add runtime overlay callables
      without subclassing.

PreviewWithStatusBar
    Composite: canvas + status bar below (原始分辨率 / 当前预览分辨率).
    Subclass override ``_get_status_segments()`` to extend status content (open/closed).

draw_checker_background
    Stand-alone helper: draw a Photoshop-style checkerboard via a QPainter.

Example::

    from app_common.preview_canvas import PreviewCanvas

    canvas = PreviewCanvas()
    canvas.set_source_pixmap(pixmap)
    canvas.set_show_focus_box(True)
    canvas.set_focus_box((0.3, 0.4, 0.7, 0.6))
"""
from app_common.preview_canvas.canvas import PreviewCanvas, PreviewWithStatusBar, draw_checker_background

__all__ = ["PreviewCanvas", "PreviewWithStatusBar", "draw_checker_background"]
