# Pipeline case analysis (ordered by edge-case category)


## Included edge-case categories 

---

## 1) Promo junk in titles (BOGO, “LIMIT 4”, etc.)

### Case 1.1 — LIMIT text doesn’t affect UPC match
- **Scraped**: `sc_011` “Chobani Strawberry Greek Yogurt 5.3 ounces (LIMIT 4)”, UPC `818290010119`
- **Existing**: `ex_007` “Chobani Greek Yogurt, Strawberry, 5.3 oz”, UPC `818290010119`
- **Handled by**
  - Promo stripping removes “limit 4” during normalization (but UPC makes it moot).
  - `match_one` returns immediately on UPC equality.
- **Decision**: match (`"upc"`, score `0.99`)

### Case 1.2 — “ONLINE ONLY” promo phrase doesn’t change a normal match
- **Scraped**: `sc_029` “Tide Original Liquid Laundry Detergent 92 fl oz - ONLINE ONLY”
- **Existing**: `ex_001` “Tide Original Liquid Laundry Detergent, 92 fl oz”
- **Handled by**
  - Promo stripping removes “online only”.
  - Brand bucket (`tide`) yields the right candidate set.
  - Size is exact (`92 fl oz`), tokens overlap strongly.
- **Decision**: match (typically `"brand_size_token"`)

---

## 2) Synonyms / retailer-specific wording

### Case 2.1 — “Pacs” canonicalized to “pods” + promo stripped
- **Scraped**: `sc_005` “Tide PODS Laundry Pacs Original 16ct - 2 for $10 (promo)”
- **Existing**: `ex_003` “Tide PODS Laundry Detergent Pacs, Original Scent, 16 ct”
- **Handled by**
  - Promo phrases removed (“2 for”, “$”, “promo”).
  - `apply_synonyms`: “pacs” → “pods”.
  - Brand extracted: `tide`; size: `16 ct`.
- **Decision**: match (`"brand_size_token"`)

---

## 3) Pack / multi-count sizes (12-pack, 10pk, etc.)

### Case 3.1 — 12-pack count matches catalog 12pk
- **Scraped**: `sc_009` “Coca Cola Soda 12-pack (12 fl oz cans)”, size_raw “12 pack”
- **Existing**: `ex_005` “Coca-Cola Soda - 12pk/12 fl oz Cans” (or `ex_006`)
- **Handled by**
  - `pack` canonicalizes to count (`ct`) → size becomes `12 ct`.
  - Brand bucket (`coca-cola`) + token similarity picks the right 12-pack.
- **Decision**: match (`"brand_size_token"`)

### Case 3.2 — Different pack count (10ct) should not match 12ct
- **Scraped**: `sc_048` “Coca-Cola Mini Cans Soda 10pk 7.5oz”, size_raw “10 ct”
- **Existing**: `ex_005`/`ex_006` (12-pack, 12 fl oz cans)
- **Handled by**
  - `size_score` for counts is strict: abs(10−12)=2 > 1 → `size_score=0.0`.
  - Even if tokens overlap on “coca-cola” and “soda”, the size signal drags the total below acceptance.
- **Decision**: reject (typically `"rejected_low_confidence"`)

---

## 4) Unit conversions and unit-dimension mismatches

### Case 4.1 — `oz` vs `fl oz` are different dimensions → reject
- **Scraped**: `sc_001` “NEW! Tide liquid detergent - Original - 92oz Value Pack”, size_raw “92 oz”
- **Existing**: `ex_001` “Tide Original Liquid Laundry Detergent, 92 fl oz”, size_raw “92 fl oz”
- **Handled by**
  - `_dimension("oz")` → mass, `_dimension("fl oz")` → volume_floz.
  - Different dimensions → `size_score=0.0`.
- **Decision**: reject (expected)

### Case 4.2 — 16 oz == 1 lb canonicalization → match
- **Scraped**: `sc_051` “Boneless Skinless Chicken Breast 16 oz”
- **Existing**: `ex_009` “Boneless Skinless Chicken Breast, 1 lb (approx.)” (or `ex_010`)
- **Handled by**
  - `_to_canonical_value`: `1 lb` → `16 oz` (mass canonicalization).
  - Size aligns + strong token overlap (“boneless skinless chicken breast”).
- **Decision**: match (typically `"brand_size_token"`; no UPC)

