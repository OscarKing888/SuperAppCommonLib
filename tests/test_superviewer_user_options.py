import os
import importlib

from app_common import superviewer_user_options
from app_common.superviewer_user_options import normalize_user_options

_browser_core = importlib.import_module("app_common.file_browser._browser_core")
_workers = importlib.import_module("app_common.file_browser._workers")
_panel_module = importlib.import_module("app_common.file_browser._panel")


def _expected_metadata_workers() -> int:
    cpu_count = max(1, os.cpu_count() or 1)
    return max(1, min(8, cpu_count // 4 or 1))


def test_default_workers_split_metadata_and_persistent_thumbnail_generation() -> None:
    cpu_count = max(1, os.cpu_count() or 1)
    metadata_workers = _expected_metadata_workers()

    options = normalize_user_options({})

    assert options["metadata_loader_workers"] == metadata_workers
    assert options["persistent_thumb_workers"] == max(1, cpu_count - metadata_workers)


def test_bird_hover_color_validates_and_roundtrips(tmp_path, monkeypatch):
    key = superviewer_user_options.KEY_BIRD_HOVER_COLOR
    for value in (None, '', 'red', '#11223344', '#oops', 123):
        assert normalize_user_options({key: value})[key] == '#FF0000'
    monkeypatch.setattr(superviewer_user_options, 'get_user_options_path', lambda: str(tmp_path / 'options.cfg'))
    saved = superviewer_user_options.save_user_options({key: ' #aabbcc '})
    assert saved[key] == '#AABBCC'
    assert superviewer_user_options.load_user_options()[key] == '#AABBCC'


def test_legacy_options_keep_persistent_worker_count_and_add_metadata_default() -> None:
    custom_persistent_workers = max(1, os.cpu_count() or 1) + 7

    options = normalize_user_options({"persistent_thumb_workers": custom_persistent_workers})

    assert options["metadata_loader_workers"] == _expected_metadata_workers()
    assert options["persistent_thumb_workers"] == custom_persistent_workers


def test_legacy_default_persistent_workers_migrate_to_remaining_cpu_budget() -> None:
    cpu_count = max(1, os.cpu_count() or 1)
    metadata_workers = _expected_metadata_workers()

    options = normalize_user_options({"persistent_thumb_workers": cpu_count})

    assert options["metadata_loader_workers"] == metadata_workers
    assert options["persistent_thumb_workers"] == max(1, cpu_count - metadata_workers)


def test_metadata_workers_boost_when_thumbnail_work_is_idle(monkeypatch) -> None:
    previous = superviewer_user_options.get_runtime_user_options()
    monkeypatch.delenv("SuperViewer_METADATA_WORKERS", raising=False)
    monkeypatch.setattr(_browser_core.os, "cpu_count", lambda: 32)
    try:
        superviewer_user_options.apply_runtime_user_options(
            {
                "thumbnail_loader_workers": 32,
                "metadata_loader_workers": 8,
                "persistent_thumb_workers": 24,
                "persistent_thumb_max_size": 128,
                "key_navigation_fps": 24,
                "keep_view_on_switch": 1,
            }
        )

        assert _browser_core._metadata_loader_worker_count_for_thumbnail_state(False) == 12
        assert _browser_core._metadata_loader_worker_count_for_thumbnail_state(True) == 8
    finally:
        superviewer_user_options.apply_runtime_user_options(previous)


def test_metadata_worker_env_override_disables_idle_boost(monkeypatch) -> None:
    monkeypatch.setenv("SuperViewer_METADATA_WORKERS", "6")
    monkeypatch.setattr(_browser_core.os, "cpu_count", lambda: 32)

    assert _browser_core._metadata_loader_worker_count_for_thumbnail_state(False) == 6


def test_persistent_thumbnail_worker_env_override_is_independent(monkeypatch) -> None:
    monkeypatch.setenv("SuperViewer_METADATA_WORKERS", "5")
    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "17")

    assert _browser_core._metadata_loader_worker_count() == 5
    assert _browser_core._persistent_thumb_cache_worker_count() == 17


def test_metadata_chunk_size_allows_requested_parallelism(monkeypatch) -> None:
    monkeypatch.setattr(_workers, "_METADATA_CHUNK_SIZE", 150)

    chunk_size = _workers._metadata_chunk_size_for_worker_count(1000, 24)
    chunk_count = (1000 + chunk_size - 1) // chunk_size

    assert chunk_count >= 24
    assert chunk_size < 150


def test_persistent_thumbnail_worker_starts_while_metadata_is_running(monkeypatch, tmp_path) -> None:
    class _Signal:
        def connect(self, _slot) -> None:
            pass

    class _FakePersistentWorker:
        instances = []

        def __init__(self, paths, current_dir, **kwargs) -> None:
            self.paths = list(paths)
            self.current_dir = current_dir
            self.kwargs = kwargs
            self.progress_updated = _Signal()
            self.finished_summary = _Signal()
            self.started = False
            self.instances.append(self)

        def start(self) -> None:
            self.started = True

    class _RunningMetadata:
        def isRunning(self) -> bool:
            return True

    monkeypatch.setattr(_panel_module, "PersistentThumbCacheWorker", _FakePersistentWorker)
    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "7")
    panel = _panel_module.FileListPanel.__new__(_panel_module.FileListPanel)
    panel._background_shutdown_requested = False
    panel._background_shutdown_started = False
    panel._file_writes_allowed = lambda *_args, **_kwargs: True
    panel._persistent_thumb_cache_pending_paths = [str(tmp_path / "img.jpg")]
    panel._persistent_thumb_cache_base_dir = str(tmp_path)
    panel._persistent_thumb_cache_pending_priority = 0
    panel._persistent_thumb_cache_worker = None
    panel._report_full_cache = None
    panel._report_cache = {}
    panel._thumb_size = 128
    panel._metadata_loader = _RunningMetadata()

    _panel_module.FileListPanel._start_persistent_thumb_cache_worker(panel)

    assert _FakePersistentWorker.instances
    worker = _FakePersistentWorker.instances[0]
    assert worker.started
    assert worker.kwargs["worker_count"] == 7
    assert panel._persistent_thumb_cache_pending_paths == []


