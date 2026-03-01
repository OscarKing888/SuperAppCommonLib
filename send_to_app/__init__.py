# -*- coding: utf-8 -*-
"""
发送到外部应用 (send_to_app)

- 配置：独立 extern_app.json，与主程序同目录（或由调用方指定 config_dir）。
- 核心：发送文件列表（全路径）到指定外部应用；接收由本应用启动时挂接回调处理。
- 冷启动：命令行参数传入文件列表。
- 热启动：通过单例 IPC 接收其它进程发来的文件列表并回调。
"""

from .config import (
    CONFIG_FILENAME,
    get_config_path,
    load_config,
    save_config,
)
from .receive import (
    ensure_file_open_aware_application,
    get_initial_file_list_from_argv,
    install_file_open_handler,
    normalize_file_paths,
    send_file_list_to_running_app,
    SingleInstanceReceiver,
)
from .send import (
    resolve_app_path,
    send_files_to_app,
)

__all__ = [
    "CONFIG_FILENAME",
    "get_config_path",
    "load_config",
    "save_config",
    "ensure_file_open_aware_application",
    "get_initial_file_list_from_argv",
    "install_file_open_handler",
    "normalize_file_paths",
    "send_file_list_to_running_app",
    "SingleInstanceReceiver",
    "resolve_app_path",
    "send_files_to_app",
]

# 兼容旧调用：get_external_apps / open_file_with_app
def get_external_apps(config_dir=None):
    """返回配置中的外部应用列表 [{"name": str, "path": str, "app_id": str?}]。"""
    return load_config(config_dir=config_dir).get("apps", [])


def open_file_with_app(filepath: str, app: dict, base_directory: str = ""):
    """用指定应用打开单个文件（兼容旧接口）。"""
    send_files_to_app([filepath] if filepath else [], app, base_directory)
