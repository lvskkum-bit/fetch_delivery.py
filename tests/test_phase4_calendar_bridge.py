"""4S-3 offline cross-repository trust boundaries; zero external network."""
from __future__ import annotations
import io
import json
import zipfile
import hashlib
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import phase4_calendar_bridge as cb

RUN = 38078845264
SHA = "2420429da437de93c7f37263cbb07bbce52a3efe"
DAY = "2026-10-09"
NOW = datetime(2026,10,11,7,0,tzinfo=timezone.utc)


def evidence():
    p = {
        "status":"PASS","verified":True,
        "expected_completed_trading_date":DAY,
        "calendar_source":"OFFICIAL_NSE_EQUITY_CALENDAR",
        "evidence_ref":f"approved-calendar-evidence-{DAY}",
        "verified_at":"2026-10-11T05:00:52Z",
        "session_status":"CLOSED",
        "production_write":False,
        "source_provenance":{
            "nse_calendar":{"source_identity":"OFFICIAL_NSE_EQUITY_CALENDAR",
                            "evidence_ref":"official_nse_equity_calendar-2026-10-09-abbccdd",
                            "source_sha256":"a"*64},
            "upstox_timings":{"source_identity":"UPSTOX_MARKET_TIMINGS",
                              "evidence_ref":"upstox_market_timings-2026-10-09-effeedd",
                              "source_sha256":"b"*64}
        },
    }
    p["evidence_sha256"]=cb._json_bytes(p,"evidence_sha256")
    return p


