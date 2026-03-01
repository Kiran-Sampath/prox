# run_match.py
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from .db import get_supabase, fetch_table, upsert_matches
from .match import match_all, MatchResult


def load_json(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}, got {type(data)}")
    return data


def write_results_csv(path: Path, results: List[MatchResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scraped_id", "matched_existing_id", "match_score", "match_method"])
        for r in results:
            w.writerow([r.scraped_id, r.matched_existing_id or "", r.match_score, r.match_method])


def print_summary(results: List[MatchResult]) -> None:
    total = len(results)
    matched = sum(1 for r in results if r.matched_existing_id is not None)
    rejected = total - matched

    by_method: Dict[str, int] = {}
    for r in results:
        by_method[r.match_method] = by_method.get(r.match_method, 0) + 1

    print("\n=== Match Summary ===")
    print(f"Total scraped: {total}")
    print(f"Matched:       {matched} ({matched/total:.1%})")
    print(f"Rejected:      {rejected} ({rejected/total:.1%})")
    print("\nBy method:")
    for k, v in sorted(by_method.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {k:28s} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Track C matcher: scraped -> existing")
    parser.add_argument("--existing", default="data/existing_products.json", help="Path to existing_products.json")
    parser.add_argument("--scraped", default="data/scraped_products.json", help="Path to scraped_products.json")
    parser.add_argument("--out", default="validation/results.csv", help="Output CSV path")
    parser.add_argument("--supabase", action="store_true", help="Load existing/scraped from Supabase tables instead of JSON")
    parser.add_argument("--write-matches", action="store_true", help="Write match results to Supabase product_matches table")
    args = parser.parse_args()

    if args.supabase:
        sb = get_supabase()
        existing = fetch_table(sb, "existing_products")
        scraped = fetch_table(sb, "scraped_products")
    else:
        existing_path = Path(args.existing)
        scraped_path = Path(args.scraped)
        existing = load_json(existing_path)
        scraped = load_json(scraped_path)
        sb = None

    results = match_all(scraped, existing)

    out_path = Path(args.out)
    write_results_csv(out_path, results)

    if args.write_matches:
        if sb is None:
            sb = get_supabase()
        rows = [
            {
                "scraped_product_id": r.scraped_id,
                "matched_existing_id": r.matched_existing_id,
                "match_score": r.match_score,
                "match_method": r.match_method,
            }
            for r in results
        ]
        upsert_matches(sb, rows)
        print(f"Wrote {len(rows)} matches to Supabase product_matches")

    print_summary(results)
    print(f"\nWrote: {out_path.resolve()}\n")


if __name__ == "__main__":
    main()