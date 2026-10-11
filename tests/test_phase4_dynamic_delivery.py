"""Task 4S-2 — dynamic delivery producer RED/GREEN regression.

All tests are offline. No writes to the repo's main branch or external systems.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import csv
import io
import json
import os

import pytest

import delivery_source_contract as contract
import fetch_delivery

NOW = datetime(2026, 10, 11, 6, 35, tzinfo=timezone.utc)
CURRENT = ["BSE"] + [f"S{i:02d}" for i in range(49)]
OLD = ["WIPRO"] + CURRENT[1:]
DATE = "2026-10-09"


def membership(symbols=None, fetched=None):
    symbols = CURRENT if symbols is None else symbols
    return {
        "status": "PASS", "source": "NSE Indices — NIFTY 50 Index Constituent",
        "source_url": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
        "fetched_at_utc": fetched or "2026-10-11T06:30:00+00:00",
        "count": 50, "symbols": symbols[:],
        "constituents": [{
            "symbol": name, "company_name": name+" Limited",
            "industry": "Financial Services", "series": "EQ",
            "isin": f"INE{i:07d}"
        } for i,name in enumerate(symbols)],
    }


def delivery(symbols=None, current=DATE):
    symbols = CURRENT if symbols is None else symbols
    return {
        "generatedAt": "2026-10-11T12:05:00+05:30",
        "previousTradingDate": "2026-10-08",
        "currentTradingDate": current,
        "total": 50, "pass": 50, "fail": 0,
        "rows": [{
            "symbol": s, "previousDate": "2026-10-08",
            "currentDate": current, "previousDeliveryQty": 100,
            "currentDeliveryQty": 150, "deliveryRatio": 1.5,
            "currentDeliveryPercent": 51.5, "status": "PASS",
        } for s in symbols],
    }


def csv_payload(symbols, *, quantity=100, percent=55):
    target = io.StringIO()
    writer = csv.DictWriter(target, fieldnames=["SYMBOL", "SERIES", "DELIV_QTY", "DELIV_PER"])
    writer.writeheader()
    for s in symbols:
        writer.writerow({"SYMBOL":s, "SERIES":"EQ", "DELIV_QTY":quantity, "DELIV_PER":percent})
    return target.getvalue()


def test_current_membership_includes_bse_not_wipro():
    evidence = contract.validate_membership(membership(), now=NOW)
    assert evidence["symbols"] == set(CURRENT)
    assert "BSE" in evidence["symbols"]
    assert "WIPRO" not in evidence["symbols"]
    assert len(evidence["source_sha256"]) == 64


def test_member_source_rejects_duplicates_even_if_50():
    broken = membership(CURRENT[1:] + [CURRENT[1]])
    with pytest.raises(ValueError, match="DUPLICATE|COVERAGE"):
        contract.validate_membership(broken, now=NOW)


def test_member_source_rejects_stale_fetch_unrelated_to_session_date():
    old = membership(fetched="2026-09-24T18:00:00+00:00")
    with pytest.raises(ValueError, match="STALE"):
        contract.validate_membership(old, now=NOW)


def test_member_source_rejects_wrong_url_and_future_receipt():
    bad = membership()
    bad["source_url"] = "https://unknown.example/constituents.csv"
    with pytest.raises(ValueError, match="SOURCE"):
        contract.validate_membership(bad, now=NOW)
    with pytest.raises(ValueError, match="FUTURE"):
        contract.validate_membership(
            membership(fetched="2026-12-01T00:00:00+00:00"), now=NOW
        )


def test_parser_uses_exact_membership_and_drops_old_wipro():
    symbols = contract.validate_membership(membership(), now=NOW)["symbols"]
    parsed = fetch_delivery.parse_delivery(csv_payload(CURRENT+["WIPRO"]), symbols)
    assert set(parsed) == set(CURRENT)
    assert "BSE" in parsed and "WIPRO" not in parsed


def test_parser_rejects_duplicate_equity_series_row():
    with pytest.raises(ValueError, match="DUPLICATE"):
        fetch_delivery.parse_delivery(csv_payload(CURRENT+["BSE"]), set(CURRENT))


def test_delivery_rejects_old_wipro_producer_before_write():
    proof = contract.validate_membership(membership(), now=NOW)
    with pytest.raises(ValueError, match="UNIVERSE"):
        contract.validate_delivery_artifact(delivery(OLD), proof, expected_date=DATE)


def test_delivery_rejects_missing_rows_and_duplicate_symbols():
    proof = contract.validate_membership(membership(), now=NOW)
    missing = delivery()
    missing["rows"] = missing["rows"][:-1]
    missing["total"] = 49
    with pytest.raises(ValueError, match="COVERAGE"):
        contract.validate_delivery_artifact(missing, proof, expected_date=DATE)
    duplicate = delivery(CURRENT[1:]+[CURRENT[1]])
    with pytest.raises(ValueError, match="DUPLICATE"):
        contract.validate_delivery_artifact(duplicate, proof, expected_date=DATE)


def test_delivery_rejects_stale_complete_session_even_if_valid_50():
    proof = contract.validate_membership(membership(), now=NOW)
    with pytest.raises(ValueError, match="DATE"):
        contract.validate_delivery_artifact(
            delivery(current="2026-10-08"), proof, expected_date=DATE
        )


def test_delivery_rejects_mixed_row_dates_and_invalid_quantity_ratio():
    proof = contract.validate_membership(membership(), now=NOW)
    broken = delivery()
    broken["constituent_sha256"] = proof["source_sha256"]
    broken["rows"][0]["currentDate"] = "2026-10-08"
    with pytest.raises(ValueError, match="ROW_DATE"):
        contract.validate_delivery_artifact(broken, proof, expected_date=DATE)
    broken = delivery()
    broken["constituent_sha256"] = proof["source_sha256"]
    broken["rows"][0]["deliveryRatio"] = -2
    with pytest.raises(ValueError, match="RATIO"):
        contract.validate_delivery_artifact(broken, proof, expected_date=DATE)


def test_delivery_requires_explicit_completed_session_date():
    proof = contract.validate_membership(membership(), now=NOW)
    with pytest.raises(ValueError, match="EXPECTED_DATE"):
        contract.validate_delivery_artifact(delivery(), proof, expected_date=None)


def test_valid_cache_binds_constituent_checksum_and_date():
    proof = contract.validate_membership(membership(), now=NOW)
    artifact = delivery()
    artifact["constituent_sha256"] = proof["source_sha256"]
    out = contract.validate_delivery_artifact(artifact, proof, expected_date=DATE)
    assert out["status"] == "PASS"
    assert out["production_write"] is False


def test_write_is_atomic_and_failure_leaves_previous_bytes(tmp_path, monkeypatch):
    output = tmp_path / "delivery_latest.json"
    output.write_text('{"old":true}', encoding="utf-8")
    original = output.read_bytes()
    def abort(*args, **kwargs):
        raise OSError("simulated rename interruption")
    monkeypatch.setattr(contract.os, "replace", abort)
    with pytest.raises(OSError):
        contract.write_atomic_json(output, {"new":True})
    assert output.read_bytes() == original


def test_main_fails_closed_before_fetch_when_no_verified_date(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXPECTED_COMPLETED_TRADING_DATE", raising=False)
    monkeypatch.setattr(fetch_delivery, "find_latest_two_sessions",
                        lambda: (_ for _ in ()).throw(AssertionError("fetch forbidden")))
    with pytest.raises(ValueError, match="EXPECTED_DATE"):
        fetch_delivery.main()


def test_all_day_and_membership_dates_are_distinct_not_forced_equal():
    proof = contract.validate_membership(membership(), now=NOW)
    artifact = delivery()
    artifact["constituent_sha256"] = proof["source_sha256"]
    out = contract.validate_delivery_artifact(artifact, proof, expected_date=DATE)
    assert out["status"] == "PASS"


def test_fail_count_blocks_publication_even_if_50_rows():
    proof = contract.validate_membership(membership(), now=NOW)
    artifact = delivery()
    artifact["fail"] = 1
    artifact["pass"] = 49
    artifact["rows"][0]["status"] = "FAIL"
    with pytest.raises(ValueError, match="FAIL|ROW"):
        contract.validate_delivery_artifact(artifact, proof, expected_date=DATE)
