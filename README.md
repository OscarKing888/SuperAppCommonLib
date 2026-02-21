# app_common

通用 UI 子库：关于对话框、App 信息条、about 配置。可整体作为 Sub Git 库使用。

## 结构

- **about_dialog**：关于对话框 + about 配置
  - `about.cfg`：默认“关于”信息（JSON，含 `about` 键）
  - `config.py`：`load_about_info(override_path=None)` 从 about.cfg 加载，可选外部文件覆盖
  - `dialog.py`：`show_about_dialog(parent, about_info, logo_path=..., banner_path=...)`
- **app_info_bar**：图标 + 主副标题 + “关于...” 按钮
  - `widget.py`：`AppInfoBar(parent, title=..., subtitle=..., icon_path=..., on_about_clicked=...)`

## 依赖

- Python 3.10+
- PyQt5 或 PyQt6

## 用法

```python
from app_common import show_about_dialog, load_about_info, AppInfoBar

# 加载关于信息（默认读 about_dialog/about.cfg，可选 override_path 覆盖）
info = load_about_info(override_path="/path/to/EXIF.cfg")
show_about_dialog(parent, info, logo_path="...", banner_path="...")

bar = AppInfoBar(parent, title="MyApp", subtitle="...", icon_path="...", on_about_clicked=lambda: ...)
layout.addWidget(bar)
```
