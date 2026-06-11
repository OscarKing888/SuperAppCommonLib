from pathlib import Path
import os

from app_common.send_to_app.receive import normalize_file_paths


def test_normalize_file_paths_skips_apple_double_metadata_files(tmp_path: Path) -> None:
    photo = tmp_path / "DSC06705.jpg"
    apple_double = tmp_path / "._DSC06705.jpg"

    normalized = normalize_file_paths([str(photo), str(apple_double), str(photo)])

    assert normalized == [os.path.abspath(os.path.normpath(str(photo)))]
