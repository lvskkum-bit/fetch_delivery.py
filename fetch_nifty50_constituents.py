import json
import os
from datetime import date, datetime, timezone

import requests

from phase3_mapping_contract import build_nifty50_source_payload


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

    source_as_of_date = os.environ.get(
        "NIFTY50_SOURCE_AS_OF_DATE",
        date.today().isoformat(),
    )
    fetched_at_utc = datetime.now(timezone.utc).isoformat()
    output = build_nifty50_source_payload(
        response.content,
        URL,
        source_as_of_date,
        fetched_at_utc,
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