def test_metadata_and_thumbnail_progress_show_their_worker_counts(monkeypatch) -> None:
    class _Progress:
        def __init__(self) -> None:
            self.format = ""
            self.tooltip = ""

        def setRange(self, *_args) -> None:
            pass

        def setValue(self, *_args) -> None:
            pass

        def setFormat(self, value) -> None:
            self.format = value

        def setToolTip(self, value) -> None:
            self.tooltip = value

        def show(self) -> None:
            pass

        def hide(self) -> None:
            pass

    monkeypatch.setenv("SuperViewer_PERSISTENT_THUMB_WORKERS", "13")
    panel = _panel_module.FileListPanel.__new__(_panel_module.FileListPanel)
    panel._meta_progress = _Progress()
    panel._persistent_thumb_progress = _Progress()
    panel._persistent_thumb_cache_total = 20
    panel._persistent_thumb_cache_done = 3
    panel._persistent_thumb_cache_status_text = "生成预览缩略图"
    panel._persistent_thumb_cache_scope_dirs = []
    panel._persistent_thumb_cache_current_path = ""
    panel._persistent_thumb_cache_base_dir = ""
    panel._persistent_thumb_cache_generated = 2
    panel._persistent_thumb_cache_skipped = 1
    panel._persistent_thumb_cache_failed = 0
    panel._thumb_size = 128

    _panel_module.FileListPanel._show_meta_progress_status(
        panel,
        "正在读取元数据",
        busy=False,
        value=3,
        total=20,
        worker_count=5,
    )
    _panel_module.FileListPanel._update_persistent_thumb_progress_widget(panel)

    assert "(5线程)" in panel._meta_progress.format
    assert "(13线程)" in panel._persistent_thumb_progress.format
    assert "- 生成线程: 13" in panel._persistent_thumb_progress.tooltip


def test_bird_sharpness_max_birds_defaults_to_no_limit_and_is_clamped() -> None:
    key = superviewer_user_options.KEY_BIRD_SHARPNESS_MAX_BIRDS
    assert normalize_user_options({})[key] == 0
    assert normalize_user_options({key: 12})[key] == 12
    assert normalize_user_options({key: -3})[key] == 0
    assert normalize_user_options({key: "x"})[key] == 0
    assert normalize_user_options({key: 10 ** 6})[key] == superviewer_user_options.BIRD_SHARPNESS_MAX_BIRDS_LIMIT
    applied = superviewer_user_options.apply_runtime_user_options({key: 5})
    try:
        assert applied[key] == 5 and superviewer_user_options.get_bird_sharpness_max_birds() == 5
    finally:
        superviewer_user_options.apply_runtime_user_options(None)


