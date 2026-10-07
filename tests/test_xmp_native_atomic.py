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