### Case 4.3 — Another conversion: 1 lb scraped matches 16 oz catalog spaghetti
- **Scraped**: `sc_052` “Barilla Spaghetti Pasta No.5 1 lb”
- **Existing**: `ex_013` “Barilla Spaghetti Pasta No.5, 16 oz” (or `ex_014`)
- **Handled by**
  - Canonical mass conversion makes `1 lb` comparable to `16 oz`.
  - Brand bucket (`barilla`) + tokens (“spaghetti”, “no 5”) align.
- **Decision**: match (typically `"brand_size_token"`)

---

## 5) Missing size vs present size

### Case 5.1 — Missing size triggers the “reject unless very confident” rule
- **Scraped**: `sc_028` “Chobani Greek Yogurt Strawberry (size missing)”, size_raw `null`
- **Existing**: `ex_007` / `ex_008` (Chobani Strawberry 5.3 oz)
- **Handled by**
  - Missing size → `size_score=0.4` (neutral-ish).
  - Additional safeguard: if scraped size is missing and total < `0.85` → `"rejected_missing_size"`.
- **Decision**: reject unless the remaining signals are strong enough to clear `0.85`

---

## 6) Brand learning + aliases (e.g., Coke → Coca-Cola)

### Case 6.1 — Brand alias + UPC makes it exact
- **Scraped**: `sc_008` “Coke Classic 12pk 12oz cans”, UPC `049000001327`
- **Existing**: `ex_005` “Coca-Cola Soda - 12pk/12 fl oz Cans”, UPC `049000001327` (also `ex_006`)
- **Handled by**
  - `BRAND_ALIASES`: `coke` → `coca-cola`
  - UPC match path returns immediately.
- **Decision**: match (`"upc"`, score `0.99`)

---

## 7) Variant / flavor guardrails (reject the wrong variant)

### Case 7.1 — Flavor mismatch: vanilla vs strawberry
- **Scraped**: `sc_034` “Chobani Greek Yogurt Vanilla 5.3oz”
- **Existing**: `ex_007` / `ex_008` Chobani **Strawberry** 5.3 oz
- **Handled by**
  - `has_variant_clash`: flavor token sets differ (`{vanilla}` vs `{strawberry}`) → candidate skipped.
- **Decision**: reject

### Case 7.2 — Zero sugar vs regular soda
- **Scraped**: `sc_041` “Coke Zero Sugar 12pk 12oz cans”
- **Existing**: `ex_005`/`ex_006` regular Coca-Cola 12-pack
- **Handled by**
  - `has_variant_clash`: “zero” presence differs → candidate skipped.
- **Decision**: reject

### Case 7.3 — Salted vs unsalted butter
- **Scraped**: `sc_023` “Kerrygold Unsalted Butter 8oz”
- **Existing**: `ex_019`/`ex_020` Kerrygold **Salted** 8 oz
- **Handled by**
  - `has_variant_clash`: “unsalted” mismatch → candidate skipped.
- **Decision**: reject

### Case 7.4 — Double Stuf vs original Oreo
- **Scraped**: `sc_043` “OREO Double Stuf Chocolate Sandwich Cookies Family Size 19.1oz”
- **Existing**: `ex_011` / `ex_012` Oreo **Original** family size 19.1 oz
- **Handled by**
  - `has_variant_clash`: presence of `{double, stuf}` differs → candidate skipped.
- **Decision**: reject

---

## 10) Weak text similarity safeguard (min token threshold)

### Case 10.1 — “Same size + generic words” can be blocked by token floor
- **Scraped**: `sc_050` “Store Brand Dish Soap Original 24 fl oz”
- **Existing**: most tempting candidate is `ex_004` “Dawn Ultra Dishwashing Liquid Soap… 24 fl oz”
- **Handled by**
  - Candidate generation (lexical fallback) can surface Dawn due to shared tokens (“dish”, “soap”, “24 fl oz”).
  - Safeguard: if best candidate’s token similarity < `0.75` → `"rejected_low_token_similarity"`.
- **Decision**: reject when the name overlap isn’t strong enough (this is the system’s “don’t force it” brake)

---

## 12) Retailer-aware tiebreak (+0.02 boost)

### Case 12.1 — Prefer same retailer when two catalog rows are equivalent (Chobani)
- **Scraped**: `sc_010` retailer `ralphs` “Chobani Greek Yogurt Strawberry 5.3oz”
- **Existing**: `ex_007` retailer `ralphs` vs `ex_008` retailer `target` (same UPC product)
- **Handled by**
  - `retailer_boost` adds `+0.02` when retailers match.
  - When candidates are otherwise tied, the boost nudges toward `ex_007`.
