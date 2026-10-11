"""GitHub Actions CLI: fail-closed verified Stock Master calendar handoff.

No token contents, raw payload or secrets ever logged. Date output is produced
ONLY after HTTPS GitHub run+artifact provenance checks have fully passed.
"""
from __future__ import annotations

import json
import os
import sys

from phase4_calendar_bridge import CalendarBridgeError, retrieve_approved_calendar


def execute(*, env=None) -> dict:
    env = os.environ if env is None else env
    try:
        approved_run = int(env.get("PHASE4_CALENDAR_APPROVED_RUN_ID",""))
    except (TypeError,ValueError) as e:
        raise CalendarBridgeError("CALENDAR_BRIDGE_WAIT:RUN_APPROVAL_MISSING") from e
    proof = retrieve_approved_calendar(
        token=env.get("STOCK_MASTER_CALENDAR_READ_TOKEN",""),
        approved_run_id=approved_run,
        approved_head_sha=env.get("PHASE4_CALENDAR_APPROVED_HEAD_SHA",""),
        expected_date=env.get("PHASE4_APPROVED_COMPLETED_SESSION_DATE",""),
    )
    output_path=env.get("GITHUB_OUTPUT","")
    if not output_path:
        raise CalendarBridgeError("CALENDAR_BRIDGE_WAIT:CI_OUTPUT_PATH_MISSING")
    # The approval line contains only date/digest/run identity, not any secret.
    with open(output_path,"a",encoding="utf-8") as f:
        f.write("verified_session_date="+proof["expected_completed_trading_date"]+"\n")
        f.write("calendar_evidence_sha256="+proof["evidence_sha256"]+"\n")
    return {"status":"PASS","session":proof["expected_completed_trading_date"],
            "run_id":proof["source_run_id"],"production_write":False}


def main():
    try:
        result = execute()
        print(json.dumps(result,sort_keys=True))
        return 0
    except (CalendarBridgeError,OSError) as e:
        print(json.dumps({"status":"WAIT","reason":"VERIFIED_CALENDAR_BRIDGE_NOT_READY",
                          "production_write":False},sort_keys=True))
        return 2


if __name__=="__main__":
    sys.exit(main())
