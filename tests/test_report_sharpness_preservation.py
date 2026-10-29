"""Report sharpness stays independent of bird clarity, including legacy sidecars."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import _batch_read_xmp_sidecar
from app_common.file_browser._browser_core import _metadata_sharpness_text
from app_common.report_db import ReportDB

SP = 'https://superbirdtools.local/xmp/superpicky/1.0/'
PS = 'http://ns.adobe.com/photoshop/1.0/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
CITY = 'XMP-photoshop:City'


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / '中文白鹭.ARW'
    path.write_bytes(b'original camera raw bytes')
    return path


def report(photo, sharpness):
    db = ReportDB(str(photo.parent))
    try:
        db.insert_photo({'filename': photo.stem, 'original_path': str(photo),
                         'bird_species_cn': '报告白鹭', 'adj_sharpness': sharpness,
                         'confidence': 0.8})
    finally:
        db.close()
    return photo.parent / '.superpicky' / 'report.db'


def sidecar(photo, city=None, archive=None, score='203', storage=None, raw_city=None, version='sbt-blur-v19-jpeg'):
    root = ET.Element('{adobe:ns:meta/}xmpmeta')
    desc = ET.SubElement(ET.SubElement(root, f'{{{RDF}}}RDF'), f'{{{RDF}}}Description', {f'{{{RDF}}}about': ''})
    fields = {'bird_species_cn': '手工鸟名', 'bird_sharpness_score': score,
              'bird_sharpness_version': version, 'adj_sharpness': archive,
              'city': raw_city, 'bird_sharpness_storage_version': storage}
    for key, value in fields.items():
        if value is not None:
            ET.SubElement(desc, f'{{{SP}}}{key}').text = str(value)
    if city is not None:
        desc.set(f'{{{PS}}}City', str(city))  # Attribute properties are valid too.
    ET.ElementTree(root).write(photo.with_suffix('.xmp'), encoding='utf-8', xml_declaration=True)


def detection(score='577'):
    return {'XMP-superpicky:bird_sharpness_score': score,
            'XMP-superpicky:bird_sharpness_version': 'sbt-blur-v19-jpeg'}


@pytest.mark.parametrize('sharpness', [0, 812.5])
@pytest.mark.parametrize('existing', [False, True])
def test_detection_hydrates_report_score_even_with_species_marker(photo, sharpness, existing):
    database = report(photo, sharpness)
    original, original_db = photo.read_bytes(), database.read_bytes()
    if existing:
        sidecar(photo, score=None, version=None)
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), detection())
    record = writer.read(str(photo))
    assert float(record[CITY]) == float(record['adj_sharpness']) == sharpness
    assert record['bird_sharpness_score'] == '577'
    assert record['bird_sharpness_storage_version'] == '2'
    if existing:
        assert record['bird_species_cn'] == '手工鸟名'
        assert 'confidence' not in record  # The rest still observes the species gate.
    flat = _batch_read_xmp_sidecar([str(photo)])[str(photo)]
    assert _metadata_sharpness_text(flat) == str(sharpness).removesuffix('.0')
    assert photo.read_bytes() == original and database.read_bytes() == original_db


@pytest.mark.parametrize('api', ['ensure', 'write', 'superpicky'])
def test_legacy_detection_cannot_poison_city_or_archived_report_score(photo, api):
    database = report(photo, 812.5)
    before_db = database.read_bytes()
    sidecar(photo, city='203.00', archive='203', raw_city='203')
    writer = PhotoMetaDataXMP()
    if api == 'ensure':
        assert writer.ensure_report_sharpness(str(photo))
    elif api == 'write':
        assert writer.write(str(photo), detection())
    else:
        assert writer.write_superpicky_fields(str(photo), {key.split(':', 1)[1]: v for key, v in detection().items()})
    result = writer.read(str(photo))
    for key in (CITY, 'adj_sharpness', 'city'):
        assert float(result[key]) == 812.5
    assert result['bird_sharpness_score'] == ('203' if api == 'ensure' else '577')
    assert result['bird_species_cn'] == '手工鸟名'
    assert database.read_bytes() == before_db
    original = photo.with_suffix('.xmp').read_bytes()
    assert writer.ensure_report_sharpness(str(photo))
    assert photo.with_suffix('.xmp').read_bytes() == original  # No repeated publication.


def test_legacy_without_report_uses_trusted_archive_and_preserves_other_resource(photo):
    sidecar(photo, city=203, archive=700)
    xmp = photo.with_suffix('.xmp')
    tree = ET.parse(xmp)
    rdf = tree.getroot().find(f'{{{RDF}}}RDF')
    ET.SubElement(rdf, f'{{{RDF}}}Description', {f'{{{RDF}}}about': 'https://example.org/other', f'{{{PS}}}City': '203'})
    tree.write(xmp, encoding='utf-8')
    writer = PhotoMetaDataXMP()
    assert writer.ensure_report_sharpness(str(photo))
    assert float(writer.read(str(photo))[CITY]) == 700
    other = ET.parse(xmp).find(f'.//{{{RDF}}}Description[@{{{RDF}}}about="https://example.org/other"]')
    assert other.get(f'{{{PS}}}City') == '203'


def test_legacy_without_any_original_score_clears_pollution_and_later_hydrates_report(photo):
    sidecar(photo, city=203, archive=203, raw_city=203)
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), detection())
    result = writer.read(str(photo))
    assert all(key not in result for key in (CITY, 'adj_sharpness', 'city'))
    assert 'metadata_cleared_fields' not in result
    assert result['bird_sharpness_score'] == '577'
    report(photo, 0)
    assert writer.ensure_report_sharpness(str(photo))
    result = writer.read(str(photo))
    assert float(result[CITY]) == float(result['adj_sharpness']) == 0


def test_custom_city_and_new_storage_equal_to_clarity_remain_unchanged(photo):
    report(photo, 812.5)
    sidecar(photo, city='650.5', archive='203')
    writer = PhotoMetaDataXMP()
    assert writer.ensure_report_sharpness(str(photo))
    result = writer.read(str(photo))
    assert result[CITY] == '650.5' and float(result['adj_sharpness']) == 812.5
    # A known new storage record may legitimately have a report score equal to clarity.
    sidecar(photo, city='203', archive='203', storage='2')
    assert writer.ensure_report_sharpness(str(photo))
    result = writer.read(str(photo))
    assert result[CITY] == result['adj_sharpness'] == '203'


def test_first_new_detection_marks_existing_equal_city_as_separate(photo):
    sidecar(photo, city='577', archive='577', score=None, version=None)
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), detection())
    report(photo, 888)
    assert writer.ensure_report_sharpness(str(photo))
    assert writer.read(str(photo))[CITY] == '577'


def test_user_clear_markers_and_current_city_edit_have_priority(photo):
    report(photo, 812.5)
    sidecar(photo, city='203', archive='203')
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), {CITY: '203'})  # Explicit edit even though equal to old clarity.
    assert writer.ensure_report_sharpness(str(photo))
    assert writer.read(str(photo))[CITY] == '203'
    assert writer.write(str(photo), {CITY: ''})
    assert writer.write(str(photo), detection())
    assert writer.ensure_report_sharpness(str(photo))
    record = writer.read(str(photo))
    assert record[CITY] == ''
    assert CITY in json.loads(record['metadata_cleared_fields'])


def test_ensure_is_atomic_and_no_source_does_not_create_empty_sidecar(photo, monkeypatch):
    writer = PhotoMetaDataXMP()
    assert writer.ensure_report_sharpness(str(photo))
    assert not photo.with_suffix('.xmp').exists()
    sidecar(photo, city=203)
    before = photo.with_suffix('.xmp').read_bytes()
    monkeypatch.setattr(writer, '_write_tree_atomic', lambda *_: False)
    assert not writer.ensure_report_sharpness(str(photo))
    assert photo.with_suffix('.xmp').read_bytes() == before
    assert photo.read_bytes() == b'original camera raw bytes'


def test_corrupt_sidecar_is_preserved(photo):
    xmp = photo.with_suffix('.xmp')
    xmp.write_bytes(b'<invalid')
    assert not PhotoMetaDataXMP().ensure_report_sharpness(str(photo))
    assert xmp.read_bytes() == b'<invalid'


@pytest.mark.parametrize('storage', ['3', 'future-format'])
def test_future_storage_is_not_migrated_or_downgraded(photo, storage):
    report(photo, 900)
    sidecar(photo, city='203', archive='203', storage=storage)
    writer = PhotoMetaDataXMP()
    assert writer.ensure_report_sharpness(str(photo))
    assert writer.write(str(photo), detection())
    record = writer.read(str(photo))
    assert record[CITY] == record['adj_sharpness'] == '203'
    assert record['bird_sharpness_storage_version'] == storage


def test_city_clear_hides_all_browser_and_proxy_fallbacks_without_deleting_archive(photo):
    from app_common.exif_io.photo_meta import PhotoMetaDataProxy, PhotoMetaDataReportDB
    from app_common.exif_io.metadata_store import apply_fields

    report(photo, 812.5)
    sidecar(photo, city='812.5', archive='812.5', storage='2', raw_city='812.5')
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), {CITY: ''})
    assert writer.ensure_report_sharpness(str(photo))
    direct = writer.read(str(photo))
    flat = _batch_read_xmp_sidecar([str(photo)])[str(photo)]
    class Embedded:
        def read(self, path):
            return {'IPTC:City': '800', 'head_sharp': '790'}
    merged = PhotoMetaDataProxy(exif=Embedded(), xmp=writer,
                               report_db=PhotoMetaDataReportDB()).read(str(photo))
    pending = apply_fields({CITY: 900, 'adj_sharpness': 800, 'head_sharp': 790}, {CITY: ''})
    for record in (direct, flat, merged, pending):
        assert _metadata_sharpness_text(record) == ''
        assert record['adj_sharpness'] == record['head_sharp'] == ''
    assert ET.parse(photo.with_suffix('.xmp')).find(f'.//{{{SP}}}adj_sharpness').text == '812.5'
    # Setting a new explicit value makes it visible while the original report archive remains intact.
    assert writer.write(str(photo), {CITY: '701.5'})
    assert _metadata_sharpness_text(writer.read(str(photo))) == '701.5'
    assert writer.read(str(photo))['adj_sharpness'] == '812.5'
