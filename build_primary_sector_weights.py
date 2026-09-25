#!/usr/bin/env python3
"""Build the fail-closed August-2026 Primary Sector Index audit artifact."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from phase3_mapping_contract import (
    canonical_sha256,
    choose_primary_index_from_weight_evidence,
    extract_factsheet_weight_evidence,
    validate_primary_weights_50,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factsheet-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--memberships", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = json.loads(args.source.read_text(encoding="utf-8"))
    memberships = json.loads(args.memberships.read_text(encoding="utf-8"))
    companies = {
        row["symbol"]: row["company_name"] for row in source["constituents"]
    }
    membership_rows = {row["symbol"]: row for row in memberships["rows"]}

    jobs: list[tuple[int, str, str]] = []
    with (args.factsheet_dir / "jobs.tsv").open(encoding="utf-8") as handle:
        for number, index_name, url in csv.reader(handle, delimiter="\t"):
            jobs.append((int(number), index_name, url))

    evidence = {}
    sources = []
    for number, index_name, url in jobs:
        pdf_path = args.factsheet_dir / f"{number:02d}.pdf"
        text_path = args.factsheet_dir / f"{number:02d}.txt"
        if not pdf_path.is_file() or not text_path.is_file():
            raise SystemExit(f"missing official factsheet evidence: {index_name}")
        item = extract_factsheet_weight_evidence(
            text_path.read_text(encoding="utf-8", errors="ignore"), companies
        )
        evidence[index_name] = item
        sources.append(
            {
                "index_name": index_name,
                "factsheet_url": url,
                "factsheet_as_of_date": item["as_of_date"],
                "factsheet_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                "unlisted_weight_upper_bound": item[
                    "unlisted_weight_upper_bound"
                ],
            }
        )

    rows = []
    for symbol in sorted(companies):
        applicable = membership_rows[symbol]["all_applicable_indices"]
        selected = choose_primary_index_from_weight_evidence(
            symbol, applicable, evidence
        )
        comparisons = []
        for index_name in applicable:
            index_evidence = evidence[index_name]
            if symbol in index_evidence["verified_weights"]:
                comparisons.append(
                    {
                        "index_name": index_name,
                        "evidence_type": "EXACT_TOP_CONSTITUENT_WEIGHT",
                        "weight": index_evidence["verified_weights"][symbol],
                    }
                )
            else:
                comparisons.append(
                    {
                        "index_name": index_name,
                        "evidence_type": "NON_TOP_CONSTITUENT_UPPER_BOUND",
                        "weight_upper_bound": index_evidence[
                            "unlisted_weight_upper_bound"
                        ],
                    }
                )
        rows.append(
            {
                "symbol": symbol,
                "all_applicable_indices": applicable,
                "weight_comparisons": comparisons,
                **selected,
            }
        )

    gate = validate_primary_weights_50(rows, set(companies))
    payload = {
        "schema_version": "phase3-primary-sector-weight-v1",
        "status": "PASS",
        "scope_lock": "SECTOR_AND_INDUSTRY_TREND_INDICES_ONLY",
        "weight_as_of_date": "2026-08-31",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "official_factsheet_count": len(sources),
        "nifty50_symbol_count": len(rows),
        "verified_primary_index_count": sum(
            row["verification_status"] == "VERIFIED" for row in rows
        ),
        "review_required_count": 0,
        "primary_weight_gate": gate,
        "method": "Highest exact August-2026 weight; a non-top-10 membership is bounded by that official factsheet's tenth weight and cannot win unless the bound is below the exact winner.",
        "production_write": False,
        "sources": sources,
        "rows": rows,
    }
    payload["source_checksum"] = canonical_sha256(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": payload["status"],
        "factsheets": payload["official_factsheet_count"],
        "verified": payload["verified_primary_index_count"],
        "gate": gate,
        "checksum": payload["source_checksum"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
