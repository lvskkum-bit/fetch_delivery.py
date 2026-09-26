#!/usr/bin/env python3
"""Combine the locked Phase 3 inputs into one fail-closed mapping artifact."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from phase3_mapping_contract import MappingContractError, canonical_sha256, validate_nifty50_source_payload


def _fail(code: str, message: str) -> None:
    raise MappingContractError(code, message)


def _rows_by_symbol(payload: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows") or []
    keyed = {str(row.get("symbol") or "").strip().upper(): row for row in rows}
    if len(rows) != 50 or len(keyed) != 50 or "" in keyed:
        _fail("COMPONENT_50_GATE", f"{label}: rows={len(rows)}, unique={len(keyed)}")
    return keyed


def build_final_mapping(
    source: dict[str, Any],
    classifications: dict[str, Any],
    memberships: dict[str, Any],
    weights: dict[str, Any],
    instruments: dict[str, Any],
    last_known_good: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_nifty50_source_payload(source, expected_source_date=source.get("source_as_of_date"))
    for label, payload in (
        ("classification", classifications),
        ("membership", memberships),
        ("primary_weight", weights),
        ("instrument", instruments),
    ):
        if payload.get("status") != "PASS":
            _fail("COMPONENT_NOT_READY", f"{label} status={payload.get('status')}")

    if canonical_sha256(weights) != weights.get("source_checksum"):
        _fail("SOURCE_CHECKSUM_MISMATCH", "primary-weight checksum mismatch")
    if canonical_sha256(instruments) != instruments.get("source_checksum"):
        _fail("SOURCE_CHECKSUM_MISMATCH", "instrument checksum mismatch")

    verification_date = str(classifications.get("source_as_of_date") or "")
    if not verification_date or str(instruments.get("source_as_of_date") or "") != verification_date:
        _fail("DAILY_VERIFICATION_DATE_MISMATCH", "classification/instrument dates differ")
    if str(memberships.get("catalog_verification_date") or "") != verification_date:
        _fail("DAILY_VERIFICATION_DATE_MISMATCH", "membership catalog was not verified on daily date")
    if str(source.get("source_as_of_date") or "") > verification_date:
        _fail("DAILY_VERIFICATION_DATE_MISMATCH", "constituent source is newer than verification date")

    source_rows = {row["symbol"]: row for row in source["constituents"]}
    expected = set(source["symbols"])
    components = {
        "classification": _rows_by_symbol(classifications, "classification"),
        "membership": _rows_by_symbol(memberships, "membership"),
        "primary_weight": _rows_by_symbol(weights, "primary_weight"),
        "instrument": _rows_by_symbol(instruments, "instrument"),
    }
    for label, keyed in components.items():
        if set(keyed) != expected:
            _fail("COMPONENT_SYMBOL_SET", f"{label} symbol set mismatch")

    rows = []
    for symbol in source["symbols"]:
        base = source_rows[symbol]
        cls = components["classification"][symbol]
        mem = components["membership"][symbol]
        weight = components["primary_weight"][symbol]
        instrument = components["instrument"][symbol]
        isins = {base.get("isin"), cls.get("isin"), mem.get("isin"), instrument.get("isin")}
        if len(isins) != 1 or not next(iter(isins)):
            _fail("ROW_IDENTITY_MISMATCH", f"{symbol}: ISIN mismatch")
        applicable = mem.get("all_applicable_indices") or []
        if applicable != weight.get("all_applicable_indices"):
            _fail("MEMBERSHIP_WEIGHT_MISMATCH", f"{symbol}: membership lists differ")
        primary = weight.get("primary_sector_index")
        if primary not in applicable:
            _fail("PRIMARY_MEMBERSHIP_MISMATCH", f"{symbol}: Primary is not applicable")
        if weight.get("verification_status") != "VERIFIED" or instrument.get("verification_status") != "VERIFIED":
            _fail("REVIEW_REQUIRED", f"{symbol}: locked component is not VERIFIED")
        if instrument.get("instrument_key") != f"NSE_EQ|{base['isin']}":
            _fail("INSTRUMENT_KEY_MISMATCH", f"{symbol}: invalid instrument key")
        row = {
            "symbol": symbol,
            "company_name": base["company_name"],
            "isin": base["isin"],
            "sector": cls.get("sector"),
            "basic_industry": cls.get("basic_industry"),
            "all_membership_indices": applicable,
            "weight_comparisons": weight.get("weight_comparisons"),
            "primary_sector_index": primary,
            "primary_weight": weight.get("primary_weight"),
            "instrument_key": instrument.get("instrument_key"),
            "exchange_token": instrument.get("exchange_token"),
            "verification_as_of_date": verification_date,
            "weight_as_of_date": weights.get("weight_as_of_date"),
            "verification_status": "VERIFIED",
        }
        required = ("symbol", "isin", "sector", "basic_industry", "all_membership_indices", "primary_sector_index", "primary_weight", "instrument_key")
        if any(row.get(key) in (None, "", []) for key in required):
            _fail("PARTIAL_ROW", f"{symbol}: required field blank")
        row["row_checksum"] = canonical_sha256(row, excluded_keys=())
        rows.append(row)

    previous = last_known_good or {}
    added = sorted(expected - set(previous)) if previous else []
    removed = sorted(set(previous) - expected) if previous else []
    transition = "NO_CHANGE" if previous and not added and not removed else ("BASELINE_CREATED" if not previous else "ATOMIC_TRANSITION_READY")
    mapping = {
        "schema_version": "nifty50-mapping-v2",
        "status": "PASS",
        "decision": "READY",
        "verification_as_of_date": verification_date,
        "constituent_source_as_of_date": source["source_as_of_date"],
        "weight_as_of_date": weights["weight_as_of_date"],
        "count": len(rows),
        "production_write": False,
        "component_checksums": {
            "nifty50": source["source_checksum"],
            "primary_weights": weights["source_checksum"],
            "instruments": instruments["source_checksum"],
        },
        "rows": rows,
    }
    mapping["source_checksum"] = canonical_sha256(mapping)
    audit = {
        "schema_version": "phase3-mapping-audit-v2",
        "final_status": "PASS",
        "decision": "READY",
        "transition_status": transition,
        "verification_as_of_date": verification_date,
        "added": added,
        "removed": removed,
        "gate_counts": {
            "constituents": "50/50",
            "classifications": "50/50",
            "memberships": "50/50",
            "primary_weights": "50/50",
            "instruments": "50/50",
            "duplicates": 0,
            "partial_rows": 0,
        },
        "mapping_checksum": mapping["source_checksum"],
        "production_write": False,
    }
    audit["audit_checksum"] = canonical_sha256(audit, excluded_keys=("audit_checksum",))
    return mapping, audit


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = handle.name
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("nifty50_latest.json"))
    parser.add_argument("--artifacts", type=Path, default=Path("phase3_artifacts"))
    args = parser.parse_args()
    a = args.artifacts
    mapping, audit = build_final_mapping(
        _read(args.source),
        _read(a / "nifty50_classification_latest.json"),
        _read(a / "nifty50_memberships_latest.json"),
        _read(a / "nifty50_primary_sector_weights_latest.json"),
        _read(a / "nifty50_instruments_latest.json"),
    )
    _write(a / "nifty50_mapping_latest.json", mapping)
    _write(a / "phase3_mapping_audit_latest.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
