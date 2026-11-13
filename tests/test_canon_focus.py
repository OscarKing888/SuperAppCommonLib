"""Canon AF arrays, mask words and display geometry (no camera files required)."""
import pytest

from app_common.focus_calc import (
    CameraFocusType, extract_focus_box, extract_focus_box_for_display,
    get_focus_point_for_display, resolve_focus_camera_type_from_metadata,
)


@pytest.fixture
def canon_metadata():
    # EOS R6m2 / 211A7718(1).CR3: 1053 个槽位，只有第 0 个有效。
    def padded(first):
        return " ".join(map(str, [first] + [0] * 1052))

    return {
        "IFD0:Make": "Canon", "IFD0:Model": "Canon EOS R6m2",
        "IFD0:Orientation": 1,
        "ExifIFD:ExifImageWidth": 6000, "ExifIFD:ExifImageHeight": 4000,
        "Canon:FocusMode": 0, "Canon:AFAreaMode": 9,
        "Canon:NumAFPoints": 1053, "Canon:ValidAFPoints": 1,
        "Canon:AFImageWidth": 6000, "Canon:AFImageHeight": 4000,
        "Canon:AFAreaWidths": padded(159), "Canon:AFAreaHeights": padded(159),
        "Canon:AFAreaXPositions": padded(-11), "Canon:AFAreaYPositions": padded(301),
        "Canon:AFPointsInFocus": "1 " + "0 " * 65,
        "Canon:AFPointsSelected": "1 " + "0 " * 65,
    }


EXPECTED_BOX = (2909.5 / 6000, 1619.5 / 4000, 3068.5 / 6000, 1778.5 / 4000)


@pytest.mark.parametrize("size", [(6000, 4000), (1200, 800), (1, 1)])
def test_r6m2_spot_af_uses_camera_frame_not_preview_size(canon_metadata, size):
    assert resolve_focus_camera_type_from_metadata(canon_metadata) == CameraFocusType.CANON_GENERIC
    assert extract_focus_box(canon_metadata, *size) == pytest.approx(EXPECTED_BOX)
    assert get_focus_point_for_display(canon_metadata, *size) == pytest.approx((2989/6000, 1699/4000))


@pytest.mark.parametrize("orientation,expected", [
    (1, EXPECTED_BOX),
    (3, (1-EXPECTED_BOX[2], 1-EXPECTED_BOX[3], 1-EXPECTED_BOX[0], 1-EXPECTED_BOX[1])),
    (6, (1-EXPECTED_BOX[3], EXPECTED_BOX[0], 1-EXPECTED_BOX[1], EXPECTED_BOX[2])),
    (8, (EXPECTED_BOX[1], 1-EXPECTED_BOX[2], EXPECTED_BOX[3], 1-EXPECTED_BOX[0])),
])
def test_canon_orientation_applied_once(canon_metadata, orientation, expected):
    canon_metadata["IFD0:Orientation"] = orientation
    assert extract_focus_box_for_display(canon_metadata, 800, 1200) == pytest.approx(expected)


def test_printed_point_zero_is_an_index_not_no_focus(canon_metadata):
    canon_metadata.update({"Canon:AFAreaMode": "Spot AF", "Canon:FocusMode": "One-shot AF",
                           "Canon:AFPointsInFocus": 0, "Canon:AFPointsSelected": 0})
    assert extract_focus_box(canon_metadata, 6000, 4000) == pytest.approx(EXPECTED_BOX)


def test_signed_masks_across_words_and_active_points_precede_selection(canon_metadata):
    canon_metadata.update({
        "Canon:NumAFPoints": 17, "Canon:ValidAFPoints": 17,
        "Canon:AFAreaXPositions": [0] * 15 + [-600, 600],
        "Canon:AFAreaYPositions": [0] * 15 + [400, -400],
        "Canon:AFAreaWidths": [60] * 17, "Canon:AFAreaHeights": [40] * 17,
        "Canon:AFPointsInFocus": "-32768 1", "Canon:AFPointsSelected": "1 0",
    })
    assert extract_focus_box(canon_metadata, 6000, 4000) == pytest.approx((.395, .395, .605, .605))


