"""NIFTY50 producer contract for Task 4S-2.

This module does not call NSE or Upstox and never makes trading decisions.
It only validates same-run current constituents and delivery data BEFORE a
local cache may be atomically replaced. Market-date authority must be supplied
by a separately verified market calendar, not inferred from the file mtime.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OFFICIAL_CONSTITUENT_URL = (
    "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
)
EXPECTED_MEMBERS = 50
MAX_MEMBERSHIP_FETCH_AGE = timedelta(hours=2)
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9&-]{1,25}$")


def _day(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        result = date.fromisoformat(value)
        return value if result.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _symbols(values: Any) -> set[str]:
    if not isinstance(values, list) or len(values) != EXPECTED_MEMBERS:
        raise ValueError("COVERAGE_INVALID")
    if any(not isinstance(s, str) or not SYMBOL_PATTERN.fullmatch(s) for s in values):
        raise ValueError("COVERAGE_INVALID")
    result = set(values)
    if len(result) != EXPECTED_MEMBERS:
        raise ValueError("DUPLICATE_SYMBOL")
    return result


def validate_membership(
    payload: Any, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Validate exactly one recent official NIFTY50 source artifact."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("AUDIT_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    if not isinstance(payload, dict) or payload.get("status") != "PASS":
        raise ValueError("SOURCE_NOT_PASS")
    if payload.get("source_url") != OFFICIAL_CONSTITUENT_URL:
        raise ValueError("SOURCE_URL_INVALID")
    if type(payload.get("count")) is not int or payload["count"] != EXPECTED_MEMBERS:
        raise ValueError("COVERAGE_INVALID")
    symbols = _symbols(payload.get("symbols"))
    rows = payload.get("constituents")
    if not isinstance(rows, list):
        raise ValueError("CONSTITUENT_ROWS_MISSING")
    records = _symbols([r.get("symbol") if isinstance(r, dict) else None for r in rows])
    if records != symbols:
        raise ValueError("CONSTITUENT_SYMBOL_SET_MISMATCH")
    for row in rows:
        if row.get("series") != "EQ" or not str(row.get("isin") or "").strip():
            raise ValueError("CONSTITUENT_METADATA_INVALID")
        if not str(row.get("industry") or "").strip():
            raise ValueError("CONSTITUENT_METADATA_INVALID")

    received = _stamp(payload.get("fetched_at_utc"))
    if received is None:
        raise ValueError("FETCH_TIME_INVALID")
    elapsed = now - received
    if elapsed < -timedelta(minutes=1):
        raise ValueError("FUTURE_MEMBERSHIP_FETCH")
    if elapsed > MAX_MEMBERSHIP_FETCH_AGE:
        raise ValueError("STALE_MEMBERSHIP_FETCH")

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return {
        "symbols": symbols,
        "source_sha256": hashlib.sha256(canonical).hexdigest(),
        "fetched_at_utc": received.isoformat(),
        "status": "PASS",
        "production_write": False,
    }


def load_membership(path: str | Path = "nifty50_latest.json",
                    *, now: datetime | None = None) -> dict[str, Any]:
    """Read current-cycle local source; never fall back to stale hardcoded symbols."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return validate_membership(data, now=now)


def _safe_float(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    n = float(value)
    return n if math.isfinite(n) else None


def validate_delivery_artifact(
    artifact: Any, membership_proof: dict[str, Any],
    *, expected_date: str | None,
) -> dict[str, Any]:
    """Reject incomplete/mixed/old data before any file or Sheet publication.

    The expected date is an external *already verified* calendar decision.
    Passing a date string alone does not create external source authenticity.
    """
    expected = _day(expected_date)
    if expected is None:
        raise ValueError("EXPECTED_DATE_REQUIRED")
    if not isinstance(artifact, dict):
        raise ValueError("DELIVERY_ARTIFACT_MALFORMED")
    current = _day(artifact.get("currentTradingDate"))
    previous = _day(artifact.get("previousTradingDate"))
    if current != expected or previous is None or previous >= current:
        raise ValueError("DELIVERY_DATE_MISMATCH")
    if (type(artifact.get("total")) is not int or artifact.get("total") != 50
            or type(artifact.get("pass")) is not int or artifact.get("pass") != 50
            or type(artifact.get("fail")) is not int or artifact.get("fail") != 0):
        raise ValueError("DELIVERY_FAIL_COUNT_OR_COVERAGE")
    if not isinstance(membership_proof, dict):
        raise ValueError("SOURCE_PROOF_MISSING")
    authorized = membership_proof.get("symbols")
    sha = membership_proof.get("source_sha256")
    if not isinstance(authorized, set) or len(authorized) != EXPECTED_MEMBERS:
        raise ValueError("SOURCE_PROOF_MISSING")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        raise ValueError("SOURCE_PROOF_MISSING")

    rows = artifact.get("rows")
    if not isinstance(rows, list) or len(rows) != EXPECTED_MEMBERS:
        raise ValueError("DELIVERY_COVERAGE_INVALID")
    symbols = [r.get("symbol") if isinstance(r, dict) else None for r in rows]
    row_set = _symbols(symbols)
    if row_set != authorized:
        raise ValueError("DELIVERY_UNIVERSE_MISMATCH")
    if artifact.get("constituent_sha256") != sha:
        raise ValueError("CONSTITUENT_CHECKSUM_MISMATCH")
    for row in rows:
        if row.get("currentDate") != current or row.get("previousDate") != previous:
            raise ValueError("DELIVERY_ROW_DATE_MISMATCH")
        if row.get("status") != "PASS":
            raise ValueError("DELIVERY_ROW_STATUS_FAIL")
        p = _safe_float(row.get("previousDeliveryQty"))
        c = _safe_float(row.get("currentDeliveryQty"))
        ratio = _safe_float(row.get("deliveryRatio"))
        pct = _safe_float(row.get("currentDeliveryPercent"))
        if p is None or c is None or p <= 0 or c < 0:
            raise ValueError("DELIVERY_QUANTITY_INVALID")
        if ratio is None or ratio < 0 or not math.isclose(ratio, c/p, rel_tol=1e-8, abs_tol=1e-9):
            raise ValueError("DELIVERY_RATIO_INVALID")
        if pct is None or not 0 <= pct <= 100:
            raise ValueError("DELIVERY_PERCENT_INVALID")
    return {
        "status": "PASS", "production_write": False,
        "currentTradingDate": current,
        "constituent_sha256": sha, "count": EXPECTED_MEMBERS,
    }


def write_atomic_json(path: str | Path, payload: Any) -> None:
    """Only replace the target after complete fsync; retain prior bytes on errors."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=target.name + ".", suffix=".tmp", delete=False
        ) as f:
            tmp_path = Path(f.name)
            json.dump(payload, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
