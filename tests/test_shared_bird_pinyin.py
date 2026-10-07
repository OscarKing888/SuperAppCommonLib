"""共享带声调词表、别名、声调和缓存。"""
from concurrent.futures import ThreadPoolExecutor
from app_common import bird_pinyin as pinyin


def test_table_and_known_bird_readings():
    assert len(pinyin._load_table()) == 11388
    for name, text in {'白头鹎': 'bái tóu bēi', '中杓鹬': 'zhōng sháo yù',
                       '秘鲁企鹅': 'bì lǔ qǐ é', '黑喉小䴙䴘': 'hēi hóu xiǎo pì tī'}.items():
        assert pinyin.pinyin_for(name) == text
    assert pinyin.pinyin_for('未知鸟名') == pinyin.pinyin_for(None) == ''


def test_table_loaded_once_across_threads(monkeypatch):
    pinyin.reset_cache()
    calls = []
    def load():
        calls.append(1)
        return {'家燕': 'jiā yàn'}
    monkeypatch.setattr(pinyin, '_load_table', load)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(pinyin.pinyin_for, [' 家燕 '] * 40)) == ['jiā yàn'] * 40
        assert calls == [1]
    finally:
        pinyin.reset_cache()


def test_stored_fields_prefer_new_xmp_name_and_reject_old_species():
    meta = {'bird_species_cn': '白头鹎', 'bird_species_pinyin': '旧拼音',
            'XMP-superpicky:pinyin_name': 'bái tóu bēi'}
    assert pinyin.stored_pinyin(meta) == 'bái tóu bēi'
    meta['pinyin_name_source'] = '家燕'
    assert pinyin.stored_pinyin(meta) == ''
    assert pinyin.bird_name({'XMP-dc:Title': {'x-default': '家燕'}, 'bird_species_cn': '旧鸟名'}) == '家燕'


def test_browser_reload_retains_saved_pinyin_and_source(tmp_path):
    from app_common.exif_io.photo_meta import PhotoMetaDataXMP
    from app_common.file_browser._workers import MetadataLoader
    path = str(tmp_path / '中文.jpg')
    assert PhotoMetaDataXMP().write(path, {'XMP-dc:Title': '白头鹎',
        'XMP-superpicky:pinyin_name': 'bái tóu bēi', 'XMP-superpicky:pinyin_name_source': '白头鹎'})
    rec = PhotoMetaDataXMP().read(path)
    loader = MetadataLoader([path], meta_proxy=object())
    parsed = loader._parse_rec(rec)
    assert parsed['pinyin_name'] == 'bái tóu bēi'
    assert parsed['pinyin_name_source'] == '白头鹎'
    assert pinyin.stored_pinyin(parsed) == 'bái tóu bēi'
