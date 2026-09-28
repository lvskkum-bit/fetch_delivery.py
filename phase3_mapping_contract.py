"""Fail-closed contracts shared by the Phase 3 source producers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
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


class MappingContractError(ValueError):
    """A stable, machine-readable mapping-contract failure."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _mapping_fail(code: str, message: str) -> None:
    raise MappingContractError(code, message)


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


def source_date_from_last_modified(last_modified_value: str) -> str:
    try:
        last_modified = parsedate_to_datetime(str(last_modified_value or ""))
    except (TypeError, ValueError, OverflowError):
        _fail("SOURCE_DATE_PROVENANCE", "valid HTTP Last-Modified evidence is required")
    if last_modified.tzinfo is None:
        _fail("SOURCE_DATE_PROVENANCE", "Last-Modified must include a timezone")
    return last_modified.astimezone(timezone.utc).date().isoformat()


def _resolve_approved_calendar_evidence() -> dict[str, Any] | None:
    """Return trusted market-calendar evidence.

    Phase 3 does not yet have an approved calendar artifact/verifier in this
    repository, so production remains fail-closed until one is wired here.
    Tests may monkeypatch this internal resolver.
    """
    return None


def _validate_source_date_provenance(
    provenance: Any,
    source_date: date,
    source_url: str,
    fetched_at_utc: str,
    source_content_sha256: Any,
) -> None:
    if not isinstance(provenance, dict):
        _fail("SOURCE_DATE_PROVENANCE", "source date provenance is missing")
    if provenance.get("method") != "HTTP_LAST_MODIFIED_UTC_DATE":
        _fail("SOURCE_DATE_PROVENANCE", "unsupported source date derivation method")
    if provenance.get("source_url") != source_url:
        _fail("SOURCE_DATE_PROVENANCE", "provenance URL does not match source URL")

    derived_date = source_date_from_last_modified(
        str(provenance.get("last_modified") or "")
    )
    if derived_date != str(provenance.get("derived_source_as_of_date") or ""):
        _fail("SOURCE_DATE_PROVENANCE", "derived source date does not match Last-Modified")
    if derived_date != source_date.isoformat():
        _fail("SOURCE_DATE_PROVENANCE", "source date does not match Last-Modified")

    expected_date = provenance.get("expected_completed_trading_date")
    expected_method = provenance.get("expected_date_method")
    expected_ref = str(provenance.get("expected_date_evidence_ref") or "")
    expected_evidence_sha256 = provenance.get("expected_date_evidence_sha256")
    if not expected_date or not expected_method or not expected_ref or not re.fullmatch(
        r"[0-9a-f]{64}", str(expected_evidence_sha256 or "")
    ):
        _fail(
            "TRADING_DATE_EVIDENCE_UNAVAILABLE",
            "independent approved trading-date evidence is required",
        )
    approved_calendar_evidence = _resolve_approved_calendar_evidence()
    if not approved_calendar_evidence or approved_calendar_evidence.get("verified") is not True:
        _fail(
            "TRADING_DATE_EVIDENCE_UNAVAILABLE",
            "no verified approved trading-calendar evidence is available",
        )
    evidence_date = approved_calendar_evidence.get("expected_completed_trading_date")
    evidence_ref = approved_calendar_evidence.get("evidence_ref")
    evidence_sha256 = approved_calendar_evidence.get("evidence_sha256")
    if (
        not evidence_date
        or evidence_ref != expected_ref
        or evidence_sha256 != expected_evidence_sha256
        or str(evidence_date) != str(expected_date)
    ):
        _fail("TRADING_DATE_EVIDENCE_MISMATCH", "calendar evidence does not match verified evidence")
    if expected_method != "EXTERNAL_APPROVED_MARKET_CALENDAR":
        _fail("TRADING_DATE_EVIDENCE_MISMATCH", "approved calendar method is invalid")
    if not re.search(rf"{re.escape(str(expected_date))}$", expected_ref):
        _fail("TRADING_DATE_EVIDENCE_MISMATCH", "calendar evidence reference date differs")
    if _parse_iso_date(str(expected_date), "TRADING_DATE_EVIDENCE_MISMATCH") != source_date:
        _fail("TRADING_DATE_EVIDENCE_MISMATCH", "expected completed trading date differs from source date")
    requested_date = provenance.get("requested_source_as_of_date")
    if requested_date is not None and _parse_iso_date(
        str(requested_date), "SOURCE_DATE_PROVENANCE"
    ) != source_date:
        _fail("SOURCE_DATE_PROVENANCE", "requested date conflicts with HTTP evidence")

    if not isinstance(source_content_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_content_sha256
    ):
        _fail("SOURCE_DATE_PROVENANCE", "response content SHA-256 is missing or invalid")
    if provenance.get("response_content_sha256") != source_content_sha256:
        _fail("SOURCE_DATE_PROVENANCE", "response content checksum evidence differs")

    fetched_at = datetime.fromisoformat(str(fetched_at_utc).replace("Z", "+00:00"))
    last_modified = parsedate_to_datetime(str(provenance["last_modified"]))
    if fetched_at.tzinfo is None or fetched_at.astimezone(timezone.utc) < last_modified.astimezone(timezone.utc):
        _fail("SOURCE_DATE_PROVENANCE", "fetch timestamp predates source evidence")


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
    payload: dict[str, Any],
    expected_source_date: str | None = None,
) -> dict[str, Any]:
    if payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        _fail("SCHEMA_VERSION", "unsupported source schema")
    if payload.get("status") != "PASS":
        _fail("SOURCE_STATUS", "source status is not PASS")
    _validate_url(str(payload.get("source_url") or ""))
    source_date = _parse_iso_date(
        str(payload.get("source_as_of_date") or ""), "SOURCE_DATE_FORMAT"
    )
    fetched_at_utc = str(payload.get("fetched_at_utc") or "")
    _validate_timestamp(fetched_at_utc)

    if expected_source_date is not None:
        expected = _parse_iso_date(expected_source_date, "EXPECTED_DATE_FORMAT")
        if source_date != expected:
            _fail(
                "SOURCE_DATE_STALE",
                f"source {source_date} does not match expected {expected}",
            )

    _validate_source_date_provenance(
        payload.get("source_date_provenance"),
        source_date,
        str(payload.get("source_url") or ""),
        fetched_at_utc,
        payload.get("source_content_sha256"),
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
    source_date_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_url(source_url)
    _parse_iso_date(source_as_of_date, "SOURCE_DATE_FORMAT")
    _validate_timestamp(fetched_at_utc)
    response_content_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    provenance = dict(source_date_provenance or {})
    provenance["response_content_sha256"] = response_content_sha256

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
        "source_content_sha256": response_content_sha256,
        "source_date_provenance": provenance,
        "count": EXPECTED_STOCKS,
        "symbols": sorted(constituent_symbols),
        "constituents": constituents,
    }
    payload["source_checksum"] = canonical_sha256(payload)
    validate_nifty50_source_payload(
        payload,
        expected_source_date=source_as_of_date,
    )
    return payload


