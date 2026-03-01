## Track C – Data Quality & Image Matching

### Goal

**Messy data → high-confidence product matching.**

We take scraped retailer product data (no images) and link it to an existing product catalog that has images. The pipeline does:

- Parsing and normalizing product names and sizes
- Matching and scoring (brand, size, token similarity)
- Storing results in Supabase
- A small labeled set to catch false positives

We bias toward **rejecting borderline cases** rather than making bad matches.

---

## Project structure

- **data/**
  - `existing_products.json` – Mock “master” catalog with `image_url` (23 rows). At least two products per brand so brands are learned; chicken is kept as a non-brand edge case.
  - `scraped_products.json` – Messy scraped rows from multiple retailers (52 rows), including size-conversion cases like sc_051, sc_052.
  - `learned_brands.json` – Optional output from `build_brands.py` for inspection. Brand extraction in `normalize.py` learns from the catalog at import and does not use this file.
- **sql/**
  - `schema.sql` – Postgres/Supabase schema for `existing_products`, `scraped_products`, and `product_matches`. **Paste this into the Supabase SQL Editor** (Dashboard → SQL Editor) to create the tables before seeding.
- **src/**
  - `normalize.py` – Name/size parsing, normalization, promo stripping, synonyms, pack-size parsing.
  - `match.py` – Candidate generation, scoring, unit conversion and tolerance, variant guardrails.
  - `build_brands.py` – Learns brand tokens from existing products (frequency-based merge of hyphenated forms). Optional; normalize learns from the catalog directly.
  - `run_match.py` – CLI to run the matcher from JSON or Supabase.
  - `db.py` – Supabase client helpers.
  - `seed_supabase.py` – Seeds Supabase from `data/*.json`.
  - `evaluate.py` – Compares matcher output to labeled ground truth.
- **tests/**
  - `test_size_score.py` – 13 tests for size scoring (unit conversion, tolerance, dimensions).
- **Root**
  - `.env.example` – Template for `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
  - `.env` – Your secrets (not committed).
- **validation/**
  - `results.csv` – Latest matcher output.
  - `sample_20.csv` – Labeled evaluation set (52 rows, one per scraped product).

---

## 1. Parsing & normalization

In `src/normalize.py`. Pipeline: `normalize_text` → `remove_promo_phrases` → `apply_synonyms` → brand/size extraction → `tokenize_core_name`.

- **Text cleanup**
  - Lowercase, strip odd punctuation, normalize whitespace.
  - **Promo phrases** are stripped (e.g. `buy one get one`, `bogo`, `save $`, `club size`, `limited time`, `best by`, `limit N`, `2 for`). Single-token promo terms (`new`, `sale`, `promo`, `rollback`, etc.) are dropped during tokenization; we keep `original` because it helps distinguish products.
  - **Stopwords** like `and`, `the`, `of`, `scent`, `flavor` are removed from the core name.

- **Synonyms** (word-boundary, longest first)
  - `laundry pacs` / `pacs` / `pac` → `pods`
  - `soft drink` / `pop` → `soda`

- **Brand extraction** (catalog-driven)
  - Brands are learned at import from `existing_products.json`: we count first-token and first-two-token (hyphenated) phrases, then merge by frequency (e.g. `barilla-spaghetti` folds into `barilla` when the root wins; `coca-cola` is kept when it beats `coca`). Anything that appears at least twice and isn’t in the stopword list becomes a known brand—add products to the catalog and the brand is recognized, no code change.
  - **Aliases:** e.g. `coke` → `coca-cola`. “Coca cola” / “cocacola” in the name is normalized to `coca-cola`. If no known brand is found we return `None` (e.g. chicken, store brand).

- **Size parsing**
  - We parse both `size_raw` and `product_name`. Pack patterns like `12 x 12 oz`, `12 pack 12 fl oz` become a total size (e.g. 144 fl oz). Units are canonicalized via `UNIT_ALIASES` (fl oz, oz, lb, ct, ml, L). Result is `(size_value, size_unit)`.

- **Core name & tokens**
  - After stripping promo and brand we drop stopwords. Tokens like `16ct` / `12pk` are split into number + `ct` so they align with catalog “16 ct”. Output is `core_name` (for fuzzy matching) and `tokens` (for variant detection).

The result is a `ParsedProduct`: brand, core_name, size_value/unit, tokens.

---

## 2. Matching & scoring

In `src/match.py`.

### Candidate generation

- We build `by_upc` (UPC → existing products) and `by_brand` (brand → existing products).
- For each scraped product: if we have a UPC we look it up first (fast path). Else if we have a brand we use that bucket. Else we fall back to a global lexical search over all existing names (capped), then score and filter.

### Scoring

- **Brand:** Exact match → 1.0, else 0.0.
- **Size** (dimension-aware, with conversion and tolerance):
  - Dimensions: `mass` (oz, lb), `volume_floz` (fl oz), `volume_metric` (ml, L), `count` (ct). Oz and fl oz are separate (weight vs volume).
  - Missing value or unit → 0.4. Different dimensions (e.g. oz vs fl oz) → 0.0.
  - Numeric: convert to canonical (e.g. lb → oz, L → ml), then match if within ±2%. Count: within ±1.
  - Examples: 16 oz vs 1 lb → 1.0; 500 ml vs 0.5 L → 1.0; 6 oz vs 6 fl oz → 0.0.
- **Token similarity:** `rapidfuzz` token_set_ratio on `core_name`, normalized to [0, 1].

**Weights:** When both have a brand: 0.30 brand + 0.25 size + 0.45 token. When either lacks a brand: 0.20 brand + 0.25 size + 0.55 token (so size and token can carry, e.g. chicken). Optional retailer bonus for same retailer.

### Variant guardrails

We avoid matching across variants. `has_variant_clash` rejects candidate pairs that differ on:

- **Flavor** (e.g. strawberry vs vanilla)
- **Sugar** (zero vs regular)
- **Butter** (salted vs unsalted)
- **Milk fat** (whole vs 2%/lowfat)
- **Oreo** (Double Stuf vs original)
- **Pasta shape** (penne vs spaghetti)
- **Meat pack size** (e.g. 1 lb vs 2 lb chicken, same dimension but outside tolerance)
- **Same-brand multipack count** (e.g. 6ct vs 8ct)

We use dimension and canonical size so 16 oz vs 1 lb is not a clash. If every candidate is filtered out we reject with `rejected_no_candidates_after_variant_filter`.

### Thresholds

- Min token similarity 0.75; below that → `rejected_low_token_similarity`.
- Score ≥ 0.85 → accept (`brand_size_token`).
- Score ≥ 0.75 → borderline accept (`brand_size_token_borderline`).
- Otherwise → `rejected_low_confidence`. UPC matches short-circuit with method `"upc"`.

Overall we’re **conservative**: we’d rather reject than force a bad match.

---

## 3. Supabase

Schema in `sql/schema.sql`: `existing_products` (master catalog with `image_url`), `scraped_products` (raw scraped rows), `product_matches` (scraped_product_id, matched_existing_id, match_score, match_method, matched_at).

**Setup:** Create the tables by pasting the contents of `sql/schema.sql` into the **Supabase SQL Editor** (Dashboard → SQL Editor → New query), then run it. After that you can seed and run the matcher.

- **db.py** – Loads `.env` from project root, exposes `get_supabase`, `fetch_table`, `upsert_rows`, `upsert_matches`.
- **seed_supabase.py** – Reads local JSON and upserts into Supabase.
- **run_match.py** – Can use local JSON or Supabase:
  - `python -m src.run_match` → JSON in, writes `validation/results.csv`.
  - `python -m src.run_match --supabase --write-matches` → loads existing + scraped from Supabase, writes `product_matches` there and still writes `results.csv` locally.

---

## 4. Sample dataset: structure, edge cases & production

### How we structured the sample dataset

We use two files that mirror a real setup: a **master catalog** and **scraped retailer data**.

- **existing_products.json (23 rows)** – Acts as the "source of truth" catalog with `id`, `product_name`, `size_raw`, `upc`, `image_url`, `retailer`. We included at least two products per brand so brand learning works (e.g. Tide, Dawn, Chobani each have multiple entries). We added chicken (no brand) on purpose so we can test the no-brand path. IDs are `ex_001`, `ex_002`, … so we can reference them in ground truth.
- **scraped_products.json (52 rows)** – Simulates messy rows you'd get from multiple retailers: same products with different titles, promos, typos, missing UPCs, and size formats. Each row has `id` (sc_001 … sc_052), `retailer`, `product_name`, `size_raw`, `upc` (often null), `product_url`. We didn't aim for realism in URLs; we aimed for **coverage**: every important edge case appears at least once so the matcher and validator can be tested.
- **validation/sample_20.csv (52 rows)** – One row per scraped product. Columns: `scraped_id`, `expected_outcome` (match or reject), `expected_existing_id` (e.g. ex_005 or ex_005|ex_006 when two catalog rows are the same product), and `notes`. This is our hand-labeled ground truth. We ordered rows by scraped_id so it's easy to see the full set and compare with `results.csv`.

So: **one scraped product → one ground-truth row**. That lets us report accuracy as "52/52" and catch every mismatch.

### Edge cases we intentionally included

We added these so we can test specific behaviors and avoid false positives:

| What we added | Why |
|---------------|-----|
| **Promo noise** | "NEW!", "SALE", "Rollback", "2 for $10", "BEST BY 2027", "LIMIT 4", "ONLINE ONLY" – to stress-test promo stripping and make sure we don't match on junk. |
| **UPC vs no UPC** | Some scraped rows have UPC, some don't. We want to see UPC matches win when present and brand+size+token take over when UPC is missing. |
| **Flavor / variant** | Chobani strawberry vs vanilla, Oreo Double Stuf vs original – so we reject cross-variant matches (strawberry ≠ vanilla). |
| **Sugar** | Coke Zero vs Coke, Chobani Zero Sugar vs regular – we reject when "zero" appears on one side and not the other. |
| **Fat level** | Fairlife whole vs 2%, Kerrygold salted vs unsalted – we reject whole vs 2% and salted vs unsalted. |
| **Pasta shape** | Barilla spaghetti vs penne – we reject different shapes even when brand matches (and we document that penne→spaghetti is a known limitation we track). |
| **Pack / count** | Bounty 6 double rolls vs 8 rolls, Tide PODS 16ct vs 32ct, Coke 12pk vs mini 10pk 7.5oz – we reject when pack size or count is outside tolerance. |
| **Size units** | 92 oz vs 92 fl oz (Tide, Dawn) – we treat oz (weight) and fl oz (volume) as different dimensions and reject. 16 oz vs 1 lb chicken – we accept (same mass, conversion). |
| **Typos** | "T1de", "Chikcen" – to test that we can still match when the brand is recoverable (T1de→Tide) and that we don't break on spelling (Chikcen→chicken, no brand). |
| **Missing size** | One Chobani row with no size – we expect reject unless there's strong other evidence (ambiguous). |
| **No brand** | Chicken breast, store-brand dish soap – we expect no match to a branded catalog entry; brand is None and we rely on size + token for generic chicken. |
| **Same product, multiple catalog rows** | ex_005 and ex_006 both Coke 12pk (same UPC) – ground truth allows either so we can accept "match to ex_005 or ex_006". |

In `sample_20.csv` we mark many of these as **expected reject** even when names look similar, so the system stays biased against false positives and we can measure that with evaluate.py.

### Adapting this to real production data

- **Catalog** – Replace `existing_products.json` with your real products table: canonical id, name, size, UPC, brand (if you have it), image_url, etc. Ensure at least a few products per brand so catalog-driven brand learning still works. In production you'd typically have thousands or millions of rows; indexes on `upc` and a normalized `brand` (or first-token) keep candidate generation fast.
- **Scraped feed** – Replace `scraped_products.json` with live scraped or API data from each retailer. Keep the same shape (id, product_name, size_raw, upc, retailer) so normalize and match don't need to change. If your feed has extra fields (price, category), add them to the schema and pass them through; the matcher only needs name, size, UPC, and optionally retailer.
- **Ground truth** – For production you don't label every row. Label a **sample** (e.g. 500–2000 rows per retailer or per category) that includes the edge cases you care about (promos, variants, no UPC, typos). Use that sample to run evaluate.py and tune thresholds. As you add new edge cases in the wild, add them to the sample and expected outcomes so regressions show up.
- **Scale** – Use Supabase (or your DB) for existing + scraped tables; run_match with `--supabase --write-matches` so matches live in the DB. Add matcher_version and status (e.g. proposed / approved / rejected) so you can re-run and compare, and optionally send low-confidence matches to human review before treating them as linked.

In short: our sample dataset is structured like a minimal production setup (catalog + scraped + one ground-truth row per scraped product), with edge cases added by design so we can test and document behavior. For production you keep that structure and swap in real catalog and feeds, label a representative sample, and scale the pipeline with your DB and tooling.

---

## 5. Validation

`evaluate.py` compares matcher output to ground truth in `validation/sample_20.csv` (columns: scraped_id, expected_outcome, expected_existing_id, notes). Use `ex_005|ex_006` when multiple existing rows are the same product.

```bash
python -m src.run_match
# or with Supabase:
python -m src.run_match --supabase --write-matches

python src/evaluate
```

You get accuracy and a list of mismatches (wrong match, wrong reject, or wrong existing ID).

**False match analysis.** When you run evaluate you get four mismatches: we expect REJECT but the matcher returns MATCH (false positive). Each is analysed separately below.

- **sc_054** – Scraped row is a bundle (Tide and Downy duo). We want it rejected because it is two products, not one. The matcher has no bundle concept so it matches to single Tide 92 (ex_001).
- **sc_055** – Scraped row is Tide-Style, a store-brand copycat not real Tide. We want it rejected. The matcher does not detect lookalikes like Tide-Style so it treats it as Tide and matches to ex_001.
- **sc_056** – Scraped row is Tide 92 fl oz refill pouch; the catalog has Tide 92 bottle. We want it rejected for different packaging form. The matcher does not use packaging form so it matches to the bottle (ex_001).
- **sc_057** – Scraped row is Dawn Hand Soap 24 fl oz; the catalog has Dawn Dish Soap 24 fl oz. We want it rejected for different product type. The matcher has no product-type concept so it matches to Dawn 24 dish soap (ex_004).

In short the matcher is missing bundles, copycat or lookalike brands, packaging form, and product type or category; until we add that these four show up as mismatches.

**Suggested fixes.** For each mismatch above, a possible fix is:

- **sc_054 (bundle)** – Detect bundle cues in the scraped name or size (e.g. "duo", "&", "with", "bundle", "2-pack" when it means two different products). If detected, reject or treat as a different SKU so we do not match to a single catalog item.
- **sc_055 (copycat)** – Detect lookalike patterns such as "X-Style", "X like", "comparable to X" in the product name. When detected, do not assign the brand X to the scraped row (or reject the candidate) so it does not match to the real brand.
- **sc_056 (packaging form)** – Parse or tag packaging form (e.g. bottle, pouch, refill, tub) on both scraped and catalog side. Reject when the forms differ for the same brand and size.
- **sc_057 (product type)** – Add product type or category (e.g. hand soap vs dish soap, napkins vs paper towels). Parse or tag it from the name or from a category field; reject when scraped and catalog types differ.

These can be implemented as extra checks in the matcher (e.g. in normalize or match) or as a separate pre-filter before scoring.



---

## 6. Brand learning & tests

- **build_brands.py** – Optional. Learns brand tokens from `existing_products.json` (same logic as normalize: first token, first-two-token, frequency merge) and writes `learned_brands.json` for inspection. normalize.py does not depend on it; it learns from the catalog at import.
- **test_size_score.py** – 13 tests for size scoring: conversions (16 oz vs 1 lb, 500 ml vs 0.5 L, etc.), different dimensions (6 oz vs 6 fl oz → 0), count tolerance (±1), missing size (0.4), numeric ±2%. Run: `python -m pytest tests/test_size_score.py -v`. Needs `pytest` from requirements.

---

DELETEME

### How we structured the sample dataset

We use two files that mirror a real setup: a **master catalog** and **scraped retailer data**.

- **existing_products.json (23 rows)** – Acts as the “source of truth” catalog with `id`, `product_name`, `size_raw`, `upc`, `image_url`, `retailer`. We included at least two products per brand so brand learning works (e.g. Tide, Dawn, Chobani each have multiple entries). We added chicken (no brand) on purpose so we can test the no-brand path. IDs are `ex_001`, `ex_002`, … so we can reference them in ground truth.
- **scraped_products.json (52 rows)** – Simulates messy rows you’d get from multiple retailers: same products with different titles, promos, typos, missing UPCs, and size formats. Each row has `id` (sc_001 … sc_052), `retailer`, `product_name`, `size_raw`, `upc` (often null), `product_url`. We didn’t aim for realism in URLs; we aimed for **coverage**: every important edge case appears at least once so the matcher and validator can be tested.
- **validation/sample_20.csv (52 rows)** – One row per scraped product. Columns: `scraped_id`, `expected_outcome` (match or reject), `expected_existing_id` (e.g. ex_005 or ex_005|ex_006 when two catalog rows are the same product), and `notes`. This is our hand-labeled ground truth. We ordered rows by scraped_id so it’s easy to see the full set and compare with `results.csv`.

So: **one scraped product → one ground-truth row**. That lets us report accuracy as “52/52” and catch every mismatch.

### Edge cases we intentionally included

We added these so we can test specific behaviors and avoid false positives:

| What we added | Why |
|---------------|-----|
| **Promo noise** | “NEW!”, “SALE”, “Rollback”, “2 for $10”, “BEST BY 2027”, “LIMIT 4”, “ONLINE ONLY” – to stress-test promo stripping and make sure we don’t match on junk. |
| **UPC vs no UPC** | Some scraped rows have UPC, some don’t. We want to see UPC matches win when present and brand+size+token take over when UPC is missing. |
| **Flavor / variant** | Chobani strawberry vs vanilla, Oreo Double Stuf vs original – so we reject cross-variant matches (strawberry ≠ vanilla). |
| **Sugar** | Coke Zero vs Coke, Chobani Zero Sugar vs regular – we reject when “zero” appears on one side and not the other. |
| **Fat level** | Fairlife whole vs 2%, Kerrygold salted vs unsalted – we reject whole vs 2% and salted vs unsalted. |
| **Pasta shape** | Barilla spaghetti vs penne – we reject different shapes even when brand matches (and we document that penne→spaghetti is a known limitation we track). |
| **Pack / count** | Bounty 6 double rolls vs 8 rolls, Tide PODS 16ct vs 32ct, Coke 12pk vs mini 10pk 7.5oz – we reject when pack size or count is outside tolerance. |
| **Size units** | 92 oz vs 92 fl oz (Tide, Dawn) – we treat oz (weight) and fl oz (volume) as different dimensions and reject. 16 oz vs 1 lb chicken – we accept (same mass, conversion). |
| **Typos** | “T1de”, “Chikcen” – to test that we can still match when the brand is recoverable (T1de→Tide) and that we don’t break on spelling (Chikcen→chicken, no brand). |
| **Missing size** | One Chobani row with no size – we expect reject unless there’s strong other evidence (ambiguous). |
| **No brand** | Chicken breast, store-brand dish soap – we expect no match to a branded catalog entry; brand is None and we rely on size + token for generic chicken. |
| **Same product, multiple catalog rows** | ex_005 and ex_006 both Coke 12pk (same UPC) – ground truth allows either so we can accept “match to ex_005 or ex_006”. |

In `sample_20.csv` we mark many of these as **expected reject** even when names look similar, so the system stays biased against false positives and we can measure that with evaluate.py.

### Adapting this to real production data

- **Catalog** – Replace `existing_products.json` with your real products table: canonical id, name, size, UPC, brand (if you have it), image_url, etc. Ensure at least a few products per brand so catalog-driven brand learning still works. In production you’d typically have thousands or millions of rows; indexes on `upc` and a normalized `brand` (or first-token) keep candidate generation fast.
- **Scraped feed** – Replace `scraped_products.json` with live scraped or API data from each retailer. Keep the same shape (id, product_name, size_raw, upc, retailer) so normalize and match don’t need to change. If your feed has extra fields (price, category), add them to the schema and pass them through; the matcher only needs name, size, UPC, and optionally retailer.
- **Ground truth** – For production you don’t label every row. Label a **sample** (e.g. 500–2000 rows per retailer or per category) that includes the edge cases you care about (promos, variants, no UPC, typos). Use that sample to run evaluate.py and tune thresholds. As you add new edge cases in the wild, add them to the sample and expected outcomes so regressions show up.
- **Scale** – Use Supabase (or your DB) for existing + scraped tables; run_match with `--supabase --write-matches` so matches live in the DB. Add matcher_version and status (e.g. proposed / approved / rejected) so you can re-run and compare, and optionally send low-confidence matches to human review before treating them as linked.

In short: our sample dataset is structured like a minimal production setup (catalog + scraped + one ground-truth row per scraped product), with edge cases added by design so we can test and document behavior. For production you keep that structure and swap in real catalog and feeds, label a representative sample, and scale the pipeline with your DB and tooling.

---

## 7. Scale & cost strategy

### How this reduces future scraping cost

We treat the **master catalog** as the single place that has rich, trusted data (canonical name, size, UPC, images). New retailer feeds only need **cheap textual data**: product name, size string, and UPC when available. We don’t re-scrape images or full product pages per retailer. Instead we:

- **Parse** the scraped name and size (normalize.py).
- **Match** the parsed row to an existing catalog entry (match.py).
- **Store** the link in `product_matches` (scraped_product_id → matched_existing_id).

So you pay once for high-quality catalog data (including images); every additional retailer is parsing + matching against that catalog, not a new image scrape. That cuts both scraping cost and the risk of duplicate or inconsistent product records.

### How to reuse data across retailers

Every retailer feed shares the same **existing_products** (master) table. When we match a scraped row to an existing product, we write one row in `product_matches`: `(scraped_product_id, matched_existing_id, ...)`. So:

- **Many scraped rows (across retailers) can point to the same catalog row.** Walmart’s “Tide Original 92 fl oz” and Target’s “Tide Liquid Laundry Detergent 92 fl oz” both link to the same `existing_products` row. One catalog entry, one set of images and attributes; multiple retailer listings.
- **UPC on one retailer helps everywhere.** If Walmart gives us a UPC and we match to the catalog, that catalog row is now linked. When Target sends the same product without a UPC, we can still match by brand + size + token; the link is reused.
- **You can compare prices, promos, and availability** across retailers by joining scraped data to the same `matched_existing_id`, without duplicating product or image data.

### How to create a master product table

In this repo, `existing_products` is the stand-in for the master product table. In production you’d create a real **products** (or `master_products`) table with:

- **Identity:** stable `id` (e.g. UUID or your own prefix like `ex_001`).
- **Canonical attributes:** product name, brand (if you store it), size value + unit, UPC.
- **Rich content:** `image_url` (or multiple images), category, description if needed.
- **Metadata:** created_at, updated_at, source.

You can backfill this table from your best available source (one retailer’s feed, a data provider, or a manual catalog). Then:

- **Scraped / retailer-specific tables** keep raw data per retailer: their product_id, their name string, size_raw, upc, price, promo, url, etc. They do **not** store images or canonical names; they store only what that retailer gives you.
- **product_matches** (or `scraped_product_links`) stores `(scraped_product_id, matched_existing_id, match_score, match_method)`. That’s the link from “retailer listing” to “master product.”

So: one master product table (canonical, with images); many retailer tables (raw); one link table (matches). The matcher’s job is to fill the link table from scraped rows to master rows.

### How this scales as the database grows

- **Candidate generation stays cheap:** We don’t scan the whole catalog per scraped row. We use **UPC index** (exact lookup when UPC is present) and **brand index** (filter to that brand’s products). Only when there’s no UPC and no brand do we fall back to a bounded lexical search over names. So as the catalog grows to hundreds of thousands or millions of rows, most work is index lookups and small buckets.
- **Indexes:** On the master table, index `upc` (unique or not, depending on duplicates) and a normalized `brand` (or first-token) so `by_upc` and `by_brand` are fast. On `product_matches`, index `scraped_product_id` (for upserts and lookups) and optionally `matched_existing_id` (for “where is this product sold?”).
- **Incremental runs:** You can run the matcher on new or updated scraped rows only, and upsert into `product_matches` by `scraped_product_id`. You don’t have to rematch the whole history every time.
- **Later extensions:** When the catalog is very large, you can add precomputed embeddings for product names and use vector search for the “no UPC, no brand” path instead of a big lexical scan. You can also partition or shard by brand or category. The current design (UPC → brand → limited fallback) already keeps the heavy work bounded.

In short: we reduce cost by reusing one catalog and matching cheap text; we reuse data by linking many retailer rows to the same master row; we create a master product table as the single source of truth and link tables to it; and we scale by indexing UPC and brand and only falling back to broader search when needed.

---

## 8. Deferred / future work

- **Structured attributes & config-driven variant rules** – Extract flavor, fat%, zero sugar, etc. into structured fields; score or reject by attribute mismatch; move rule lists to YAML/JSON and log when rules fire.
- **Score calibration & debug columns** – Document how thresholds (0.85, 0.75, min token) were chosen (e.g. from score distributions). Persist brand_score, size_score, token_score, gap, candidate_count (e.g. in `features_json`) for auditing and re-calibration.
- **Supabase match lifecycle** – Add matcher_version, status (proposed/approved/rejected), features_json to product_matches; upsert key (scraped_product_id, matcher_version) for idempotency and history; support human review and delta comparison between runs. 