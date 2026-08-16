# Product Deduplication & Standardization Engine

A pipeline for cleaning up messy, multi-vendor product catalog data. It takes
raw product listings pulled from different feeds (each with their own naming
conventions, unit abbreviations, and brand spellings), standardizes them
against a shared rule set, and flags duplicate or inconsistent listings for
review.

This started from a fairly common problem in catalog / inventory teams: the
same physical product ends up listed multiple times because one vendor feed
writes "Amul Toned Milk 500ml" and another writes "AMUL TONED MILK 500 ML -"
or "amul toned milk 0.5l". Nothing about that is a real duplicate check
failure on the vendor's end, it's just inconsistent text, but if you don't
catch it you get inflated SKU counts, broken analytics, and confused
customers seeing the "same" item twice in search results.

## What it does

1. **Normalize** raw product names — fix casing, strip stray punctuation and
   double spaces, pull out the pack size (e.g. `500gm`, `1.5 Litre`) from the
   free-text name.
2. **Standardize** brand names against a lookup table (`config/rules.json`),
   convert sizes to a common base unit per family (grams for weight,
   millilitres for volume) so `1kg` and `1000g` are recognized as equal, and
   assign a category based on keyword matches in the product name.
3. **Match** — first collapse exact standardized duplicates (same brand, same
   name, same size) with no fuzzy logic needed. Then, within each brand
   block, compare remaining names using RapidFuzz's token-sort ratio to catch
   near-duplicates that aren't byte-identical after standardization.
4. **Report** — anything that couldn't be confidently auto-resolved (a fuzzy
   match in the ambiguous score band, an unmapped brand, a missing price or
   category, a category that conflicts with what the name implies) goes into
   `exception_report.xlsx` for a human to check.

## Project layout

```
product-deduplication-engine/
├── README.md
├── requirements.txt
├── data/
│   ├── sample_products.xlsx        synthetic 10,500-row test catalog
│   └── generate_sample_data.py     script that built it
├── src/
│   ├── main.py                     pipeline entry point
│   ├── normalizer.py               text/unit cleanup
│   ├── matcher.py                  blocking + fuzzy duplicate detection
│   ├── standardizer.py             brand/category/unit rules
│   └── reporter.py                 metrics + Excel report writer
├── config/
│   └── rules.json                  brand aliases, unit map, category keywords
├── tests/
│   └── test_pipeline.py            unit tests (pytest)
└── outputs/
    ├── standardized_products.xlsx
    └── exception_report.xlsx
```

## Running it

```
pip install -r requirements.txt
python src/main.py --input data/sample_products.xlsx --config config/rules.json --output-dir outputs
```

Optional flags default to the paths above, so `python src/main.py` on its own
also works from the project root.

Run the tests with:

```
pytest tests/test_pipeline.py -v
```

## How matching actually works

The naive approach — compare every product name against every other name —
is O(n²), which gets slow fast once you're past a couple thousand rows.
Instead, records are first grouped ("blocked") by standardized brand, and
fuzzy comparison only happens within a block. A product from "Amul" is never
compared against a product from "Nestle" — they can't be the same listing,
so there's no reason to waste a comparison on them.

Within a block, two thresholds control the outcome:

- **Score ≥ 95** (token-sort ratio): auto-merged into the same duplicate
  cluster. High enough that a false positive is rare.
- **Score 80–94**: flagged in the exception report as an ambiguous pair
  rather than merged automatically, since names in this range can be genuine
  near-duplicates or two related-but-different products (e.g. "Refined
  Sunflower Oil 1L" vs "Refined Sunflower Oil 1.5L" scores fairly high on
  text similarity alone).
- **Below 80**: not considered a match candidate.

Size acts as a hard gate on top of the name score — two records with
otherwise identical names but different standardized sizes are never merged,
since that's almost always a genuinely different SKU (different pack size),
not a duplicate listing.

Records that already collapse to an identical `brand + normalized name +
size` key skip fuzzy scoring entirely and merge directly — most exact
duplicates in a scraped or multi-feed catalog fall into this bucket.

## Metrics on the sample dataset

The numbers below are from an actual run against `data/sample_products.xlsx`
(10,500 rows), not hand-picked. Regenerating the sample data or adjusting
`config/rules.json` will change these slightly.

| Metric | Value |
|---|---|
| Total records processed | 10,500 |
| Duplicate clusters found | 2,397 |
| Records involved in duplicates | 6,340 |
| Estimated unique products after dedup | 6,557 |
| Duplicate rate | 37.6% |
| Auto-merged pairs (score ≥ 95) | 6,596 |
| Pairs flagged for manual review (score 80–94) | 5,979 |
| Records sent to exception report | 1,953 (18.6%) |
| Records with an unmapped brand | 731 |
| Records with a category conflict | 821 |
| Records with a missing field | 543 |
| Processing time | ~5 seconds |

The duplicate rate looks steep, but it's expected for this dataset: the
sample generator deliberately reuses a smaller pool of underlying products
across multiple vendor feeds with varied text formatting (that's the whole
point of the test data — it's meant to exercise the matching logic, not to
mirror any particular real catalog's actual duplication rate).

The exception rate looks high at first glance (18.6%), but it's mostly
records that are individually fine and just need one field confirmed — an
unrecognized brand spelling, a category the keyword rules couldn't infer
confidently, a blank price — not records that are actually broken. The
report separates those reasons out so review work can be triaged instead of
treated as one flat pile.

## Known limitations

- Brand and category rules are keyword/lookup based, not ML-driven. They
  work well for a catalog with a known, relatively stable set of brands and
  categories, but a brand alias or category keyword that isn't in
  `config/rules.json` won't be recognized — it'll show up in the exception
  report as unmapped rather than silently guessed at.
- Fuzzy matching is name-based (token-sort ratio) with a size gate. It
  doesn't currently use price, description text, or image similarity, so two
  genuinely different products with very similar names and identical pack
  sizes could still land in the review band together — which is exactly
  why that band goes to a human rather than auto-merging.
- The sample dataset is synthetic, generated by `data/generate_sample_data.py`
  to mimic realistic catalog messiness (case inconsistency, unit
  abbreviations, misspelled brands, missing fields). It's meant for testing
  the pipeline's logic, not as a real product dataset.

## Tech stack

Python, Pandas, RapidFuzz, Regular Expressions, openpyxl (Excel I/O), pytest.
