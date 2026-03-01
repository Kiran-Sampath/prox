# normalize.py
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Dict


# Promo and stop words
PROMO_TERMS = {
    "new", "sale", "promo", "rollback", "value", "pack", "family", "size",
    "limit", "limited", "2", "for", "$", "best", "by", "deal",
    "bogo", "save", "club", "time", "weekend", "online", "only", "get",
    "buy", "one", "approximate", "approx",
}

# Strip these before tokenization (longer phrases first)
PROMO_PHRASES = [
    "buy one get one",
    "bogo",
    "save $",
    "club size",
    "limited time",
    "best value",
    "online only",
    "weekend deal",
    "best by",
    "use by",
    "limit 4",
    "limit 2",
    "limit 6",
    "2 for",
    "limit 3",
]

# Skip these in core name; keep "original"—it distinguishes products
STOPWORDS = {
    "and", "&", "the", "a", "an", "of", "with", "in", "on", "to",
    "scent", "flavor", "flavours", "approx", "approximately", "-",
}


# Phrase/token → canonical form (longest keys first)
SYNONYM_MAP = {
    "laundry pacs": "pods",
    "laundry pac": "pods",
    "pacs": "pods",
    "pac": "pods",
    "soft drinks": "soda",
    "soft drink": "soda",
    "pop": "soda",
}


# Unit aliases
UNIT_ALIASES: Dict[str, str] = {
    "floz": "fl oz",
    "fl.oz": "fl oz",
    "fl oz": "fl oz",
    "fluidounce": "fl oz",
    "fluidounces": "fl oz",

    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",

    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",

    "ct": "ct",
    "count": "ct",
    "counts": "ct",
    "pk": "ct",
    "pack": "ct",
    "packs": "ct",
    "roll": "ct",
    "rolls": "ct",

    "ml": "ml",
    "l": "l",
}


@dataclass(frozen=True)
class ParsedProduct:
    raw_name: str
    raw_size: Optional[str]
    normalized_name: str

    brand: Optional[str]
    core_name: str

    size_value: Optional[float]
    size_unit: Optional[str]

    tokens: List[str]


# Basic text normalization
_ws_re = re.compile(r"\s+")
_non_alnum_re = re.compile(r"[^a-z0-9\s\.\-\/&]+")

def normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, keep separators, normalize whitespace."""
    s = s.strip().lower()
    s = s.replace("_", " ").replace("|", " ").replace(",", " ").replace("(", " ").replace(")", " ")
    s = _non_alnum_re.sub(" ", s)
    s = _ws_re.sub(" ", s).strip()
    return s


def remove_promo_phrases(s: str) -> str:
    """Strip known promo phrases (longest first)."""
    out = s
    for phrase in PROMO_PHRASES:
        out = re.sub(re.escape(phrase), " ", out, flags=re.IGNORECASE)
    return _ws_re.sub(" ", out).strip()


def apply_synonyms(s: str) -> str:
    """Replace synonym phrases with canonical form (longest first, word boundaries)."""
    out = s
    for key in sorted(SYNONYM_MAP.keys(), key=len, reverse=True):
        pattern = r"\b" + re.escape(key) + r"\b"
        out = re.sub(pattern, SYNONYM_MAP[key], out, flags=re.IGNORECASE)
    return _ws_re.sub(" ", out).strip()


# Size parsing
_size_re = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>fl\s*oz|floz|oz|ounces?|lb|lbs|pounds?|ct|count|counts|pack|pk|rolls?|ml|l)\b"
)

# Pack patterns: "12 x 12 oz", "12 pack 12 fl oz" → total size
_pack_x_re = re.compile(
    r"(?P<count>\d+)\s*[x×]\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>fl\s*oz|floz|oz|ounces?|lb|lbs|pounds?|ml|l)\b",
    re.IGNORECASE
)
_pack_pk_re = re.compile(
    r"(?P<count>\d+)\s*(?:pack|pk)\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>fl\s*oz|floz|oz|ounces?|lb|lbs|pounds?|ml|l)\b",
    re.IGNORECASE
)
_pack_x_compact_re = re.compile(
    r"(?P<count>\d+)[x×](?P<val>\d+(?:\.\d+)?)\s*(?P<unit>fl\s*oz|floz|oz|ounces?|lb|lbs|pounds?|ml|l)\b",
    re.IGNORECASE
)

def canonicalize_unit(unit: str) -> str:
    u = unit.strip().lower()
    u = u.replace(" ", "")
    if u in ("floz", "fl.oz", "fluidounce", "fluidounces"):
        u = "floz"
    return UNIT_ALIASES.get(u, UNIT_ALIASES.get(unit.strip().lower(), unit.strip().lower()))

def parse_size(size_raw: Optional[str], name: str) -> Tuple[Optional[float], Optional[str]]:
    """Try size_raw first, then parse from name. Handles pack patterns like 12×12oz, 12 pack 12 fl oz."""
    def _try_pack(s_norm: str) -> Tuple[Optional[float], Optional[str]]:
        for pattern in (_pack_x_re, _pack_pk_re, _pack_x_compact_re):
            m = pattern.search(s_norm)
            if m:
                count = int(m.group("count"))
                val = float(m.group("val"))
                unit = canonicalize_unit(m.group("unit"))
                total = count * val
                return total, unit
        return None, None

    def _parse(s: str) -> Tuple[Optional[float], Optional[str]]:
        s_norm = normalize_text(s)
        val, unit = _try_pack(s_norm)
        if val is not None and unit is not None:
            return val, unit
        m = _size_re.search(s_norm)
        if not m:
            return None, None
        val = float(m.group("val"))
        unit = canonicalize_unit(m.group("unit"))
        return val, unit

    if size_raw:
        val, unit = _parse(size_raw)
        if val is not None and unit is not None:
            return val, unit

    return _parse(name)


# Brands learned from catalog at import
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CATALOG_PATH = _PROJECT_ROOT / "data" / "existing_products.json"

BRAND_STOPWORDS = {
    "new", "sale", "promo", "organic", "fresh", "best", "boneless", "skinless",
    "family", "value", "pack", "size", "original", "ultra", "free", "gentle",
    "liquid", "laundry", "detergent", "dishwashing", "soap", "soda", "milk",
    "yogurt", "cookies", "pasta", "paper", "towels", "butter", "chicken",
    "breast", "the", "a", "an", "and", "&", "of", "with", "in", "on", "to",
    "approx", "approximately", "rollback", "limit", "limited", "deal",
}
BRAND_ALIASES: Dict[str, str] = {"coke": "coca-cola"}


def _extract_first_tokens(product_name: str) -> List[str]:
    """First token and first-two-token hyphenated from normalized name."""
    norm = normalize_text(product_name)
    tokens = norm.split()
    if not tokens:
        return []
    out = [tokens[0]]
    if len(tokens) >= 2:
        out.append(f"{tokens[0]}-{tokens[1]}")
    return out


def _learn_brands_from_catalog(
    existing_rows: List[dict],
    name_key: str = "product_name",
    min_count: int = 2,
) -> set:
    """Learn brand tokens from catalog; merge hyphenated forms by frequency."""
    counter: Counter = Counter()
    for row in existing_rows:
        name = row.get(name_key) or ""
        if not name.strip():
            continue
        for token in _extract_first_tokens(name):
            if not token:
                continue
            key = token.replace(" ", "-").strip()
            if key and key not in BRAND_STOPWORDS:
                counter[key] += 1
    count_map = {k: v for k, v in counter.items() if v >= min_count and k not in BRAND_STOPWORDS}
    to_drop = set()
    for token in list(count_map.keys()):
        if "-" not in token or token in to_drop:
            continue
        root = token.split("-", 1)[0]
        if root == token or root not in count_map or root in to_drop:
            continue
        if count_map[root] >= count_map[token]:
            count_map[root] += count_map[token]
            to_drop.add(token)
        else:
            count_map[token] += count_map[root]
            to_drop.add(root)
    for token in to_drop:
        del count_map[token]
    return set(count_map.keys())


def _get_known_brands(catalog_path: Optional[Path] = None) -> set:
    """Load catalog and return learned brand set; empty if file missing."""
    path = catalog_path or _DEFAULT_CATALOG_PATH
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return set()
        return _learn_brands_from_catalog(data)
    except (json.JSONDecodeError, OSError):
        return set()


KNOWN_BRANDS = _get_known_brands()


# Brand extraction
def extract_brand(normalized_name: str) -> Optional[str]:
    """Use KNOWN_BRANDS and BRAND_ALIASES (e.g. coke→coca-cola). Returns first matching brand or None."""
    tokens = normalized_name.split()
    joined = normalized_name.replace(" ", "")
    if "cocacola" in joined:
        return "coca-cola"
    for t in tokens[:5]:
        tt = t.replace("-", "")
        if t in BRAND_ALIASES or tt in BRAND_ALIASES:
            return BRAND_ALIASES.get(t, BRAND_ALIASES.get(tt))
        if t in KNOWN_BRANDS or tt in KNOWN_BRANDS:
            return BRAND_ALIASES.get(t, BRAND_ALIASES.get(tt, t))
    return None


# Tokenization / core name
def strip_promo_terms(tokens: List[str]) -> List[str]:
    out: List[str] = []
    for t in tokens:
        tt = t.strip().lower()
        if not tt:
            continue
        if tt in PROMO_TERMS:
            continue
        if tt.isdigit() and tt in {"2", "3", "4", "10"}:
            continue
        out.append(tt)
    return out

def tokenize_core_name(normalized_name: str, brand: Optional[str]) -> Tuple[str, List[str]]:
    """Return core_name string and token list."""
    tokens = normalized_name.split()
    tokens = strip_promo_terms(tokens)

    if brand:
        brand_simple = brand.replace("-", "")
        cleaned: List[str] = []
        removed = False
        for t in tokens:
            t_simple = t.replace("-", "")
            if not removed and (t == brand or t_simple == brand_simple):
                removed = True
                continue
            cleaned.append(t)
        tokens = cleaned

    tokens = [t for t in tokens if t not in STOPWORDS]

    # Split 16ct/12pk into number + ct
    expanded: List[str] = []
    for t in tokens:
        m = re.match(r"^(\d+)(ct|pk)$", t, re.IGNORECASE)
        if m:
            expanded.append(m.group(1))
            expanded.append("ct")
        else:
            expanded.append(t)
    tokens = expanded

    core_name = " ".join(tokens).strip()
    return core_name, tokens


# Main parse (used by match.py)
def parse_product(product_name: str, size_raw: Optional[str]) -> ParsedProduct:
    """Main entry point for normalization."""
    norm_name = normalize_text(product_name)
    norm_name = remove_promo_phrases(norm_name)
    norm_name = apply_synonyms(norm_name)
    brand = extract_brand(norm_name)
    size_value, size_unit = parse_size(size_raw, norm_name)
    core_name, tokens = tokenize_core_name(norm_name, brand)

    return ParsedProduct(
        raw_name=product_name,
        raw_size=size_raw,
        normalized_name=norm_name,
        brand=brand,
        core_name=core_name,
        size_value=size_value,
        size_unit=size_unit,
        tokens=tokens,
    )


if __name__ == "__main__":
    samples = [
        ("NEW! Tide liquid detergent - Original - 92oz Value Pack", "92 oz"),
        ("Tide Free and Gentle Liquid Detergent 92 fl. oz.", "92 fl oz"),
        ("Tide PODS Laundry Pacs Original 16ct - 2 for $10 (promo)", "16 ct"),
        ("Chobani Strawberry Greek Yogurt 5.3 ounces (LIMIT 4)", "5.3 ounces"),
        ("Barilla Spaghetti 1lb (16 oz) - BEST BY 2027", "1 lb"),
    ]
    for name, size in samples:
        p = parse_product(name, size)
        print("-" * 80)
        print("RAW:", p.raw_name)
        print("NORM:", p.normalized_name)
        print("BRAND:", p.brand)
        print("CORE:", p.core_name)
        print("SIZE:", p.size_value, p.size_unit)
        print("TOKENS:", p.tokens)

    scraped_path = _PROJECT_ROOT / "data" / "scraped_products.json"
    if scraped_path.exists():
        print("\n" + "=" * 80)
        print("EXTRACTED BRANDS (from data/scraped_products.json)")
        print("=" * 80)
        with open(scraped_path, encoding="utf-8") as f:
            scraped = json.load(f)
        print(f"{'id':<10} | {'product_name':<50} | brand")
        print("-" * 95)
        for r in scraped:
            p = parse_product(r.get("product_name", ""), r.get("size_raw"))
            name = (p.raw_name[:48] + "..") if len(p.raw_name) > 50 else p.raw_name
            brand = p.brand or "(none)"
            print(f"{r['id']:<10} | {name:<50} | {brand}")