def test_selected_point_is_used_when_focus_mask_is_empty(canon_metadata):
    canon_metadata["Canon:AFPointsInFocus"] = [0] * 66
    assert extract_focus_box(canon_metadata, 6000, 4000) == pytest.approx(EXPECTED_BOX)


@pytest.mark.parametrize("changes", [
    {"Canon:AFPointsInFocus": [0] * 66, "Canon:AFPointsSelected": [0] * 66},
    {"Canon:ValidAFPoints": 0},
    {"Canon:ValidAFPoints": 1054},
    {"Canon:AFAreaWidths": [0] * 1053},
    {"Canon:AFImageWidth": 0},
    {"Canon:AFAreaYPositions": ""},
    {"Canon:AFPointsInFocus": "2 " + "0 " * 65, "Canon:AFPointsSelected": "2 " + "0 " * 65},
    {"Canon:AFPointsInFocus": "1 0", "Canon:AFPointsSelected": "1 0"},
    {"Canon:AFAreaMode": 0},
    {"Canon:FocusMode": 3},
    {"Canon:FocusMode": 6},
    {"Canon:FocusMode": "Manual Focus (3)"},
])
def test_invalid_or_manual_records_never_fabricate_a_focus_box(canon_metadata, changes):
    canon_metadata.update(changes)
    assert extract_focus_box(canon_metadata, 6000, 4000) is None


def test_old_afinfo_shared_size_and_no_minimum_enlargement(canon_metadata):
    canon_metadata.pop("Canon:AFAreaWidths")
    canon_metadata.pop("Canon:AFAreaHeights")
    canon_metadata.pop("Canon:AFAreaMode")
    canon_metadata.update({"Canon:AFAreaWidth": 12, "Canon:AFAreaHeight": 8})
    box = extract_focus_box(canon_metadata, 6000, 4000)
    assert box[2] - box[0] == pytest.approx(12 / 6000)
    assert box[3] - box[1] == pytest.approx(8 / 4000)


def test_powershot_y_direction_differs_from_eos(canon_metadata):
    canon_metadata["IFD0:Model"] = "Canon PowerShot G7 X"
    assert get_focus_point_for_display(canon_metadata, 6000, 4000) == pytest.approx((2989/6000, 2301/4000))


def test_canon_fields_win_over_track4_and_unqualified_aliases(canon_metadata):
    reordered = {"Track4:AFImageWidth": 100, "AFImageHeight": 100, **canon_metadata}
    assert extract_focus_box(reordered, 6000, 4000) == pytest.approx(EXPECTED_BOX)


def test_track4_only_fields_are_supported(canon_metadata):
    raw = {key.replace("Canon:", "Track4:"): value for key, value in canon_metadata.items()}
    assert extract_focus_box(raw, 6000, 4000) == pytest.approx(EXPECTED_BOX)


def test_edge_box_is_clipped_without_moving_its_camera_centre(canon_metadata):
    canon_metadata["Canon:AFAreaXPositions"] = [-2990] + [0] * 1052
    box = extract_focus_box(canon_metadata, 6000, 4000)
    assert box[0] == 0
    assert box[2] == pytest.approx(89.5 / 6000)


def test_canon_without_makernotes_keeps_standard_subject_area_support():
    raw = {"Make": "Canon", "SubjectArea": "3000 2000 120 80"}
    assert extract_focus_box(raw, 6000, 4000) == pytest.approx((.49, .49, .51, .51))


def test_explicit_no_active_canon_points_does_not_fall_back_to_subject_area(canon_metadata):
    canon_metadata.update({"SubjectArea": "3000 2000 120 80",
                           "Canon:AFPointsInFocus": [0]*66, "Canon:AFPointsSelected": [0]*66})
    assert extract_focus_box(canon_metadata, 6000, 4000) is None
