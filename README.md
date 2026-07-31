# app_common

SuperViewer 与 SuperBirdStamp 共用的照片处理、元数据和 UI 组件库，可作为
submodule 使用。共享代码改动必须同时保持两个应用的行为兼容。

## 结构

- **about_dialog**：关于对话框 + about 配置
  - `about.cfg`：默认“关于”信息（JSON，含 `about` 键）
  - `config.py`：`load_about_info(override_path=None)` 从 about.cfg 加载，可选外部文件覆盖
  - `dialog.py`：`show_about_dialog(parent, about_info, logo_path=..., banner_path=...)`
- **exif_io**：EXIF/XMP 读取和 XMP sidecar 写入
  - `exiftools_mac`：macOS 下的 exiftools 工具
  - `exiftools_win`：Windows 下的 exiftools 工具
- **file_browser**：可排序/过滤的列表与缩略图浏览器
- **focus_calc**：超焦距计算
- **preview_canvas**：图片预览组件
- **report_db**：`慧眼选鸟`报告数据库兼容层；应用内仅作为只读 fallback/hydration
- **send_to_app**：发送/接收文件到与其他应用
- **ui_style**：UI 样式
- **app_info_bar**：图标 + 主副标题 + “关于...” 按钮
  - `widget.py`：`AppInfoBar(parent, title=..., subtitle=..., icon_path=..., on_about_clicked=...)`
- **png_to_ico**：从 icons/app_icon.png 生成对应 .ico 和（在 macOS 下）.icns。

## 共享行为约束

- 用户可编辑元数据只写到同目录、同 stem 的 `.xmp` sidecar，不修改 RAW/
  原图，也不回写 `report.db`。宽松的 DxO 派生 stem 或父目录 XMP 回退只用于
  读取；复制、移动、粘贴、删除及写入使用
  `exif_io.find_same_stem_xmp_sidecar()`。
- 文件粘贴把一个 clipboard payload 中的主图和 XMP 作为一次事务：全部先
  stage，再提交；任一异常会回滚全部已复制/移动的文件。
- `report.db` 缓存按完整路径、root-relative 路径和 root-scoped 唯一 stem
  建索引；直接 DB 索引按 DB 路径、mtime 与大小失效。删除只添加当前会话的
  精确 path/scope tombstone，不删除数据库行。
- 持久缩略图层级为 `128/256/512/1024/2048`。元数据与持久缩略图使用独立
  worker pool，可并发运行；可分别用 `SuperViewer_METADATA_WORKERS` 和
  `SuperViewer_PERSISTENT_THUMB_WORKERS` 覆盖。
- 非 JPEG 内存缩略图记录已满足的最大请求层级，较小缓存不能命中或覆盖之后
  的较大请求。内存预算自适应，硬上限为 16 GiB。
- 应用驱动的按键连播默认关闭，由 SuperViewer 显式启用；SuperBirdStamp
  保留 Qt 原生列表导航行为。

## 依赖

- Python 3.10+
- PyQt5 或 PyQt6

## 用法

```python
from app_common import show_about_dialog, load_about_info, AppInfoBar

# 加载关于信息（默认读 about_dialog/about.cfg，可选 override_path 覆盖）
info = load_about_info(override_path="/path/to/super_viewer.cfg")
show_about_dialog(parent, info, logo_path="...", banner_path="...")

bar = AppInfoBar(parent, title="MyApp", subtitle="...", icon_path="...", on_about_clicked=lambda: ...)
layout.addWidget(bar)
```