def extract_classification(
    constituent: dict[str, Any], quote_payload: dict[str, Any]
) -> dict[str, str]:
    if not isinstance(quote_payload, dict):
        _mapping_fail(
            "CLASSIFICATION_RESPONSE_TYPE", "quote response must be an object"
        )

    symbol = str(constituent.get("symbol") or "").strip().upper()
    response_symbol = str(
        (quote_payload.get("info") or {}).get("symbol") or symbol
    ).strip().upper()
    if response_symbol != symbol:
        _mapping_fail(
            "CLASSIFICATION_SYMBOL_MISMATCH",
            f"expected {symbol}, received {response_symbol}",
        )

    industry_info = quote_payload.get("industryInfo")
    if not isinstance(industry_info, dict):
        _mapping_fail(
            "CLASSIFICATION_RESPONSE_TYPE", "industryInfo must be an object"
        )

    return {
        "symbol": symbol,
        "macro_sector": str(constituent.get("macro_sector") or "").strip(),
        "sector": str(industry_info.get("sector") or "").strip(),
        "industry": str(industry_info.get("industry") or "").strip(),
        "basic_industry": str(
            industry_info.get("basicIndustry") or ""
        ).strip(),
    }


def validate_classification_50(
    rows: list[dict[str, Any]], expected_symbols: set[str]
) -> dict[str, Any]:
    if len(rows) != EXPECTED_STOCKS:
        _mapping_fail(
            "CLASSIFICATION_COUNT_50", f"expected 50 rows, found {len(rows)}"
        )

    symbols = [str(row.get("symbol") or "").strip().upper() for row in rows]
    if len(set(symbols)) != EXPECTED_STOCKS:
        _mapping_fail(
            "CLASSIFICATION_SYMBOL_UNIQUE", "classification symbols not unique"
        )
    if set(symbols) != {str(symbol).strip().upper() for symbol in expected_symbols}:
        _mapping_fail(
            "CLASSIFICATION_SYMBOL_SET", "classification symbol set mismatch"
        )

    gates = (
        ("macro_sector", "CLASSIFICATION_MACRO_SECTOR"),
        ("sector", "CLASSIFICATION_SECTOR"),
        ("industry", "CLASSIFICATION_INDUSTRY"),
        ("basic_industry", "CLASSIFICATION_BASIC_INDUSTRY"),
    )
    for field, code in gates:
        for row in rows:
            if not str(row.get(field) or "").strip():
                _mapping_fail(code, f"blank {field} for {row.get('symbol')}")

    return {"status": "PASS", "count": EXPECTED_STOCKS}