def test_bird_sharpness_tile_options_default_clamp_and_reach_the_getter() -> None:
    o = superviewer_user_options
    defaults = normalize_user_options({})
    assert defaults[o.KEY_BIRD_SHARPNESS_FULL_TILE] == 1024 and defaults[o.KEY_BIRD_SHARPNESS_MF_CENTER] == 1
    assert defaults[o.KEY_BIRD_SHARPNESS_MF_CENTER_PERCENT] == 50 and defaults[o.KEY_BIRD_SHARPNESS_MF_TILE] == 256
    assert defaults[o.KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT] == 10
    clamped = normalize_user_options({o.KEY_BIRD_SHARPNESS_FULL_TILE: 5, o.KEY_BIRD_SHARPNESS_MF_TILE: 10 ** 6,
                                      o.KEY_BIRD_SHARPNESS_MF_CENTER: "x", o.KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT: 0})
    assert clamped[o.KEY_BIRD_SHARPNESS_FULL_TILE] == 128 and clamped[o.KEY_BIRD_SHARPNESS_MF_TILE] == 2048
    assert clamped[o.KEY_BIRD_SHARPNESS_MF_CENTER] == 1 and clamped[o.KEY_BIRD_SHARPNESS_MF_SHARPEST_PERCENT] == 1
    o.apply_runtime_user_options({o.KEY_BIRD_SHARPNESS_MF_CENTER: 0, o.KEY_BIRD_SHARPNESS_MF_TILE: 128})
    try:
        assert o.get_bird_sharpness_tile_options() == {"full_tile": 1024, "mf_center": False, "mf_center_percent": 50,
                                                       "mf_tile": 128, "mf_sharpest_percent": 10}
    finally:
        o.apply_runtime_user_options(None)


def test_bird_sharpness_models_and_enhanced_search_options() -> None:
    o = superviewer_user_options
    d = normalize_user_options({})
    assert (d[o.KEY_BIRD_SHARPNESS_DETECTOR], d[o.KEY_BIRD_SHARPNESS_SAM_MODEL]) == ("auto", "")
    assert (d[o.KEY_BIRD_SHARPNESS_SAM_SCOPE], d[o.KEY_BIRD_SHARPNESS_ENH_MODE]) == ("all", "off")
    assert d[o.KEY_BIRD_SHARPNESS_ENH_GRID] == 2 and d[o.KEY_BIRD_SHARPNESS_ENH_REGION_PERCENT] == 70
    assert d[o.KEY_BIRD_SHARPNESS_ENH_MIN_CONF_PERCENT] == 50
    picked = normalize_user_options({o.KEY_BIRD_SHARPNESS_DETECTOR: "yolo26x-seg.pt",
                                     o.KEY_BIRD_SHARPNESS_SAM_MODEL: "sam2.1_l.pt",
                                     o.KEY_BIRD_SHARPNESS_ENH_MODE: "manual", o.KEY_BIRD_SHARPNESS_ENH_GRID: 99})
    assert picked[o.KEY_BIRD_SHARPNESS_DETECTOR] == "yolo26x-seg.pt" and picked[o.KEY_BIRD_SHARPNESS_ENH_GRID] == 6
    assert picked[o.KEY_BIRD_SHARPNESS_SAM_MODEL] == "sam2.1_l.pt" and picked[o.KEY_BIRD_SHARPNESS_ENH_MODE] == "manual"
    bad = normalize_user_options({o.KEY_BIRD_SHARPNESS_DETECTOR: "../evil.pt", o.KEY_BIRD_SHARPNESS_SAM_MODEL: 3,
                                  o.KEY_BIRD_SHARPNESS_ENH_MODE: "always"})
    assert (bad[o.KEY_BIRD_SHARPNESS_DETECTOR], bad[o.KEY_BIRD_SHARPNESS_SAM_MODEL]) == ("auto", "")
    assert bad[o.KEY_BIRD_SHARPNESS_ENH_MODE] == "off"
    params = o.get_bird_sharpness_params()
    assert set(params) == set(o.BIRD_SHARPNESS_PARAM_KEYS) and params["enh_lift"] is True
    entries = o.bird_sharpness_params_to_options({"enh_lift": False, "detector": "yolo11x.pt", "unknown": 1})
    assert entries == {o.KEY_BIRD_SHARPNESS_ENH_LIFT: 0, o.KEY_BIRD_SHARPNESS_DETECTOR: "yolo11x.pt"}
    assert (d[o.KEY_BIRD_SHARPNESS_PIXELS], d[o.KEY_BIRD_SHARPNESS_GREY_FILL]) == ("outline", 0)
    assert params["bird_pixels"] == "outline" and params["grey_fill"] is False
    chosen = normalize_user_options({o.KEY_BIRD_SHARPNESS_PIXELS: "box", o.KEY_BIRD_SHARPNESS_GREY_FILL: 1})
    assert (chosen[o.KEY_BIRD_SHARPNESS_PIXELS], chosen[o.KEY_BIRD_SHARPNESS_GREY_FILL]) == ("box", 1)
    odd = normalize_user_options({o.KEY_BIRD_SHARPNESS_PIXELS: "mask", o.KEY_BIRD_SHARPNESS_GREY_FILL: "x"})
    assert (odd[o.KEY_BIRD_SHARPNESS_PIXELS], odd[o.KEY_BIRD_SHARPNESS_GREY_FILL]) == ("outline", 0)
    assert o.bird_sharpness_params_to_options({"bird_pixels": "box", "grey_fill": True}) == \
        {o.KEY_BIRD_SHARPNESS_PIXELS: "box", o.KEY_BIRD_SHARPNESS_GREY_FILL: 1}
    assert d[o.KEY_BIRD_SHARPNESS_MIN_BIRD_SIDE] == 0 and params["min_bird_side"] == 0
    assert normalize_user_options({o.KEY_BIRD_SHARPNESS_MIN_BIRD_SIDE: 64})[o.KEY_BIRD_SHARPNESS_MIN_BIRD_SIDE] == 64
    assert normalize_user_options({o.KEY_BIRD_SHARPNESS_MIN_BIRD_SIDE: -1})[o.KEY_BIRD_SHARPNESS_MIN_BIRD_SIDE] == 0
    # SuperViewer measures the camera's embedded JPEG unless the user picks the RAW decode or the denoised image.
    assert d[o.KEY_BIRD_SHARPNESS_IMAGE_SOURCE] == "jpeg" and params["image_source"] == "jpeg"
    for source in ("raw", "denoised"):
        assert normalize_user_options({o.KEY_BIRD_SHARPNESS_IMAGE_SOURCE: source})[o.KEY_BIRD_SHARPNESS_IMAGE_SOURCE] == source
    assert normalize_user_options({o.KEY_BIRD_SHARPNESS_IMAGE_SOURCE: "png"})[o.KEY_BIRD_SHARPNESS_IMAGE_SOURCE] == "jpeg"
    assert o.bird_sharpness_params_to_options({"image_source": "raw"}) == {o.KEY_BIRD_SHARPNESS_IMAGE_SOURCE: "raw"}


