# about_dialog

关于对话框 + about 配置。配置见同目录 `about.cfg`。

- `about.cfg`：默认“关于”信息（JSON，含 `about` 键）
- `config.py`：`load_about_info(override_path=None)` 从 about.cfg 加载，可选外部文件覆盖
- `dialog.py`：`show_about_dialog(parent, about_info, logo_path=..., banner_path=...)`

## 用法

```python
from app_common.about_dialog import show_about_dialog, load_about_info

info = load_about_info(override_path="/path/to/EXIF.cfg")  # 可选覆盖
show_about_dialog(parent, info, logo_path="...", banner_path="...")
```