def classify_index(index_row: dict[str, Any], rules: dict[str, Any]) -> str:
    index_name = str(index_row.get("name") or "").strip().upper()
    index_type = str(index_row.get("index_type") or "").strip().upper()
    eligible = {str(value).upper() for value in rules.get("eligible_types", [])}
    excluded = {str(value).upper() for value in rules.get("excluded_types", [])}
    excluded_names = {
        str(value).strip().upper() for value in rules.get("excluded_names", [])
    }
    if index_name in excluded_names:
        return "EXCLUDED"
    if index_type in excluded:
        return "EXCLUDED"
    if index_type == "SECTORAL" and index_type in eligible:
        return "ELIGIBLE_SECTORAL"
    if index_type == "THEMATIC" and index_type in eligible:
        return "ELIGIBLE_THEMATIC"
    return "EXCLUDED"


def resolve_memberships(
    symbol: str,
    active_indices: list[dict[str, Any]],
    constituent_sets: dict[str, set[str]],
    rules: dict[str, Any],
) -> list[str]:
    normalized_symbol = str(symbol).strip().upper()
    memberships = []
    for index_row in active_indices:
        if classify_index(index_row, rules) == "EXCLUDED":
            continue
        name = str(index_row.get("name") or "").strip()
        constituents = {
            str(value).strip().upper()
            for value in constituent_sets.get(name, set())
        }
        if normalized_symbol in constituents:
            memberships.append(name)
    memberships = sorted(set(memberships))
    if not memberships:
        _mapping_fail(
            "MEMBERSHIP_REVIEW_REQUIRED",
            f"no eligible membership for {normalized_symbol}",
        )
    return memberships


