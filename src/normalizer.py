"""
normalizer.py

Text and unit normalization utilities used before matching and
standardization. Kept deliberately separate from standardizer.py:
normalization is lossless cleanup (case, whitespace, punctuation),
while standardization applies business rules (brand aliases, category
assignment) on top of normalized text.
"""

import re


UNIT_TOKEN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kg|kgs|kilograms?|g|gm|gms|grams?|grm|"
    r"ml|mls|milliliters?|millilitre|l|ltr|ltrs|litres?|liters?|"
    r"pc|pcs|pieces?|pack|pk|packs)\b",
    flags=re.IGNORECASE,
)

MULTI_SPACE_RE = re.compile(r"\s+")
PUNCT_EDGES_RE = re.compile(r"^[\s\-,.]+|[\s\-,.]+$")
INNER_NOISE_RE = re.compile(r"\s*--\s*|\s*,\s*|\s*-\s*")


def clean_whitespace(text):
    if text is None:
        return ""
    text = str(text)
    text = INNER_NOISE_RE.sub(" ", text)
    text = MULTI_SPACE_RE.sub(" ", text)
    text = PUNCT_EDGES_RE.sub("", text)
    return text.strip()


def normalize_case(text):
    return clean_whitespace(text).lower()


def extract_size_and_unit(text, unit_map):
    """
    Pull the first quantity + unit token out of a free-text product name,
    e.g. '500gm' or '1.5 Litre'. Returns (value, normalized_unit) or
    (None, None) if nothing is found.
    """
    if not text:
        return None, None
    match = UNIT_TOKEN_RE.search(text)
    if not match:
        return None, None
    value_raw, unit_raw = match.groups()
    unit_key = unit_raw.lower()
    normalized_unit = unit_map.get(unit_key, unit_key)
    try:
        value = float(value_raw)
    except ValueError:
        return None, None
    return value, normalized_unit


def strip_size_tokens(text):
    """Remove quantity+unit substrings from a name so name-matching
    focuses on the descriptive part of the product, not the pack size."""
    if not text:
        return ""
    return clean_whitespace(UNIT_TOKEN_RE.sub(" ", text))


def remove_stopwords(text, stopwords):
    if not text:
        return ""
    tokens = [t for t in text.split(" ") if t and t.lower() not in stopwords]
    return " ".join(tokens)


def normalize_unit_token(unit_raw, unit_map):
    if unit_raw is None:
        return None
    key = str(unit_raw).strip().lower()
    return unit_map.get(key, key)


def to_base_unit(value, unit, conversion_map):
    """Convert a (value, unit) pair to a common base unit for the unit
    family (grams for weight, millilitres for volume) so a 1kg pack and
    a 1000g pack compare equal."""
    if value is None or unit is None:
        return None, None
    rule = conversion_map.get(unit)
    if not rule:
        return value, unit
    return value * rule["factor"], rule["base"]


def build_normalized_name(raw_name, unit_map, stopwords):
    """Full normalization pipeline for a raw product name string.
    Returns the cleaned descriptive name (size tokens and stopwords
    removed) plus the extracted size/unit, all in one pass so callers
    don't re-run regexes."""
    cleaned = clean_whitespace(raw_name)
    value, unit = extract_size_and_unit(cleaned, unit_map)
    descriptive = strip_size_tokens(cleaned)
    descriptive = normalize_case(descriptive)
    descriptive = remove_stopwords(descriptive, stopwords)
    return {
        "normalized_name": descriptive,
        "size_value": value,
        "size_unit": unit,
    }
