# Reports Pilot Runbook

Managed pilot operating guide for the Korean equities research console in `~/hermes_v2`.

This runbook is optimized for fast customer onboarding, not generic platform abstraction.

## 1. Pilot Scope

Current pilot surface:

- `tradecraft-reports-api`
- `tradecraft-reports-worker`
- `web/reports-console`
- `.runtime/naver_reports.db`
- optional RAG sync and Telegram test alerts

Do not treat this runbook as a launch guide for:

- KIS auto-trading
- portfolio coach
- multi-tenant self-serve SaaS

## 2. Environment Setup

Run from repo root:

```bash
cd /Users/juhwan/hermes_v2
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
npm --prefix web/reports-console install
npm --prefix web/reports-console run build
```

Required runtime settings:

- `TRADECRAFT_NAVER_REPORTS_ENABLED=true`
- `TRADECRAFT_NAVER_REPORTS_DB_PATH=.runtime/naver_reports.db`
- `TRADECRAFT_REPORTS_API_TOKEN` or `TRADECRAFT_REPORTS_API_TOKENS`
- `TRADECRAFT_REPORTS_UI_ALLOWED_CIDRS`
- `TRADECRAFT_REPORTS_WORKER_STATE_PATH=.runtime/reports_worker_state.json`

Recommended pilot settings:

- `TRADECRAFT_REPORTS_API_TOKENS=new-token,old-token`
- `TRADECRAFT_RAG_ENABLED=true`
- `TRADECRAFT_TELEGRAM_BOT_TOKEN` and `TRADECRAFT_TELEGRAM_CHAT_ID` if test alerts should work

Preflight checks before customer access:

- `npm --prefix web/reports-console run build`
- `tradecraft-reports-stack`
- `curl http://127.0.0.1:8010/v1/health`
- confirm the UI build exists under `src/tradecraft/reports_api/web_dist/`
- confirm `.runtime/reports_worker_state.json` is being written

## 3. Access Provisioning

For the current managed-pilot phase, provision access in the simplest safe way:

1. Generate a pilot API token.
2. Add the new token first in `TRADECRAFT_REPORTS_API_TOKENS`.
3. Restrict UI access with `TRADECRAFT_REPORTS_UI_ALLOWED_CIDRS`.
4. If the pilot needs alert testing, set the pilot Telegram `chat_id` in the saved view or use the default `TRADECRAFT_TELEGRAM_CHAT_ID`.
5. Share only:
   - the pilot URL
   - the agreed access token
   - the expected source IP or VPN path

Do not open broad public access during this phase.

## 4. Rollout Checklist

### Before rollout

- [ ] Pull the latest approved code for the reports subsystem.
- [ ] Verify `.env` contains reports API token configuration.
- [ ] Verify seed URLs and crawl interval are correct.
- [ ] Verify the SQLite DB path resolves correctly.
- [ ] Build the reports console frontend.
- [ ] Run the focused reports test suite:

```bash
pytest -q tests/test_naver_reports.py tests/test_reports_microservice_api.py tests/test_reports_microservice_ui_api.py tests/test_reports_microservice_worker.py tests/test_reports_microservice_stack.py tests/test_config.py
```

### Launch

- [ ] Start `tradecraft-reports-stack`.
- [ ] Open the console and verify:
  - overview loads
  - recent reports load
  - report drill-down works
  - saved-view CRUD works
  - alert preview works
- [ ] Check `GET /v1/health` for readiness and worker state.
- [ ] Check `GET /ui-api/overview` for quality warnings before customer handoff.

### Customer handoff

- [ ] Send pilot URL and token out of band.
- [ ] Confirm the user can open the console from the expected network.
- [ ] Walk through one saved-view flow live.
- [ ] Trigger one Telegram test alert if alerting is in scope.
- [ ] Record the first support contact path and response expectation.

## 5. Smoke Test Commands

Health:

```bash
curl http://127.0.0.1:8010/v1/health
```

Authenticated status:

```bash
curl -H "Authorization: Bearer $TRADECRAFT_REPORTS_API_TOKEN" \
  http://127.0.0.1:8010/v1/reports/status
```

UI overview:

```bash
curl http://127.0.0.1:8010/ui-api/overview
```

Recent reports:

```bash
curl "http://127.0.0.1:8010/ui-api/reports/recent?limit=5"
```

## 6. Support Workflow

Daily operating loop:

1. Check `GET /ui-api/overview`.
2. Review worker status and last-success timestamp.
3. Review quality issues for stale ingest, HTML-tainted names, unknown categories, and symbol-directory drift.
4. Sample one recent report detail screen.
5. Review one saved view and one alert preview.

When the pilot user reports bad data:

1. Capture the report id or exact title.
2. Check the detail view and the raw `detail_url`.
3. Inspect `GET /v1/reports/status` and `GET /v1/health`.
4. If symbol or company metadata is wrong, run a symbol-directory refresh and re-check quality warnings.
5. If alerts are failing, run a Telegram test from the saved view and confirm `chat_id` plus bot token configuration.

## 7. Rollback And Recovery

### Bad deploy

- Stop the current reports stack.
- Restore the last known-good code or worktree.
- Rebuild the frontend:

```bash
npm --prefix web/reports-console run build
```

- Restart `tradecraft-reports-stack`.
- Re-run the smoke tests above.

### Token problem

- Add a replacement token first in `TRADECRAFT_REPORTS_API_TOKENS`.
- Restart the reports stack.
- Move clients to the new token.
- Remove the old token only after confirmation.

### Bad or stale data

- Trigger a crawl once.
- Trigger a symbol-directory refresh.
- Re-check the quality warnings in `/ui-api/overview`.
- If the DB is corrupt or unusable, restore `.runtime/naver_reports.db` from backup or re-seed from crawl sources before reopening access.

## 8. Pilot Exit Criteria

The deployment is in a pilot-safe state when:

- `tradecraft-reports-stack` starts without preflight errors
- `GET /v1/health` returns readiness without errors
- the worker state is present and not stale
- the console loads recent reports and drill-down data
- saved views and alert preview/test work
- any remaining quality warnings are understood and acceptable for the pilot
