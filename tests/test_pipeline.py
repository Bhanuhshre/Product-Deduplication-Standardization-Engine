"""
test_pipeline.py

Unit tests for the core pipeline modules. Run with:
    pytest tests/test_pipeline.py -v

These focus on the normalization, standardization, and matching logic
in isolation rather than the full Excel-to-Excel run, since that logic
is what actually determines correctness of the dedup results.
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from normalizer import (
    clean_whitespace,
    extract_size_and_unit,
    strip_size_tokens,
    build_normalized_name,
    to_base_unit,
)
from standardizer import resolve_brand, infer_category, build_standard_key
from matcher import find_duplicates, UnionFind


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "rules.json")


@pytest.fixture(scope="module")
def config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------
# normalizer
# ---------------------------------------------------------------------

def test_clean_whitespace_collapses_and_trims():
    assert clean_whitespace("  Amul   Milk -- 500g  ") == "Amul Milk 500g"


def test_clean_whitespace_handles_none():
    assert clean_whitespace(None) == ""


def test_extract_size_and_unit_basic(config):
    value, unit = extract_size_and_unit("Amul Toned Milk 500ml", config["unit_normalization"])
    assert value == 500
    assert unit == "ml"


def test_extract_size_and_unit_decimal(config):
    value, unit = extract_size_and_unit("Refined Oil 1.5 Litre", config["unit_normalization"])
    assert value == 1.5
    assert unit == "l"


def test_extract_size_and_unit_no_match(config):
    value, unit = extract_size_and_unit("Assorted Snack Box", config["unit_normalization"])
    assert value is None
    assert unit is None


def test_strip_size_tokens_removes_quantity():
    result = strip_size_tokens("Britannia Cream Biscuit 200gm")
    assert "200" not in result
    assert "gm" not in result.lower()
    assert "Biscuit" in result


def test_build_normalized_name_removes_stopwords(config):
    result = build_normalized_name(
        "Fresh Amul Butter 100g - Premium", config["unit_normalization"], set(config["stopwords"])
    )
    assert "fresh" not in result["normalized_name"]
    assert "premium" not in result["normalized_name"]
    assert result["size_value"] == 100
    assert result["size_unit"] == "g"


def test_to_base_unit_kg_to_g(config):
    value, unit = to_base_unit(1, "kg", config["unit_conversion_to_base"])
    assert value == 1000
    assert unit == "g"


def test_to_base_unit_unknown_unit_passthrough(config):
    value, unit = to_base_unit(5, "dozen", config["unit_conversion_to_base"])
    assert value == 5
    assert unit == "dozen"


# ---------------------------------------------------------------------
# standardizer
# ---------------------------------------------------------------------

def test_resolve_brand_known_alias(config):
    brand, mapped = resolve_brand("coca cola", config["brand_aliases"])
    assert brand == "Coca-Cola"
    assert mapped is True


def test_resolve_brand_case_insensitive_alias(config):
    brand, mapped = resolve_brand("COCA-COLA", config["brand_aliases"])
    assert brand == "Coca-Cola"
    assert mapped is True


def test_resolve_brand_unknown_falls_back_to_title_case(config):
    brand, mapped = resolve_brand("some random brand", config["brand_aliases"])
    assert brand == "Some Random Brand"
    assert mapped is False


def test_resolve_brand_empty_returns_none(config):
    brand, mapped = resolve_brand("", config["brand_aliases"])
    assert brand is None
    assert mapped is False


def test_infer_category_from_keywords(config):
    category, mismatch = infer_category("toned milk", config["category_keywords"])
    assert category == "Dairy"
    assert mismatch is False


def test_infer_category_respects_existing_value(config):
    category, mismatch = infer_category("toned milk", config["category_keywords"], existing_category="Dairy")
    assert category == "Dairy"
    assert mismatch is False


def test_infer_category_flags_conflict(config):
    category, mismatch = infer_category("toned milk", config["category_keywords"], existing_category="Snacks")
    assert category == "Snacks"
    assert mismatch is True


def test_build_standard_key_is_deterministic():
    key1 = build_standard_key("Amul", "toned milk", 500.0, "ml")
    key2 = build_standard_key("Amul", "toned milk", 500.0, "ml")
    assert key1 == key2


def test_build_standard_key_differs_on_size():
    key1 = build_standard_key("Amul", "toned milk", 500.0, "ml")
    key2 = build_standard_key("Amul", "toned milk", 1000.0, "ml")
    assert key1 != key2


# ---------------------------------------------------------------------
# matcher
# ---------------------------------------------------------------------

def test_union_find_groups_transitively():
    uf = UnionFind(["a", "b", "c", "d"])
    uf.union("a", "b")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("c")
    assert uf.find("a") != uf.find("d")


def test_find_duplicates_groups_near_identical_names():
    records = [
        {"id": "1", "name": "amul toned milk", "brand": "Amul", "base_size_value": 500.0, "base_size_unit": "ml"},
        {"id": "2", "name": "amul toned milk", "brand": "Amul", "base_size_value": 500.0, "base_size_unit": "ml"},
        {"id": "3", "name": "britannia biscuit", "brand": "Britannia", "base_size_value": 200.0, "base_size_unit": "g"},
    ]
    clusters, pairs = find_duplicates(
        records, id_field="id", name_field="name", block_field="brand",
        auto_merge_threshold=95, review_low=80, review_high=95,
    )
    assert len(clusters) == 1
    cluster_members = list(clusters.values())[0]
    assert set(cluster_members) == {"1", "2"}


def test_find_duplicates_respects_size_mismatch():
    records = [
        {"id": "1", "name": "amul toned milk", "brand": "Amul", "base_size_value": 500.0, "base_size_unit": "ml"},
        {"id": "2", "name": "amul toned milk", "brand": "Amul", "base_size_value": 1000.0, "base_size_unit": "ml"},
    ]
    clusters, pairs = find_duplicates(
        records, id_field="id", name_field="name", block_field="brand",
        auto_merge_threshold=95, review_low=80, review_high=95,
    )
    assert len(clusters) == 0


def test_find_duplicates_does_not_cross_brand_blocks():
    records = [
        {"id": "1", "name": "toned milk", "brand": "Amul", "base_size_value": 500.0, "base_size_unit": "ml"},
        {"id": "2", "name": "toned milk", "brand": "Nestle", "base_size_value": 500.0, "base_size_unit": "ml"},
    ]
    clusters, pairs = find_duplicates(
        records, id_field="id", name_field="name", block_field="brand",
        auto_merge_threshold=95, review_low=80, review_high=95,
    )
    assert len(clusters) == 0
