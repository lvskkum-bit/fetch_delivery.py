#!/usr/bin/env python3
"""
NIFTY50 ENRICHMENT 50/50 DRY-RUN + MASTER-C PARITY AUDIT v1.1

SAFE / READ-ONLY CONTRACT
-------------------------
- Reads existing: nifty50_latest.json
- Fetches NSE equity classification for EXACT same 50 symbols
- Adds temporary fields:
    sector
    nse_industry
    basic_industry
- Compares:
    legacy industry  <-> NSE sector
    current Master C <-> NSE industry
- NEVER overwrites nifty50_latest.json
- Writes only temporary dry-run/audit files:
    nifty50_enriched_dry_run.json
    nifty50_enrichment_audit.json

PASS requires:
- 50/50 membership
- 50/50 unique
- 50/50 NSE classification success
- 50/50 sector nonblank
- 50/50 nse_industry nonblank
- 0 true legacy-industry vs NSE-sector differences
- 50/50 Master-C semantic parity
"""

import json
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from phase3_mapping_contract import validate_classification_50


VERSION = "NIFTY50-ENRICHMENT-DRYRUN-v1.1"

INPUT_JSON = Path(
    os.environ.get("NIFTY50_INPUT_JSON", "nifty50_latest.json")
)

DRY_RUN_JSON = Path(
    os.environ.get(
        "NIFTY50_DRY_RUN_JSON",
        "nifty50_enriched_dry_run.json",
    )
)

AUDIT_JSON = Path(
    os.environ.get(
        "NIFTY50_AUDIT_JSON",
        "nifty50_enrichment_audit.json",
    )
)

EXPECTED_STOCKS = 50

NSE_HOME = "https://www.nseindia.com/"
NSE_QUOTE_API = "https://www.nseindia.com/api/quote-equity"
NSE_QUOTE_PAGE = "https://www.nseindia.com/get-quotes/equity"

REQUEST_TIMEOUT = 20
MAX_ATTEMPTS = 3
REQUEST_PAUSE_SECONDS = 0.20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


# ============================================================
# CURRENT MASTER-C BASELINE
#
# Evidence source:
# Actual Function-3 execution log supplied by user.
#
# C = current granular NSE Industry-level classification.
#
# This baseline is ONLY for the dry-run parity audit.
# It is NOT written into production JSON.
# ============================================================

CURRENT_MASTER_C: Dict[str, str] = {
    "ADANIENT": "Metals & Minerals Trading",
    "ADANIPORTS": "Transport Services",
    "APOLLOHOSP": "Healthcare Services",
    "ASIANPAINT": "Consumer Durables",
    "AXISBANK": "Banks",
    "BAJAJ-AUTO": "Automobiles",
    "BAJAJFINSV": "Finance",
    "BAJFINANCE": "Finance",
    "BEL": "Aerospace & Defense",
    "BHARTIARTL": "Telecom Services",
    "CIPLA": "Pharmaceuticals & Biotechnology",
    "COALINDIA": "Consumable Fuels",
    "DRREDDY": "Pharmaceuticals & Biotechnology",
    "EICHERMOT": "Automobiles",
    "ETERNAL": "Retailing",
    "GRASIM": "Cement & Cement Products",
    "HCLTECH": "IT - Software",
    "HDFCBANK": "Banks",
    "HDFCLIFE": "Insurance",
    "HINDALCO": "Non - Ferrous Metals",
    "HINDUNILVR": "Fast Moving Consumer Goods",
    "ICICIBANK": "Banks",
    "INDIGO": "Transport Services",
    "INFY": "IT - Software",
    "ITC": "Fast Moving Consumer Goods",
    "JIOFIN": "Finance",
    "JSWSTEEL": "Ferrous Metals",
    "KOTAKBANK": "Banks",
    "LT": "Construction",
    "M&M": "Automobiles",
    "MARUTI": "Automobiles",
    "MAXHEALTH": "Healthcare Services",
    "NESTLEIND": "Food Products",
    "NTPC": "Power",
    "ONGC": "Oil",
    "POWERGRID": "Power",
    "RELIANCE": "Petroleum Products",
    "SBILIFE": "Insurance",
    "SBIN": "Banks",
    "SHRIRAMFIN": "Finance",
    "SUNPHARMA": "Pharmaceuticals & Biotechnology",
    "TATACONSUM": "Agricultural Food & other Products",
    "TATASTEEL": "Ferrous Metals",
    "TCS": "IT - Software",
    "TECHM": "IT - Software",
    "TITAN": "Consumer Durables",
    "TMPV": "Automobiles",
    "TRENT": "Retailing",
    "ULTRACEMCO": "Cement & Cement Products",
    "WIPRO": "IT - Software",
}


