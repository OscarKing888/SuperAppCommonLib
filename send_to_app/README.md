# send_to_app 模块

「发送到外部应用」与「接收发送到本应用」的完整实现。**跨平台：Windows、macOS。**

## 配置

- **配置文件**：与主程序同目录下的 `extern_app.json`（可由调用方通过 `config_dir` 指定）。
- **格式**：`{"apps": [{"name": "显示名", "path": "应用路径", "app_id": "可选热接收ID"}, ...]}`

## 核心（无 UI）

- **config**：`load_config(config_dir=...)` / `save_config(apps, config_dir=...)`，读写 `extern_app.json`。
- **send**：`send_files_to_app(file_paths: list[str], app: dict, base_directory="")`，优先按 socket 协议热发送；失败时再启动指定应用并传入文件列表。

## 设置 UI

- **settings_ui**：`show_external_apps_settings_dialog(parent, config_dir=..., on_saved=...)`，编辑应用列表并写回 `extern_app.json`。

## 接收机制（与目录列表多选同等）

冷启动与热接收采用**同一套逻辑**：收到文件列表后，打开首文件所在目录，待目录加载完成后在文件列表中**多选**这些路径，并刷新预览/EXIF 为第一项，效果与用户在目录内多选文件一致。

1. **冷启动 argv**：`get_initial_file_list_from_argv(argv=None)` 从命令行参数解析出文件列表（遇 `-` 开头参数停止）。主程序对该列表调用与热接收相同的统一处理入口。
2. **macOS FileOpen**：`ensure_file_open_aware_application(argv=None)` + `install_file_open_handler(app, on_files_received)` 负责接收 `QFileOpenEvent / QEvent.FileOpen`，并在主窗口创建前先缓存、创建后再冲刷，兼容 `.app` 冷启动、Finder“打开方式”、`open -a App file1 file2`。
3. **热启动 socket**：`SingleInstanceReceiver(app_id, on_files_received)` 在首进程中监听；其它进程通过 `send_file_list_to_running_app(app_id, file_paths)` 发送文件列表。若发送成功，调用方应退出，由已运行实例在回调中处理。
4. **统一处理**：主程序将三种入口都汇总到同一个 `on_files_received(paths)` 处理函数；后续目录打开、多选、预览刷新逻辑只保留一套。

## 协议

- 发送到外部应用：若配置了 `app_id`，先按 QLocalSocket / UTF-8 JSON 协议 `{"files": [...]}` 热发送给已运行实例；失败时再用目标应用路径启动进程，文件列表作为命令行参数（macOS 上通过 `open -a App 文件1 文件2 ...`）。
- 发送到本应用：客户端连接 QLocalServer，发送一行 UTF-8 JSON：`{"files": ["path1", "path2", ...]}`。

## 跨平台说明

- **Windows**：发送用 `QProcess.startDetached(exe, [file1, file2, ...])`；单例 IPC 使用 Named Pipe（`QLocalServer`）；选择应用时过滤器为 `*.exe`，初始目录为 `ProgramFiles`。
- **macOS**：发送用 `open -a App 文件1 文件2 ...`，`resolve_app_path` 处理 `.app` 与 Adobe 风格目录；单例 IPC 使用 Unix domain socket；选择应用时过滤器为 `*.app`，初始目录为 `/Applications`。
- 路径统一使用 `os.path`，不写死 `/` 或 `\\`；配置文件与主程序同目录，两平台通用。

## 使用约定

- 发送时文件使用**全路径**；接收到的列表也由调用方转为绝对路径后处理。
- 若目标应用支持本模块同款单例 IPC，建议在配置中填写 `app_id`（例如 BirdStamp 为 `birdstamp`），这样运行中的实例可以直接接收文件列表。
- 冷启动时一般通过命令行传入一个文件列表（如 macOS：`SuperEXIF.app file1.jpg`；Windows：`SuperEXIF.exe file1.jpg`）。
- App 运行时，可接收其它进程通过同一协议发来的文件列表并在回调中处理。
