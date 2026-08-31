# Securo financial operations

## Production

Production uses `docker-compose.prod.yml` with image digests pinned in the
file. The `migrate` service must complete before backend, frontend or Celery
starts. Ports are bound to loopback and the only public route is the existing
Tailscale Serve hostname (Funnel remains disabled).

```sh
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

Rollback is the previous digest pair plus the restored encrypted PostgreSQL,
attachments and `agent_knowledge` backup. Never remove named volumes during a
rollback.

## Staging

Create a separate Compose project with `docker-compose.staging.yml`, restore a
copy of the encrypted backup, and run `backend/scripts/pseudonymize_staging.py`
before exposing the loopback frontend on `STAGING_FRONTEND_PORT`. The staging
network is Docker-internal (`internal: true`), provider credentials and agents
are disabled, and the project must be removed after at most seven days.

## Data reconciliation

The following scripts are idempotent and workspace-scoped:

- `financial_manifest.py` — non-PII baseline/post-change counts;
- `financial_migration.py` — AssetGroups, Positions, Collections and One Tower
  categories/tag;
- `audit_rules.py` — disables unsafe rules before category-only previews;
- `normalize_bills.py` — normalizes `open/closed/paid/overdue` states;
- `create_budget_draft.py` — creates advisory next-month budgets from three
  completed months.

Run them with the backend image or a development bind mount, then rerun the
manifest. No script physically deletes a financial row.
