# build_brands.py
"""
Learn brand tokens from existing_products and write learned_brands.json for inspection.
Does not change normalize.py; use the output to seed KNOWN_BRANDS if you like.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .normalize import normalize_text

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "existing_products.json"
DEFAULT_OUTPUT = ROOT / "data" / "learned_brands.json"

# Tokens we never treat as brands (noise / descriptors)
BRAND_STOPWORDS = {
    "new", "sale", "promo", "organic", "fresh", "best", "boneless", "skinless",
    "family", "value", "pack", "size", "original", "ultra", "free", "gentle",
    "liquid", "laundry", "detergent", "dishwashing", "soap", "soda", "milk",
    "yogurt", "cookies", "pasta", "paper", "towels", "butter", "chicken",
    "breast", "the", "a", "an", "and", "&", "of", "with", "in", "on", "to",
    "approx", "approximately", "rollback", "limit", "limited", "deal",
}


def load_existing_products(path: Path) -> list:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data)}")
    return data


def extract_first_tokens(product_name: str) -> list:
    """First token and, if present, first two as hyphenated (e.g. coca-cola)."""
    norm = normalize_text(product_name)
    tokens = norm.split()
    if not tokens:
        return []
    out = [tokens[0]]
    if len(tokens) >= 2:
        out.append(f"{tokens[0]}-{tokens[1]}")
    return out


def learn_brands(
    existing_rows: list,
    name_key: str = "product_name",
    min_count: int = 2,
    stopwords: set = None,
) -> tuple:
    """
    Count first-token (and first-two-token) phrases; return those with >= min_count, excluding stopwords.
    For hyphenated vs root (e.g. coca-cola vs coca): keep the form with higher count and merge the other in.
    Returns (sorted brand list, count map).
    """
    stop = stopwords or BRAND_STOPWORDS
    counter = Counter()

    for row in existing_rows:
        name = row.get(name_key) or ""
        if not name.strip():
            continue
        for token in extract_first_tokens(name):
            if not token:
                continue
            key = token.replace(" ", "-").strip()
            if key and key not in stop:
                counter[key] += 1

    count_map = {k: v for k, v in counter.items() if v >= min_count and k not in stop}

    # Hyphenated vs root: keep higher count, merge the other in; tie -> keep root
    to_drop = set()
    for token in list(count_map.keys()):
        if "-" not in token or token in to_drop:
            continue
        root = token.split("-", 1)[0]
        if root == token or root not in count_map or root in to_drop:
            continue
        count_root = count_map[root]
        count_hyphenated = count_map[token]
        if count_root >= count_hyphenated:
            count_map[root] += count_hyphenated
            to_drop.add(token)
        else:
            count_map[token] += count_root
            to_drop.add(root)
    for token in to_drop:
        del count_map[token]

    brands = sorted(count_map.keys())
    return brands, dict(count_map)


def main(
    input_path: Path = None,
    output_path: Path = None,
    min_count: int = 2,
) -> None:
    input_path = input_path or DEFAULT_INPUT
    output_path = output_path or DEFAULT_OUTPUT

    if not input_path.exists():
        print(f"Input not found: {input_path}")
        return

    existing = load_existing_products(input_path)
    brands, counts = learn_brands(existing, min_count=min_count)

    payload = {
        "brands": brands,
        "counts": counts,
        "min_count": min_count,
        "source": str(input_path),
        "num_products": len(existing),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Learned {len(brands)} brands from {len(existing)} products (min_count={min_count})")
    print(f"Wrote: {output_path}")
    print("\nBrands:", brands)
    print("\nCounts:", counts)
    print("\nTo paste into normalize.py KNOWN_BRANDS:")
    print("KNOWN_BRANDS = {" + ", ".join(f'"{b}"' for b in brands) + "}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Learn brand tokens from existing_products.json")
    parser.add_argument("--input", type=Path, default=None, help="Path to existing_products.json")
    parser.add_argument("--output", type=Path, default=None, help="Path to write learned_brands.json")
    parser.add_argument("--min-count", type=int, default=2, help="Min occurrences to keep (default 2)")
    args = parser.parse_args()
    main(input_path=args.input, output_path=args.output, min_count=args.min_count)
