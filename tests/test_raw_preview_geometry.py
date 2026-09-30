from types import SimpleNamespace

import pytest

from app_common.raw_preview_geometry import rawpy_camera_crop_box, map_camera_focus_box


def sizes(**kwargs):
    return SimpleNamespace(**(dict(width=1000, height=800, left_margin=20, top_margin=10,
                                   crop_left_margin=120, crop_top_margin=90,
                                   crop_width=600, crop_height=400, flip=0) | kwargs))


@pytest.mark.parametrize('flip,expected', [
    (0, (.1, .1, .7, .6)), (3, (.3, .4, .9, .9)),
    (5, (.1, .3, .6, .9)), (6, (.4, .1, .9, .7)),
])
def test_camera_crop_uses_active_sensor_origin_and_display_rotation(flip, expected):
    assert rawpy_camera_crop_box(sizes(flip=flip)) == pytest.approx(expected)


def test_focus_maps_into_crop_without_changing_full_raw_image():
    # 相机焦点框为裁切画面的 25%..75%；完整 RAW 中对应 x=250..550, y=180..380。
    assert map_camera_focus_box((.25, .25, .75, .75), rawpy_camera_crop_box(sizes())) == pytest.approx(
        (.25, .225, .55, .475))


@pytest.mark.parametrize('value', [None, SimpleNamespace(width=1000, height=800),
                                  sizes(crop_width=0), sizes(crop_left_margin=0),
                                  sizes(crop_width=1200), sizes(crop_top_margin=65535)])
def test_missing_or_invalid_camera_crop_never_guesses(value):
    assert rawpy_camera_crop_box(value) is None
    box = (.2, .3, .4, .5)
    assert map_camera_focus_box(box, None) == box


@pytest.mark.parametrize('crop', [(0, 0, 0, 0), (float('nan'), 0, 1, 1), 'corrupt'])
def test_invalid_transport_geometry_does_not_move_focus(crop):
    box = (.2, .3, .4, .5)
    assert map_camera_focus_box(box, crop) == box
