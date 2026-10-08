"""Shared badge mappings keep persisted options, aliases and stale metadata coherent."""
import pytest

from app_common.metadata_badges import (
    badge_kind_for_field, metadata_badge_style, metadata_badge_value,
    normalize_metadata_badge_options,
)
from app_common.superviewer_user_options import normalize_user_options


@pytest.mark.parametrize("code,background,foreground", [
    ("LC", "#68BB53", "#2E261F"), ("NT", "#D1E744", "#2E261F"),
    ("VU", "#E5D42D", "#2E261F"), ("EN", "#E77B4D", "#2E261F"),
    ("CR", "#B61917", "#FFFFFF"), ("CR(PE)", "#B61917", "#FFFFFF"),
    ("CR(PEW)", "#B61917", "#FFFFFF"), ("EW", "#542344", "#FFFFFF"),
    ("EX", "#000000", "#FFFFFF"), ("DD", "#595959", "#FFFFFF"),
    ("NE", "#CCCCCC", "#2E261F"),
])
def test_cornell_conservation_palette_and_case(code, background, foreground):
    text, bg, fg = metadata_badge_style("iucn", f" {code.lower()} ")
    assert text.startswith(code + " · ")
    assert (bg, fg) == (background, foreground)


def test_config_validation_preserves_old_rarity_and_custom_iucn():
    opts = normalize_user_options({
        "rarity_badge_epic_background": "#123abc", "iucn_badge_en_text": "濒危物种",
        "iucn_badge_en_background": "#abc123", "iucn_badge_en_foreground": "invalid",
        "iucn_badge_lc_text": "bad\nlabel",
    })
    assert metadata_badge_style("rarity", 50, opts)[1] == "#123ABC"
    assert metadata_badge_style("iucn", "EN", opts) == ("濒危物种", "#ABC123", "#2E261F")
    assert metadata_badge_style("iucn", "LC", opts)[0] == "LC · 无危"
    assert normalize_metadata_badge_options(opts) == normalize_metadata_badge_options(
        normalize_user_options(opts))


@pytest.mark.parametrize("field", ["iucn_category", "report.iucn_category", "XMP-superpicky:iucn_category",
                                    "XMP-iptcCore:IntellectualGenre", "XMP-Iptc4xmpCore:IntellectualGenre"])
def test_aliases_unknown_and_stale_species(field):
    assert badge_kind_for_field("{" + field + "}") == "iucn"
    assert metadata_badge_value("iucn", {field: "EN"}) == "EN"
    stale = {field: "EN", "title": "苍鹭", "birdid_rarity_source": "白鹭"}
    assert metadata_badge_style("iucn", metadata_badge_value("iucn", stale))[0] == "未知"
    assert metadata_badge_style("iucn", "invalid")[0] == "未知"
    assert metadata_badge_style("iucn", None)[0] == "未知"
    assert badge_kind_for_field("iso") is None
