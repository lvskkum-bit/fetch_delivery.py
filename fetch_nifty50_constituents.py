import csv
import io
import json
from datetime import datetime, timezone

import requests


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

    text = response.content.decode(
        "utf-8-sig",
        errors="replace"
    )

    reader = csv.DictReader(
        io.StringIO(text)
    )

    rows = []
    symbols = []

    for row in reader:

        symbol = str(
            row.get("Symbol", "")
        ).strip().upper()

        if not symbol:
            continue

        symbols.append(symbol)

        rows.append({
            "symbol": symbol,
            "company_name": str(
                row.get("Company Name", "")
            ).strip(),

            "industry": str(
                row.get("Industry", "")
            ).strip(),

            "series": str(
                row.get("Series", "")
            ).strip(),

            "isin": str(
                row.get("ISIN Code", "")
            ).strip()
        })


    unique_symbols = sorted(set(symbols))

    duplicate_count = (
        len(symbols) -
        len(unique_symbols)
    )

    print("RAW ROWS =", len(rows))
    print("UNIQUE SYMBOLS =", len(unique_symbols))
    print("DUPLICATES =", duplicate_count)


    if len(unique_symbols) != 50:

        raise RuntimeError(
            "NIFTY50 AUDIT FAIL — "
            "EXPECTED 50 UNIQUE SYMBOLS, FOUND "
            + str(len(unique_symbols))
        )


    if duplicate_count != 0:

        raise RuntimeError(
            "NIFTY50 AUDIT FAIL — DUPLICATES FOUND"
        )


    output = {
        "source": "NSE Indices — NIFTY 50 Index Constituent",
        "source_url": URL,

        "fetched_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "count": len(unique_symbols),

        "symbols": unique_symbols,

        "constituents": rows,

        "status": "PASS"
    }


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
    print("COUNT =", len(unique_symbols))
    print("STATUS = PASS — 50/50")
    print("====================================")


if __name__ == "__main__":
    main()
