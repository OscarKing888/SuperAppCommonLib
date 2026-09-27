# 关于对话框

`load_about_info()` 和 `load_about_images()` 使用同一覆盖顺序：模块内 `about.cfg` → 应用 `override_path` → `override_paths` 中的附加配置。JSON 必须使用 UTF-8，解析错误包含路径、行列号。

```json
{
  "about": {
    "app_name": "{app_name}",
    "version": "{version}",
    "作者": "作者名",
    "网站": "https://example.com"
  },
  "images": [
    {
      "path": "images/qr.png",
      "label": "扫码访问网站",
      "size": 256,
      "url": "https://example.com"
    }
  ]
}
```

`about` 按字段覆盖，空字符串隐藏该行。`images` 是整个列表替换：省略则继承，`[]` 则清空。每层相对图片路径相对于该层配置文件；图片缺失会记录路径并跳过，不会换成默认二维码。`size` 是图片显示区域的逻辑像素长边（32–2048），图片保持宽高比。`label` 为纯文本，`url` 可选，图片卡片支持鼠标左键打开。

`AboutDialog` / `show_about_dialog()` 共用内容自适应布局：按图片数量和系统屏幕可用区域定初始尺寸，缩小窗口时自动换行，超出高度时滚动，确定按钮始终可用。文字跟随系统字体、DPI 和颜色；链接值按富文本转义处理。`logo_path` / `banner_path` 兼容参数仍可选用。

SuperBirdTools 的名称/版本/窗口标题由仓库根 `app_metadata.json` 管理，应用会将其身份字段应用到 About 内容上；`about.cfg` 中不要另存版本字面量。
