# Securo financial operations

## Production

Production uses `docker-compose.prod.yml` with image digests pinned in the
file. `SECRET_KEY` must be supplied by the deployment environment; there is no
production fallback. The `migrate` service must complete before backend,
frontend or Celery starts. Ports are bound to loopback and the only public
route is the existing Tailscale Serve hostname (Funnel remains disabled).

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
before exposing the loopback frontend on `STAGING_FRONTEND_PORT`. The script
deletes attachment metadata and backing files because invoices and filenames
cannot be safely pseudonymized. The staging network is Docker-internal
(`internal: true`), provider credentials, IPCA refresh, and agents are
disabled, and the project must be removed after at most seven days. The
`!override` port declaration keeps staging on its own port instead of inheriting
production's `3220` mapping.

The optional `agents` profile also has no secret fallback. Set a dedicated
`AGENTS_MCP_JWT_SECRET` before enabling it; an empty value fails closed.

Celery refreshes the official BCB SGS 433 IPCA cache daily in production. The
task stores source and retrieval time with each observation and is disabled in
staging.

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
