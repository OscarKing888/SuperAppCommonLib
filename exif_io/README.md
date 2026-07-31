# exif_io

EXIF/XMP 配置、ExifTool 路径、元数据读取与 XMP sidecar 写入。内含
**exiftools_mac** / **exiftools_win**。

## 结构

- **exif.cfg**：EXIF 相关配置（tag 顺序、中文名、隐藏项等）
- **config.py**：`load_exif_settings(override_path=None)` 等
- **exiftool_path.py**：`get_exiftool_executable_path()`，优先模块内 exiftools_*
- **writer.py**：ExifTool 读取与兼容写入 API；写入目标统一为 XMP sidecar
- **exiftool_runner.py**：共享 `-stay_open` 进程、逐命令 timeout/cancel 与关闭管理
- **xmp_sidecar.py**：XMP 读取；提供宽松读取 resolver 和严格生命周期 resolver
- **exiftools_mac/**、**exiftools_win/**：平台 exiftool 可执行文件

## 写入与进程约束

- `find_xmp_sidecar()` 可为读取匹配 DxO 派生文件名和父目录来源。
  写入、复制、移动、重命名和删除必须使用
  `find_same_stem_xmp_sidecar()`，只匹配同目录、同 stem、大小写不敏感的
  `.xmp`。
- `run_exiftool_assignments()` 不修改原图。已有严格同 stem sidecar 时直接
  更新；否则通过 `-o` 创建 `<image-stem>.xmp`。
- 非 ASCII 或含换行的 ExifTool 值使用 UTF-8 临时文件与 `-Tag<=file`
  重定向，临时文件在成功、失败或取消后都会清理。
- stay-open 命令有默认超时，并接受调用方 `cancel_event`。超时/单命令取消会
  丢弃失步进程，下一条命令自动重启；应用退出必须调用
  `close_exiftool_process()`（同时注册了 `atexit` 兜底）。

## 依赖

- piexif
- 可选：系统 PATH 中的 exiftool（若未打包 exiftools_*）

## 用法

```python
from app_common.exif_io import (
    get_exiftool_executable_path,
    run_exiftool_json,
    write_exif_with_exiftool,
    write_exif_with_exiftool_by_key,
    write_meta_with_exiftool,
    write_meta_with_piexif,
)
path = get_exiftool_executable_path()
data = run_exiftool_json("/path/to/image.jpg")
write_meta_with_exiftool("/path/to/image.jpg", "Title", "标题")
```
