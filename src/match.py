# match.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set

from rapidfuzz import fuzz, process

from .normalize import ParsedProduct, parse_product

# When UPC and brand don't find anything, we fall back to lexical search
FALLBACK_TOP_K = 80
GLOBAL_FALLBACK_TOP_K = 25

# Size match: ±2% for numeric units, ±1 for count
SIZE_TOLERANCE_PCT = 0.02
COUNT_TOLERANCE = 1


@dataclass(frozen=True)
class MatchResult:
    scraped_id: str
    matched_existing_id: Optional[str]
    match_score: float
    match_method: str


def brand_score(a: ParsedProduct, b: ParsedProduct) -> float:
    if not a.brand or not b.brand:
        return 0.0
    return 1.0 if a.brand == b.brand else 0.0


def _dimension(u: Optional[str]) -> Optional[str]:
    """Unit dimension: mass, volume_metric, volume_floz, or count. oz and fl oz are separate."""
    if u is None:
        return None
    u = u.strip().lower()
    if u in {"oz", "lb"}:
        return "mass"
    if u in {"fl oz"}:
        return "volume_floz"
    if u in {"ml", "l"}:
        return "volume_metric"
    if u in {"ct"}:
        return "count"
    return None


def _to_canonical_value(value: float, unit: str) -> Optional[Tuple[float, str]]:
    """Convert (value, unit) to (canonical_value, dimension): mass→oz, volume_metric→ml, volume_floz→fl oz, count→ct."""
    u = unit.strip().lower()
    dim = _dimension(unit)
    if dim is None:
        return None
    if dim == "mass":
        if u == "lb":
            return (value * 16.0, "mass")
        return (value, "mass")  # oz
    if dim == "volume_metric":
        if u == "l":
            return (value * 1000.0, "volume_metric")
        return (value, "volume_metric")  # ml
    if dim == "volume_floz":
        return (value, "volume_floz")
    if dim == "count":
        return (value, "count")
    return None


def _unit_family(u: Optional[str]) -> Optional[str]:
    """Same as _dimension; kept for compatibility."""
    return _dimension(u)


def size_score(
    a: ParsedProduct,
    b: ParsedProduct,
    tolerance_pct: float = SIZE_TOLERANCE_PCT,
    count_tolerance: int = COUNT_TOLERANCE,
) -> float:
    """
    Size match: missing => 0.4; different dimensions (e.g. oz vs fl oz) => 0.0.
    Same dimension: convert to canonical, then match if within tolerance (numeric ±%, count ±1).
    """
    if a.size_value is None or b.size_value is None or a.size_unit is None or b.size_unit is None:
        return 0.4

    ca = _to_canonical_value(a.size_value, a.size_unit)
    cb = _to_canonical_value(b.size_value, b.size_unit)
    if ca is None or cb is None:
        return 0.0
    val_a, dim_a = ca
    val_b, dim_b = cb
    if dim_a != dim_b:
        return 0.0

    if dim_a == "count":
        return 1.0 if abs(val_a - val_b) <= count_tolerance else 0.0
    lo = min(val_a, val_b)
    hi = max(val_a, val_b)
    if lo <= 0:
        return 1.0 if abs(val_a - val_b) < 1e-9 else 0.0
    return 1.0 if (hi - lo) / lo <= tolerance_pct else 0.0


def token_similarity(a: ParsedProduct, b: ParsedProduct) -> float:
    if not a.core_name or not b.core_name:
        return 0.0
    return fuzz.token_set_ratio(a.core_name, b.core_name) / 100.0


def overall_score(a: ParsedProduct, b: ParsedProduct) -> Tuple[float, float]:
    """Returns (total_score, token_score). Weights: brand 0.30/0.20, size 0.25, token 0.45/0.55 (higher token when no brand)."""
    bs = brand_score(a, b)
    ss = size_score(a, b)
    ts = token_similarity(a, b)
    if a.brand and b.brand:
        total = 0.30 * bs + 0.25 * ss + 0.45 * ts
    else:
        total = 0.20 * bs + 0.25 * ss + 0.55 * ts
    return total, ts


