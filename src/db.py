# db.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from supabase import create_client, Client

# Load .env from project root so "python -m src.*" works
_load_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_load_env, override=True)

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment/.env")
    return create_client(url, key)

def fetch_table(sb: Client, table: str, limit: int = 5000) -> List[Dict[str, Any]]:
    resp = sb.table(table).select("*").limit(limit).execute()
    return list(resp.data or [])

def upsert_rows(sb: Client, table: str, rows: List[Dict[str, Any]], chunk: int = 500) -> None:
    for i in range(0, len(rows), chunk):
        part = rows[i:i+chunk]
        sb.table(table).upsert(part).execute()

def upsert_matches(sb: Client, rows: List[Dict[str, Any]], chunk: int = 500) -> None:
    """Rows: scraped_product_id, matched_existing_id, match_score, match_method."""
    for i in range(0, len(rows), chunk):
        part = rows[i:i+chunk]
        sb.table("product_matches").upsert(part, on_conflict="scraped_product_id").execute()