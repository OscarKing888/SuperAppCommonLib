import math

from app_common.focus_calc import extract_focus_box_for_display, resolve_focus_camera_type_from_metadata


def _sample_sony_focus_metadata(makernote_key: str) -> dict[str, object]:
    return {
        "Make": "SONY",
        "Model": "ILCE-1M2",
        "ExifImageWidth": 5472,
        "ExifImageHeight": 3648,
        makernote_key: "5472 3648 2736 1824 640 480",
    }


def test_extract_focus_box_for_display_accepts_legacy_and_precache_sony_makernote_keys() -> None:
    legacy_raw = _sample_sony_focus_metadata("Makernote Tag 0x2027")
    precache_raw = _sample_sony_focus_metadata("MakernoteTag0x2027")

    legacy_focus_box = extract_focus_box_for_display(
        legacy_raw,
        5472,
        3648,
        camera_type=resolve_focus_camera_type_from_metadata(legacy_raw),
    )
    precache_focus_box = extract_focus_box_for_display(
        precache_raw,
        5472,
        3648,
        camera_type=resolve_focus_camera_type_from_metadata(precache_raw),
    )

    assert legacy_focus_box is not None
    assert precache_focus_box == legacy_focus_box
    expected = (
        0.4415204678362573,
        0.4342105263157895,
        0.5584795321637427,
        0.5657894736842105,
    )
    assert all(math.isclose(actual, target, rel_tol=1e-9, abs_tol=1e-9) for actual, target in zip(legacy_focus_box, expected))
