"""拍摄地点与 GPS/原图相互独立，中文写入和清空必须真实往返。"""
from pathlib import Path

import pytest
from PIL import Image
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._workers import MetadataLoader
from app_common.shooting_location import LOCATION_TAG, shooting_location, write_shooting_location


@pytest.mark.parametrize('extension', ['.jpg', '.ARW'])
def test_location_round_trip_and_clear_preserves_gps_and_source(tmp_path, extension):
    photo = tmp_path / ('白鹭' + extension)
    if extension == '.jpg':
        Image.new('RGB', (16, 12)).save(photo)
    else:
        photo.write_bytes(b'raw source must never be rewritten')
    original = photo.read_bytes()
    xmp = photo.with_suffix('.xmp')
    xmp.write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/"
 xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
 exif:GPSLatitude="31,13.2N" exif:GPSLongitude="121,28.8E" exif:GPSAltitude="5/1">
<photoshop:City>650.00</photoshop:City></rdf:Description></rdf:RDF></x:xmpmeta>''', encoding='utf-8')
    store = PhotoMetaDataXMP()
    before = store.read(str(photo))
    loader = MetadataLoader([], None)
    try:
        for value in ['上海·崇明东滩（保护区） & 湿地', '云南 高黎贡山', '']:
            updates = write_shooting_location(str(photo), value)
            rec = store.read(str(photo))
            assert shooting_location(rec) == value
            assert shooting_location(loader._parse_rec(rec)) == value
            assert shooting_location(updates) == value
            assert photo.read_bytes() == original
            assert {k: v for k, v in rec.items() if 'GPS' in k} == {k: v for k, v in before.items() if 'GPS' in k}
            assert rec['XMP-photoshop:City'] == before['XMP-photoshop:City']
    finally:
        loader.deleteLater()


def test_corrupt_sidecar_and_missing_photo_are_not_overwritten(tmp_path):
    photo = tmp_path / 'a.jpg'
    Image.new('RGB', (4, 4)).save(photo)
    sidecar = photo.with_suffix('.xmp')
    sidecar.write_bytes(b'<broken>')
    with pytest.raises(OSError):
        write_shooting_location(str(photo), '北京')
    assert sidecar.read_bytes() == b'<broken>'
    missing = tmp_path / 'missing.jpg'
    with pytest.raises(ValueError):
        write_shooting_location(str(missing), '北京')
    assert not missing.with_suffix('.xmp').exists()


def test_explicit_empty_location_never_uses_gps_or_old_cache():
    assert shooting_location({LOCATION_TAG: '', 'shooting_location': '旧地点', 'GPSLatitude': '31'}) == ''
    assert shooting_location({'GPSLatitude': '31', 'XMP-photoshop:City': '650.00'}) == ''
