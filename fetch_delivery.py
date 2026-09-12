import csv
import io
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# NIFTY 50 — CURRENT WORKING UNIVERSE
# ============================================================

NIFTY50 = {
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
}


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

def parse_delivery(text):

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

        if symbol not in NIFTY50:
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
        current_text
    )

    previous_data = parse_delivery(
        previous_text
    )

    rows = []

    pass_count = 0

    for symbol in sorted(NIFTY50):

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

    # --------------------------------------------------------
    # WRITE CACHE
    # --------------------------------------------------------

    with open(
        "delivery_latest.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False
        )

    print("-----------------------------------")
    print("NIFTY50 DELIVERY CACHE")
    print("-----------------------------------")
    print(
        "PREVIOUS:",
        previous_date.isoformat()
    )
    print(
        "CURRENT :",
        current_date.isoformat()
    )
    print(
        "TOTAL   :",
        len(rows)
    )
    print(
        "PASS    :",
        pass_count
    )
    print(
        "FAIL    :",
        len(rows) - pass_count
    )
    print(
        "JSON    : delivery_latest.json"
    )
    print("-----------------------------------")

    # --------------------------------------------------------
    # STRICT AUDIT
    # --------------------------------------------------------

    if len(rows) != 50:

        raise RuntimeError(
            "Expected 50 NIFTY50 stocks, found "
            + str(len(rows))
        )

    if pass_count != 50:

        failed = [
            row["symbol"]
            for row in rows
            if row["status"] != "PASS"
        ]

        print(
            "FAILED SYMBOLS:",
            failed
        )

        raise RuntimeError(
            "Delivery audit failed: "
            + str(pass_count)
            + "/50 PASS"
        )

    print(
        "FINAL STATUS: PASS — 50/50"
    )


if __name__ == "__main__":
    main()