def validate_memberships_50(
    rows: list[dict[str, Any]], expected_symbols: set[str]
) -> dict[str, Any]:
    if len(rows) != EXPECTED_STOCKS:
        _mapping_fail("MEMBERSHIP_COUNT_50", "membership rows must equal 50")

    symbols = [str(row.get("symbol") or "").strip().upper() for row in rows]
    if len(set(symbols)) != EXPECTED_STOCKS:
        _mapping_fail("MEMBERSHIP_SYMBOL_UNIQUE", "membership symbols not unique")
    if set(symbols) != {str(symbol).strip().upper() for symbol in expected_symbols}:
        _mapping_fail("MEMBERSHIP_SYMBOL_SET", "membership symbol set mismatch")

    for row in rows:
        memberships = row.get("all_applicable_indices") or []
        if not isinstance(memberships, list) or not memberships:
            _mapping_fail(
                "MEMBERSHIP_REVIEW_REQUIRED",
                f"no eligible membership for {row.get('symbol')}",
            )

    return {"status": "PASS", "count": EXPECTED_STOCKS}


def choose_primary_index(
    memberships: list[str],
    weights: dict[str, dict[str, float]],
    metadata: dict[str, dict[str, Any]],
    symbol: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    normalized_symbol = str(symbol).strip().upper()
    type_priority = {
        str(key).upper(): int(value)
        for key, value in rules.get("type_priority", {}).items()
    }
    candidates = []
    for index_name in memberships:
        index_meta = metadata.get(index_name, {})
        index_type = str(index_meta.get("index_type") or "").strip().upper()
        if index_type not in {"SECTORAL", "THEMATIC"}:
            continue
        raw_weight = weights.get(index_name, {}).get(normalized_symbol)
        if raw_weight is None:
            continue
        candidates.append(
            {
                "index": index_name,
                "weight": float(raw_weight),
                "type_priority": type_priority.get(index_type, 0),
                "specificity_rank": int(index_meta.get("specificity_rank") or 0),
            }
        )
    if not candidates:
        _mapping_fail(
            "WEIGHT_REVIEW_REQUIRED",
            f"no verified eligible weight for {normalized_symbol}",
        )
    candidates.sort(
        key=lambda item: (
            -item["weight"],
            -item["type_priority"],
            -item["specificity_rank"],
            item["index"],
        )
    )
    chosen = candidates[0]
    return {
        "primary_sector_index": chosen["index"],
        "primary_weight": chosen["weight"],
        "verification_status": "VERIFIED",
    }


def _normalize_company_name(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).lower().replace("limited", "ltd"),
    )


def extract_factsheet_weight_evidence(
    factsheet_text: str,
    company_names_by_symbol: dict[str, str],
) -> dict[str, Any]:
    if not isinstance(factsheet_text, str):
        _mapping_fail("WEIGHT_SOURCE_TYPE", "factsheet text must be a string")

    date_match = re.search(
        r"\b(August)\s+(31),\s+(2026)\b", factsheet_text, re.IGNORECASE
    )
    if not date_match:
        _mapping_fail("WEIGHT_SOURCE_DATE", "August 31, 2026 not found")

    marker = factsheet_text.lower().find("top constituents by weightage")
    if marker < 0:
        _mapping_fail("WEIGHT_SOURCE_TABLE", "top constituents table not found")
    section = factsheet_text[marker:]
    end = section.find("## Based")
    if end >= 0:
        section = section[:end]

    weighted_lines: list[tuple[str, float]] = []
    for line in section.splitlines():
        match = re.match(r"^\s*(.+?)\s+([0-9]+(?:\.[0-9]+)?)\s*$", line)
        if match and not any(
            label in match.group(1).lower()
            for label in ("weight", "p/e", "p/b", "dividend")
        ):
            weighted_lines.append((match.group(1).strip(), float(match.group(2))))

    if not weighted_lines:
        _mapping_fail("WEIGHT_SOURCE_TABLE", "no constituent weights found")

    verified_weights: dict[str, float] = {}
    for symbol, company_name in company_names_by_symbol.items():
        normalized_name = _normalize_company_name(company_name)
        for raw_name, weight in weighted_lines:
            if normalized_name in _normalize_company_name(raw_name):
                verified_weights[str(symbol).strip().upper()] = weight
                break

    return {
        "as_of_date": "2026-08-31",
        "verified_weights": verified_weights,
        "unlisted_weight_upper_bound": weighted_lines[-1][1],
    }