def test_bird_sharpness_edge_estimator_defaults_to_standard() -> None:
    key = superviewer_user_options.KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR
    assert normalize_user_options({})[key] == "standard"
    assert normalize_user_options({key: "dense"})[key] == "dense"
    assert normalize_user_options({key: "bogus"})[key] == "standard"
    superviewer_user_options.apply_runtime_user_options({key: "dense"})
    try:
        assert superviewer_user_options.get_bird_sharpness_edge_estimator() == "dense"
    finally:
        superviewer_user_options.apply_runtime_user_options(None)


def test_bird_detection_options_mapping_normalization_and_runtime():
    opts = superviewer_user_options
    before = opts.get_runtime_user_options()
    custom = {'detect_long_edge': 2048, 'detect_imgsz': 1280, 'detect_conf_percent': 10,
              'duplicate_box_percent': 90, 'duplicate_mask_percent': 80, 'flock_mode': 'off', 'exclude_birds': False}
    try:
        values = opts.bird_sharpness_params_to_options(custom)
        opts.apply_runtime_user_options(values)
        result = opts.get_bird_sharpness_params()
        assert all(result[k] == v for k, v in custom.items())
        assert result['exclude_birds'] is False
        normalized = opts.normalize_user_options({opts.KEY_BIRD_SHARPNESS_DETECT_CONF_PERCENT: -1,
                    opts.KEY_BIRD_SHARPNESS_DUPLICATE_MASK_PERCENT: 500,
                    opts.KEY_BIRD_SHARPNESS_FLOCK_MODE: 'invalid'})
        assert normalized[opts.KEY_BIRD_SHARPNESS_DETECT_CONF_PERCENT] == 5
        assert normalized[opts.KEY_BIRD_SHARPNESS_DUPLICATE_MASK_PERCENT] == 100
        assert normalized[opts.KEY_BIRD_SHARPNESS_FLOCK_MODE] == 'auto'
    finally:
        opts.apply_runtime_user_options(before)
