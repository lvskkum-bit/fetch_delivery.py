"""Task 4S-8: offline tests of authenticated dry-run isolation and BSE-only roster."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path

import pytest
from delivery_source_contract import load_membership
from phase4s8_calendar_bound_delivery_dryrun import execute, PreflightWait

DAY="2026-10-09"
RUN=38078845264
SHA="2420429da437de93c7f37263cbb07bbce52a3efe"
TIME=datetime(2026,10,11,11,45,tzinfo=timezone.utc)
SYMS=sorted(["BSE"]+[f"S{i:02d}" for i in range(49)])
ENV={"PHASE4_CALENDAR_APPROVED_RUN_ID":str(RUN),
     "PHASE4_CALENDAR_APPROVED_HEAD_SHA":SHA,
     "PHASE4_APPROVED_COMPLETED_SESSION_DATE":DAY,
     "PHASE4_APPROVED_PREVIOUS_SESSION_DATE":"2026-10-08",
     "STOCK_MASTER_CALENDAR_READ_TOKEN":"mock-test-token-not-real"}


def fake_calendar(**kw):
    assert kw["approved_run_id"]==RUN
    assert kw["approved_head_sha"]==SHA
    assert kw["expected_date"]==DAY
    assert kw["token"]=="mock-test-token-not-real"
    return {"status":"PASS","verified":True,"production_write":False,
            "expected_completed_trading_date":DAY,
            "source_run_id":RUN,"source_head_sha":SHA,"evidence_sha256":"a"*64}


def fake_roster():
    data={
        "source":"NSE Indices — NIFTY 50 Index Constituent",
        "source_url":"https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
        "fetched_at_utc":"2026-10-11T11:44:00+00:00","status":"PASS",
        "count":50,"symbols":SYMS,
        "constituents":[{
            "symbol":s,"company_name":s+" Limited","industry":"Financial Services",
            "series":"EQ","isin":"INE"+str(i).zfill(7)
        } for i,s in enumerate(SYMS)]
    }
    Path("nifty50_latest.json").write_text(json.dumps(data),encoding="utf-8")


def fake_delivery():
    proof=load_membership(now=TIME)
    rows=[{"symbol":s,"previousDate":"2026-10-08","currentDate":DAY,
           "previousDeliveryQty":100,"currentDeliveryQty":150,
           "deliveryRatio":1.5,"currentDeliveryPercent":55.0,
           "status":"PASS"} for s in SYMS]
    artifact={"constituent_sha256":proof["source_sha256"],
            "constituent_fetched_at_utc":proof["fetched_at_utc"],
            "generatedAt":"2026-10-11T17:15:00+05:30",
            "previousTradingDate":"2026-10-08","currentTradingDate":DAY,
            "total":50,"pass":50,"fail":0,"rows":rows}
    Path("delivery_latest.json").write_text(json.dumps(artifact),encoding="utf-8")


def get(**kwargs):
    return execute(env=ENV,get_calendar=fake_calendar,fetch_roster=fake_roster,
                   fetch_delivery_data=fake_delivery,now=TIME,**kwargs)


def test_exact_bse_dryrun_in_ephemeral_cwd_preserves_old_repository_bytes(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    old=tmp_path/"delivery_latest.json"
    old.write_bytes(b'{"old_production_WIPRO":true}')
    new=get()
    assert new["status"]=="PASS"
    assert new["BSE_included"] and new["WIPRO_excluded"]
    assert new["approved_session"]==DAY
    assert new["published_cache_changed"] is False
    assert new["production_write"] is False and new["sheet_write"] is False
    assert old.read_bytes()==b'{"old_production_WIPRO":true}'
    assert list(tmp_path.iterdir())==[old]


def test_secret_missing_never_calls_github_or_http(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    env={k:v for k,v in ENV.items() if k!="STOCK_MASTER_CALENDAR_READ_TOKEN"}
    with pytest.raises(PreflightWait,match="PRIVATE_CALENDAR_READ_SECRET_MISSING"):
        execute(env=env,get_calendar=lambda **k:(_ for _ in ()).throw(AssertionError("No calendar read")),
            fetch_roster=lambda:(_ for _ in ()).throw(AssertionError("No NSE read")),now=TIME)


def test_wrong_calendar_completed_date_wait_before_fetch(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    def wrong(**kw):
        p=fake_calendar(**kw);p["expected_completed_trading_date"]="2026-10-08";return p
    with pytest.raises(PreflightWait,match="TRUSTED_CALENDAR_NOT_APPROVED"):
        execute(env=ENV,get_calendar=wrong,fetch_roster=lambda:(_ for _ in ()).throw(AssertionError("No NSE read")),now=TIME)


def test_stale_old_universe_fails_without_touching_published_cache(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    old=tmp_path/"delivery_latest.json";old.write_bytes(b'{"published":"unchanged"}')
    def wrong_roster():
        fake_roster()
        p=Path("nifty50_latest.json")
        o=json.loads(p.read_text())
        o["symbols"][0]="WIPRO"
        p.write_text(json.dumps(o))
    with pytest.raises((ValueError,PreflightWait),match="IDENTITY|ROSTER|UNIVERSE|SYMBOL_SET"):
        execute(env=ENV,get_calendar=fake_calendar,fetch_roster=wrong_roster,
                fetch_delivery_data=lambda:(_ for _ in ()).throw(AssertionError("No delivery read")),now=TIME)
    assert old.read_bytes()==b'{"published":"unchanged"}'


def test_partial_delivery_fails_before_publish(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    old=tmp_path/"delivery_latest.json";old.write_bytes(b'{"published":"untouched"}')
    def partial():
        fake_delivery()
        p=Path("delivery_latest.json")
        artifact=json.loads(p.read_text());artifact["rows"].pop()
        artifact["total"]=49;artifact["pass"]=49
        p.write_text(json.dumps(artifact))
    with pytest.raises(ValueError,match="COVERAGE|TOTAL|ROWS"):
        execute(env=ENV,get_calendar=fake_calendar,fetch_roster=fake_roster,
                fetch_delivery_data=partial,now=TIME)
    assert old.read_bytes()==b'{"published":"untouched"}'


def test_wrong_source_date_rejected_after_calendar(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    def wrong_date():
        fake_delivery()
        p=Path("delivery_latest.json")
        obj=json.loads(p.read_text());obj["currentTradingDate"]="2026-10-08"
        p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match="DATE"):
        execute(env=ENV,get_calendar=fake_calendar,fetch_roster=fake_roster,
                fetch_delivery_data=wrong_date,now=TIME)


def test_no_cwd_leak_or_expected_date_leak_on_fetch_failure(tmp_path,monkeypatch):
    import os
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXPECTED_COMPLETED_TRADING_DATE","SENTINEL_PRIOR_DATE")
    def fail(): raise RuntimeError("NSE unavailable")
    with pytest.raises(RuntimeError,match="NSE unavailable"):
        execute(env=ENV,get_calendar=fake_calendar,fetch_roster=fail,now=TIME)
    assert Path.cwd()==tmp_path
    assert os.environ["EXPECTED_COMPLETED_TRADING_DATE"]=="SENTINEL_PRIOR_DATE"


def test_wrong_claimed_run_sha_rejected(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    def tampered(**kw):
        p=fake_calendar(**kw);p["source_head_sha"]="f"*40;return p
    with pytest.raises(PreflightWait,match="TRUSTED_CALENDAR_NOT_APPROVED"):
        execute(env=ENV,get_calendar=tampered,fetch_roster=lambda:None,now=TIME)


def test_real_csv_internal_date_must_equal_file_date():
    import csv, io
    from phase4s8_calendar_bound_delivery_dryrun import _exact_equity_archive_date
    stream=io.StringIO()
    writer=csv.DictWriter(stream,fieldnames=["SYMBOL","SERIES","DATE1","DELIV_QTY"])
    writer.writeheader()
    for symbol in SYMS:
        writer.writerow({"SYMBOL":symbol,"SERIES":"EQ",
                        "DATE1":"09-Oct-2026","DELIV_QTY":"100"})
    body=stream.getvalue()
    _exact_equity_archive_date(body,DAY,set(SYMS))
    with pytest.raises(PreflightWait,match="ARCHIVE_INTERNAL_SESSION_DATE_MISMATCH"):
        _exact_equity_archive_date(body,"2026-10-11",set(SYMS))


def test_no_DATE1_header_does_not_qualify():
    from phase4s8_calendar_bound_delivery_dryrun import _exact_equity_archive_date
    body="SYMBOL,SERIES,DELIV_QTY\\nBSE,EQ,200\\n"
    with pytest.raises(PreflightWait,match="ARCHIVE_DATE_OR_SYMBOL_HEADER_MISSING"):
        _exact_equity_archive_date(body,DAY,set(SYMS))


def test_duplicate_eq_stock_or_partial_50_csv_is_rejected():
    from phase4s8_calendar_bound_delivery_dryrun import _exact_equity_archive_date
    body="SYMBOL,SERIES,DATE1\\n"+ "".join(f"{s},EQ,09-Oct-2026\\n" for s in SYMS)
    with pytest.raises(PreflightWait,match="DUPLICATE_EXACT_CASH_SYMBOL"):
        _exact_equity_archive_date(body+"BSE,EQ,09-Oct-2026\\n",DAY,set(SYMS))
    with pytest.raises(PreflightWait,match="ARCHIVE_EXACT_50_CASH_ROWS_UNVERIFIED"):
        _exact_equity_archive_date(body.splitlines(True)[0]+ "".join(body.splitlines(True)[1:-1]),DAY,set(SYMS))


def test_missing_approved_previous_date_blocks_before_any_http(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    env=dict(ENV)
    env.pop("PHASE4_APPROVED_PREVIOUS_SESSION_DATE")
    with pytest.raises(PreflightWait,match="PREVIOUS_APPROVED_SESSION_REQUIRED"):
        execute(env=env,get_calendar=lambda **k:(_ for _ in ()).throw(AssertionError("calendar should not run")),
                fetch_roster=lambda:(_ for _ in ()).throw(AssertionError("no NSE")),now=TIME)


def test_weekend_requested_previous_session_never_fetches(monkeypatch):
    from phase4s8_calendar_bound_delivery_dryrun import _exact_trading_sessions
    import fetch_delivery as fd
    monkeypatch.setattr(fd,"fetch_day",lambda d:(_ for _ in ()).throw(AssertionError("no weekend fetch")))
    with pytest.raises(PreflightWait,match="APPROVED_SESSION_SEQUENCE_INVALID"):
        _exact_trading_sessions(DAY,"2026-10-11",set(SYMS))