- **Decision**: match; prefer same-retailer row

### Case 12.2 — Prefer Target Oreo row for a Target scrape
- **Scraped**: `sc_014` retailer `target` Oreo family size 19.1 oz
- **Existing**: `ex_011` retailer `target` vs `ex_012` retailer `walmart` (same UPC product)
- **Handled by**
  - Same `+0.02` retailer tiebreak.
- **Decision**: match; prefer `ex_011`

---

## 13) Fallback when UPC/brand is missing or wrong

### Case 13.1 — Brand typo forces full lexical fallback (“T1de”)
- **Scraped**: `sc_003` “T1de Original Laundry Detergent Liquid 92floz (Rollback)”
- **Existing**: `ex_001` “Tide Original Liquid Laundry Detergent, 92 fl oz”
- **Handled by**
  - Brand extraction likely fails (`t1de` ≠ `tide`), and UPC is missing → no UPC/brand bucket.
  - `_lexical_fallback` uses RapidFuzz `process.extract(..., scorer=fuzz.token_set_ratio)` over all catalog names.
  - Size aligns (`92 fl oz`) and tokens overlap strongly even with one typo.
- **Decision**: match when token similarity ≥ `0.75` and overall ≥ `0.85`



## 4 known false positives (why they fail today)

These 4 are designed “should reject” cases (see `validation/sample_20.csv`), but the **current guardrails do not cover** the underlying product-type/packaging semantics, so brand+size+token similarity can incorrectly push them over the acceptance threshold.

### False 1 — Bundle treated like a single product
- **Scraped**: `sc_054` “Tide & Downy Laundry Duo 92 fl oz”
- **Incorrect match target**: `ex_001` Tide Original 92 fl oz (or another Tide 92 fl oz)
- **Why it can match incorrectly**
  - Brand extraction likely returns `tide` (first token).
  - Size is an exact match (`92 fl oz`), so `size_score = 1.0`.
  - Token similarity can still be moderately high due to shared laundry/size tokens.
  - With both brand and size “perfect”, even a medium token similarity can reach:
    - total ≈ `0.30*1 + 0.25*1 + 0.45*0.70 = 0.865` → **passes**.
- **What’s missing**
  - No “bundle/duo/multi-product” detector in `has_variant_clash` or normalization.
  - No rule requiring key product-type tokens like “detergent” to be present on both sides.

### False 2 — Copycat/store-brand “style” product matches the real brand
- **Scraped**: `sc_055` “Tide Style Laundry Detergent 92 fl oz”
- **Incorrect match target**: `ex_001` Tide Original 92 fl oz
- **Why it can match incorrectly**
  - Brand extractor sees leading `tide` and sets brand = `tide` (even though it’s “Tide Style”).
  - Size matches exactly.
  - Token similarity remains high because the rest of the phrase overlaps heavily (“laundry detergent 92 fl oz”).
- **What’s missing**
  - A negative-brand cue list (e.g. “compare to”, “style”, “similar to”, “generic”) to down-weight or reject.

### False 3 — Packaging-form mismatch (refill pouch vs bottle)
- **Scraped**: `sc_056` “Tide Liquid Laundry Detergent 92 fl oz Refill Pouch”
- **Incorrect match target**: `ex_001` Tide bottle 92 fl oz
- **Why it can match incorrectly**
  - Brand = `tide`, size = `92 fl oz`, token similarity very high (the candidate is basically the same string plus “refill pouch”).
  - There is **no guardrail** for packaging form (“pouch”, “refill”, “jug”, “bottle”, etc.).
- **What’s missing**
  - Packaging tokens in `has_variant_clash` (similar to how “salted/unsalted” and “zero sugar” are handled).

### False 4 — Product-type mismatch (hand soap vs dish soap)
- **Scraped**: `sc_057` “Dawn Hand Soap 24 fl oz”
- **Incorrect match target**: `ex_004` “Dawn Ultra Dishwashing Liquid Soap … 24 fl oz”
- **Why it can match incorrectly**
  - Brand = `dawn`
  - Size = `24 fl oz`
  - Token overlap includes `dawn`, `soap`, and size tokens; RapidFuzz can still score high even though **hand** vs **dishwashing** implies different products.
  - No guardrail for “hand” vs “dish/dishwashing”.
- **What’s missing**
  - Product-type guardrails (e.g. `hand soap` vs `dish soap`) or category classification before matching.

