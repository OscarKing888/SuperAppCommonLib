# app_info_bar

App 信息条：图标（可选）+ 主副标题 + “关于...” 按钮。

- `widget.py`：`AppInfoBar(parent, title=..., subtitle=..., icon_path=..., on_about_clicked=...)`

## 用法

```python
from app_common.app_info_bar import AppInfoBar

bar = AppInfoBar(parent, title="MyApp", subtitle="...", icon_path="...", on_about_clicked=callback)
layout.addWidget(bar)
```
