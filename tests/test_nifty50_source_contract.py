import copy
import csv
import io
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import fetch_nifty50_constituents as fetcher
import phase3_mapping_contract as source_contract
from phase3_mapping_contract import (
    SourceContractError,
    build_nifty50_source_payload,
    canonical_sha256,
    validate_nifty50_source_payload,
)


OFFICIAL_URL = (
    "https://www.niftyindices.com/IndexConstituent/"
    "ind_nifty50list.csv"
)
SOURCE_DATE = "2026-09-25"
FETCHED_AT = "2026-09-25T21:54:04+00:00"
LAST_MODIFIED = "Fri, 25 Sep 2026 18:00:18 GMT"
APPROVED_CALENDAR_EVIDENCE = {
    "verified": True,
    "expected_completed_trading_date": SOURCE_DATE,
    "evidence_ref": "approved-calendar-evidence-2026-09-25",
    "evidence_sha256": "a" * 64,
}


@pytest.fixture(autouse=True)
def trusted_calendar_resolver(monkeypatch):
    monkeypatch.setattr(
        source_contract,
        "_resolve_approved_calendar_evidence",
        lambda: dict(APPROVED_CALENDAR_EVIDENCE),
    )


def make_rows(count=50):
    return [
        {
            "Company Name": f"Company {index:02d} Ltd.",
            "Industry": f"Macro {index % 10}",
            "Symbol": f"SYM{index:02d}",
            "Series": "EQ",
            "ISIN Code": f"INE{index:09d}",
        }
        for index in range(count)
    ]


def csv_bytes(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "Company Name",
            "Industry",
            "Symbol",
            "Series",
            "ISIN Code",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def build(rows=None, url=OFFICIAL_URL):
    content = csv_bytes(rows or make_rows())
    return build_nifty50_source_payload(
        content,
        url,
        SOURCE_DATE,
        FETCHED_AT,
        {
            "method": "HTTP_LAST_MODIFIED_UTC_DATE",
            "source_url": url,
            "last_modified": LAST_MODIFIED,
            "derived_source_as_of_date": SOURCE_DATE,
            "expected_completed_trading_date": SOURCE_DATE,
            "expected_date_method": "EXTERNAL_APPROVED_MARKET_CALENDAR",
            "expected_date_evidence_ref": "approved-calendar-evidence-2026-09-25",
            "expected_date_evidence_sha256": "a" * 64,
            "etag": '"fixture-etag"',
        },
    )


def test_valid_payload_has_exact_v2_contract_and_checksum():
    payload = build()

    assert payload["schema_version"] == "nifty50-source-v2"
    assert payload["status"] == "PASS"
    assert payload["count"] == 50
    assert len(payload["symbols"]) == 50
    assert len(set(payload["symbols"])) == 50
    assert len(payload["constituents"]) == 50
    assert len({row["isin"] for row in payload["constituents"]}) == 50
    assert {row["series"] for row in payload["constituents"]} == {"EQ"}
    assert set(payload["symbols"]) == {
        row["symbol"] for row in payload["constituents"]
    }
    assert payload["source_as_of_date"] == SOURCE_DATE
    assert payload["fetched_at_utc"] == FETCHED_AT
    assert payload["source_checksum"] == canonical_sha256(payload)
    assert validate_nifty50_source_payload(
        payload,
        expected_source_date=SOURCE_DATE,
    )["status"] == "PASS"


def test_refreshed_authoritative_source_matches_expected_trading_date():
    source_path = Path(__file__).resolve().parents[1] / "nifty50_latest.json"
    payload = json.loads(source_path.read_text(encoding="utf-8"))

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(payload, expected_source_date="2026-09-25")

    assert caught.value.code in {"SOURCE_STATUS", "TRADING_DATE_EVIDENCE_REQUIRED"}
    assert payload["source_as_of_date"] == "2026-09-25"
    assert len(payload["symbols"]) == 50
    assert len(payload["constituents"]) == 50
    assert len({row["symbol"] for row in payload["constituents"]}) == 50
    assert len({row["isin"] for row in payload["constituents"]}) == 50
    assert payload["source_url"] == OFFICIAL_URL
    assert payload["source_checksum"] == canonical_sha256(payload)
    assert payload["source_date_provenance"]["method"] == "HTTP_LAST_MODIFIED_UTC_DATE"
    assert payload["source_date_provenance"]["last_modified"] == LAST_MODIFIED
    assert payload["source_date_provenance"]["derived_source_as_of_date"] == "2026-09-25"
    assert payload["source_date_provenance"]["response_content_sha256"] == payload[
        "source_content_sha256"
    ]


def test_source_payload_without_date_provenance_fails_closed():
    payload = build()
    payload.pop("source_date_provenance")
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload,
            expected_source_date=SOURCE_DATE,
        )

    assert caught.value.code == "SOURCE_DATE_PROVENANCE"


