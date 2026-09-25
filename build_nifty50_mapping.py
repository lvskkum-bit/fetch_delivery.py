#!/usr/bin/env python3
"""Build and audit the fail-closed Phase 3 NIFTY50 mapping artifact."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phase3_mapping_contract import (
    MappingContractError,
    canonical_sha256,
    choose_primary_index,
    validate_classification_50,
    validate_nifty50_source_payload,
)


MAPPING_SCHEMA_VERSION = "nifty50-mapping-v1"
RULES = {
    "type_priority": {"SECTORAL": 2, "THEMATIC": 1},
}


def _mapping_fail(code: str, message: str) -> None:
    raise MappingContractError(code, message)


def _month(value: str) -> str:
    return str(value or "")[:7]


def _combined_row_checksum(row: dict[str, Any]) -> str:
    return canonical_sha256({"row": row}, excluded_keys=())


def build_mapping(
    source: dict[str, Any],
    classifications: dict[str, dict[str, Any]],
    memberships: dict[str, list[str]],
    weights: dict[str, Any],
    instruments: dict[str, dict[str, Any]],
    last_known_good: dict[str, dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_date = str(source.get("source_as_of_date") or "")
    validate_nifty50_source_payload(source, expected_source_date=source_date)
    symbols = [str(value).strip().upper() for value in source["symbols"]]
    symbol_set = set(symbols)

    if set(classifications) != symbol_set:
        _mapping_fail("CLASSIFICATION_SYMBOL_SET", "classification set mismatch")
    validate_classification_50(list(classifications.values()), symbol_set)
    if set(memberships) != symbol_set:
        _mapping_fail("MEMBERSHIP_50", "membership set mismatch")
    if set(instruments) != symbol_set:
        _mapping_fail("INSTRUMENT_MAPPING_50", "instrument set mismatch")

    for symbol in symbols:
        cls_date = str(classifications[symbol].get("as_of_date") or "")
        instrument_date = str(instruments[symbol].get("as_of_date") or "")
        if cls_date != source_date or instrument_date != source_date:
            _mapping_fail(
                "SOURCE_DATE_MISMATCH",
                f"{symbol}: source={source_date}, classification={cls_date}, instrument={instrument_date}",
            )

    previous = last_known_good or {}
    previous_symbols = set(previous)
    added = sorted(symbol_set - previous_symbols)
    removed = sorted(previous_symbols - symbol_set)
    unchanged = sorted(symbol_set & previous_symbols)
    weight_month = _month(str(weights.get("as_of_date") or ""))
    expected_month = _month(source_date)

    constituent_by_symbol = {
        str(row["symbol"]).strip().upper(): row for row in source["constituents"]
    }
    rows = []
    for symbol in symbols:
        constituent = constituent_by_symbol[symbol]
        classification = classifications[symbol]
        member_indices = sorted(set(memberships[symbol]))
        if not member_indices:
            _mapping_fail(
                "MEMBERSHIP_REVIEW_REQUIRED", f"empty membership for {symbol}"
            )

        index_weights = {
            index_name: weights.get("values", {}).get(index_name, {}).get(symbol)
            for index_name in member_indices
            if weights.get("values", {}).get(index_name, {}).get(symbol) is not None
        }
        is_new = symbol in added
        if weight_month != expected_month or not index_weights:
            if is_new:
                _mapping_fail(
                    "NEW_STOCK_WEIGHT_WAIT",
                    f"new symbol {symbol} lacks verified current-month weight",
                )
            last = previous.get(symbol, {})
            primary = {
                "primary_sector_index": str(
                    last.get("primary_sector_index") or ""
                ),
                "primary_weight": last.get("primary_weight"),
                "verification_status": "WEIGHT_SOURCE_STALE",
            }
            if not primary["primary_sector_index"]:
                _mapping_fail(
                    "WEIGHT_REVIEW_REQUIRED",
                    f"no last-known-good Primary for {symbol}",
                )
        else:
            primary = choose_primary_index(
                member_indices,
                weights["values"],
                weights["metadata"],
                symbol,
                RULES,
            )

        row = {
            "symbol": symbol,
            "company_name": constituent["company_name"],
            "isin": constituent["isin"],
            "macro_sector": classification["macro_sector"],
            "sector": classification["sector"],
            "industry": classification["industry"],
            "basic_industry": classification["basic_industry"],
            "all_membership_indices": member_indices,
            "index_wise_weights": index_weights,
            "primary_sector_index": primary["primary_sector_index"],
            "primary_weight": primary["primary_weight"],
            "previous_primary_index": str(
                previous.get(symbol, {}).get("primary_sector_index") or ""
            ),
            "classification_source": classification.get("source_url", ""),
            "membership_source": source["source_url"],
            "weight_source": weights.get("source_url", ""),
            "classification_as_of_date": classification["as_of_date"],
            "membership_as_of_date": source_date,
            "weight_as_of_date": weights.get("as_of_date", ""),
            "instrument_key": instruments[symbol]["instrument_key"],
            "source_checksum": "",
            "verification_status": primary["verification_status"],
            "review_reason": (
                "weight source stale; last-known-good retained"
                if primary["verification_status"] == "WEIGHT_SOURCE_STALE"
                else ""
            ),
            "last_verified_ist": datetime.now(timezone.utc).isoformat(),
        }
        row["source_checksum"] = _combined_row_checksum(row)
        rows.append(row)

    partial_rows = sum(
        1
        for row in rows
        if not all(
            [
                row["symbol"],
                row["isin"],
                row["macro_sector"],
                row["sector"],
                row["industry"],
                row["basic_industry"],
                row["all_membership_indices"],
                row["primary_sector_index"],
                row["instrument_key"],
                row["source_checksum"],
            ]
        )
    )
    duplicate_count = len(rows) - len({row["symbol"] for row in rows})
    if len(rows) != 50 or partial_rows or duplicate_count:
        _mapping_fail(
            "MAPPING_VALIDATION_FAILED",
            f"rows={len(rows)}, partial={partial_rows}, duplicates={duplicate_count}",
        )

    payload = {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "status": "PASS",
        "source_as_of_date": source_date,
        "source_payload_checksum": source["source_checksum"],
        "count": 50,
        "rows": rows,
    }
    payload["source_checksum"] = canonical_sha256(payload)

    transition_status = (
        "NO_CHANGE" if not added and not removed else "ATOMIC_TRANSITION_READY"
    )
    audit = {
        "schema_version": "phase3-mapping-audit-v1",
        "final_status": "PASS",
        "transition_status": transition_status,
        "source_as_of_date": source_date,
        "added": added,
        "removed": removed,
        "unchanged_count": len(unchanged),
        "gate_counts": {
            "source_symbols": "50/50",
            "mapping_rows": "50/50",
            "classification": "50/50",
            "memberships": "50/50",
            "primary_index": "50/50",
            "instrument_mapping": "50/50",
            "duplicates": duplicate_count,
            "partial_rows": partial_rows,
        },
        "mapping_checksum": payload["source_checksum"],
    }
    return payload, audit


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--classifications-json", type=Path, required=True)
    parser.add_argument("--memberships-json", type=Path, required=True)
    parser.add_argument("--weights-json", type=Path, required=True)
    parser.add_argument("--instruments-json", type=Path, required=True)
    parser.add_argument("--last-known-good-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["dry-run"], default="dry-run")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    last_known_good = (
        _read_json(args.last_known_good_json) if args.last_known_good_json else None
    )
    payload, audit = build_mapping(
        _read_json(args.source_json),
        _read_json(args.classifications_json),
        _read_json(args.memberships_json),
        _read_json(args.weights_json),
        _read_json(args.instruments_json),
        last_known_good,
    )
    if not args.no_publish:
        _atomic_write(args.output_dir / "nifty50_mapping_latest.json", payload)
    _atomic_write(args.output_dir / "phase3_mapping_audit_latest.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
