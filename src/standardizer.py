"""
standardizer.py

Applies the business rules in config/rules.json on top of the
normalizer output: resolves brand aliases to a canonical brand name,
converts sizes to a common base unit per unit family, and assigns a
category when one is missing or inconsistent, based on keyword hits
in the product name.
"""

from normalizer import normalize_case, to_base_unit


def resolve_brand(brand_raw, brand_aliases):
    """Map a raw brand string to its canonical form. Falls back to a
    title-cased version of the input if no alias rule matches, rather
    than dropping the record -- unmapped brands still get a consistent
    display format and are easy to spot in the exception report.

    A raw value that already matches a canonical brand name
    case-insensitively (e.g. 'AMUL' when 'Amul' is a known canonical
    form) is treated as mapped, since it needed no alias resolution to
    be correct."""
    if not brand_raw or not str(brand_raw).strip():
        return None, False
    key = normalize_case(brand_raw)
    if key in brand_aliases:
        return brand_aliases[key], True

    canonical_values = {v.lower(): v for v in brand_aliases.values()}
    if key in canonical_values:
        return canonical_values[key], True

    return str(brand_raw).strip().title(), False


def infer_category(normalized_name, category_keywords, existing_category=None):
    """Assign a category by keyword match against the normalized product
    name. An existing category value is trusted if provided and already
    a known category; otherwise inference runs and disagreement is
    flagged by the caller via the returned flag."""
    inferred = None
    for category, keywords in category_keywords.items():
        for kw in keywords:
            if kw in normalized_name:
                inferred = category
                break
        if inferred:
            break

    if existing_category and str(existing_category).strip():
        existing = str(existing_category).strip()
        mismatch = inferred is not None and inferred != existing
        return existing, mismatch
    return inferred, False


def standardize_size(size_value, size_unit, conversion_map):
    base_value, base_unit = to_base_unit(size_value, size_unit, conversion_map)
    return base_value, base_unit


def build_standard_key(brand_std, normalized_name, base_value, base_unit):
    """Deterministic key used for exact-duplicate detection prior to
    fuzzy matching: same brand, same descriptive name, same size in the
    same base unit -> almost certainly the same catalog item."""
    brand_part = (brand_std or "unknown").lower()
    name_part = (normalized_name or "").lower()
    size_part = f"{base_value}_{base_unit}" if base_value is not None else "nosize"
    return f"{brand_part}|{name_part}|{size_part}"
