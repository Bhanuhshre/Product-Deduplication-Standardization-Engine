"""
main.py

Entry point for the product deduplication and standardization pipeline.

Usage:
    python src/main.py
    python src/main.py --input data/sample_products.xlsx --config config/rules.json

Pipeline stages:
    1. Load raw catalog rows from Excel.
    2. Normalize free-text names, extract size/unit tokens.
    3. Standardize brand names and categories against config/rules.json,
       convert sizes to a common base unit.
    4. Detect duplicates: exact-key blocking, then fuzzy matching
       within brand blocks for near-duplicates.
    5. Assemble the exception report (ambiguous matches, missing
       fields, unmapped brands, category conflicts).
    6. Write outputs/standardized_products.xlsx and
       outputs/exception_report.xlsx, and print a metrics summary.
"""

import argparse
import json
import time
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from normalizer import build_normalized_name, normalize_case, normalize_unit_token
from standardizer import resolve_brand, infer_category, standardize_size, build_standard_key
from matcher import find_duplicates, classify_pairs
from reporter import compute_metrics, write_standardized_output, write_exception_report


def load_config(config_path):
    with open(config_path, "r") as f:
        return json.load(f)


def load_catalog(input_path):
    df = pd.read_excel(input_path)
    return df


def run_pipeline(input_path, config_path, output_dir):
    start = time.time()
    config = load_config(config_path)
    df = load_catalog(input_path)
    total_records = len(df)

    unit_map = config["unit_normalization"]
    stopwords = set(config["stopwords"])
    brand_aliases = config["brand_aliases"]
    category_keywords = config["category_keywords"]
    conversion_map = config["unit_conversion_to_base"]
    match_cfg = config["matching"]

    records = []
    unmapped_brand_count = 0
    category_mismatch_count = 0
    missing_field_count = 0

    for _, row in df.iterrows():
        name_info = build_normalized_name(row.get("raw_product_name"), unit_map, stopwords)

        brand_std, brand_mapped = resolve_brand(row.get("brand_raw"), brand_aliases)
        if not brand_mapped:
            unmapped_brand_count += 1

        size_value = row.get("size_value")
        size_unit_raw = row.get("size_unit_raw")
        resolved_unit = (
            normalize_unit_token(size_unit_raw, unit_map)
            if pd.notna(size_unit_raw)
            else name_info["size_unit"]
        )
        base_value, base_unit = standardize_size(
            size_value if pd.notna(size_value) else name_info["size_value"],
            resolved_unit,
            conversion_map,
        )

        category, mismatch = infer_category(
            name_info["normalized_name"], category_keywords, row.get("category")
        )
        if mismatch:
            category_mismatch_count += 1

        missing = []
        if not brand_std:
            missing.append("brand")
        if pd.isna(row.get("price")):
            missing.append("price")
        if not category:
            missing.append("category")
        if missing:
            missing_field_count += 1

        standard_key = build_standard_key(brand_std, name_info["normalized_name"], base_value, base_unit)

        records.append({
            "product_id": row.get("product_id"),
            "raw_product_name": row.get("raw_product_name"),
            "normalized_name": name_info["normalized_name"],
            "brand_raw": row.get("brand_raw"),
            "brand_standardized": brand_std,
            "brand_mapped_by_rule": brand_mapped,
            "category": category,
            "category_conflict": mismatch,
            "base_size_value": base_value,
            "base_size_unit": base_unit,
            "price": row.get("price"),
            "vendor_source": row.get("vendor_source"),
            "standard_key": standard_key,
            "missing_fields": ",".join(missing) if missing else "",
        })

    duplicate_clusters, pair_scores = find_duplicates(
        records,
        id_field="product_id",
        name_field="normalized_name",
        block_field="brand_standardized",
        auto_merge_threshold=match_cfg["auto_merge_threshold"],
        review_low=match_cfg["review_band_low"],
        review_high=match_cfg["review_band_high"],
    )

    # also collapse exact standard_key matches into clusters, since those
    # are guaranteed duplicates and shouldn't need a fuzzy score at all
    exact_key_groups = {}
    for r in records:
        exact_key_groups.setdefault(r["standard_key"], []).append(r["product_id"])
    for key, ids in exact_key_groups.items():
        if len(ids) > 1:
            root = ids[0]
            duplicate_clusters.setdefault(root, [])
            for pid in ids:
                if pid not in duplicate_clusters[root]:
                    duplicate_clusters[root].append(pid)

    # re-flatten in case exact-key merging created overlapping clusters
    seen = {}
    merged_clusters = {}
    for root, members in duplicate_clusters.items():
        canonical = None
        for m in members:
            if m in seen:
                canonical = seen[m]
                break
        canonical = canonical or root
        merged_clusters.setdefault(canonical, set()).add(canonical)
        for m in members:
            merged_clusters[canonical].add(m)
            seen[m] = canonical
    duplicate_clusters = {k: sorted(v) for k, v in merged_clusters.items() if len(v) > 1}

    id_to_cluster = {}
    for cluster_id, members in duplicate_clusters.items():
        for m in members:
            id_to_cluster[m] = cluster_id

    for r in records:
        r["duplicate_cluster_id"] = id_to_cluster.get(r["product_id"], "")
        r["is_duplicate"] = r["product_id"] in id_to_cluster

    auto_merged_pairs, review_pairs = classify_pairs(
        pair_scores,
        match_cfg["auto_merge_threshold"],
        match_cfg["review_band_low"],
        match_cfg["review_band_high"],
    )

    standardized_df = pd.DataFrame(records).drop(columns=["standard_key"])

    exception_mask = (
        (standardized_df["missing_fields"] != "")
        | (standardized_df["brand_mapped_by_rule"] == False)  # noqa: E712
        | (standardized_df["category_conflict"] == True)  # noqa: E712
    )
    exception_df = standardized_df[exception_mask].copy()
    exception_df["exception_reason"] = exception_df.apply(_reason, axis=1)

    review_pairs_df = pd.DataFrame(review_pairs) if review_pairs else pd.DataFrame(
        columns=["id_a", "id_b", "name_a", "name_b", "score", "block"]
    )

    metrics = compute_metrics(
        total_records=total_records,
        standardized_df=standardized_df,
        duplicate_clusters=duplicate_clusters,
        auto_merged_pairs=auto_merged_pairs,
        review_pairs=review_pairs,
        exception_df=exception_df,
        unmapped_brand_count=unmapped_brand_count,
        category_mismatch_count=category_mismatch_count,
        missing_field_count=missing_field_count,
    )
    metrics["processing_time_seconds"] = round(time.time() - start, 2)

    os.makedirs(output_dir, exist_ok=True)
    std_path = os.path.join(output_dir, "standardized_products.xlsx")
    exc_path = os.path.join(output_dir, "exception_report.xlsx")

    write_standardized_output(std_path, standardized_df)
    write_exception_report(exc_path, exception_df, review_pairs_df, metrics)

    return metrics, std_path, exc_path


def _reason(row):
    reasons = []
    if row["missing_fields"]:
        reasons.append(f"missing: {row['missing_fields']}")
    if row["brand_mapped_by_rule"] is False:
        reasons.append("brand not in alias table")
    if row["category_conflict"]:
        reasons.append("category conflicts with inferred value")
    return "; ".join(reasons)


def main():
    parser = argparse.ArgumentParser(description="Product deduplication and standardization pipeline")
    parser.add_argument("--input", default="data/sample_products.xlsx")
    parser.add_argument("--config", default="config/rules.json")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    metrics, std_path, exc_path = run_pipeline(args.input, args.config, args.output_dir)

    print("Pipeline complete.")
    print(f"Standardized output: {std_path}")
    print(f"Exception report: {exc_path}")
    print()
    print("Metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
