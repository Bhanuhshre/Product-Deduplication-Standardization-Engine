"""
matcher.py

Duplicate detection in two stages:

1. Exact-key blocking: records sharing a standard_key (brand + name +
   size, all standardized) are auto-grouped without needing fuzzy
   comparison at all.
2. Fuzzy pass within brand blocks: for records that didn't collapse to
   an identical key, compare normalized names with RapidFuzz's
   token_sort_ratio (order-independent, good for retail names like
   "Amul Toned Milk" vs "Toned Milk Amul"). Matches are grouped with a
   union-find so transitive matches (A~B, B~C) merge into one cluster.

Blocking by brand keeps this roughly O(n) instead of the O(n^2) an
all-pairs comparison would need across a 10k+ row catalog.
"""

from collections import defaultdict
from rapidfuzz import fuzz


class UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def block_by_key(records, key_field):
    blocks = defaultdict(list)
    for r in records:
        blocks[r[key_field]].append(r)
    return blocks


def find_duplicates(records, id_field, name_field, block_field,
                     auto_merge_threshold=95, review_low=80, review_high=95):
    """
    records: list of dicts, each already carrying normalized_name and
             a blocking field (brand_normalized).
    Returns:
        clusters: dict[cluster_id] -> list of record ids
        pair_scores: list of dicts describing each compared pair and its
                     similarity score, for the exception report
    """
    ids = [r[id_field] for r in records]
    uf = UnionFind(ids)
    pair_scores = []

    blocks = block_by_key(records, block_field)

    for block_key, block_records in blocks.items():
        if len(block_records) < 2:
            continue
        n = len(block_records)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = block_records[i], block_records[j]
                name_a = a[name_field] or ""
                name_b = b[name_field] or ""
                if not name_a or not name_b:
                    continue
                score = fuzz.token_sort_ratio(name_a, name_b)

                # size must match (or both unknown) to be considered the
                # same product -- name similarity alone can't tell a
                # 200g pack from a 1kg pack of the same item
                size_compatible = (
                    a.get("base_size_value") is None
                    or b.get("base_size_value") is None
                    or (
                        a.get("base_size_unit") == b.get("base_size_unit")
                        and a.get("base_size_value") == b.get("base_size_value")
                    )
                )

                if score >= review_low and size_compatible:
                    pair_scores.append({
                        "id_a": a[id_field],
                        "id_b": b[id_field],
                        "name_a": name_a,
                        "name_b": name_b,
                        "score": score,
                        "block": block_key,
                    })
                    if score >= auto_merge_threshold:
                        uf.union(a[id_field], b[id_field])

    clusters = defaultdict(list)
    for rid in ids:
        root = uf.find(rid)
        clusters[root].append(rid)

    # keep only clusters with more than one member (actual duplicate groups)
    duplicate_clusters = {root: members for root, members in clusters.items() if len(members) > 1}

    return duplicate_clusters, pair_scores


def classify_pairs(pair_scores, auto_merge_threshold, review_low, review_high):
    """Bucket compared pairs into auto-merged / needs-review / rejected
    bands for reporting purposes."""
    auto_merged, needs_review = [], []
    for p in pair_scores:
        if p["score"] >= auto_merge_threshold:
            auto_merged.append(p)
        elif review_low <= p["score"] < auto_merge_threshold:
            needs_review.append(p)
    return auto_merged, needs_review
