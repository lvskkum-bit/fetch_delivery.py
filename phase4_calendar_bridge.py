"""Task 4S-3: authenticated cross-repository trusted calendar artifact handoff.

GitHub's default GITHUB_TOKEN cannot read a different private repository.
An independently provisioned fine-grained Actions:read token for the Stock
Master repository must be supplied as STOCK_MASTER_CALENDAR_READ_TOKEN.
The *run ID*, *head SHA*, and *expected date* are explicit approvals. This
module never writes production caches or calls a broker.

A hash proves payload integrity against accidental alteration; source
authenticity comes from an authenticated GitHub Actions run bound to the
approved repository, workflow, branch, commit and run ID. No local JSON,
untrusted environment date, or hand-copied digest alone authorizes delivery.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

REPO = "lvskkum-bit/nifty50-stock-master-appsscript"
API_ROOT = f"https://api.github.com/repos/{REPO}"
WORKFLOW = ".github/workflows/phase4_official_source_probe.yml"
BRANCH = "correction/phase4-remaining-ci-readiness"
ARTIFACT_NAME = "phase4-calendar-evidence"
ARTIFACT_FILE = "market_calendar_latest.json"
MAX_ARCHIVE_BYTES = 1_048_576
MAX_JSON_BYTES = 64 * 1024
MAX_AGE = timedelta(hours=72)
ISO_SHA = re.compile(r"^[a-f0-9]{40}$")
HASH = re.compile(r"^[a-f0-9]{64}$")


class CalendarBridgeError(ValueError):
    """All ambiguous or stale calendar evidence is a hard WAIT."""


def _wait(message: str) -> CalendarBridgeError:
    return CalendarBridgeError("CALENDAR_BRIDGE_WAIT:" + message)


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise _wait("INVALID_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise _wait("INVALID_TIMESTAMP") from e
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _wait("INVALID_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def _day(value: Any) -> str:
    if not isinstance(value, str):
        raise _wait("INVALID_DATE")
    try:
        d = date.fromisoformat(value)
    except ValueError as e:
        raise _wait("INVALID_DATE") from e
    if d.isoformat() != value:
        raise _wait("INVALID_DATE")
    return value


def _json_bytes(payload: dict[str, Any], key: str) -> str:
    data = {k:v for k,v in payload.items() if k != key}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_provenance(provenance: Any, date_value: str, *, now: datetime) -> None:
    if not isinstance(provenance, dict):
        raise _wait("SOURCE_PROVENANCE_MISSING")
    for section, identity in (
        ("nse_calendar", "OFFICIAL_NSE_EQUITY_CALENDAR"),
        ("upstox_timings", "UPSTOX_MARKET_TIMINGS")
    ):
        record = provenance.get(section)
        if not isinstance(record, dict) or record.get("source_identity") != identity:
            raise _wait("SOURCE_IDENTITY_INVALID")
        ref = record.get("evidence_ref")
        digest = record.get("source_sha256")
        if (not isinstance(ref, str) or date_value not in ref
                or not isinstance(digest, str) or not HASH.fullmatch(digest)):
            raise _wait("SOURCE_BINDING_INVALID")
    # On a same-day market close the Upstox market-status proof is required.
    verified = now.astimezone(timezone(timedelta(hours=5,minutes=30))).date()
    if date_value == verified.isoformat():
        status = provenance.get("upstox_market_status")
        if not isinstance(status, dict) or status.get("source_identity") != "UPSTOX_MARKET_STATUS":
            raise _wait("STATUS_SOURCE_PROOF_MISSING")


def _validate_calendar(payload: Any, expected_date: str, *, now: datetime) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _wait("INVALID_CALENDAR_PAYLOAD")
    date_value = _day(payload.get("expected_completed_trading_date"))
    if date_value != expected_date:
        raise _wait("CALENDAR_SESSION_MISMATCH")
    verified_at = _datetime(payload.get("verified_at"))
    if verified_at > now or now - verified_at > MAX_AGE:
        raise _wait("CALENDAR_EXPIRED_OR_FUTURE")
    if (payload.get("status") != "PASS" or payload.get("verified") is not True
            or payload.get("production_write") is not False
            or payload.get("session_status") != "CLOSED"
            or payload.get("calendar_source") != "OFFICIAL_NSE_EQUITY_CALENDAR"
            or payload.get("evidence_ref") != f"approved-calendar-evidence-{date_value}"):
        raise _wait("CALENDAR_NOT_APPROVED")
    checksum = payload.get("evidence_sha256")
    if not isinstance(checksum, str) or not HASH.fullmatch(checksum):
        raise _wait("BAD_CALENDAR_CHECKSUM")
    if _json_bytes(payload, "evidence_sha256") != checksum:
        raise _wait("CALENDAR_CHECKSUM_MISMATCH")
    _source_provenance(payload.get("source_provenance"), date_value, now=verified_at)
    return payload


def _request_json(url: str, token: str) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise _wait("PRIVATE_REPO_READ_TOKEN_MISSING")
    if not url.startswith(API_ROOT + "/") or "?" in url:
        raise _wait("URL_NOT_ALLOWED")
    try:
        r = requests.get(url, headers={
            "Authorization": "Bearer "+token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }, timeout=(3, 10), allow_redirects=False)
        if r.status_code != 200 or r.url != url or len(r.content) > MAX_ARCHIVE_BYTES:
            raise _wait("GITHUB_API_UNAVAILABLE")
        result = r.json()
        if not isinstance(result, dict):
            raise _wait("INVALID_GITHUB_RESPONSE")
        return result
    except (requests.RequestException, ValueError) as err:
        if isinstance(err, CalendarBridgeError):
            raise
        raise _wait("GITHUB_API_UNAVAILABLE") from err


def _download_zip(run_artifact_id: int, token: str) -> bytes:
    # GitHub's artifact download API legitimately redirects to signed
    # short-lived storage; only the GitHub API request is bearer-authenticated.
    # Never send the private GitHub token to the redirect destination.
    url = f"{API_ROOT}/actions/artifacts/{run_artifact_id}/zip"
    headers = {"Authorization":"Bearer "+token, "Accept":"application/vnd.github+json"}
    try:
        first = requests.get(url, headers=headers, timeout=(3, 10),
                             allow_redirects=False)
        if first.status_code not in (302, 307) or first.url != url:
            raise _wait("ARTIFACT_REDIRECT_MISSING")
        location = first.headers.get("Location", "")
        from urllib.parse import urlsplit
        parsed = urlsplit(location)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise _wait("ARTIFACT_REDIRECT_INVALID")
        if parsed.hostname not in (
            "results-receiver.actions.githubusercontent.com",
            "pipelines.actions.githubusercontent.com",
            "productionresultssa0.blob.core.windows.net",
            # Authenticated github.com artifact download redirected here
            # on 2026-10-11; only the observed exact Azure host is added.
            "productionresultssa19.blob.core.windows.net",
        ):
            raise _wait("ARTIFACT_REDIRECT_UNTRUSTED")
        second = requests.get(location, headers={"Accept":"application/zip"},
                              timeout=(3, 20), allow_redirects=False, stream=True)
        if second.status_code != 200 or second.url != location:
            raise _wait("ARTIFACT_FETCH_FAILED")
        content = b""
        for chunk in second.iter_content(chunk_size=65536):
            content += chunk
            if len(content) > MAX_ARCHIVE_BYTES:
                raise _wait("ARTIFACT_TOO_LARGE")
        return content
    except (requests.RequestException, ValueError) as err:
        if isinstance(err, CalendarBridgeError):
            raise
        raise _wait("ARTIFACT_FETCH_FAILED") from err


def _decode_archive(blob: bytes) -> dict[str, Any]:
    if not isinstance(blob, bytes) or len(blob) > MAX_ARCHIVE_BYTES:
        raise _wait("ARCHIVE_OVERSIZED")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            files = archive.infolist()
            if len(files) != 1 or files[0].filename != ARTIFACT_FILE:
                raise _wait("ARCHIVE_CONTENTS_NOT_ALLOWED")
            if files[0].file_size > MAX_JSON_BYTES or files[0].compress_size > MAX_ARCHIVE_BYTES:
                raise _wait("ARCHIVE_UNCOMPRESSED_SIZE")
            contents = archive.read(files[0], pwd=None)
            if len(contents) > MAX_JSON_BYTES:
                raise _wait("ARCHIVE_JSON_OVERSIZED")
            data = json.loads(contents.decode("utf-8"))
        if not isinstance(data, dict):
            raise _wait("ARCHIVE_NOT_A_CALENDAR")
        return data
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as err:
        if isinstance(err, CalendarBridgeError):
            raise
        raise _wait("ARCHIVE_INVALID") from err


def retrieve_approved_calendar(*, token: str, approved_run_id: int,
    approved_head_sha: str, expected_date: str,
    now: datetime | None=None) -> dict[str, Any]:
    """Read exactly one approved successful run and validate its artifact.

    Approving a GitHub run/commit is a distinct human or automated trust step.
    A GITHUB_TOKEN from a different private repository is insufficient.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise _wait("CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    day = _day(expected_date)
    if (type(approved_run_id) is not int or approved_run_id <= 0
            or not isinstance(approved_head_sha, str)
            or not ISO_SHA.fullmatch(approved_head_sha)):
        raise _wait("APPROVED_RUN_ID_OR_SHA_MISSING")
    run = _request_json(f"{API_ROOT}/actions/runs/{approved_run_id}", token)
    if (run.get("id") != approved_run_id
            or run.get("conclusion") != "success" or run.get("status") != "completed"
            or run.get("head_sha") != approved_head_sha
            or run.get("head_branch") != BRANCH
            or run.get("repository",{}).get("full_name") != REPO
            or not str(run.get("path","")).split("@")[0] == WORKFLOW):
        raise _wait("RUN_IDENTITY_OR_OUTCOME_NOT_APPROVED")
    started = _datetime(run.get("created_at"))
    completed = _datetime(run.get("updated_at"))
    if (started > now or completed > now or now-completed > MAX_AGE):
        raise _wait("RUN_NOT_FRESH")
    listing = _request_json(f"{API_ROOT}/actions/runs/{approved_run_id}/artifacts",token)
    matches = [a for a in listing.get("artifacts",[]) if
               isinstance(a,dict) and a.get("name")==ARTIFACT_NAME and a.get("expired") is False]
    if len(matches) != 1:
        raise _wait("ARTIFACT_NOT_UNIQUE_OR_EXPIRED")
    artifact = matches[0]
    if (artifact.get("workflow_run", {}).get("id") not in (None, approved_run_id)
            or type(artifact.get("id")) is not int
            or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES):
        raise _wait("ARTIFACT_METADATA_INVALID")
    payload = _decode_archive(_download_zip(artifact["id"],token))
    approved = _validate_calendar(payload, day, now=now)
    verification = _datetime(approved["verified_at"])
    if verification < started-timedelta(minutes=1) or verification > completed+timedelta(minutes=10):
        raise _wait("RUN_AND_EVIDENCE_TIMESTAMP_MISMATCH")
    return {
        "status":"PASS", "verified":True,
        "expected_completed_trading_date":day,
        "evidence_sha256":approved["evidence_sha256"],
        "source_provenance":approved["source_provenance"],
        "source_run_id":approved_run_id,
        "source_head_sha":approved_head_sha,
        "production_write":False,
    }