def choose_primary_index_from_weight_evidence(
    symbol: str,
    memberships: list[str],
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized_symbol = str(symbol).strip().upper()
    exact: list[tuple[float, str]] = []
    unknown_bounds: list[tuple[float, str]] = []
    for index_name in memberships:
        index_evidence = evidence.get(index_name)
        if not isinstance(index_evidence, dict):
            _mapping_fail(
                "WEIGHT_REVIEW_REQUIRED",
                f"missing official weight evidence for {index_name}",
            )
        weights = index_evidence.get("verified_weights") or {}
        if normalized_symbol in weights:
            exact.append((float(weights[normalized_symbol]), index_name))
        else:
            bound = index_evidence.get("unlisted_weight_upper_bound")
            if bound is None:
                _mapping_fail(
                    "WEIGHT_REVIEW_REQUIRED",
                    f"missing unlisted-weight bound for {index_name}",
                )
            unknown_bounds.append((float(bound), index_name))

    if not exact:
        _mapping_fail(
            "WEIGHT_REVIEW_REQUIRED",
            f"no exact verified weight for {normalized_symbol}",
        )
    exact.sort(key=lambda item: (-item[0], item[1]))
    winner_weight, winner_index = exact[0]
    maximum_unknown = max(unknown_bounds, default=(0.0, ""))
    if maximum_unknown[0] >= winner_weight:
        _mapping_fail(
            "WEIGHT_REVIEW_REQUIRED",
            f"unknown weight in {maximum_unknown[1]} can equal or exceed winner",
        )
    return {
        "primary_sector_index": winner_index,
        "primary_weight": winner_weight,
        "verification_status": "VERIFIED",
    }


def validate_primary_weights_50(
    rows: list[dict[str, Any]], expected_symbols: set[str]
) -> dict[str, Any]:
    if len(rows) != EXPECTED_STOCKS:
        _mapping_fail("PRIMARY_WEIGHT_COUNT_50", "primary rows must equal 50")
    symbols = [str(row.get("symbol") or "").strip().upper() for row in rows]
    if len(set(symbols)) != EXPECTED_STOCKS:
        _mapping_fail("PRIMARY_WEIGHT_SYMBOL_UNIQUE", "primary symbols not unique")
    if set(symbols) != {str(value).strip().upper() for value in expected_symbols}:
        _mapping_fail("PRIMARY_WEIGHT_SYMBOL_SET", "primary symbol set mismatch")
    for row in rows:
        if row.get("verification_status") != "VERIFIED":
            _mapping_fail(
                "PRIMARY_WEIGHT_NOT_VERIFIED",
                f"weight is not verified for {row.get('symbol')}",
            )
        if not str(row.get("primary_sector_index") or "").strip():
            _mapping_fail(
                "PRIMARY_INDEX_BLANK", f"blank primary index for {row.get('symbol')}"
            )
        if row.get("primary_weight") is None:
            _mapping_fail(
                "PRIMARY_WEIGHT_BLANK", f"blank primary weight for {row.get('symbol')}"
            )
    return {"status": "PASS", "count": EXPECTED_STOCKS}


def apply_weight_freshness(
    current: dict[str, Any],
    last_known_good: dict[str, Any] | None,
    is_new: bool,
    expected_month: str,
) -> dict[str, Any]:
    current_month = str(current.get("weight_as_of_month") or "").strip()
    if current_month == expected_month:
        result = dict(current)
        result["verification_status"] = "VERIFIED"
        return result

    if not is_new and last_known_good:
        return {
            **last_known_good,
            "verification_status": "WEIGHT_SOURCE_STALE",
            "review_reason": (
                f"expected weight month {expected_month}; found {current_month or 'blank'}"
            ),
        }

    return {
        "primary_sector_index": "",
        "primary_weight": None,
        "verification_status": "REVIEW_REQUIRED + WAIT",
        "review_reason": (
            f"new stock requires verified weight month {expected_month}"
        ),
    }
