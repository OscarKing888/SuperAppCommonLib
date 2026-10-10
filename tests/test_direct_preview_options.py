"""Persistent and live preview limits, including compatibility with old options."""
import pytest

from app_common import superviewer_user_options as options


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch):
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options(None))


def test_old_options_keep_exact_pixel_default():
    normalized = options.normalize_user_options({"keep_view_on_switch": 0})
    assert normalized[options.KEY_DIRECT_PREVIEW_LIMIT_MODE] == options.DIRECT_PREVIEW_BY_PIXELS
    assert normalized[options.KEY_DIRECT_PREVIEW_MAX_PIXELS] == 40 * 1024 * 1024
    assert normalized["keep_view_on_switch"] == 0
    assert options.get_direct_preview_limit() == (options.DIRECT_PREVIEW_BY_PIXELS, 40 * 1024 * 1024)


@pytest.mark.parametrize("value", [None, "invalid", -1, 10**20, float("nan"), float("inf")])
def test_invalid_preview_limits_fall_back(value):
    keys = (options.KEY_DIRECT_PREVIEW_LIMIT_MODE, options.KEY_DIRECT_PREVIEW_MAX_PIXELS,
            options.KEY_DIRECT_PREVIEW_MAX_FILE_MB)
    defaults = options.normalize_user_options(None)
    result = options.normalize_user_options(dict.fromkeys(keys, value))
    assert all(result[key] == defaults[key] for key in keys)


@pytest.mark.parametrize("mode", [options.DIRECT_PREVIEW_BY_PIXELS, options.DIRECT_PREVIEW_BY_FILE_SIZE])
@pytest.mark.parametrize("disabled", [False, True])
def test_save_reload_and_live_limit_preserve_both_values(tmp_path, mode, disabled):
    path = tmp_path / "中文用户选项.cfg"
    requested = {
        options.KEY_DIRECT_PREVIEW_LIMIT_MODE: mode,
        options.KEY_DIRECT_PREVIEW_MAX_PIXELS: 0 if disabled else 50_123_456,
        options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0 if disabled else 24,
    }
    saved = options.save_user_options(requested, str(path))
    loaded = options.load_user_options(str(path))
    assert loaded == saved
    assert all(loaded[key] == value for key, value in requested.items())
    options.apply_runtime_user_options(loaded)
    expected = requested[options.KEY_DIRECT_PREVIEW_MAX_PIXELS] if mode == options.DIRECT_PREVIEW_BY_PIXELS else requested[options.KEY_DIRECT_PREVIEW_MAX_FILE_MB] * 1024 * 1024
    assert options.get_direct_preview_limit() == (mode, expected)


def test_broken_config_uses_compatible_defaults(tmp_path):
    path = tmp_path / "坏配置.cfg"
    path.write_text("{", encoding="utf-8")
    assert options.load_user_options(str(path)) == options.normalize_user_options(None)
