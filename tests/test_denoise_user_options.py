"""旧配置兼容、新降噪设置归一化与 UTF-8 存储。"""
import pytest

from app_common.superviewer_user_options import (DENOISE_DEFAULT_OPTIONS, load_user_options,
                                                normalize_user_options, save_user_options)


def test_existing_options_gain_denoise_defaults_without_changing_browser_values():
    options = normalize_user_options({"thumbnail_loader_workers": 7, "key_navigation_fps": 60})
    assert options["thumbnail_loader_workers"] == 7 and options["key_navigation_fps"] == 60
    assert {key: options[key] for key in DENOISE_DEFAULT_OPTIONS} == DENOISE_DEFAULT_OPTIONS


@pytest.mark.parametrize("name", ["../output", "C:\\output", "nul", "COM1.jpg", "a/b", "a."])
def test_invalid_portable_subdir_resets_to_default(name):
    assert normalize_user_options({"denoise_subdir": name})["denoise_subdir"] == "denoised"


def test_denoise_options_handle_malformed_values_and_clamp_numbers():
    result = normalize_user_options({"denoise_output_mode": [], "denoise_device": {},
                                    "denoise_format": "png", "denoise_strength": 999,
                                    "denoise_workers": 0, "denoise_output_directory": None})
    assert result["denoise_output_mode"] == "source_subdir"
    assert result["denoise_device"] == "auto" and result["denoise_format"] == "tiff"
    assert result["denoise_strength"] == 100 and result["denoise_workers"] == 1
    assert result["denoise_output_directory"] == ""


def test_chinese_options_round_trip_and_ask_mode_persist(tmp_path):
    path = tmp_path / "用户.cfg"
    options = {"denoise_output_mode": "ask", "denoise_output_directory": str(tmp_path / "鸟类成片"),
               "denoise_subdir": "降噪照片", "denoise_strength": 0, "denoise_workers": 3}
    saved = save_user_options(options, str(path))
    assert load_user_options(str(path)) == saved
    assert "降噪照片" in path.read_text(encoding="utf-8")
    assert saved["denoise_strength"] == 0
