# Task 4S-8 — Authenticated Calendar-bound BSE Delivery Migration: READ-ONLY PROOF

Date: 2026-10-11 (India). **STAGED CODE + VERIFIED TEMP-DIR REAL NSE REPLAY: PASS.**
**PUBLISHED PRODUCER CACHE / `main`: UNCHANGED. NO STOCK SHEET WRITE.**

## Important live NSE archive observation

An initial dynamic producer replay called `fetch_day(2026-10-11)` on a
**Sunday**. NSE's historical delivery archive returned HTTP 200 with a
CSV-like payload for that non-trading date. HTTP 200 alone is not proof of
a completed exchange session. The original `find_latest_two_sessions()`
permissively accepts valid-looking CSV payloads. Therefore THIS WORKFLOW
CANNOT BE APPROVED FOR PRODUCTION PUBLICATION based solely on that
date-scanning logic.

The isolated 4S8 dry-run now avoids any calendar inference:
- Authentic private Stock Master approved run **38078845264**, exact
  commit `2420429da437de93c7f37263cbb07bbce52a3efe`,
  successful `phase4_official_source_probe.yml` evidence for the
  **2026-10-09** completed trading session, source-bound SHA-256 digest
  `acbeb024cfe68fbad3c7a5280c629f598a634add367e6a20997bdbad693603f0`.
- Explicit **previous** historical trading session `2026-10-08`,
  which was confirmed in previous real NSE replay. This prior date is
  approved for one historical **read-only test**; production must obtain
  source-authenticated preceding session proof too.
- Only request precisely those two official NSE archive filenames.
  Never query Sunday/weekend as a latest cash session.
- Parse NSE archive `DATE1` from the actual rows for all 50 current EQ
  stocks and require every row's date to exactly match the requested
  completed session. Reject duplicate or missing EQ rows.
- Fetch official CURRENT 50 NIFTY50 constituents in the same temp directory,
  bind membership checksum, enforce BSE IN/WIPRO OUT, calculate ratios and
  validate no failures. No use of a frozen 50-stock hardcoded roster.
- After validation, report only source/checksum/coverage info, never a
  credential or GitHub artifact signed download URL.
- All writes confined to an OS `TemporaryDirectory` outside the checkout;
  the directory is deleted after this job.
- GitHub Actions workflow uses `contents:read`,
  `actions/checkout@v4` with `persist-credentials:false`, and contains
  ZERO `git commit`, `git push`, Google Sheet or broker commands.

## Verified GitHub run

https://github.com/lvskkum-bit/fetch_delivery.py/actions/runs/38138129787

- **54/54 offline tests PASS** (4S-2 current roster, 4S-3/4 trusted
  calendar and redirect, 4S-8 exact-session temp isolation).
- Real private-calendar authentication, real NIFTY50 membership GET and
  real NSE `sec_bhavdata_full_09102026.csv`/
  `sec_bhavdata_full_08102026.csv` GETs: **SUCCESS**.
- Explicit `DATE1` all 50 records validation PASS.
- `AUTHENTICATED_BSE_DELIVERY_TEMP_ONLY`:
  `status=PASS`, approved_session `2026-10-09`, rows `50`,
  pass `50`, fail `0`, BSE_included `true`,
  WIPRO_excluded `true`, production_write `false`,
  published_cache_changed `false`, sheet_write `false`,
  broker_order `false`.
- Temporary delivery output SHA-256
  `d167ec9550677e3a2b28e78370f76f7598d9ce2f5adaddcf34957f5ce67c5274`
  and official roster canonical SHA256
  `13c0091e3708200291799d861ecbcc5b6c1d7a2834ec52736e9952face7c7b7c`.
- Current published `main/delivery_latest.json` is still old WIPRO
  and lacks BSE. No publication was executed.

## Additional code correction awaiting production review

In this isolated producer branch, `.github/workflows/delivery.yml` final
pre-publication date validation was corrected to use
`steps.calendar.outputs.verified_session_date`, **NOT** a bare repository
variable. **However**, the existing producer live generator's permissive
`find_latest_two_sessions()` is still NOT changed on `main`. A future
controlled publication requires strict authenticated calendar-derived
CURRENT+PREVIOUS session dates and strict DATE1 verification to be
integrated into the **actual publishing path**, not just this dry-run.

## Status and next approval boundary

- User has already configured private cross-repo fine-grained GitHub
  Actions:Read secret correctly; authenticated read succeeds.
- This run proves the protected historical 9 Oct dataset can produce a
  complete BSE-ready delivery artifact in memory/temporary files.
- It DOES NOT prove the next market session's data is fresh, Apps Script
  version/trigger parity, sector-source finalization, multi-sheet
  transactional rollback or live trading readiness.
- **Do not merge this branch to producer `main`, trigger production
  publication, or change the Live Sheet** until a separate reviewed,
  fail-closed current/previous-session production publisher is approved.

**Release: HOLD.**
