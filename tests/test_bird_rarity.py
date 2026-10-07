"""稀有度边界、0/null、旧侧车和用户配置。"""
import pytest
from app_common.bird_rarity import RARITY_DEFAULT_OPTIONS, rarity_level, rarity_metadata
from app_common.superviewer_user_options import normalize_user_options, save_user_options, load_user_options


@pytest.mark.parametrize('score,level', [(0,'common'), (7.999,'common'), (8,'uncommon'), (24.999,'uncommon'),
    (25,'rare'), (49.999,'rare'), (50,'epic'), (74.999,'epic'), (75,'legendary'), (100,'legendary'),
    (None,'unknown'), ('','unknown'), (True,'unknown'), (-1,'unknown'), (101,'unknown'),
    (float('nan'),'unknown'), (float('inf'),'unknown')])
def test_score_boundaries(score, level):
    assert rarity_level(score) == level


def test_legacy_sidecar_zero_and_report_priority():
    assert rarity_metadata({'XMP-iptcExt:Event': ['0.00'], 'XMP-iptcCore:IntellectualGenre': 'NT',
                            'report.gbif_rarity_100': 99}) == (0, 'NT')
    assert rarity_metadata({'XMP-superpicky:gbif_rarity_100': '75', 'gbif_rarity_100': 1}) == (75, '')
    assert rarity_metadata({'report.gbif_rarity_100': 50, 'report.iucn_category': 'LC'}) == (50, 'LC')


def test_null_or_changed_species_never_revives_old_report():
    meta = {'bird_species_cn': '白头鹎', 'XMP-superpicky:birdid_rarity_source': '白头鹎',
            'gbif_rarity_100': 99, 'report.gbif_rarity_100': 99, 'iucn_category': 'CR'}
    assert rarity_metadata(meta) == (None, '')
    meta['XMP-superpicky:gbif_rarity_100'] = 0
    assert rarity_metadata(meta) == (0, '')
    meta['bird_species_cn'] = '家燕'
    assert rarity_metadata(meta) == (None, '')


def test_badge_config_roundtrip_chinese_and_invalid_fallback(tmp_path):
    options = normalize_user_options({'rarity_badge_epic_text': '珍稀传说',
        'rarity_badge_epic_background': '#abcdef', 'rarity_badge_epic_foreground': '#102030',
        'rarity_badge_common_text': '', 'rarity_badge_rare_background': 'red; border: none',
        'rarity_badge_legendary_text': 'a'*33})
    assert options['rarity_badge_epic_background'] == '#ABCDEF'
    for key in ('rarity_badge_common_text', 'rarity_badge_rare_background', 'rarity_badge_legendary_text'):
        assert options[key] == RARITY_DEFAULT_OPTIONS[key]
    path = str(tmp_path / '中文配置.cfg')
    assert save_user_options(options, path) == load_user_options(path)
    assert load_user_options(path)['rarity_badge_epic_text'] == '珍稀传说'
    for key, value in RARITY_DEFAULT_OPTIONS.items():
        if key.endswith('_foreground'): assert value == '#FFFFFF'
