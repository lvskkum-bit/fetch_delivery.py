"""Task 4S-3 — live NSE *read-only* replay for completed 2026-10-09.

Network reads fetch official constituents + NSE archived 2 cash sessions.
All temporary files remain in a deleted temp directory. No repo mutations,
GitHub push, Apps Script writes, trading orders, or cache publication.
A date argument is only an observation window; this is NOT a trusted-calendar
source and cannot authorize a production release.
"""
from __future__ import annotations
import json
import os
import tempfile
from datetime import date, datetime, timezone

import fetch_delivery
import fetch_nifty50_constituents
from delivery_source_contract import load_membership


def run_replay(*, expected_date: str, previous_date: str) -> dict:
    d=date.fromisoformat(expected_date)
    p=date.fromisoformat(previous_date)
    if d<=p:
        raise ValueError("INVALID_REPLAY_DATES")
    with tempfile.TemporaryDirectory(prefix="phase4-nse-observation-") as temp:
        working=os.getcwd()
        try:
            os.chdir(temp)
            # This writes ONLY the temporary working directory, NEVER the
            # user's GitHub checkout or live published cache.
            fetch_nifty50_constituents.main()
            proof=load_membership("nifty50_latest.json")
            syms=proof["symbols"]
            current_csv=fetch_delivery.fetch_day(d)
            previous_csv=fetch_delivery.fetch_day(p)
            if not current_csv or not previous_csv:
                raise ValueError("NSE_DELIVERY_SOURCE_UNAVAILABLE")
            current=fetch_delivery.parse_delivery(current_csv,syms)
            previous=fetch_delivery.parse_delivery(previous_csv,syms)
            if set(current)!=syms or set(previous)!=syms:
                raise ValueError("NSE_DELIVERY_MEMBERSHIP_COVERAGE_WAIT")
            invalid=[s for s in sorted(syms) if (
                current[s].get("deliveryQty") is None
                or previous[s].get("deliveryQty") is None
                or current[s].get("deliveryPercent") is None
                or previous[s].get("deliveryQty")<=0
            )]
            if invalid:
                raise ValueError("NSE_DELIVERY_FIELD_VALIDATION_WAIT")
            return {
                "status":"PASS","replay_only":True,"production_write":False,
                "verified_calendar_gate":False,
                "replay_session":expected_date,"previous_session":previous_date,
                "universe_count":len(syms),"current_rows":len(current),
                "previous_rows":len(previous),
                "BSE_included":"BSE" in syms and "BSE" in current,
                "WIPRO_excluded":"WIPRO" not in syms and "WIPRO" not in current,
                "source_sha256":proof["source_sha256"]
            }
        finally:
            os.chdir(working)


def main():
    try:
        result=run_replay(expected_date="2026-10-09",previous_date="2026-10-08")
        print(json.dumps(result,sort_keys=True))
        return 0 if result["status"]=="PASS" else 2
    except (RuntimeError,ValueError,OSError,KeyError,TypeError) as err:
        # No upstream payloads or authentication values are logged.
        print(json.dumps({"status":"WAIT","reason":"NSE_READONLY_REPLAY_UNVERIFIED",
                          "production_write":False,"replay_only":True},sort_keys=True))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
