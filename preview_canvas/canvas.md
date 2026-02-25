下面是可直接迁移到另一个 app 的“焦点提取 + 预览显示”流程说明。

**流程总览**
1. 从原始 metadata 解析相机类型（`CameraFocusType`）
2. 用 `focus_calc.extract_focus_box(...)` 提取归一化焦点框（`0~1`）
3. 把焦点框传给 `PreviewCanvas`（或 `PreviewWithStatusBar` 包装的 canvas）
4. 用 canvas 的选项接口控制“显示焦点”开关（不自己画、不自己处理缩放）

**核心代码位置**
- 焦点计算模块：`app_common/focus_calc.py`
  - `CameraFocusType`：`app_common/focus_calc.py:16`
  - 机型映射表：`app_common/focus_calc.py:198`
  - `resolve_focus_camera_type(...)`：`app_common/focus_calc.py:207`
  - `resolve_focus_camera_type_from_metadata(...)`：`app_common/focus_calc.py:220`
  - `get_focus_point(...)`：`app_common/focus_calc.py:359`
  - `extract_focus_box(...)`：`app_common/focus_calc.py:371`
- 预览 canvas 焦点显示能力：`app_common/preview_canvas/canvas.py`
  - `PreviewOverlayState`：`app_common/preview_canvas/canvas.py:59`
  - `PreviewOverlayOptions`：`app_common/preview_canvas/canvas.py:70`
  - `apply_overlay_state(...)`：`app_common/preview_canvas/canvas.py:150`
  - `apply_overlay_options(...)`：`app_common/preview_canvas/canvas.py:156`
  - `set_focus_box(...)`：`app_common/preview_canvas/canvas.py:166`
  - `set_show_focus_box(...)`：`app_common/preview_canvas/canvas.py:173`
  - 焦点框绘制（含缩放/平移适配）：`app_common/preview_canvas/canvas.py:557`

**焦点提取流程（给另一个 app 用）**
- `focus_calc` 返回的是“相对于输入图像”的归一化焦点框。
- 如果你预览显示的是“原图（未裁切未加边）”，可直接传给 canvas。
- 如果你预览显示的是“裁切后/加边后”的图，需要先做坐标变换，再传给 canvas。
- 这个项目里变换用的是 `editor_core.transform_source_box_after_crop_padding(...)`（如果你在另一个 app 也复用 `editor_core`，可以直接用）。

**最小接入示例（只显示焦点，推荐基于 `PreviewCanvas`）**
```python
from app_common.focus_calc import resolve_focus_camera_type_from_metadata, extract_focus_box
from app_common.preview_canvas import PreviewCanvas, PreviewOverlayOptions, PreviewOverlayState

# 1) 创建 canvas
self.preview = PreviewCanvas()

# 2) 设置图像（QPixmap）
self.preview.set_source_pixmap(pixmap)

# 3) 提取焦点框（raw_metadata + 原图尺寸）
camera_type = resolve_focus_camera_type_from_metadata(raw_metadata)
focus_box = extract_focus_box(raw_metadata, image_width, image_height, camera_type=camera_type)

# 4) 传给 canvas（状态）
self.preview.apply_overlay_state(PreviewOverlayState(focus_box=focus_box))

# 5) 控制是否显示（选项）
self.preview.apply_overlay_options(
    PreviewOverlayOptions(show_focus_box=self.show_focus_check.isChecked())
)
```

**“显示焦点”工具栏选项实现（你要的模式）**
- 主编辑器实现参考：
  - 复选框创建与连接：`birdstamp/gui/editor.py:531`、`birdstamp/gui/editor.py:533`
  - 统一处理回调：`birdstamp/gui/editor.py:666`
  - 从 UI 构建 overlay options：`birdstamp/gui/editor_renderer.py:53`
  - 应用到 canvas：`birdstamp/gui/editor_renderer.py:62`
- 模板编辑器实现参考：
  - 复选框创建与连接：`birdstamp/gui/editor_template_dialog.py:796`、`birdstamp/gui/editor_template_dialog.py:798`
  - 构建 options：`birdstamp/gui/editor_template_dialog.py:1870`
  - 应用 options：`birdstamp/gui/editor_template_dialog.py:1879`
  - toggled 回调：`birdstamp/gui/editor_template_dialog.py:1881`

**你在另一个 app 里可以直接照抄的“显示焦点”选项写法**
```python
# UI
self.show_focus_check = QCheckBox("显示对焦点")
self.show_focus_check.setChecked(True)
self.show_focus_check.toggled.connect(self._on_preview_overlay_toggled)

def _on_preview_overlay_toggled(self, _checked: bool) -> None:
    self.preview.apply_overlay_options(
        PreviewOverlayOptions(show_focus_box=bool(self.show_focus_check.isChecked()))
    )
```

**如果你用 `EditorPreviewCanvas`（不仅焦点，还要鸟框/裁切效果）**
- 扩展状态/选项类型在：
  - `birdstamp/gui/editor_preview_canvas.py:28` `EditorPreviewOverlayState`
  - `birdstamp/gui/editor_preview_canvas.py:39` `EditorPreviewOverlayOptions`
- 用法类似，只是把 `focus_box` 放进 `EditorPreviewOverlayState(...)`，并用 `EditorPreviewOverlayOptions(show_focus_box=...)`

**机型扩展方式（后续新增相机时）**
1. 在 `app_common/focus_calc.py:16` 给 `CameraFocusType` 加新枚举
2. 在 `app_common/focus_calc.py:198` 的 `_CAMERA_MODEL_TO_FOCUS_TYPE` 增加机型映射
3. 新增该机型的提取函数（point/box）
4. 注册到 `_FOCUS_POINT_EXTRACTORS` / `_FOCUS_BOX_EXTRACTORS`

**注意点**
- `focus_calc` 是纯计算模块（无 GUI 依赖），适合在 CLI/服务端复用。
- `PreviewCanvas` 已负责焦点框绘制和缩放/拖拽后的显示变换，外部不要再按屏幕坐标重算焦点框。
- 只有当“预览图不是原图坐标系”时，才需要在传入 canvas 前做 box 坐标变换。

1. 如果你希望，我可以再给你一份“另一个 app”的最小可运行 PyQt demo（含 `QCheckBox + PreviewCanvas + focus_calc`）。