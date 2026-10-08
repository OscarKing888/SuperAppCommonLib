"""Bird-name copy snapshots and atomic XMP paste fields, independent of Qt."""
from __future__ import annotations

from .bird_pinyin import (
    PINYIN_ALIASES, PINYIN_FIELD, PINYIN_SOURCE_FIELD, bird_name, pinyin_for, stored_pinyin,
)
from .bird_rarity import (
    IUCN_FIELD, RARITY_COMPAT_FIELDS, RARITY_FIELD, RARITY_MISSING_FIELD,
    RARITY_SOURCE_FIELD, rarity_metadata,
)
from .shooting_location import LOCATION_FIELD, shooting_location


def _has_field(metadata: dict, field: str) -> bool:
    return any(prefix + field in metadata for prefix in ("XMP-superpicky:", "", "report."))


def _field(metadata: dict, field: str):
    return next((metadata[prefix + field] for prefix in ("XMP-superpicky:", "", "report.")
                 if prefix + field in metadata), "")


def species_snapshot(*sources: dict) -> dict:
    """Sources are ordered report/cache/XMP; never borrow another species' data."""
    name = next((bird_name(source) for source in reversed(sources) if bird_name(source)), "")
    result = {"bird_species_cn": name, "bird_species_en": "", PINYIN_FIELD: "",
              RARITY_FIELD: "", IUCN_FIELD: "", LOCATION_FIELD: ""}
    for source in sources:
        if _has_field(source, LOCATION_FIELD):
            result[LOCATION_FIELD] = shooting_location(source)
        source_name = bird_name(source)
        if source_name and source_name != name:
            continue
        if _has_field(source, "bird_species_en"):
            result["bird_species_en"] = str(_field(source, "bird_species_en") or "").strip()
        if any(_has_field(source, key) for key in (*PINYIN_ALIASES, PINYIN_SOURCE_FIELD)):
            result[PINYIN_FIELD] = stored_pinyin(source, name)
        if any(_has_field(source, key) for key in (RARITY_FIELD, IUCN_FIELD, RARITY_SOURCE_FIELD)) or any(
            key in source for key in (
                *RARITY_COMPAT_FIELDS.values(), "XMP-Iptc4xmpExt:Event", "XMP-Iptc4xmpCore:IntellectualGenre",
            )
        ):
            score, category = rarity_metadata({"bird_species_cn": name, **source})
            result[RARITY_FIELD] = score if score is not None else ""
            result[IUCN_FIELD] = category
    return result


def species_paste_fields(payload: dict) -> tuple[dict, dict]:
    """Return one XMP transaction and matching cache aliases; preserve numeric zero."""
    cn = str(payload.get("bird_species_cn") or "").strip()
    en = str(payload.get("bird_species_en") or "").strip()
    title = cn or en
    if not title:
        return {}, {}
    score, category = rarity_metadata(payload)
    values = {
        **{key: "" for key in PINYIN_ALIASES},
        "bird_species_cn": title, "bird_species_en": en, "title": title,
        PINYIN_FIELD: stored_pinyin(payload, title) or pinyin_for(cn),
        PINYIN_SOURCE_FIELD: title,
        RARITY_FIELD: score if score is not None else "", IUCN_FIELD: category,
        RARITY_SOURCE_FIELD: title,
        RARITY_MISSING_FIELD: ",".join(key for key, missing in (
            (RARITY_FIELD, score is None), (IUCN_FIELD, not category),
        ) if missing),
    }
    if LOCATION_FIELD in payload:
        values[LOCATION_FIELD] = shooting_location(payload)
    fields = {f"XMP-superpicky:{key}": value for key, value in values.items()}
    fields.update({
        "XMP-dc:Title": title,
        RARITY_COMPAT_FIELDS[RARITY_FIELD]: f"{score:.2f}" if score is not None else "",
        RARITY_COMPAT_FIELDS[IUCN_FIELD]: category,
    })
    updates = {**fields, **values, **{f"report.{key}": value for key, value in values.items()}, "Title": title}
    return fields, updates
