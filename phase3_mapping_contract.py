"""Fail-closed contracts shared by the Phase 3 source producers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse


EXPECTED_STOCKS = 50
SOURCE_SCHEMA_VERSION = "nifty50-source-v2"
ALLOWED_SOURCE_HOSTS = {
    "www.niftyindices.com",
    "nsearchives.nseindia.com",
}


class SourceContractError(ValueError):
    """A stable, machine-readable source-contract failure."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _fail(code: str, message: str) -> None:
    raise SourceContractError(code, message)


def _parse_iso_date(value: str, code: str) -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        _fail(code, f"invalid ISO date: {value!r}")
    if str(parsed) != str(value):
        _fail(code, f"date must be YYYY-MM-DD: {value!r}")
    return parsed


def _validate_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        _fail("FETCHED_AT_FORMAT", f"invalid ISO timestamp: {value!r}")


def canonical_sha256(
    payload: dict[str, Any],
    excluded_keys: tuple[str, ...] = ("source_checksum",),
) -> str:
    canonical = {
        key: value for key, value in payload.items() if key not in excluded_keys
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
        _fail("SOURCE_URL_ALLOWLIST", f"unapproved source URL: {source_url}")


def _validate_constituents(constituents: list[dict[str, Any]]) -> list[str]:
    if len(constituents) != EXPECTED_STOCKS:
        _fail("COUNT_50", f"expected 50 rows, found {len(constituents)}")

    symbols = [str(row.get("symbol") or "").strip().upper() for row in constituents]
    isins = [str(row.get("isin") or "").strip().upper() for row in constituents]

    if any(not symbol for symbol in symbols):
        _fail("SYMBOL_NONBLANK", "blank symbol")
    if len(set(symbols)) != EXPECTED_STOCKS:
        _fail("SYMBOL_UNIQUE", "symbols are not 50/50 unique")
    if any(not isin for isin in isins):
        _fail("ISIN_NONBLANK", "blank ISIN")
    if len(set(isins)) != EXPECTED_STOCKS:
        _fail("ISIN_UNIQUE", "ISIN values are not 50/50 unique")

    for row in constituents:
        if not str(row.get("company_name") or "").strip():
            _fail("COMPANY_NONBLANK", f"blank company for {row.get('symbol')}")
        if not str(row.get("macro_sector") or "").strip():
            _fail(
                "MACRO_SECTOR_NONBLANK",
                f"blank macro sector for {row.get('symbol')}",
            )
        if str(row.get("series") or "").strip().upper() != "EQ":
            _fail("SERIES_EQ", f"non-EQ series for {row.get('symbol')}")

    return symbols


def validate_nifty50_source_payload(
    payload: dict[str, Any], expected_source_date: str | None = None
) -> dict[str, Any]:
    if payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        _fail("SCHEMA_VERSION", "unsupported source schema")
    if payload.get("status") != "PASS":
        _fail("SOURCE_STATUS", "source status is not PASS")
    _validate_url(str(payload.get("source_url") or ""))
    source_date = _parse_iso_date(
        str(payload.get("source_as_of_date") or ""), "SOURCE_DATE_FORMAT"
    )
    _validate_timestamp(str(payload.get("fetched_at_utc") or ""))

    if expected_source_date is not None:
        expected = _parse_iso_date(expected_source_date, "EXPECTED_DATE_FORMAT")
        if source_date != expected:
            _fail(
                "SOURCE_DATE_STALE",
                f"source {source_date} does not match expected {expected}",
            )

    constituents = payload.get("constituents")
    if not isinstance(constituents, list):
        _fail("CONSTITUENTS_TYPE", "constituents must be a list")
    constituent_symbols = _validate_constituents(constituents)

    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or len(symbols) != EXPECTED_STOCKS:
        _fail("SYMBOLS_50", "symbols must contain exactly 50 values")
    normalized_symbols = [str(symbol).strip().upper() for symbol in symbols]
    if len(set(normalized_symbols)) != EXPECTED_STOCKS:
        _fail("SYMBOL_UNIQUE", "symbols are not 50/50 unique")
    if set(normalized_symbols) != set(constituent_symbols):
        _fail("SYMBOL_CONSTITUENT_PARITY", "symbol sets differ")
    if payload.get("count") != EXPECTED_STOCKS:
        _fail("COUNT_FIELD", "count field must equal 50")

    expected_checksum = canonical_sha256(payload)
    if payload.get("source_checksum") != expected_checksum:
        _fail("SOURCE_CHECKSUM", "canonical SHA-256 mismatch")

    return {
        "status": "PASS",
        "count": EXPECTED_STOCKS,
        "source_checksum": expected_checksum,
    }


def build_nifty50_source_payload(
    csv_bytes: bytes,
    source_url: str,
    source_as_of_date: str,
    fetched_at_utc: str,
) -> dict[str, Any]:
    _validate_url(source_url)
    _parse_iso_date(source_as_of_date, "SOURCE_DATE_FORMAT")
    _validate_timestamp(fetched_at_utc)

    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    constituents = []
    for source_row in reader:
        symbol = str(source_row.get("Symbol") or "").strip().upper()
        if not symbol and not any(str(value or "").strip() for value in source_row.values()):
            continue
        constituents.append(
            {
                "symbol": symbol,
                "company_name": str(source_row.get("Company Name") or "").strip(),
                "macro_sector": str(source_row.get("Industry") or "").strip(),
                "series": str(source_row.get("Series") or "").strip().upper(),
                "isin": str(source_row.get("ISIN Code") or "").strip().upper(),
            }
        )

    constituent_symbols = _validate_constituents(constituents)
    payload = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "status": "PASS",
        "source": "NSE Indices — NIFTY 50 Index Constituent",
        "source_url": source_url,
        "source_as_of_date": source_as_of_date,
        "fetched_at_utc": fetched_at_utc,
        "count": EXPECTED_STOCKS,
        "symbols": sorted(constituent_symbols),
        "constituents": constituents,
    }
    payload["source_checksum"] = canonical_sha256(payload)
    validate_nifty50_source_payload(payload, expected_source_date=source_as_of_date)
    return payload