def zip_bytes(p=None, extra=None):
    f=io.BytesIO()
    with zipfile.ZipFile(f,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr(cb.ARTIFACT_FILE,json.dumps(evidence() if p is None else p))
        if extra:
            z.writestr(extra,"extra")
    return f.getvalue()


def run():
    return {
        "id":RUN,"conclusion":"success","status":"completed","head_sha":SHA,
        "head_branch":cb.BRANCH,
        "repository":{"full_name":cb.REPO},
        "path":cb.WORKFLOW+"@refs/heads/"+cb.BRANCH,
        "created_at":"2026-10-11T04:57:00Z",
        "updated_at":"2026-10-11T05:03:00Z",
    }


def listing():
    return {"artifacts":[{
        "id":11692701343,"name":cb.ARTIFACT_NAME,"expired":False,
        "size_in_bytes":3000,"workflow_run":{"id":RUN},
    }]}


def mock(monkeypatch, *, r=None, l=None, blob=None):
    r=run() if r is None else r
    l=listing() if l is None else l
    urls=[]
    def request(url, token):
        assert token == "private-test-token"
        urls.append(url)
        if url.endswith(f"/runs/{RUN}"):
            return r
        if url.endswith(f"/runs/{RUN}/artifacts"):
            return l
        raise AssertionError("unexpected external URL")
    monkeypatch.setattr(cb,"_request_json",request)
    monkeypatch.setattr(cb,"_download_zip",lambda aid, token: zip_bytes() if blob is None else blob)
    return urls


def get(**kw):
    return cb.retrieve_approved_calendar(
        token="private-test-token",approved_run_id=RUN,
        approved_head_sha=SHA,expected_date=DAY,now=NOW,**kw)


def refuses(fn,match):
    with pytest.raises(cb.CalendarBridgeError,match=match):
        fn()


def test_valid_github_metadata_and_exact_zip_proof_authorize_read_only_date(monkeypatch):
    urls=mock(monkeypatch)
    result=get()
    assert result["status"]=="PASS"
    assert result["verified"] is True
    assert result["production_write"] is False
    assert result["expected_completed_trading_date"]==DAY
    assert result["source_run_id"]==RUN
    assert result["evidence_sha256"]==evidence()["evidence_sha256"]
    assert len(urls)==2


def test_missing_approval_sha_is_wait_no_network(monkeypatch):
    monkeypatch.setattr(cb,"_request_json",lambda *a: (_ for _ in ()).throw(AssertionError("network")))
    refuses(lambda:cb.retrieve_approved_calendar(token="",approved_run_id=RUN,approved_head_sha="",expected_date=DAY,now=NOW),"APPROVED_RUN_ID_OR_SHA_MISSING")


@pytest.mark.parametrize("key,value",[
    ("head_branch","main"),("head_sha","f"*40),("conclusion","failure"),
    ("status","in_progress"),("path",".github/workflows/delivery.yml@main"),
    ("id",1234),("repository",{"full_name":"untrusted/bot"}),
])
def test_wrong_run_identity_never_qualifies(monkeypatch,key,value):
    r=run();r[key]=value
    mock(monkeypatch,r=r)
    refuses(get,"RUN_IDENTITY_OR_OUTCOME_NOT_APPROVED")


def test_expires_old_github_run_even_with_valid_payload(monkeypatch):
    r=run();r["updated_at"]="2026-10-01T05:03:00Z"
    mock(monkeypatch,r=r)
    refuses(get,"RUN_NOT_FRESH")


def test_mismatched_approved_session_never_qualifies(monkeypatch):
    mock(monkeypatch)
    refuses(lambda: cb.retrieve_approved_calendar(
        token="private-test-token",approved_run_id=RUN,
        approved_head_sha=SHA,expected_date="2026-10-08",now=NOW
    ),"CALENDAR_SESSION_MISMATCH")


def test_tampered_calendar_digest_rejected(monkeypatch):
    p=evidence();p["expected_completed_trading_date"]="2026-10-08"
    mock(monkeypatch,blob=zip_bytes(p))
    refuses(get,"CALENDAR_SESSION_MISMATCH")
    p=evidence();p["source_provenance"]["nse_calendar"]["source_sha256"]="f"*64
    mock(monkeypatch,blob=zip_bytes(p))
    refuses(get,"CALENDAR_CHECKSUM_MISMATCH")


def test_source_identities_required_even_if_attacker_rehashes(monkeypatch):
    p=evidence();p["source_provenance"]["upstox_timings"]["source_identity"]="FAKE"
    p["evidence_sha256"]=cb._json_bytes(p,"evidence_sha256")
    mock(monkeypatch,blob=zip_bytes(p))
    refuses(get,"SOURCE_IDENTITY_INVALID")


def test_archive_extra_file_rejected(monkeypatch):
    mock(monkeypatch,blob=zip_bytes(extra="../../evil.py"))
    refuses(get,"ARCHIVE_CONTENTS_NOT_ALLOWED")


def test_zip_bomb_size_rejected_from_metadata(monkeypatch):
    l=listing();l["artifacts"][0]["size_in_bytes"]=9_999_999
    mock(monkeypatch,l=l)
    refuses(get,"ARTIFACT_METADATA_INVALID")


def test_missing_or_expired_artifact_rejected(monkeypatch):
    l=listing();l["artifacts"][0]["expired"]=True
    mock(monkeypatch,l=l)
    refuses(get,"ARTIFACT_NOT_UNIQUE_OR_EXPIRED")


def test_artifact_copy_to_other_run_rejected(monkeypatch):
    l=listing();l["artifacts"][0]["workflow_run"]["id"]=456
    mock(monkeypatch,l=l)
    refuses(get,"ARTIFACT_METADATA_INVALID")


def test_approval_timestamp_cannot_precede_run(monkeypatch):
    p=evidence();p["verified_at"]="2026-10-10T05:00:52Z"
    p["evidence_sha256"]=cb._json_bytes(p,"evidence_sha256")
    mock(monkeypatch,blob=zip_bytes(p))
    refuses(get,"RUN_AND_EVIDENCE_TIMESTAMP_MISMATCH")


def test_private_token_not_sent_to_artifact_redirect(monkeypatch):
    class First:
        status_code=302
        url=f"{cb.API_ROOT}/actions/artifacts/22/zip"
        headers={"Location":"https://pipelines.actions.githubusercontent.com/abc"}
    class Second:
        status_code=200
        url="https://pipelines.actions.githubusercontent.com/abc"
        def iter_content(self,chunk_size): yield b"ZIP"
    seen=[]
    def fake_get(url,headers,**kw):
        seen.append((url,headers))
        return First() if len(seen)==1 else Second()
    monkeypatch.setattr(cb.requests,"get",fake_get)
    assert cb._download_zip(22,"dont-leak-me")==b"ZIP"
    assert seen[0][1]["Authorization"]=="Bearer dont-leak-me"
    assert "Authorization" not in seen[1][1]


def test_redirect_to_lookalike_domain_is_never_followed(monkeypatch):
    class Bad:
        status_code=302
        url=f"{cb.API_ROOT}/actions/artifacts/22/zip"
        headers={"Location":"https://pipelines.actions.githubusercontent.com.evil.example/"}
    monkeypatch.setattr(cb.requests,"get",lambda *args,**kw:Bad())
    refuses(lambda:cb._download_zip(22,"private-test-token"),"ARTIFACT_REDIRECT_UNTRUSTED")
