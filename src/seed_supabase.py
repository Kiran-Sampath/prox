# seed_supabase.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .db import get_supabase, upsert_rows

def load_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def main() -> None:
    sb = get_supabase()

    existing = load_json(Path("data/existing_products.json"))
    scraped = load_json(Path("data/scraped_products.json"))

    upsert_rows(sb, "existing_products", existing)
    upsert_rows(sb, "scraped_products", scraped)

    print(f"Seeded existing_products: {len(existing)} rows")
    print(f"Seeded scraped_products:  {len(scraped)} rows")

if __name__ == "__main__":
    main()