import csv
import io
import json
import hashlib
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


# The constituent roster is always loaded from the same-run official fetch.
# Never fall back to an embedded 50-stock list or silently trust a stale cache.
SOURCE_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"


def load_verified_membership(path="nifty50_latest.json"):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("CURRENT_CONSTITUENTS_UNAVAILABLE") from exc

    if not isinstance(raw, dict) or raw.get("status") != "PASS" or (
        raw.get("source_url") != SOURCE_URL or raw.get("count") != 50
    ):
        raise RuntimeError("CURRENT_CONSTITUENTS_NOT_PASS")

    symbols = raw.get("symbols")
    rows = raw.get("constituents")
    if not isinstance(symbols, list) or not isinstance(rows, list) or (
        len(symbols) != 50 or len(rows) != 50
    ):
        raise RuntimeError("CONSTITUENT_COVERAGE_INVALID")
    if any(not isinstance(s, str) or
           not re.fullmatch(r"[A-Z0-9&-]{1,25}", s) for s in symbols):
        raise RuntimeError("CONSTITUENT_SYMBOL_INVALID")
    membership = set(symbols)
    row_symbols = [r.get("symbol") if isinstance(r, dict) else None
                   for r in rows]
    if len(membership) != 50 or set(row_symbols) != membership or (
        len(set(row_symbols)) != 50
    ):
        raise RuntimeError("CONSTITUENT_MEMBERSHIP_MISMATCH")

    try:
        fetched = datetime.fromisoformat(
            raw["fetched_at_utc"].replace("Z", "+00:00")
        )
        if fetched.tzinfo is None or fetched.utcoffset() is None:
            raise ValueError("missing timezone")
        age = datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)
        if age.total_seconds() < -60 or age > timedelta(hours=2):
            raise ValueError("stale/future source")
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
        raise RuntimeError("CONSTITUENT_FRESH_FETCH_UNVERIFIED") from exc

    # Hash source-identifying fields; no claim of exchange digital signature.
    attestation = {
        "source_url": SOURCE_URL,
        "symbols": sorted(membership),
        "fetched_at_utc": raw["fetched_at_utc"],
    }
    digest = hashlib.sha256(json.dumps(
        attestation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return membership, digest, raw["fetched_at_utc"]


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

def parse_delivery(text, membership):

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

        if symbol not in membership:
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
            raise RuntimeError("DUPLICATE_EQUITY_DELIVERY_ROW: " + symbol)
        if (delivery_qty is None or not math.isfinite(delivery_qty) or
                delivery_qty < 0 or delivery_percent is None or
                not math.isfinite(delivery_percent) or not 0 <= delivery_percent <= 100):
            raise RuntimeError("INVALID_EQUITY_DELIVERY_ROW: " + symbol)
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

    membership, roster_digest, roster_fetched_at = load_verified_membership()
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

    if previous_date >= current_date:
        raise RuntimeError("DELIVERY_DATE_ORDER_INVALID")
    current_data = parse_delivery(current_text, membership)
    previous_data = parse_delivery(previous_text, membership)
    if set(current_data) != membership or set(previous_data) != membership:
        raise RuntimeError("DELIVERY_CONSTITUENT_UNIVERSE_MISMATCH")

    rows = []

    pass_count = 0

    for symbol in sorted(membership):

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
            rows,
        "constituent_sha256": roster_digest,
        "constituent_symbols": sorted(membership),
        "constituent_source_url": SOURCE_URL,
        "constituent_fetched_at_utc": roster_fetched_at
    }

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

    # Publish ONLY after complete 50/50 current+previous session validation.
    # os.replace makes the one-cache file replacement atomic. Both cache
    # artifacts are published together by one Git commit in delivery.yml.
    staged_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=".",
            prefix=".delivery_latest.", suffix=".tmp", delete=False
        ) as staged:
            staged_path = staged.name
            json.dump(result, staged, indent=2, ensure_ascii=False)
            staged.flush()
            os.fsync(staged.fileno())
        os.replace(staged_path, "delivery_latest.json")
        staged_path = None
    finally:
        if staged_path and os.path.exists(staged_path):
            os.unlink(staged_path)

    print(
        "FINAL STATUS: PASS — 50/50"
    )


if __name__ == "__main__":
    main()