def test_caller_date_cannot_override_official_last_modified_provenance():
    payload = build()
    payload["source_as_of_date"] = "2026-09-26"
    payload["source_date_provenance"][
        "expected_completed_trading_date"
    ] = "2026-09-26"
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload,
            expected_source_date="2026-09-26",
        )

    assert caught.value.code == "SOURCE_DATE_PROVENANCE"


def test_last_modified_alone_cannot_define_expected_trading_date():
    payload = build()
    for key in (
        "expected_completed_trading_date",
        "expected_date_method",
        "expected_date_evidence_ref",
        "expected_date_evidence_sha256",
    ):
        payload["source_date_provenance"].pop(key)
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(payload, expected_source_date=SOURCE_DATE)

    assert caught.value.code in {
        "SOURCE_DATE_PROVENANCE",
        "TRADING_DATE_EVIDENCE_UNAVAILABLE",
    }


def test_independent_calendar_evidence_allows_matching_expected_date():
    payload = build()
    payload["source_date_provenance"].update(
        {
            "expected_completed_trading_date": SOURCE_DATE,
            "expected_date_method": "EXTERNAL_APPROVED_MARKET_CALENDAR",
            "expected_date_evidence_ref": "approved-calendar-evidence-2026-09-25",
            "expected_date_evidence_sha256": "a" * 64,
        }
    )
    payload["source_checksum"] = canonical_sha256(payload)

    assert validate_nifty50_source_payload(
        payload,
        expected_source_date=SOURCE_DATE,
    )["status"] == "PASS"


def test_production_api_rejects_caller_supplied_calendar_evidence():
    payload = build()

    assert "approved_calendar_evidence" not in inspect.signature(
        validate_nifty50_source_payload
    ).parameters
    assert "approved_calendar_evidence" not in inspect.signature(
        build_nifty50_source_payload
    ).parameters

    with pytest.raises(TypeError):
        validate_nifty50_source_payload(
            payload,
            expected_source_date=SOURCE_DATE,
            approved_calendar_evidence=APPROVED_CALENDAR_EVIDENCE,
        )


def test_no_trusted_calendar_resolver_fails_closed(monkeypatch):
    payload = build()
    monkeypatch.setattr(
        source_contract, "_resolve_approved_calendar_evidence", lambda: None
    )

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(payload, expected_source_date=SOURCE_DATE)

    assert caught.value.code == "TRADING_DATE_EVIDENCE_UNAVAILABLE"


def test_matching_env_assertions_cannot_manufacture_pass(monkeypatch, tmp_path):
    class FakeResponse:
        status_code = 200
        content = csv_bytes(make_rows())
        headers = {
            "Last-Modified": LAST_MODIFIED,
            "ETag": '"fixture-etag"',
        }

        def raise_for_status(self):
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetcher.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        source_contract, "_resolve_approved_calendar_evidence", lambda: None
    )
    monkeypatch.setenv("NIFTY50_SOURCE_AS_OF_DATE", SOURCE_DATE)
    monkeypatch.setenv("NIFTY50_EXPECTED_COMPLETED_TRADING_DATE", SOURCE_DATE)
    monkeypatch.setenv(
        "NIFTY50_EXPECTED_DATE_EVIDENCE_REF",
        "approved-calendar-evidence-2026-09-25",
    )
    monkeypatch.setenv("NIFTY50_EXPECTED_DATE_EVIDENCE_SHA256", "a" * 64)

    with pytest.raises(SourceContractError) as caught:
        fetcher.main()

    assert caught.value.code == "TRADING_DATE_EVIDENCE_UNAVAILABLE"
    assert not (tmp_path / fetcher.OUTPUT_FILE).exists()


