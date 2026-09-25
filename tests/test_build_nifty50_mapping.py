import copy

import pytest

from build_nifty50_mapping import build_mapping
from phase3_mapping_contract import MappingContractError, canonical_sha256


SOURCE_DATE = "2026-09-24"


def make_source(symbols=None):
    symbols = symbols or [f"SYM{i:02d}" for i in range(50)]
    payload = {
        "schema_version": "nifty50-source-v2",
        "status": "PASS",
        "source": "NSE Indices — NIFTY 50 Index Constituent",
        "source_url": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
        "source_as_of_date": SOURCE_DATE,
        "fetched_at_utc": "2026-09-24T12:54:04+00:00",
        "count": 50,
        "symbols": sorted(symbols),
        "constituents": [
            {
                "symbol": symbol,
                "company_name": f"{symbol} Limited",
                "macro_sector": "Industrials",
                "series": "EQ",
                "isin": f"INE{i:09d}",
            }
            for i, symbol in enumerate(symbols)
        ],
    }
    payload["source_checksum"] = canonical_sha256(payload)
    return payload


def make_inputs(symbols=None):
    source = make_source(symbols)
    symbols = source["symbols"]
    classifications = {
        symbol: {
            "symbol": symbol,
            "macro_sector": "Industrials",
            "sector": "Capital Goods",
            "industry": "Industrial Products",
            "basic_industry": "Heavy Electrical Equipment",
            "source_url": "https://www.nseindia.com/api/quote-equity",
            "as_of_date": SOURCE_DATE,
            "checksum": f"classification-{symbol}",
        }
        for symbol in symbols
    }
    memberships = {
        symbol: ["NIFTY INDIA MANUFACTURING"] for symbol in symbols
    }
    weights = {
        "as_of_date": "2026-09-01",
        "metadata": {
            "NIFTY INDIA MANUFACTURING": {
                "index_type": "THEMATIC",
                "specificity_rank": 1,
            }
        },
        "values": {
            "NIFTY INDIA MANUFACTURING": {
                symbol: 2.0 for symbol in symbols
            }
        },
        "source_url": "https://www.niftyindices.com/reports/weightage",
        "checksum": "weights-checksum",
    }
    instruments = {
        symbol: {"instrument_key": f"NSE_EQ|{symbol}", "as_of_date": SOURCE_DATE}
        for symbol in symbols
    }
    return source, classifications, memberships, weights, instruments


def build_valid(last_known_good=None, symbols=None):
    args = make_inputs(symbols)
    return build_mapping(*args, last_known_good=last_known_good)


def test_builds_complete_50_row_mapping_and_audit():
    payload, audit = build_valid()
    assert payload["schema_version"] == "nifty50-mapping-v1"
    assert payload["status"] == "PASS"
    assert len(payload["rows"]) == 50
    assert len({row["symbol"] for row in payload["rows"]}) == 50
    assert len({row["isin"] for row in payload["rows"]}) == 50
    assert all(row["sector"] for row in payload["rows"])
    assert all(row["industry"] for row in payload["rows"])
    assert all(row["basic_industry"] for row in payload["rows"])
    assert all(row["all_membership_indices"] for row in payload["rows"])
    assert all(row["primary_sector_index"] for row in payload["rows"])
    assert all(row["instrument_key"] for row in payload["rows"])
    assert payload["source_checksum"] == canonical_sha256(payload)
    assert audit["gate_counts"]["mapping_rows"] == "50/50"
    assert audit["gate_counts"]["duplicates"] == 0
    assert audit["gate_counts"]["partial_rows"] == 0
    assert audit["final_status"] == "PASS"


def test_unchanged_list_returns_no_change():
    source, classifications, memberships, weights, instruments = make_inputs()
    last_known_good = {
        symbol: {
            "primary_sector_index": "NIFTY INDIA MANUFACTURING",
            "primary_weight": 2.0,
        }
        for symbol in source["symbols"]
    }
    _, audit = build_mapping(
        source,
        classifications,
        memberships,
        weights,
        instruments,
        last_known_good=last_known_good,
    )
    assert audit["transition_status"] == "NO_CHANGE"
    assert audit["added"] == []
    assert audit["removed"] == []


def test_added_and_removed_are_reconciled():
    symbols = [f"SYM{i:02d}" for i in range(49)] + ["NEWCO"]
    source, classifications, memberships, weights, instruments = make_inputs(symbols)
    last_known_good = {
        f"SYM{i:02d}": {
            "primary_sector_index": "NIFTY INDIA MANUFACTURING",
            "primary_weight": 2.0,
        }
        for i in range(50)
    }
    _, audit = build_mapping(
        source, classifications, memberships, weights, instruments, last_known_good
    )
    assert audit["added"] == ["NEWCO"]
    assert audit["removed"] == ["SYM49"]
    assert audit["transition_status"] == "ATOMIC_TRANSITION_READY"


def test_added_stock_missing_weight_is_wait():
    symbols = [f"SYM{i:02d}" for i in range(49)] + ["NEWCO"]
    source, classifications, memberships, weights, instruments = make_inputs(symbols)
    del weights["values"]["NIFTY INDIA MANUFACTURING"]["NEWCO"]
    last_known_good = {
        f"SYM{i:02d}": {
            "primary_sector_index": "NIFTY INDIA MANUFACTURING",
            "primary_weight": 2.0,
        }
        for i in range(50)
    }
    with pytest.raises(MappingContractError) as caught:
        build_mapping(
            source, classifications, memberships, weights, instruments, last_known_good
        )
    assert caught.value.code == "NEW_STOCK_WEIGHT_WAIT"


def test_added_stock_missing_instrument_is_wait():
    symbols = [f"SYM{i:02d}" for i in range(49)] + ["NEWCO"]
    source, classifications, memberships, weights, instruments = make_inputs(symbols)
    del instruments["NEWCO"]
    with pytest.raises(MappingContractError) as caught:
        build_mapping(source, classifications, memberships, weights, instruments, {})
    assert caught.value.code == "INSTRUMENT_MAPPING_50"


def test_mismatched_source_dates_fail_closed():
    source, classifications, memberships, weights, instruments = make_inputs()
    classifications["SYM00"]["as_of_date"] = "2026-09-23"
    with pytest.raises(MappingContractError) as caught:
        build_mapping(source, classifications, memberships, weights, instruments, None)
    assert caught.value.code == "SOURCE_DATE_MISMATCH"