# Variant / flavor guardrails
FLAVOR_TOKENS: Set[str] = {"strawberry", "vanilla", "chocolate"}
PASTA_SHAPES: Set[str] = {"spaghetti", "penne", "fusilli", "farfalle"}
FAT_LOW_TOKENS: Set[str] = {"2", "1", "skim", "lowfat", "low", "reduced"}
FAT_WHOLE_TOKENS: Set[str] = {"whole"}
SUGAR_ZERO_TOKENS: Set[str] = {"zero"}
OREO_DOUBLE_TOKENS: Set[str] = {"double", "stuf"}
MEAT_TOKENS: Set[str] = {"chicken", "breast"}

# Small bonus when retailer matches
RETAILER_BONUS: float = 0.02


def retailer_boost(scraped_retailer: Optional[str], existing_retailer: Optional[str]) -> float:
    if not scraped_retailer or not existing_retailer:
        return 0.0
    sr = scraped_retailer.lower()
    er = existing_retailer.lower()
    if sr == er:
        return RETAILER_BONUS
    return 0.0


def has_variant_clash(a: ParsedProduct, b: ParsedProduct) -> bool:
    """True when these look like different variants (flavor, fat, shape, etc.) so we reject instead of matching."""
    ta = set(a.tokens)
    tb = set(b.tokens)

    # Flavor
    flav_a = ta & FLAVOR_TOKENS
    flav_b = tb & FLAVOR_TOKENS
    if flav_a and flav_b and flav_a != flav_b:
        return True

    # Zero-sugar vs regular
    if bool(ta & SUGAR_ZERO_TOKENS) != bool(tb & SUGAR_ZERO_TOKENS):
        return True

    # Salted vs unsalted butter
    if ("unsalted" in ta) != ("unsalted" in tb):
        return True
    if ("salted" in ta) != ("salted" in tb):
        return True

    # Fat level
    low_a = bool(ta & FAT_LOW_TOKENS)
    low_b = bool(tb & FAT_LOW_TOKENS)
    whole_a = "whole" in ta
    whole_b = "whole" in tb
    if (whole_a != whole_b) and (low_a or low_b or whole_a or whole_b):
        return True

    # Oreo Double Stuf vs regular
    has_double_a = bool(ta & OREO_DOUBLE_TOKENS)
    has_double_b = bool(tb & OREO_DOUBLE_TOKENS)
    if has_double_a != has_double_b:
        return True

    # Pasta shape
    shape_a = ta & PASTA_SHAPES
    shape_b = tb & PASTA_SHAPES
    if shape_a and shape_b and shape_a != shape_b:
        return True

    # Meat packs: 1 lb vs 2 lb chicken breast (use canonical values + tolerance)
    dim_a = _dimension(a.size_unit)
    dim_b = _dimension(b.size_unit)
    if (
        dim_a == "mass"
        and dim_b == "mass"
        and a.size_value is not None
        and b.size_value is not None
    ):
        ca = _to_canonical_value(a.size_value, a.size_unit)
        cb = _to_canonical_value(b.size_value, b.size_unit)
        if ca and cb:
            val_a, _ = ca
            val_b, _ = cb
            lo, hi = min(val_a, val_b), max(val_a, val_b)
            if lo > 0 and (hi - lo) / lo > SIZE_TOLERANCE_PCT and (ta & MEAT_TOKENS) and (tb & MEAT_TOKENS):
                return True

    # Same-brand pack count difference
    if (
        dim_a == "count"
        and dim_b == "count"
        and a.size_value is not None
        and b.size_value is not None
        and abs(a.size_value - b.size_value) > COUNT_TOLERANCE
        and a.brand
        and b.brand
        and a.brand == b.brand
    ):
        return True

    return False


def build_existing_index(
    existing_rows: List[dict],
) -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]], List[dict], List[str]]:
    by_upc: Dict[str, List[dict]] = {}
    by_brand: Dict[str, List[dict]] = {}
    all_names: List[str] = []

    for r in existing_rows:
        parsed = parse_product(r.get("product_name", ""), r.get("size_raw"))
        r["_parsed"] = parsed
        all_names.append(parsed.normalized_name)

        upc = r.get("upc")
        if upc:
            by_upc.setdefault(str(upc), []).append(r)

        if parsed.brand:
            by_brand.setdefault(parsed.brand, []).append(r)

    return by_upc, by_brand, existing_rows, all_names


