#!/usr/bin/env python3
"""Combine the locked Phase 3 inputs into one fail-closed mapping artifact."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from phase3_mapping_contract import (
    MappingContractError,
    SourceContractError,
    canonical_sha256,
    validate_nifty50_source_payload,
)


def _fail(code: str, message: str) -> None:
    raise MappingContractError(code, message)


def _rows_by_symbol(payload: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows") or []
    keyed = {str(row.get("symbol") or "").strip().upper(): row for row in rows}
    if len(rows) != 50 or len(keyed) != 50 or "" in keyed:
        _fail("COMPONENT_50_GATE", f"{label}: rows={len(rows)}, unique={len(keyed)}")
    return keyed


def _artifact_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else []


def _audit_duplicate_count(source: dict[str, Any], *components: dict[str, Any]) -> int:
    source_symbols = source.get("symbols")
    symbol_lists = [source_symbols if isinstance(source_symbols, list) else []]
    symbol_lists.extend(
        [row.get("symbol") for row in _artifact_rows(payload)]
        for payload in components
    )
    return sum(
        len(symbols) - len({str(symbol).strip().upper() for symbol in symbols})
        for symbols in symbol_lists
    )


def _audit_partial_row_count(
    source: dict[str, Any],
    classifications: dict[str, Any],
    memberships: dict[str, Any],
    weights: dict[str, Any],
    instruments: dict[str, Any],
) -> int:
    source_constituents = source.get("constituents")
    source_rows = {
        str(row.get("symbol") or "").strip().upper(): row
        for row in (source_constituents if isinstance(source_constituents, list) else [])
        if isinstance(row, dict)
    }
    component_rows = []
    for payload in (classifications, memberships, weights, instruments):
        component_rows.append(
            {
                str(row.get("symbol") or "").strip().upper(): row
                for row in _artifact_rows(payload)
            }
        )

    required = (
        ("company_name", "isin"),
        ("isin", "sector", "basic_industry"),
        ("isin", "all_applicable_indices"),
        ("primary_sector_index", "primary_weight", "all_applicable_indices"),
        ("isin", "instrument_key"),
    )
    partial = 0
    source_symbols = source.get("symbols")
    for symbol in source_symbols if isinstance(source_symbols, list) else []:
        rows = [source_rows.get(symbol)] + [items.get(symbol) for items in component_rows]
        if any(row is None for row in rows):
            partial += 1
            continue
        if any(
            rows[index].get(field) in (None, "", [])
            for index, fields in enumerate(required)
            for field in fields
        ):
            partial += 1
    return partial


def _normalize_numbers(value: Any) -> Any:
    """Use one JSON number representation across Python and JavaScript."""
    if isinstance(value, dict):
        return {key: _normalize_numbers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(child) for child in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def build_final_mapping(
    source: dict[str, Any],
    classifications: dict[str, Any],
    memberships: dict[str, Any],
    weights: dict[str, Any],
    instruments: dict[str, Any],
    last_known_good: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_nifty50_source_payload(
        source,
        expected_source_date=source.get("source_as_of_date"),
    )
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
    source_date = str(source.get("source_as_of_date") or "")
    if source_date != verification_date:
        _fail("DAILY_VERIFICATION_DATE_MISMATCH", "constituent source and daily verification dates differ")
    if str(memberships.get("source_as_of_date") or "") != source_date:
        _fail("DAILY_VERIFICATION_DATE_MISMATCH", "memberships were built from a different constituent source date")
    if str(instruments.get("constituent_source_as_of_date") or "") != source_date:
        _fail("DAILY_VERIFICATION_DATE_MISMATCH", "instruments were resolved from a different constituent source date")
    for label, payload in (
        ("classification", classifications),
        ("membership", memberships),
    ):
        if not payload.get("source_checksum"):
            _fail("COMPONENT_CHECKSUM_MISSING", f"{label} checksum is missing")
        if canonical_sha256(payload) != payload["source_checksum"]:
            _fail("SOURCE_CHECKSUM_MISMATCH", f"{label} checksum mismatch")

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
        if str(instrument.get("as_of_date") or "") != verification_date:
            _fail(
                "DAILY_VERIFICATION_DATE_MISMATCH",
                f"{symbol}: instrument row was not verified on daily date",
            )
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
            "weight_comparisons": _normalize_numbers(weight.get("weight_comparisons")),
            "primary_sector_index": primary,
            "primary_weight": _normalize_numbers(weight.get("primary_weight")),
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
            "classification": canonical_sha256(classifications),
            "memberships": canonical_sha256(memberships),
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
        "constituent_source_as_of_date": source_date,
        "verification_as_of_date": verification_date,
        "weight_as_of_date": weights["weight_as_of_date"],
        "added": added,
        "removed": removed,
        "unchanged_count": len(expected & set(previous)),
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


def publish_final_artifacts(
    source: dict[str, Any],
    classifications: dict[str, Any],
    memberships: dict[str, Any],
    weights: dict[str, Any],
    instruments: dict[str, Any],
    output_dir: Path,
    last_known_good: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Atomically publish READY artifacts or a fail-closed WAIT audit.

    A failed build never replaces the last-known-good mapping file.
    """
    last_known_good_checksum = None
    if last_known_good is None:
        previous_path = output_dir / "nifty50_mapping_latest.json"
        try:
            previous_payload = _read(previous_path)
            previous_rows = previous_payload.get("rows") or []
            if previous_payload.get("schema_version") == "nifty50-mapping-v2":
                previous = {
                    str(row.get("symbol") or "").strip().upper(): row
                    for row in previous_rows
                    if isinstance(row, dict) and row.get("symbol")
                }
                if len(previous) == len(previous_rows):
                    last_known_good = previous
                    last_known_good_checksum = previous_payload.get("source_checksum")
        except (OSError, json.JSONDecodeError):
            pass

    try:
        mapping, audit = build_final_mapping(
            source,
            classifications,
            memberships,
            weights,
            instruments,
            last_known_good=last_known_good,
        )
    except (MappingContractError, SourceContractError) as error:
        raw_symbols = source.get("symbols")
        source_symbols = {
            str(symbol).strip().upper()
            for symbol in (raw_symbols if isinstance(raw_symbols, list) else [])
        }
        previous_symbols = set(last_known_good or {})
        added = sorted(source_symbols - previous_symbols) if previous_symbols else []
        removed = sorted(previous_symbols - source_symbols) if previous_symbols else []
        transition_status = (
            "BASELINE_UNAVAILABLE"
            if not previous_symbols
            else "NO_CHANGE"
            if not added and not removed
            else "ATOMIC_TRANSITION_REVALIDATION_REQUIRED"
        )
        component_checksums = {
            "nifty50": source.get("source_checksum"),
            "classification": classifications.get("source_checksum"),
            "memberships": memberships.get("source_checksum"),
            "primary_weights": weights.get("source_checksum"),
            "instruments": instruments.get("source_checksum"),
        }
        audit = {
            "schema_version": "phase3-mapping-audit-v2",
            "final_status": "DATA_NOT_READY",
            "decision": "WAIT",
            "error_code": error.code,
            "error_message": str(error),
            "constituent_source_as_of_date": source.get("source_as_of_date"),
            "verification_as_of_date": classifications.get("source_as_of_date"),
            "weight_as_of_date": weights.get("weight_as_of_date"),
            "added": added,
            "removed": removed,
            "transition_status": transition_status,
            "unchanged_count": len(source_symbols & previous_symbols),
            "gate_counts": {
                "constituents": f"{len(source_symbols)}/50",
                "classifications": f"{len(_artifact_rows(classifications))}/50",
                "memberships": f"{len(_artifact_rows(memberships))}/50",
                "primary_weights": f"{len(_artifact_rows(weights))}/50",
                "instruments": f"{len(_artifact_rows(instruments))}/50",
                "duplicates": _audit_duplicate_count(
                    source, classifications, memberships, weights, instruments
                ),
                "partial_rows": _audit_partial_row_count(
                    source, classifications, memberships, weights, instruments
                ),
            },
            "component_checksums": component_checksums,
            "last_known_good_mapping_checksum": last_known_good_checksum,
            "production_write": False,
        }
        audit["audit_checksum"] = canonical_sha256(
            audit, excluded_keys=("audit_checksum",)
        )
        _write(output_dir / "phase3_mapping_audit_latest.json", audit)
        return None, audit

    _write(output_dir / "nifty50_mapping_latest.json", mapping)
    _write(output_dir / "phase3_mapping_audit_latest.json", audit)
    return mapping, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("nifty50_latest.json"))
    parser.add_argument("--artifacts", type=Path, default=Path("phase3_artifacts"))
    args = parser.parse_args()
    a = args.artifacts
    mapping, audit = publish_final_artifacts(
        _read(args.source),
        _read(a / "nifty50_classification_latest.json"),
        _read(a / "nifty50_memberships_latest.json"),
        _read(a / "nifty50_primary_sector_weights_latest.json"),
        _read(a / "nifty50_instruments_latest.json"),
        output_dir=a,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if mapping is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
