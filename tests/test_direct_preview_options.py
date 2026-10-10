"""Persistent and live preview limits, including compatibility with old options."""
import pytest

from app_common import superviewer_user_options as options


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch):
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options(None))


@pytest.mark.parametrize("legacy_mode", [0, 1])
def test_old_options_use_dimensions_and_keep_file_limit(legacy_mode):
    normalized = options.normalize_user_options({"keep_view_on_switch": 0,
        "direct_preview_limit_mode": legacy_mode, "direct_preview_max_pixels": 40 * 1024 * 1024,
        options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 17})
    assert normalized[options.KEY_DIRECT_PREVIEW_MAX_WIDTH] == 2048
    assert normalized[options.KEY_DIRECT_PREVIEW_MAX_HEIGHT] == 2048
    assert normalized[options.KEY_DIRECT_PREVIEW_MAX_FILE_MB] == 17
    assert "direct_preview_limit_mode" not in normalized
    assert "direct_preview_max_pixels" not in normalized
    assert normalized["keep_view_on_switch"] == 0
    assert options.get_direct_preview_limits() == (2048, 2048, 32 * 1024 * 1024)


@pytest.mark.parametrize("value", [None, "invalid", -1, 10**20, float("nan"), float("inf")])
def test_invalid_preview_limits_fall_back(value):
    keys = (options.KEY_DIRECT_PREVIEW_MAX_WIDTH, options.KEY_DIRECT_PREVIEW_MAX_HEIGHT,
            options.KEY_DIRECT_PREVIEW_MAX_FILE_MB)
    defaults = options.normalize_user_options(None)
    result = options.normalize_user_options(dict.fromkeys(keys, value))
    assert all(result[key] == defaults[key] for key in keys)


@pytest.mark.parametrize("width,height,file_mb", [(3000, 2000, 24), (0, 0, 0), (0, 2048, 24), (2048, 0, 0)])
def test_save_reload_and_live_limit_preserve_both_values(tmp_path, width, height, file_mb):
    path = tmp_path / "中文用户选项.cfg"
    requested = {
        options.KEY_DIRECT_PREVIEW_MAX_WIDTH: width,
        options.KEY_DIRECT_PREVIEW_MAX_HEIGHT: height,
        options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: file_mb,
    }
    saved = options.save_user_options(requested, str(path))
    loaded = options.load_user_options(str(path))
    assert loaded == saved
    assert all(loaded[key] == value for key, value in requested.items())
    options.apply_runtime_user_options(loaded)
    assert options.get_direct_preview_limits() == (width, height, file_mb * 1024 * 1024)


def test_broken_config_uses_compatible_defaults(tmp_path):
    path = tmp_path / "坏配置.cfg"
    path.write_text("{", encoding="utf-8")
    assert options.load_user_options(str(path)) == options.normalize_user_options(None)
