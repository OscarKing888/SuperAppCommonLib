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
