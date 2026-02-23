# exif_io

EXIF 配置、exiftool 路径与 EXIF **读写**（exiftool + piexif）。内含 **exiftools_mac** / **exiftools_win**。

## 结构

- **exif.cfg**：EXIF 相关配置（tag 顺序、中文名、隐藏项等）
- **config.py**：`load_exif_settings(override_path=None)` 等
- **exiftool_path.py**：`get_exiftool_executable_path()`，优先模块内 exiftools_*
- **writer.py**：exiftool 读（`run_exiftool_json`）、写（`run_exiftool_assignments`、`write_exif_with_exiftool`、`write_exif_with_exiftool_by_key`、`write_meta_with_exiftool`、`write_meta_with_piexif`）
- **exiftools_mac/**、**exiftools_win/**：平台 exiftool 可执行文件

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
