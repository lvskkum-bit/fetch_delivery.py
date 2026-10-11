"""Task 4S-8: authenticated NSE calendar -> BSE delivery DRY RUN.

This program deliberately executes the real producer against a disposable
temporary directory. All source files and delivery JSON are confined to
that directory and deleted; there is NO GitHub commit, push, published cache
edit, Google Sheet/App Script mutation, broker action, or secret logging.

The pinned 2026-10-09 approval is a HISTORICAL, one-time verification
fixture: it cannot approve any later market session.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import fetch_delivery
import fetch_nifty50_constituents
from delivery_source_contract import load_membership, validate_delivery_artifact
from phase4_calendar_bridge import retrieve_approved_calendar, CalendarBridgeError


class PreflightWait(ValueError):
    pass


def execute(*, env=None, get_calendar=retrieve_approved_calendar,
            fetch_roster=fetch_nifty50_constituents.main,
            fetch_delivery_data=fetch_delivery.main, tmp_dir_factory=TemporaryDirectory,
            now=None):
    env = os.environ if env is None else env
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise PreflightWait("CLOCK_NOT_VERIFIED")

    # Deliberately independent of GitHub Actions variable publication path.
    try:
        run_id = int(env.get("PHASE4_CALENDAR_APPROVED_RUN_ID",""))
    except (ValueError, TypeError) as e:
        raise PreflightWait("PINNED_APPROVAL_MISSING") from e
    sha = env.get("PHASE4_CALENDAR_APPROVED_HEAD_SHA","")
    day = env.get("PHASE4_APPROVED_COMPLETED_SESSION_DATE","")
    token = env.get("STOCK_MASTER_CALENDAR_READ_TOKEN","")
    if not token:
        raise PreflightWait("PRIVATE_CALENDAR_READ_SECRET_MISSING")

    proof = get_calendar(token=token,approved_run_id=run_id,
                         approved_head_sha=sha, expected_date=day, now=now)
    if (proof.get("status")!="PASS" or proof.get("verified") is not True
            or proof.get("production_write") is not False
            or proof.get("expected_completed_trading_date")!=day
            or proof.get("source_run_id")!=run_id
            or proof.get("source_head_sha")!=sha
            or not isinstance(proof.get("evidence_sha256"),str)
            or len(proof["evidence_sha256"])!=64):
        raise PreflightWait("TRUSTED_CALENDAR_NOT_APPROVED")

    working=Path.cwd().resolve()
    with tmp_dir_factory(prefix="phase4s8-delivery-") as tmp:
        dest=Path(tmp).resolve()
        if dest == working or working in dest.parents:
            raise PreflightWait("TEMP_DIR_NOT_ISOLATED")
        # Keep the producer env/session proof local and limited to the
        # temp execution. Unlike a GitHub Actions var, this value originated
        # from a specific authenticated private calendar artifact.
        old=os.environ.get("EXPECTED_COMPLETED_TRADING_DATE")
        os.environ["EXPECTED_COMPLETED_TRADING_DATE"]=day
        try:
            os.chdir(dest)
            fetch_roster()
            membership=load_membership("nifty50_latest.json", now=now)
            if len(membership["symbols"])!=50 or "BSE" not in membership["symbols"] or "WIPRO" in membership["symbols"]:
                raise PreflightWait("CURRENT_BSE_50_ROSTER_UNVERIFIED")
            fetch_delivery_data()
            artifact=json.loads(Path("delivery_latest.json").read_text(encoding="utf-8"))
            verdict=validate_delivery_artifact(artifact,membership,
                                               expected_date=day)
            actual={r["symbol"] for r in artifact["rows"]}
            if (verdict["status"]!="PASS" or actual!=membership["symbols"]
                    or "BSE" not in actual or "WIPRO" in actual
                    or artifact.get("fail")!=0
                    or artifact.get("total")!=50 or artifact.get("pass")!=50):
                raise PreflightWait("DELIVERY_CURRENT_ROSTER_NOT_VERIFIED")
            digest=hashlib.sha256(Path("delivery_latest.json").read_bytes()).hexdigest()
            return {
                "status":"PASS","test":"AUTHENTICATED_BSE_DELIVERY_TEMP_ONLY",
                "approved_session":day,
                "calendar_source_run_id":run_id,
                "calendar_evidence_sha256":proof["evidence_sha256"],
                "roster_source_sha256":membership["source_sha256"],
                "delivery_temp_sha256":digest,
                "rows":50,"pass":50,"fail":0,
                "BSE_included":True,"WIPRO_excluded":True,
                "production_write":False,"published_cache_changed":False,
                "sheet_write":False,"broker_order":False,
            }
        finally:
            os.chdir(working)
            if old is None:os.environ.pop("EXPECTED_COMPLETED_TRADING_DATE",None)
            else:os.environ["EXPECTED_COMPLETED_TRADING_DATE"]=old


def main():
    try:
        result=execute()
        print("4S8_TRUSTED_PRODUCER_DRYRUN",json.dumps(result,sort_keys=True))
        return 0
    except (PreflightWait,CalendarBridgeError,ValueError,RuntimeError,
            OSError,KeyError,TypeError) as exc:
        # Do not disclose source payloads, authorization token, or redirect.
        print("4S8_TRUSTED_PRODUCER_DRYRUN",json.dumps({
            "status":"WAIT","reason":"PRODUCER_SOURCE_OR_APPROVAL_NOT_VERIFIED",
            "production_write":False,"sheet_write":False,"broker_order":False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
