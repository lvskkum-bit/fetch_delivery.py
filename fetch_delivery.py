import csv
import io
import json
import requests
import os
from delivery_source_contract import (
    load_membership, validate_delivery_artifact, write_atomic_json
)
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# Task 4S-2: no frozen stock universe. Only a recently validated
# same-run official constituent artifact can authorize delivery symbols.
# Membership date and market session date are distinct authorities.


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Referer": "https://www.nseindia.com/"
}


# ============================================================
# DOWNLOAD ONE NSE DELIVERY FILE
# ============================================================

def fetch_day(day):

    date_text = day.strftime("%d%m%Y")

    url = (
        "https://nsearchives.nseindia.com/"
        "products/content/sec_bhavdata_full_"
        + date_text
        + ".csv"
    )

    print("TRY:", day.isoformat())

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        print("HTTP:", response.status_code)

        if response.status_code != 200:
            return None

        text = response.text.strip()

        if not text:
            return None

        if "SYMBOL" not in text.upper():
            return None

        print(
            "VALID NSE FILE:",
            day.isoformat(),
            "BYTES:",
            len(response.content)
        )

        return text

    except Exception as exc:

        print(
            "FETCH ERROR:",
            day.isoformat(),
            str(exc)
        )

        return None


# ============================================================
# FIND LATEST TWO VALID TRADING SESSIONS
# Weekend / holiday automatically skipped
# ============================================================

def find_latest_two_sessions():

    today = datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).date()

    sessions = []

    for offset in range(0, 15):

        day = today - timedelta(days=offset)

        text = fetch_day(day)

        if text:

            sessions.append(
                (day, text)
            )

            if len(sessions) == 2:
                break

    if len(sessions) != 2:

        raise RuntimeError(
            "Could not find latest two valid NSE delivery sessions."
        )

    return sessions[0], sessions[1]


# ============================================================
# NORMALIZE CSV HEADER
# ============================================================

def normalize_header(value):

    return (
        str(value or "")
        .strip()
        .upper()
        .replace(" ", "")
        .replace("_", "")
        .replace(".", "")
    )


# ============================================================
# PARSE DELIVERY FILE
# ============================================================

def parse_delivery(text, symbols):

    if not isinstance(symbols, set) or len(symbols) != 50:
        raise ValueError('CURRENT_MEMBERSHIP_UNAVAILABLE')


    reader = csv.DictReader(
        io.StringIO(text)
    )

    if not reader.fieldnames:

        raise RuntimeError(
            "NSE CSV has no header."
        )

    print(
        "CSV HEADERS:",
        reader.fieldnames
    )

    header_map = {
        normalize_header(header): header
        for header in reader.fieldnames
    }

    def get_value(row, candidates):

        for candidate in candidates:

            key = normalize_header(candidate)

            if key in header_map:
                return row.get(
                    header_map[key]
                )

        return None

    output = {}

    for row in reader:

        symbol = get_value(
            row,
            ["SYMBOL"]
        )

        series = get_value(
            row,
            ["SERIES"]
        )

        if not symbol:
            continue

        symbol = str(symbol).strip().upper()

        if symbol not in symbols:
            continue

        if (
            series
            and str(series).strip().upper() != "EQ"
        ):
            continue

        delivery_qty = get_value(
            row,
            [
                "DELIV_QTY",
                "DELIVQTY",
                "DELIVERABLEQTY",
                "DELIVERABLE QUANTITY"
            ]
        )

        delivery_percent = get_value(
            row,
            [
                "DELIV_PER",
                "DELIVPER",
                "DELIVERY%",
                "%DLYQTTOTRADEDQTY",
                "% DLY QT TO TRADED QTY"
            ]
        )

        try:

            delivery_qty = float(
                str(delivery_qty)
                .replace(",", "")
                .strip()
            )

        except Exception:

            delivery_qty = None

        try:

            delivery_percent = float(
                str(delivery_percent)
                .replace("%", "")
                .replace(",", "")
                .strip()
            )

        except Exception:

            delivery_percent = None

        if symbol in output:
            raise ValueError("DUPLICATE_EQUITY_DELIVERY_ROW: " + symbol)

        output[symbol] = {
            "deliveryQty": delivery_qty,
            "deliveryPercent": delivery_percent
        }

    print(
        "NIFTY50 ROWS PARSED:",
        len(output)
    )

    return output


