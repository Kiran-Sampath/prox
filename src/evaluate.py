# evaluate.py
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List, Tuple


@dataclass(frozen=True)
class ResultRow:
    scraped_id: str
    matched_existing_id: Optional[str]
    match_score: float
    match_method: str


@dataclass(frozen=True)
class ExpectedRow:
    scraped_id: str
    expected_outcome: str  # "match" or "reject"
    expected_existing_id: Optional[str]
    notes: str


def _read_results(results_path: Path) -> Dict[str, ResultRow]:
    if not results_path.exists():
        raise FileNotFoundError(f"Missing results file: {results_path}")

    out: Dict[str, ResultRow] = {}
    with results_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            scraped_id = row["scraped_id"].strip()
            matched = row["matched_existing_id"].strip() or None
            score = float(row["match_score"])
            method = row["match_method"].strip()
            out[scraped_id] = ResultRow(
                scraped_id=scraped_id,
                matched_existing_id=matched,
                match_score=score,
                match_method=method,
            )
    return out


def _read_expected(sample_path: Path) -> List[ExpectedRow]:
    if not sample_path.exists():
        raise FileNotFoundError(f"Missing sample file: {sample_path}")

    rows: List[ExpectedRow] = []
    with sample_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            scraped_id = row["scraped_id"].strip()
            outcome = row["expected_outcome"].strip().lower()
            if outcome not in {"match", "reject"}:
                raise ValueError(f"Invalid expected_outcome for {scraped_id}: {outcome}")

            exp_id = (row.get("expected_existing_id") or "").strip() or None
            notes = (row.get("notes") or "").strip()
            rows.append(
                ExpectedRow(
                    scraped_id=scraped_id,
                    expected_outcome=outcome,
                    expected_existing_id=exp_id,
                    notes=notes,
                )
            )
    return rows


def _allowed_existing_ids(expected_existing_id: Optional[str]) -> set:
    """Allow pipe-separated existing IDs per row (e.g. ex_005|ex_006 for same product)."""
    if not expected_existing_id:
        return set()
    return {s.strip() for s in expected_existing_id.split("|") if s.strip()}


def evaluate(
    results: Dict[str, ResultRow],
    expected_rows: List[ExpectedRow],
) -> Tuple[int, int, List[str]]:
    correct = 0
    total = len(expected_rows)
    mismatches: List[str] = []

    for e in expected_rows:
        got = results.get(e.scraped_id)
        if got is None:
            mismatches.append(f"{e.scraped_id}: missing in results (expected {e.expected_outcome})")
            continue

        if e.expected_outcome == "reject":
            if got.matched_existing_id is None:
                correct += 1
            else:
                mismatches.append(
                    f"{e.scraped_id}: expected REJECT, got MATCH={got.matched_existing_id} "
                    f"(score={got.match_score}, method={got.match_method}) | {e.notes}"
                )
        else:
            allowed = _allowed_existing_ids(e.expected_existing_id)
            if got.matched_existing_id in allowed:
                correct += 1
            else:
                mismatches.append(
                    f"{e.scraped_id}: expected MATCH={e.expected_existing_id}, got "
                    f"{got.matched_existing_id} (score={got.match_score}, method={got.match_method}) | {e.notes}"
                )

    return correct, total, mismatches


def main() -> None:
    results_path = Path("validation/results.csv")
    sample_path = Path("validation/sample_20.csv")

    results = _read_results(results_path)
    expected_rows = _read_expected(sample_path)

    correct, total, mismatches = evaluate(results, expected_rows)

    print("\n=== Validation (sample_20.csv) ===")
    print(f"Cases: {total}  |  Correct: {correct}/{total} ({(correct/total)*100:.1f}%)")

    if mismatches:
        print("\nMismatches:")
        for m in mismatches:
            print(f" - {m}")
    else:
        print("\nNo mismatches OK")

    sample_matched = sum(1 for e in expected_rows if e.expected_outcome == "match")
    sample_rejected = total - sample_matched
    print(f"\nSample mix: {sample_matched} expected matches, {sample_rejected} expected rejects")
    print(f"Using results from: {results_path.resolve()}")


if __name__ == "__main__":
    main()