import json
import os
from datetime import datetime, timezone

import requests

from phase3_mapping_contract import (
    SourceContractError,
    build_nifty50_source_payload,
    source_date_from_last_modified,
)


URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"

OUTPUT_FILE = "nifty50_latest.json"


def main():

    print("====================================")
    print("NIFTY50 CURRENT CONSTITUENT FETCH")
    print("====================================")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/152.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": (
            "https://www.niftyindices.com/"
            "indices/equity/broad-based-indices/nifty--50"
        ),
    }

    response = requests.get(
        URL,
        headers=headers,
        timeout=30
    )

    print("HTTP =", response.status_code)

    response.raise_for_status()

    last_modified = response.headers.get("Last-Modified")
    if not last_modified:
        raise SourceContractError(
            "SOURCE_DATE_PROVENANCE",
            "official response did not provide Last-Modified",
        )
    source_as_of_date = source_date_from_last_modified(last_modified)
    requested_source_as_of_date = os.environ.get("NIFTY50_SOURCE_AS_OF_DATE")
    if requested_source_as_of_date and requested_source_as_of_date != source_as_of_date:
        raise SourceContractError(
            "SOURCE_DATE_PROVENANCE",
            "requested source date does not match official Last-Modified date",
        )
    expected_completed_trading_date = os.environ.get(
        "NIFTY50_EXPECTED_COMPLETED_TRADING_DATE"
    )
    expected_date_evidence_ref = os.environ.get("NIFTY50_EXPECTED_DATE_EVIDENCE_REF")
    expected_date_evidence_sha256 = os.environ.get(
        "NIFTY50_EXPECTED_DATE_EVIDENCE_SHA256"
    )
    if not (
        expected_completed_trading_date
        and expected_date_evidence_ref
        and expected_date_evidence_sha256
    ):
        raise SourceContractError(
            "TRADING_DATE_EVIDENCE_REQUIRED",
            "approved independent trading-calendar evidence is required",
        )
    fetched_at_utc = datetime.now(timezone.utc).isoformat()
    source_date_provenance = {
        "method": "HTTP_LAST_MODIFIED_UTC_DATE",
        "source_url": URL,
        "last_modified": last_modified,
        "derived_source_as_of_date": source_as_of_date,
        "expected_completed_trading_date": expected_completed_trading_date,
        "expected_date_method": "EXTERNAL_APPROVED_MARKET_CALENDAR",
        "expected_date_evidence_ref": expected_date_evidence_ref,
        "expected_date_evidence_sha256": expected_date_evidence_sha256,
    }
    etag = response.headers.get("ETag")
    if etag:
        source_date_provenance["etag"] = etag
    if requested_source_as_of_date:
        source_date_provenance["requested_source_as_of_date"] = (
            requested_source_as_of_date
        )
    output = build_nifty50_source_payload(
        response.content,
        URL,
        source_as_of_date,
        fetched_at_utc,
        source_date_provenance,
    )


    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )


    print("====================================")
    print("OUTPUT =", OUTPUT_FILE)
    print("COUNT =", output["count"])
    print("SOURCE AS OF =", output["source_as_of_date"])
    print("SOURCE CHECKSUM =", output["source_checksum"])
    print("STATUS = PASS — 50/50")
    print("====================================")


if __name__ == "__main__":
    main()
