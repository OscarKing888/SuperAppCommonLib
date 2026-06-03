# -*- coding: utf-8 -*-
from __future__ import annotations

from app_common import image_formats


def test_image_extensions_include_psd_once() -> None:
    assert ".psd" in image_formats.IMAGE_EXTENSIONS
    assert image_formats.IMAGE_EXTENSIONS.count(".psd") == 1
    assert ".psd" in image_formats.SUPPORTED_IMAGE_EXTENSIONS


def test_extension_groups_keep_raw_separate_from_pil() -> None:
    assert ".psd" in image_formats.PIL_IMAGE_EXTENSIONS
    assert ".psd" not in image_formats.RAW_IMAGE_EXTENSIONS
    assert ".cr3" in image_formats.RAW_IMAGE_EXTENSIONS
    assert ".cr3" not in image_formats.PIL_IMAGE_EXTENSIONS
