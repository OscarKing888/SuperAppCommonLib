"""Cancellation and decode errors shared by photo editing and RGB denoising."""


class ImageProcessingCancelled(Exception):
    """Cooperative cancellation; callers retain ownership of temporary results."""


class UnsupportedImage(ValueError):
    """Input cannot be decoded into a static SDR image without silent data loss."""


def check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise ImageProcessingCancelled("已取消")
