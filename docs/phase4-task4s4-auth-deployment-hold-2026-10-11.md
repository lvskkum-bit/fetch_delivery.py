# Task 4S-4 — Private Calendar Auth Smoke & Apps Script Deployment Gate

Checkpoint: 2026-10-11, India Standard Time.

## Exact read-only smoke executed

- Producer repo: `lvskkum-bit/fetch_delivery.py`.
- Isolated head: `correction/phase4-task4s4-auth-readonly-smoke`.
- Workflow `.github/workflows/phase4s4_auth_readonly_smoke.yml`
  has `contents: read`, no cache publishing job, no broker/Sheet APIs,
  no production push and no access token logging.
- GitHub Actions run [#38121074312](https://github.com/lvskkum-bit/fetch_delivery.py/actions/runs/38121074312):
  **FAILED AS EXPECTED / WAIT**:
  `STOCK_MASTER_CALENDAR_READ_TOKEN_MISSING`.
  This explicitly establishes **the producer workflow cannot yet consume
  the desired repository secret**. It is not evidence of NSE or Upstox outage.
- Smoke pins the known completed, successful Stock Master source run:
  `38078845264`, attempt 2, head SHA
  `2420429da437de93c7f37263cbb07bbce52a3efe`,
  finished trading date `2026-10-09`, branch
  `correction/phase4-remaining-ci-readiness`; expected artifact
  `phase4-calendar-evidence` ID `11692701343`. Source run
  `SUCCESS`. Artifact metadata `expired=false`,
  `expires_at=2026-10-14T05:00:53Z`.
  **This pinned run is for this read-only smoke only**, not automated
  approval for any future session or producer publication.

## Exact user action needed (never disclose token in chat)

1. From their personal GitHub account settings create a **fine-grained
   Personal Access Token** with Resource owner `lvskkum-bit`,
   **Only select repositories** -> `nifty50-stock-master-appsscript`
   ONLY, **Repository permissions -> Actions: Read-only**;
   Metadata Read is default. Choose short expiration (e.g., 7/30 days).
2. Under **the OTHER repository** `lvskkum-bit/fetch_delivery.py`
   -> Settings -> Secrets and variables -> Actions -> Repository secrets
   -> New repository secret, store its value with exact name
   `STOCK_MASTER_CALENDAR_READ_TOKEN`.
3. Report only the confirmation `Secret Added` with no token value or
   token screenshot. The assistant can retry the failed GitHub Actions run
   via authorized GitHub App rerun on the existing read-only workflow.
4. Successful replay **must** prove exact repository/run/branch/commit,
   verified NSE completed session date, source-bound calendar checksum,
   and artifact archive. Failure or stale artifact -> WAIT; do not attempt
   a production publish.

## Apps Script source-only deployment audit

Audit performed against Stock Master GitHub `main` SHA
`ab00593b06a69b68584337a42671add4f6362eeb`. The repo has
`.clasp.json` with `scriptId`, `appsscript.json`
`timeZone=Asia/Kolkata` and V8 runtime. These establish GitHub backup
configuration only, NOT the identity/version of the live deployed script.

- `MASTER_ATOMIC_SKELETON..js` `setupStockSelectionLiveRefresh615PM()`
  creates/changes triggers and immediately runs a **production-mutating**
  refresh. Do not invoke as read-only verification.
- `runStockSelectionDailyLiveRefresh()` commits Master A:O, then
  separate Sector Strength, Stock Strength delivery, Delivery Breakout
  and dashboard writes. The end-to-end multi-tab sequence does not have
  atomic transaction / verified restoration on failed intermediate writes.
- `Code.js:updateStockStrengthDelivery()` currently retrieves delivery
  cache and writes E:H without a latest-completed-session and exact
  constituent-set prewrite validator.
- GitHub `Phase4StockRefreshPreflight.js` and shadow rollback contract
  are NOT active in deployed Apps Script. Mock CI is not production proof.
- Real Apps Script execution logs, installed trigger list, code version
  parity, Upstox stored Script Properties and a disposable-copy rollback
  proof are **NOT VERIFIED**. The available GitHub / Sheets connectors
  do NOT operate the Apps Script project editor/deployments/triggers.
- Live Google Sheet remains stale: Dashboard `2026-09-30`,
  Structure `2026-09-13`, Master/Stock Strength/Delivery Breakout
  have 50 unique symbols but WIPRO (current official roster has BSE).
  Isolated observation audit tab `PHASE4_FRESHNESS_AUDIT`
  rows 38–40 records Task4S4 = WAIT; trade cells untouched.

## Gate status

**Task 4S-4: IN PROGRESS / ACCESS BLOCKED.**

- Read-only smoke code and execution path staged: **YES**.
- Crossrepo credential actually available: **NO**.
- Authenticated private calendar handoff observed: **NO**.
- Production Apps Script runtime/deployment audited: **SOURCE-ONLY**.
- Producer main published BSE migration: **NO**.
- Final Phase 4 lock: **HOLD**.

Do not merge any producer or Stock Master draft PR, copy account
tokens into documentation, or update trading sheets during this hold.


---

## Follow-up 2026-10-11 — Authenticated read-only bridge PASS (supersedes Secret missing)

After the user created and saved the correctly scoped fine-grained PAT in the
producer repo, the existing GitHub Actions smoke **attempt #3**
[run 38121074312](https://github.com/lvskkum-bit/fetch_delivery.py/actions/runs/38121074312)
progressed past secret checking and authenticated GitHub API reads. It failed
`ARTIFACT_REDIRECT_UNTRUSTED`, because the GitHub artifact download returned
a newer Azure storage hostname than the strict initial allowlist.

A dedicated token-safe hostname-only diagnostic
[run 38123830875](https://github.com/lvskkum-bit/fetch_delivery.py/actions/runs/38123830875)
identified `productionresultssa19.blob.core.windows.net`, HTTPS.
Neither the redirect's signed URL/query nor bearer token were printed.

On the **isolated correction branch only**, `phase4_calendar_bridge.py` was
updated to trust that **exact observed hostname** along with the existing
small set, while continuing to deny suffix lookalikes and to send NO
Authorization header on the redirected request. Two new offline tests cover
the observed hostname and its lookalike attack.

### Verified result
- [Actual authenticated smoke SUCCESS, run 38123865734](https://github.com/lvskkum-bit/fetch_delivery.py/actions/runs/38123865734)
  — `{"completed_session":"2026-10-09","production_write":false,
  "status":"PASS","test":"AUTHENTICATED_PRIVATE_CALENDAR_READ"}`.
- [Offline regression SUCCESS, run 38123909749](https://github.com/lvskkum-bit/fetch_delivery.py/actions/runs/38123909749)
  — **41/41 PASS**, including forged-hostname rejection.
- Secret is now **present and usable at GitHub runtime**. No further token
  creation is needed for this specific smoke.
- The pin is **historical 2026-10-09**; the upstream calendar artifact expires
  2026-10-14T05:00:53Z. Do not treat it as approval for future sessions.
- No producer `main` code change, cache publish, live Sheet trading update,
  Apps Script deployment, or broker order.

### Revised 4S-4 verdict

**CROSS-REPO AUTHENTICATION: PASS (read-only)**
**DEPLOYED APPS SCRIPT RUNTIME / TRIGGERS / MULTI-TAB PARITY: NOT VERIFIED**
**PHASE 4 FINAL LOCK: HOLD**.

Next controlled step: independent Apps Script project editor
code/version/trigger execution inspection and source-before-write gate,
tested in disposable Sheet copy, then verified latest-session provider
publication. Never execute mutating setup functions just to inspect status.