def test_independent_calendar_date_mismatch_fails_closed():
    payload = build()
    payload["source_date_provenance"].update(
        {
            "expected_completed_trading_date": "2026-09-24",
            "expected_date_method": "EXTERNAL_APPROVED_MARKET_CALENDAR",
            "expected_date_evidence_ref": "approved-calendar-evidence-2026-09-24",
            "expected_date_evidence_sha256": "b" * 64,
        }
    )
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload,
            expected_source_date=SOURCE_DATE,
        )

    assert caught.value.code == "TRADING_DATE_EVIDENCE_MISMATCH"


@pytest.mark.parametrize("unverified_date", ["2026-09-26", "2026-10-02"])
def test_unverified_weekend_or_holiday_date_cannot_pass(unverified_date):
    payload = build()
    payload["source_as_of_date"] = unverified_date
    payload["source_date_provenance"].update(
        {
            "derived_source_as_of_date": unverified_date,
            "expected_completed_trading_date": unverified_date,
        }
    )
    for key in (
        "expected_completed_trading_date",
        "expected_date_method",
        "expected_date_evidence_ref",
        "expected_date_evidence_sha256",
    ):
        payload["source_date_provenance"].pop(key, None)
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload,
            expected_source_date=unverified_date,
        )

    assert caught.value.code in {
        "SOURCE_DATE_PROVENANCE",
        "TRADING_DATE_EVIDENCE_REQUIRED",
    }


def test_source_content_checksum_is_bound_to_response_bytes():
    payload = build()
    payload["source_content_sha256"] = "0" * 64
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload,
            expected_source_date=SOURCE_DATE,
        )

    assert caught.value.code == "SOURCE_DATE_PROVENANCE"


@pytest.mark.parametrize(
    ("mutate", "gate"),
    [
        (lambda rows: rows[:-1], "COUNT_50"),
        (lambda rows: rows + [dict(rows[-1], Symbol="SYM50", **{"ISIN Code": "INE000000050"})], "COUNT_50"),
        (lambda rows: rows[:-1] + [dict(rows[-1], Symbol=rows[0]["Symbol"])], "SYMBOL_UNIQUE"),
        (lambda rows: rows[:-1] + [dict(rows[-1], **{"ISIN Code": rows[0]["ISIN Code"]})], "ISIN_UNIQUE"),
        (lambda rows: [dict(rows[0], **{"Company Name": ""})] + rows[1:], "COMPANY_NONBLANK"),
        (lambda rows: [dict(rows[0], Industry="")] + rows[1:], "MACRO_SECTOR_NONBLANK"),
        (lambda rows: [dict(rows[0], Series="BE")] + rows[1:], "SERIES_EQ"),
    ],
)
def test_invalid_csv_fails_closed_with_stable_gate(mutate, gate):
    with pytest.raises(SourceContractError) as caught:
        build(mutate(make_rows()))
    assert caught.value.code == gate


def test_non_allowlisted_source_url_is_rejected():
    with pytest.raises(SourceContractError) as caught:
        build(url="https://example.com/nifty50.csv")
    assert caught.value.code == "SOURCE_URL_ALLOWLIST"


def test_invalid_source_date_is_rejected():
    with pytest.raises(SourceContractError) as caught:
        build_nifty50_source_payload(
            csv_bytes(make_rows()), OFFICIAL_URL, "24-09-2026", FETCHED_AT
        )
    assert caught.value.code == "SOURCE_DATE_FORMAT"


def test_checksum_mutation_is_rejected():
    payload = build()
    payload["constituents"][0]["company_name"] = "Tampered Company"

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload,
            expected_source_date=SOURCE_DATE,
        )
    assert caught.value.code == "SOURCE_CHECKSUM"


def test_stale_but_checksum_valid_payload_is_rejected_independently():
    payload = build()
    payload["source_as_of_date"] = "2026-09-24"
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload, expected_source_date=SOURCE_DATE
        )
    assert caught.value.code == "SOURCE_DATE_STALE"

