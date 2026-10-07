"""标准原生字段和自定义字段必须作为一次 XMP 编辑发布。"""
from app_common.exif_io.photo_meta import PhotoMetaDataXMP


def test_native_fields_published_once_and_preserved_on_failure(tmp_path, monkeypatch):
    photo = tmp_path / '翠鸟.jpg'
    photo.write_bytes(b'original')
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '旧标题', 'XMP-dc:Description': '中文说明'})
    sidecar = photo.with_suffix('.xmp')
    before = sidecar.read_bytes()
    calls = []
    def fail(tree, path):
        calls.append(path)
        raise OSError('磁盘写入失败')
    monkeypatch.setattr(PhotoMetaDataXMP, '_write_tree_atomic', staticmethod(fail))
    assert not store.write(str(photo), {'XMP-dc:Title': '新标题', 'XMP-superpicky:bird_species_cn': '翠鸟', 'XMP-xmp:Rating': 5})
    assert calls == [sidecar]
    assert sidecar.read_bytes() == before
    assert photo.read_bytes() == b'original'


def test_mixed_compatibility_and_species_write_hydrates_report_before_new_title(tmp_path):
    from PIL import Image
    from app_common.report_db import ReportDB
    photo = tmp_path / '中文鸟片.jpg'
    Image.new('RGB', (16, 16)).save(photo)
    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo({'filename': photo.stem, 'original_path': str(photo), 'current_path': str(photo),
                         'caption': '保留报告说明', 'rating': 0, 'bird_species_cn': '家燕'})
    finally:
        db.close()
    database = tmp_path / '.superpicky/report.db'
    before = database.read_bytes()
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '白头鹎', 'XMP-superpicky:bird_species_cn': '白头鹎',
                                   'XMP-superpicky:gbif_rarity_100': 0, 'XMP-iptcExt:Event': '0.00'})
    values = store.read(str(photo))
    assert values['Title'] == '白头鹎'
    assert values['Description'] == '保留报告说明'
    assert values['rating'] == 0 and float(values['gbif_rarity_100']) == 0
    assert database.read_bytes() == before