# ============================================================
# MAIN
# ============================================================

def main():

    # An expected date must come from an independently validated calendar
    # approval step, not from today's weekday, an HTTP receipt, or a guess.
    expected_completed_date = os.environ.get("EXPECTED_COMPLETED_TRADING_DATE", "")
    if not expected_completed_date:
        raise ValueError("EXPECTED_DATE_REQUIRED: verified NSE session unavailable")
    proof = load_membership("nifty50_latest.json")
    symbols = proof["symbols"]

    print("-----------------------------------")
    print("NIFTY50 DELIVERY CACHE — START")
    print("-----------------------------------")

    current_session, previous_session = (
        find_latest_two_sessions()
    )

    current_date, current_text = (
        current_session
    )

    previous_date, previous_text = (
        previous_session
    )

    print(
        "CURRENT TRADING DATE:",
        current_date.isoformat()
    )

    print(
        "PREVIOUS TRADING DATE:",
        previous_date.isoformat()
    )

    current_data = parse_delivery(
        current_text, symbols
    )

    previous_data = parse_delivery(
        previous_text, symbols
    )

    rows = []

    pass_count = 0

    for symbol in sorted(symbols):

        current_row = current_data.get(
            symbol,
            {}
        )

        previous_row = previous_data.get(
            symbol,
            {}
        )

        current_qty = current_row.get(
            "deliveryQty"
        )

        previous_qty = previous_row.get(
            "deliveryQty"
        )

        delivery_percent = current_row.get(
            "deliveryPercent"
        )

        ratio = None

        if (
            previous_qty is not None
            and current_qty is not None
            and previous_qty > 0
        ):

            ratio = (
                current_qty /
                previous_qty
            )

        status = "PASS"

        if (
            previous_qty is None
            or current_qty is None
            or delivery_percent is None
        ):
            status = "FAIL"

        if status == "PASS":
            pass_count += 1

        rows.append({
            "symbol": symbol,
            "previousDate":
                previous_date.isoformat(),
            "currentDate":
                current_date.isoformat(),
            "previousDeliveryQty":
                previous_qty,
            "currentDeliveryQty":
                current_qty,
            "deliveryRatio":
                ratio,
            "currentDeliveryPercent":
                delivery_percent,
            "status":
                status
        })

    result = {
        "constituent_sha256": proof["source_sha256"],
        "constituent_fetched_at_utc": proof["fetched_at_utc"],
        "generatedAt":
            datetime.now(
                ZoneInfo("Asia/Kolkata")
            ).isoformat(),

        "previousTradingDate":
            previous_date.isoformat(),

        "currentTradingDate":
            current_date.isoformat(),

        "total":
            len(rows),

        "pass":
            pass_count,

        "fail":
            len(rows) - pass_count,

        "rows":
            rows
    }

    # Fail closed: validate all 50 exact current membership rows, source
    # checksum, quantities and VERIFIED expected session date BEFORE write.
    validate_delivery_artifact(
        result, proof, expected_date=expected_completed_date
    )

    # Atomic local replacement only after validation succeeds. GitHub
    # publication is a separate workflow step requiring its own approval.
    write_atomic_json("delivery_latest.json", result)

    print("-----------------------------------")
    print("NIFTY50 DELIVERY CACHE")
    print("CURRENT :", result["currentTradingDate"])
    print("TOTAL   :", result["total"])
    print("PASS    :", result["pass"])
    print("FAIL    :", result["fail"])
    print("STATUS  : PASS — validated exact current 50/50 + session")
    print("-----------------------------------")


if __name__ == "__main__":
    main()
