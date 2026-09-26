#!/usr/bin/env python3
"""Build the fail-closed NIFTY50 Upstox instrument-key artifact."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from phase3_mapping_contract import MappingContractError, canonical_sha256


EXPECTED_COUNT = 50


def _fail(code: str, message: str) -> None:
    raise MappingContractError(code, message)


def resolve_instrument_mapping(
    constituents: list[dict[str, Any]],
    instrument_master: list[dict[str, Any]],
    as_of_date: str,
) -> list[dict[str, Any]]:
    try:
        date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        _fail("INSTRUMENT_SOURCE_DATE", f"invalid date: {as_of_date!r}")

    if len(constituents) != EXPECTED_COUNT:
        _fail(
            "INSTRUMENT_CONSTITUENT_COUNT_50",
            f"expected 50 constituents, found {len(constituents)}",
        )
    symbols = [str(row.get("symbol") or "").strip().upper() for row in constituents]
    isins = [str(row.get("isin") or "").strip().upper() for row in constituents]
    if len(set(symbols)) != EXPECTED_COUNT or len(set(isins)) != EXPECTED_COUNT:
        _fail(
            "INSTRUMENT_CONSTITUENT_UNIQUE",
            "constituent symbols and ISINs must be 50/50 unique",
        )

    eligible_by_isin: dict[str, list[dict[str, Any]]] = {}
    for item in instrument_master:
        if (
            str(item.get("segment") or "").strip().upper() == "NSE_EQ"
            and str(item.get("exchange") or "").strip().upper() == "NSE"
            and str(item.get("instrument_type") or "").strip().upper() == "EQ"
        ):
            isin = str(item.get("isin") or "").strip().upper()
            eligible_by_isin.setdefault(isin, []).append(item)

    resolved = []
    for constituent in constituents:
        symbol = str(constituent.get("symbol") or "").strip().upper()
        isin = str(constituent.get("isin") or "").strip().upper()
        matches = eligible_by_isin.get(isin, [])
        if not matches:
            _fail("INSTRUMENT_MISSING", f"no NSE_EQ/EQ instrument for {symbol}")
        if len(matches) != 1:
            _fail(
                "INSTRUMENT_DUPLICATE_MATCH",
                f"{symbol} has {len(matches)} eligible instruments",
            )
        item = matches[0]
        trading_symbol = str(item.get("trading_symbol") or "").strip().upper()
        if trading_symbol != symbol:
            _fail(
                "INSTRUMENT_SYMBOL_MISMATCH",
                f"{symbol} matched instrument symbol {trading_symbol}",
            )
        instrument_key = str(item.get("instrument_key") or "").strip()
        expected_key = f"NSE_EQ|{isin}"
        if instrument_key != expected_key:
            _fail(
                "INSTRUMENT_KEY_MISMATCH",
                f"{symbol}: expected {expected_key}, found {instrument_key}",
            )
        resolved.append(
            {
                "symbol": symbol,
                "isin": isin,
                "instrument_key": instrument_key,
                "exchange_token": str(item.get("exchange_token") or "").strip(),
                "segment": "NSE_EQ",
                "instrument_type": "EQ",
                "as_of_date": as_of_date,
                "verification_status": "VERIFIED",
            }
        )

    keys = [row["instrument_key"] for row in resolved]
    if len(set(keys)) != EXPECTED_COUNT:
        _fail("INSTRUMENT_KEY_UNIQUE", "instrument keys are not 50/50 unique")
    if any(not row["exchange_token"] for row in resolved):
        _fail("INSTRUMENT_TOKEN_BLANK", "blank exchange token")
    return sorted(resolved, key=lambda row: row["symbol"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constituents-json", type=Path, required=True)
    parser.add_argument("--instrument-gzip", type=Path, required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--http-last-modified", required=True)
    parser.add_argument("--http-etag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()

    source = json.loads(args.constituents_json.read_text(encoding="utf-8"))
    compressed = args.instrument_gzip.read_bytes()
    master = json.loads(gzip.decompress(compressed))
    rows = resolve_instrument_mapping(
        source["constituents"], master, args.as_of_date
    )
    payload = {
        "schema_version": "phase3-instrument-mapping-v1",
        "status": "PASS",
        "source": "Upstox BOD Instruments — NSE JSON",
        "source_url": args.source_url,
        "source_as_of_date": args.as_of_date,
        "constituent_source_as_of_date": source["source_as_of_date"],
        "fetched_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "http_last_modified": args.http_last_modified,
        "http_etag": args.http_etag,
        "compressed_source_sha256": hashlib.sha256(compressed).hexdigest(),
        "master_record_count": len(master),
        "count": len(rows),
        "rows": rows,
        "production_write": False,
    }
    payload["source_checksum"] = canonical_sha256(payload)
    audit = {
        "schema_version": "phase3-instrument-mapping-audit-v1",
        "status": "PASS",
        "source_as_of_date": args.as_of_date,
        "symbol_count": len(rows),
        "unique_symbol_count": len({row["symbol"] for row in rows}),
        "instrument_key_count": len(rows),
        "unique_instrument_key_count": len(
            {row["instrument_key"] for row in rows}
        ),
        "isin_symbol_parity_count": sum(
            row["instrument_key"] == f"NSE_EQ|{row['isin']}" for row in rows
        ),
        "blank_count": sum(
            not row["instrument_key"] or not row["exchange_token"] for row in rows
        ),
        "duplicate_count": len(rows)
        - len({row["instrument_key"] for row in rows}),
        "review_required_count": sum(
            row["verification_status"] != "VERIFIED" for row in rows
        ),
        "input_checksum": payload["source_checksum"],
        "production_write": False,
    }
    audit["audit_checksum"] = canonical_sha256(
        audit, excluded_keys=("audit_checksum",)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.audit_output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