def _lexical_fallback(
    query: str,
    all_names: List[str],
    all_rows: List[dict],
    limit: int,
) -> List[dict]:
    if not all_names or not query:
        return []
    extracted = process.extract(
        query,
        all_names,
        scorer=fuzz.token_set_ratio,
        limit=limit,
    )
    seen_ids: Set[int] = set()
    out: List[dict] = []
    for _choice, _score, idx in extracted:
        r = all_rows[idx]
        if id(r) in seen_ids:
            continue
        seen_ids.add(id(r))
        out.append(r)
    return out


def generate_candidates(
    scraped_parsed: ParsedProduct,
    scraped_upc: Optional[str],
    by_upc: Dict[str, List[dict]],
    by_brand: Dict[str, List[dict]],
    all_rows: List[dict],
    all_names: List[str],
    fallback_top_k: int = FALLBACK_TOP_K,
    global_fallback_top_k: int = GLOBAL_FALLBACK_TOP_K,
) -> List[dict]:
    # UPC first
    if scraped_upc:
        c = by_upc.get(str(scraped_upc))
        if c:
            return c

    # Then by brand
    brand_candidates: List[dict] = []
    if scraped_parsed.brand and scraped_parsed.brand in by_brand:
        brand_candidates = by_brand[scraped_parsed.brand]

    # If we have brand candidates, add a small lexical fallback (catches wrong/missed brand)
    if brand_candidates:
        fallback = _lexical_fallback(
            scraped_parsed.normalized_name,
            all_names,
            all_rows,
            limit=global_fallback_top_k,
        )
        seen = {id(r) for r in brand_candidates}
        merged: List[dict] = list(brand_candidates)
        for r in fallback:
            if id(r) not in seen:
                seen.add(id(r))
                merged.append(r)
        return merged

    # No UPC and no brand: full lexical fallback
    return _lexical_fallback(
        scraped_parsed.normalized_name,
        all_names,
        all_rows,
        limit=fallback_top_k,
    )


def match_one(
    scraped_row: dict,
    by_upc: Dict[str, List[dict]],
    by_brand: Dict[str, List[dict]],
    all_rows: List[dict],
    all_names: List[str],
    accept_threshold: float = 0.85,
    borderline_threshold: float = 0.75,
    min_token_threshold: float = 0.75,
) -> MatchResult:
    scraped_id = scraped_row["id"]
    s_parsed = parse_product(scraped_row.get("product_name", ""), scraped_row.get("size_raw"))
    scraped_upc = scraped_row.get("upc")
    scraped_retailer = scraped_row.get("retailer")

    candidates = generate_candidates(
        s_parsed, scraped_upc, by_upc, by_brand, all_rows, all_names
    )
    if not candidates:
        return MatchResult(scraped_id, None, 0.0, "rejected_no_candidates")

    scored: List[Tuple[float, float, dict]] = []
    for c in candidates:
        c_parsed: ParsedProduct = c["_parsed"]

        if scraped_upc and c.get("upc") and str(scraped_upc) == str(c.get("upc")):
            return MatchResult(scraped_id, c["id"], 0.99, "upc")

        if has_variant_clash(s_parsed, c_parsed):
            continue

        total, tok = overall_score(s_parsed, c_parsed)
        total += retailer_boost(scraped_retailer, c.get("retailer"))
        scored.append((total, tok, c))

    if not scored:
        return MatchResult(scraped_id, None, 0.0, "rejected_no_candidates_after_variant_filter")

    scored.sort(key=lambda x: x[0], reverse=True)
    best_total, best_tok, best = scored[0]

    if best_tok < min_token_threshold:
        return MatchResult(scraped_id, None, round(best_total, 4), "rejected_low_token_similarity")

    if s_parsed.size_value is None and best_total < accept_threshold:
        return MatchResult(scraped_id, None, round(best_total, 4), "rejected_missing_size")

    if best_total >= accept_threshold:
        return MatchResult(scraped_id, best["id"], round(best_total, 4), "brand_size_token")

    if best_total >= borderline_threshold:
        return MatchResult(scraped_id, best["id"], round(best_total, 4), "brand_size_token_borderline")

    return MatchResult(scraped_id, None, round(best_total, 4), "rejected_low_confidence")


def match_all(scraped_rows: List[dict], existing_rows: List[dict]) -> List[MatchResult]:
    by_upc, by_brand, all_rows, all_names = build_existing_index(existing_rows)

    results: List[MatchResult] = []
    for s in scraped_rows:
        results.append(match_one(s, by_upc, by_brand, all_rows, all_names))
    return results