def log(message: str = "") -> None:
    print(message, flush=True)


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_classification_text(value: Any) -> str:
    """
    Normalization ONLY for parity comparison.
    Original JSON/source values are preserved unchanged.
    """
    text = str(value or "").strip().upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def read_existing_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(
            f"INPUT JSON NOT FOUND: {path}"
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"INVALID INPUT JSON: {exc}"
        ) from exc

    return data


def validate_membership_payload(
    payload: Dict[str, Any]
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:

    status = str(payload.get("status") or "").strip().upper()

    if status != "PASS":
        raise RuntimeError(
            f"INPUT SOURCE STATUS != PASS: {status}"
        )

    symbols_raw = payload.get("symbols")
    constituents_raw = payload.get("constituents")

    if not isinstance(symbols_raw, list):
        raise RuntimeError("symbols[] missing")

    if not isinstance(constituents_raw, list):
        raise RuntimeError("constituents[] missing")

    symbols = [
        normalize_symbol(x)
        for x in symbols_raw
        if normalize_symbol(x)
    ]

    if len(symbols) != EXPECTED_STOCKS:
        raise RuntimeError(
            f"MEMBERSHIP COUNT FAIL: {len(symbols)}"
        )

    if len(set(symbols)) != EXPECTED_STOCKS:
        raise RuntimeError(
            "MEMBERSHIP UNIQUE FAIL"
        )

    constituent_map: Dict[str, Dict[str, Any]] = {}

    for row in constituents_raw:
        if not isinstance(row, dict):
            continue

        symbol = normalize_symbol(row.get("symbol"))

        if not symbol:
            continue

        if symbol in constituent_map:
            raise RuntimeError(
                f"DUPLICATE CONSTITUENT: {symbol}"
            )

        constituent_map[symbol] = row

    if len(constituent_map) != EXPECTED_STOCKS:
        raise RuntimeError(
            f"CONSTITUENT COUNT FAIL: {len(constituent_map)}"
        )

    missing = [
        s for s in symbols
        if s not in constituent_map
    ]

    extra = [
        s for s in constituent_map
        if s not in set(symbols)
    ]

    if missing:
        raise RuntimeError(
            "MISSING CONSTITUENTS: " + ", ".join(missing)
        )

    if extra:
        raise RuntimeError(
            "EXTRA CONSTITUENTS: " + ", ".join(extra)
        )

    required_legacy = [
        "company_name",
        "industry",
        "series",
        "isin",
    ]

    for symbol in symbols:
        row = constituent_map[symbol]

        for key in required_legacy:
            if not str(row.get(key) or "").strip():
                raise RuntimeError(
                    f"BLANK LEGACY FIELD: "
                    f"{symbol} | {key}"
                )

    return symbols, constituent_map


def best_effort_nse_warmup(
    session: requests.Session,
    symbol: str = "RELIANCE",
) -> None:
    """
    Best-effort cookie/session warm-up only.

    IMPORTANT:
    - A 403 from NSE homepage/quote page is NOT treated as a fatal error.
    - The real evidence gate is the quote-equity API response itself.
    """
    warmup_urls = [
        NSE_HOME,
        NSE_QUOTE_PAGE,
    ]

    for url in warmup_urls:
        try:
            if url == NSE_QUOTE_PAGE:
                response = session.get(
                    url,
                    params={"symbol": symbol},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
            else:
                response = session.get(
                    url,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )

            log(
                "NSE WARMUP HTTP = "
                f"{response.status_code} | {url}"
            )

        except Exception as exc:
            log(
                "NSE WARMUP WARNING = "
                f"{url} | {exc}"
            )


def build_nse_session() -> requests.Session:
    """
    v1.1 correction:
    The previous version aborted when NSE homepage returned HTTP 403.
    GitHub-hosted runners can receive 403 at the homepage even before
    the actual quote API is tested.

    This version:
    - creates the session,
    - performs only best-effort warm-up,
    - NEVER fails solely because the homepage returns 403,
    - lets fetch_industry_info() test the real quote-equity endpoint.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    log(
        "NSE SESSION MODE = "
        "BEST-EFFORT WARMUP; HOMEPAGE 403 IS NON-FATAL"
    )

    best_effort_nse_warmup(
        session,
        "RELIANCE",
    )

    return session


def fetch_industry_info(
    session: requests.Session,
    symbol: str,
) -> Dict[str, str]:

    last_error = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):

        try:
            request_headers = {
                "Accept": "application/json,text/plain,*/*",
                "Referer": (
                    "https://www.nseindia.com/get-quotes/equity"
                    f"?symbol={symbol}"
                ),
            }

            response = session.get(
                NSE_QUOTE_API,
                params={"symbol": symbol},
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            code = response.status_code

            log(
                f"{symbol} | NSE API HTTP = {code} "
                f"| ATTEMPT={attempt}/{MAX_ATTEMPTS}"
            )

            # A blocked homepage is no longer fatal. If the actual API
            # says 401/403, warm up again and retry the API.
            if code in (401, 403):
                last_error = f"HTTP={code}"

                if attempt < MAX_ATTEMPTS:
                    best_effort_nse_warmup(
                        session,
                        symbol,
                    )
                    time.sleep(
                        0.75 * attempt
                    )
                    continue

                raise RuntimeError(
                    last_error
                )

            if code != 200:
                raise RuntimeError(
                    f"HTTP={code}"
                )

            try:
                data = response.json()
            except Exception as exc:
                preview = response.text[:200]
                raise RuntimeError(
                    "Invalid JSON from quote-equity | "
                    f"{exc} | PREVIEW={preview!r}"
                ) from exc

            industry_info = data.get("industryInfo")

            if not isinstance(industry_info, dict):
                raise RuntimeError(
                    "industryInfo missing"
                )

            sector = str(
                industry_info.get("sector") or ""
            ).strip()

            industry = str(
                industry_info.get("industry") or ""
            ).strip()

            basic_industry = str(
                industry_info.get("basicIndustry") or ""
            ).strip()

            if not sector:
                raise RuntimeError(
                    "industryInfo.sector blank"
                )

            if not industry:
                raise RuntimeError(
                    "industryInfo.industry blank"
                )

            return {
                "sector": sector,
                "nse_industry": industry,
                "basic_industry": basic_industry,
            }

        except Exception as exc:
            last_error = str(exc)

            if attempt < MAX_ATTEMPTS:
                time.sleep(
                    0.75 * attempt
                )

    raise RuntimeError(
        f"{symbol} classification fetch failed "
        f"after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def build_dry_run_payload(
    original_payload: Dict[str, Any],
    symbols: List[str],
    constituent_map: Dict[str, Dict[str, Any]],
    classifications: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:

    output = deepcopy(original_payload)

    out_rows = []

    for symbol in symbols:
        old = deepcopy(constituent_map[symbol])
        cls = classifications[symbol]

        # Phase 3 four-tier names are additive in this dry-run artifact.
        old["sector"] = cls["sector"]
        old["industry"] = cls["nse_industry"]
        old["basic_industry"] = cls["basic_industry"]

        out_rows.append(old)

    output["constituents"] = out_rows

    # Dry-run metadata is additive and temporary only.
    output["enrichment_dry_run"] = {
        "version": VERSION,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "production_file_overwritten": False,
    }

    return output


def audit_parity(
    symbols: List[str],
    constituent_map: Dict[str, Dict[str, Any]],
    classifications: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:

    sector_exact = []
    sector_text_only = []
    sector_true_difference = []

    master_c_exact = []
    master_c_text_only = []
    master_c_true_difference = []
    master_c_missing_baseline = []

    basic_nonblank = []

    for symbol in symbols:

        legacy_industry = str(
            constituent_map[symbol].get("macro_sector")
            or constituent_map[symbol].get("industry")
            or ""
        ).strip()

        nse_sector = classifications[symbol]["sector"]
        nse_industry = classifications[symbol]["nse_industry"]
        basic_industry = classifications[symbol]["basic_industry"]

        if basic_industry:
            basic_nonblank.append(symbol)

        # --------------------------
        # Legacy broad B vs NSE sector
        # --------------------------

        if legacy_industry == nse_sector:
            sector_exact.append(symbol)
        elif (
            normalize_classification_text(legacy_industry)
            ==
            normalize_classification_text(nse_sector)
        ):
            sector_text_only.append({
                "symbol": symbol,
                "legacy_industry": legacy_industry,
                "nse_sector": nse_sector,
            })
        else:
            sector_true_difference.append({
                "symbol": symbol,
                "legacy_industry": legacy_industry,
                "nse_sector": nse_sector,
            })

        # --------------------------
        # Current Master C vs NSE industry
        # --------------------------

        baseline_c = CURRENT_MASTER_C.get(symbol)

        if baseline_c is None:
            master_c_missing_baseline.append(symbol)
            continue

        if baseline_c == nse_industry:
            master_c_exact.append(symbol)
        elif (
            normalize_classification_text(baseline_c)
            ==
            normalize_classification_text(nse_industry)
        ):
            master_c_text_only.append({
                "symbol": symbol,
                "current_master_c": baseline_c,
                "nse_industry": nse_industry,
            })
        else:
            master_c_true_difference.append({
                "symbol": symbol,
                "current_master_c": baseline_c,
                "nse_industry": nse_industry,
            })

    master_c_compared = (
        len(master_c_exact)
        + len(master_c_text_only)
        + len(master_c_true_difference)
    )

    return {
        "sector_exact_count": len(sector_exact),
        "sector_exact_symbols": sector_exact,

        "sector_text_only_count": len(sector_text_only),
        "sector_text_only": sector_text_only,

        "sector_true_difference_count":
            len(sector_true_difference),
        "sector_true_difference":
            sector_true_difference,

        "master_c_exact_count": len(master_c_exact),
        "master_c_exact_symbols": master_c_exact,

        "master_c_text_only_count":
            len(master_c_text_only),
        "master_c_text_only":
            master_c_text_only,

        "master_c_true_difference_count":
            len(master_c_true_difference),
        "master_c_true_difference":
            master_c_true_difference,

        "master_c_missing_baseline_count":
            len(master_c_missing_baseline),
        "master_c_missing_baseline":
            master_c_missing_baseline,

        "master_c_compared_count":
            master_c_compared,

        "basic_industry_nonblank_count":
            len(basic_nonblank),
    }


def write_json(
    path: Path,
    payload: Dict[str, Any],
) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:

    log("=" * 72)
    log("GITHUB ENRICHMENT 50/50 DRY-RUN + MASTER-C PARITY AUDIT")
    log(f"VERSION = {VERSION}")
    log("=" * 72)

    log(f"INPUT JSON = {INPUT_JSON}")
    log("PRODUCTION JSON WRITE = NO")
    log("-" * 72)

    try:
        original_payload = read_existing_payload(
            INPUT_JSON
        )

        symbols, constituent_map = (
            validate_membership_payload(
                original_payload
            )
        )

        log(
            f"MEMBERSHIP COUNT = "
            f"{len(symbols)}/{EXPECTED_STOCKS}"
        )
        log(
            f"MEMBERSHIP UNIQUE = "
            f"{len(set(symbols))}/{EXPECTED_STOCKS}"
        )
        log(
            f"CONSTITUENTS = "
            f"{len(constituent_map)}/{EXPECTED_STOCKS}"
        )

        if len(CURRENT_MASTER_C) != EXPECTED_STOCKS:
            raise RuntimeError(
                "MASTER-C BASELINE COUNT FAIL: "
                f"{len(CURRENT_MASTER_C)}"
            )

        if set(CURRENT_MASTER_C) != set(symbols):
            missing_baseline = sorted(
                set(symbols) - set(CURRENT_MASTER_C)
            )
            extra_baseline = sorted(
                set(CURRENT_MASTER_C) - set(symbols)
            )

            raise RuntimeError(
                "MASTER-C BASELINE SYMBOL SET MISMATCH | "
                f"MISSING={missing_baseline} | "
                f"EXTRA={extra_baseline}"
            )

        log(
            "MASTER-C BASELINE = 50/50"
        )
        log("-" * 72)

        session = build_nse_session()

        classifications: Dict[
            str,
            Dict[str, str]
        ] = {}

        failures: List[Dict[str, str]] = []

        for index, symbol in enumerate(
            symbols,
            start=1,
        ):
            try:
                info = fetch_industry_info(
                    session,
                    symbol,
                )

                classifications[symbol] = info

                log(
                    f"[{index:02d}/50] "
                    f"{symbol} | "
                    f"SECTOR={info['sector']} | "
                    f"INDUSTRY={info['nse_industry']} | "
                    f"BASIC={info['basic_industry'] or 'BLANK'}"
                )

            except Exception as exc:
                failures.append({
                    "symbol": symbol,
                    "error": str(exc),
                })

                log(
                    f"[{index:02d}/50] "
                    f"{symbol} | FAIL | {exc}"
                )

            time.sleep(
                REQUEST_PAUSE_SECONDS
            )

        log("-" * 72)
        log(
            f"CLASSIFICATION REQUESTED = "
            f"{len(symbols)}"
        )
        log(
            f"CLASSIFICATION SUCCESS = "
            f"{len(classifications)}"
        )
        log(
            f"CLASSIFICATION FAILED = "
            f"{len(failures)}"
        )

        audit_base = {
            "version": VERSION,
            "generated_at_utc":
                datetime.now(
                    timezone.utc
                ).isoformat(),
            "input_json": str(INPUT_JSON),
            "production_file_overwritten": False,
            "membership_count": len(symbols),
            "membership_unique": len(set(symbols)),
            "classification_requested":
                len(symbols),
            "classification_success":
                len(classifications),
            "classification_failed":
                len(failures),
            "classification_failures":
                failures,
        }

        # Fail closed before building temp enriched JSON.
        if failures:
            audit_base["final_status"] = (
                "FAIL — CLASSIFICATION INCOMPLETE; "
                "DRY-RUN ENRICHED JSON NOT PUBLISHED"
            )

            write_json(
                AUDIT_JSON,
                audit_base,
            )

            log(
                "SECTOR NONBLANK = "
                f"{len(classifications)}/50"
            )
            log(
                "NSE INDUSTRY NONBLANK = "
                f"{len(classifications)}/50"
            )
            log(
                "DRY-RUN ENRICHED JSON WRITE = BLOCKED"
            )
            log(
                "PRODUCTION nifty50_latest.json = UNTOUCHED"
            )
            log(
                "FINAL STATUS = FAIL — "
                "CLASSIFICATION INCOMPLETE"
            )
            log("=" * 72)
            return 2

        # 50/50 classification complete.
        sector_nonblank = sum(
            1
            for s in symbols
            if classifications[s]["sector"]
        )

        industry_nonblank = sum(
            1
            for s in symbols
            if classifications[s]["nse_industry"]
        )

        if sector_nonblank != EXPECTED_STOCKS:
            raise RuntimeError(
                f"SECTOR NONBLANK FAIL: {sector_nonblank}/50"
            )

        if industry_nonblank != EXPECTED_STOCKS:
            raise RuntimeError(
                f"NSE INDUSTRY NONBLANK FAIL: "
                f"{industry_nonblank}/50"
            )

        validate_classification_50(
            [
                {
                    "symbol": symbol,
                    "macro_sector": str(
                        constituent_map[symbol].get("macro_sector")
                        or constituent_map[symbol].get("industry")
                        or ""
                    ).strip(),
                    "sector": classifications[symbol]["sector"],
                    "industry": classifications[symbol]["nse_industry"],
                    "basic_industry": classifications[symbol]["basic_industry"],
                }
                for symbol in symbols
            ],
            set(symbols),
        )

        parity = audit_parity(
            symbols,
            constituent_map,
            classifications,
        )

        audit = {
            **audit_base,
            "sector_nonblank":
                sector_nonblank,
            "nse_industry_nonblank":
                industry_nonblank,
            **parity,
        }

        log(
            f"SECTOR NONBLANK = "
            f"{sector_nonblank}/50"
        )

        log(
            f"NSE INDUSTRY NONBLANK = "
            f"{industry_nonblank}/50"
        )

        log(
            f"BASIC INDUSTRY NONBLANK = "
            f"{parity['basic_industry_nonblank_count']}/50"
        )

        log("-" * 72)

        log(
            f"SECTOR EXACT PARITY = "
            f"{parity['sector_exact_count']}/50"
        )

        log(
            f"SECTOR TEXT-ONLY DIFFERENCE = "
            f"{parity['sector_text_only_count']}"
        )

        for row in parity["sector_text_only"]:
            log(
                "SECTOR TEXT-ONLY — "
                f"{row['symbol']} | "
                f"LEGACY={row['legacy_industry']} | "
                f"NSE={row['nse_sector']}"
            )

        log(
            f"SECTOR TRUE DIFFERENCE = "
            f"{parity['sector_true_difference_count']}"
        )

        for row in parity["sector_true_difference"]:
            log(
                "SECTOR TRUE DIFFERENCE — "
                f"{row['symbol']} | "
                f"LEGACY={row['legacy_industry']} | "
                f"NSE={row['nse_sector']}"
            )

        log("-" * 72)

        log(
            f"MASTER-C EXACT PARITY = "
            f"{parity['master_c_exact_count']}/50"
        )

        log(
            f"MASTER-C TEXT-ONLY DIFFERENCE = "
            f"{parity['master_c_text_only_count']}"
        )

        for row in parity["master_c_text_only"]:
            log(
                "MASTER-C TEXT-ONLY — "
                f"{row['symbol']} | "
                f"CURRENT={row['current_master_c']} | "
                f"NSE={row['nse_industry']}"
            )

        log(
            f"MASTER-C TRUE DIFFERENCE = "
            f"{parity['master_c_true_difference_count']}"
        )

        for row in parity["master_c_true_difference"]:
            log(
                "MASTER-C TRUE DIFFERENCE — "
                f"{row['symbol']} | "
                f"CURRENT={row['current_master_c']} | "
                f"NSE={row['nse_industry']}"
            )

        log(
            f"MASTER-C COMPARED = "
            f"{parity['master_c_compared_count']}/50"
        )

        final_pass = (
            len(classifications) == EXPECTED_STOCKS
            and sector_nonblank == EXPECTED_STOCKS
            and industry_nonblank == EXPECTED_STOCKS
            and parity[
                "sector_true_difference_count"
            ] == 0
            and parity[
                "master_c_missing_baseline_count"
            ] == 0
            and parity[
                "master_c_compared_count"
            ] == EXPECTED_STOCKS
            and parity[
                "master_c_true_difference_count"
            ] == 0
        )

        if final_pass:
            dry_run_payload = (
                build_dry_run_payload(
                    original_payload,
                    symbols,
                    constituent_map,
                    classifications,
                )
            )

            audit["final_status"] = (
                "PASS — 50/50 ENRICHMENT + MASTER-C PARITY"
            )

            write_json(
                DRY_RUN_JSON,
                dry_run_payload,
            )

            write_json(
                AUDIT_JSON,
                audit,
            )

            log("-" * 72)
            log(
                f"DRY-RUN ENRICHED JSON = "
                f"{DRY_RUN_JSON}"
            )
            log(
                f"AUDIT JSON = "
                f"{AUDIT_JSON}"
            )
            log(
                "PRODUCTION nifty50_latest.json = UNTOUCHED"
            )
            log(
                "FINAL STATUS = PASS — "
                "50/50 ENRICHMENT + MASTER-C PARITY READY"
            )
            log("=" * 72)
            return 0

        audit["final_status"] = (
            "REVIEW REQUIRED — PARITY DIFFERENCE FOUND; "
            "PRODUCTION UNTOUCHED"
        )

        write_json(
            AUDIT_JSON,
            audit,
        )

        log("-" * 72)
        log(
            "DRY-RUN ENRICHED JSON WRITE = BLOCKED"
        )
        log(
            f"AUDIT JSON = {AUDIT_JSON}"
        )
        log(
            "PRODUCTION nifty50_latest.json = UNTOUCHED"
        )
        log(
            "FINAL STATUS = REVIEW REQUIRED — "
            "PARITY DIFFERENCE FOUND"
        )
        log("=" * 72)

        return 3

    except Exception as exc:

        failure_audit = {
            "version": VERSION,
            "generated_at_utc":
                datetime.now(
                    timezone.utc
                ).isoformat(),
            "production_file_overwritten": False,
            "final_status":
                "FAIL — DRY-RUN ABORTED",
            "error": str(exc),
        }

        try:
            write_json(
                AUDIT_JSON,
                failure_audit,
            )
        except Exception:
            pass

        log("-" * 72)
        log(
            f"ERROR = {exc}"
        )
        log(
            "PRODUCTION nifty50_latest.json = UNTOUCHED"
        )
        log(
            "FINAL STATUS = FAIL — DRY-RUN ABORTED"
        )
        log("=" * 72)
        return 1


if __name__ == "__main__":
    sys.exit(main())
