from __future__ import annotations

import json
from pathlib import Path

from app_common.about_dialog import config


def test_module_about_cfg_is_valid_json() -> None:
    cfg_path = Path(config.__file__).with_name("about.cfg")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))

    assert raw["about"]["作者"] == "追鸟奇遇记(osk.ch)"
    assert raw["about"]["我的小红书"] == "https://xhslink.com/m/A2cowPsYj8P"


def test_load_about_info_uses_valid_override_and_substitutions(tmp_path) -> None:
    cfg_path = tmp_path / "about.cfg"
    cfg_path.write_text(
        json.dumps(
            {
                "about": {
                    "app_name": "{app_name}",
                    "version": "{version}",
                    "作者": "配置作者",
                    "开源地址": "https://example.test/project",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    info = config.load_about_info(
        str(cfg_path),
        app_name="测试应用",
        version="1.2.3",
    )

    assert info["app_name"] == "测试应用"
    assert info["version"] == "1.2.3"
    assert info["作者"] == "配置作者"
    assert info["开源地址"] == "https://example.test/project"


def test_invalid_about_json_logs_the_parse_location(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "about.cfg"
    cfg_path.write_text('{"about": {"作者": "缺少结束括号"}', encoding="utf-8")
    warnings: list[str] = []

    monkeypatch.setattr(
        config._log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )

    assert config._load_raw_cfg(str(cfg_path)) == {}
    assert warnings
    assert str(cfg_path) in warnings[0]
    assert "line 1 column" in warnings[0]


def test_image_overrides_distinguish_missing_empty_and_invalid(tmp_path, monkeypatch):
    from PIL import Image

    default = tmp_path / 'default'
    application = tmp_path / 'application'
    user = tmp_path / 'user'
    for directory in (default, application, user):
        directory.mkdir()
        Image.new('RGB', (64, 64), 'white').save(directory / 'qr.png')
        (directory / 'about.cfg').write_text(json.dumps({
            'images': [{'path': 'qr.png', 'size': 256}]
        }), encoding='utf-8')
    monkeypatch.setattr(config, '_module_cfg_path', lambda: str(default / 'about.cfg'))
    app_cfg, user_cfg = application / 'about.cfg', user / 'about.cfg'
    load = lambda: config.load_about_images(app_cfg, override_paths=(user_cfg,))
    assert Path(load()[0]['path']).parent == user
    user_cfg.write_text('{"about":{"作者":"自定义"}}', encoding='utf-8')
    assert Path(load()[0]['path']).parent == application
    user_cfg.write_text('{"images":[]}', encoding='utf-8')
    assert load() == []
    user_cfg.write_text('{"images":[{"path":"missing.png"}]}', encoding='utf-8')
    assert load() == []
    user_cfg.write_text('{"images":[{"path":"qr.png","size":"bad"}]}', encoding='utf-8')
    assert load()[0]['size'] == 120


def test_about_info_empty_value_hides_inherited_field(tmp_path):
    path = tmp_path / 'about.cfg'
    path.write_text('{"about":{"作者":""}}', encoding='utf-8')
    assert config.load_about_info(path)['作者'] == ''
