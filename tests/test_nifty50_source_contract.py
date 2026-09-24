import copy
import csv
import io
from datetime import datetime, timezone

import pytest

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
SOURCE_DATE = "2026-09-24"
FETCHED_AT = "2026-09-24T12:54:04+00:00"


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
    return build_nifty50_source_payload(
        csv_bytes(rows or make_rows()),
        url,
        SOURCE_DATE,
        FETCHED_AT,
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
        payload, expected_source_date=SOURCE_DATE
    )["status"] == "PASS"


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
            payload, expected_source_date=SOURCE_DATE
        )
    assert caught.value.code == "SOURCE_CHECKSUM"


def test_stale_but_checksum_valid_payload_is_rejected_independently():
    payload = build()
    payload["source_as_of_date"] = "2026-09-23"
    payload["source_checksum"] = canonical_sha256(payload)

    with pytest.raises(SourceContractError) as caught:
        validate_nifty50_source_payload(
            payload, expected_source_date=SOURCE_DATE
        )
    assert caught.value.code == "SOURCE_DATE_STALE"

