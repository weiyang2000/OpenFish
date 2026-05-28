# BettaFish SaaS API

This FastAPI service implements the backend boundary described in
`docs/openapi/saas-platform.yaml`.

## Local start

```bash
uvicorn apps.api.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

The versioned API base URL is `http://localhost:8000/api/v1`. Until user
accounts are introduced, the user-level workspace defaults to `workspace_demo`.
Requests without `X-Workspace-Id` use that default; you can also pass it
explicitly:

```bash
curl -H "X-Workspace-Id: workspace_demo" \
  http://localhost:8000/api/v1/health
```

Report task artifacts are written under
`data/saas_api_artifacts/workspaces/workspace_demo/{task_id}/`: `workspace_demo`
is the user-level workspace, and `{task_id}` is the task-level workspace.

Docker Compose enables task workers by default. Report tasks call ReportEngine,
and crawler tasks call the real MindSpider/MediaCrawler adapter. Missing LLM
keys, engine inputs, database configuration, crawler accounts, or submodules
make the task fail with a structured error instead of creating placeholder
artifacts.

Report task orchestration accepts `sourceScope.orchestration.insightMode` with
`fast`, `normal`, or `deep`. The field defaults to `normal` and is only applied
when the Insight Engine is selected for the task or rerun.

## Persistence

SaaS metadata, crawler persistence, crawler-data reads, and Insight Engine
database queries all use the same repo-level database configuration. Set either
`DATABASE_URL` or `DB_DIALECT`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`,
and `DB_NAME` in `.env`.

Report export artifacts remain filesystem-based:

```bash
export BETTAFISH_API_ARTIFACT_DIR=./data/saas_api_artifacts
```

The migration plan is mirrored in `apps/api/migrations/001_init.sql` and creates
the required SaaS tables:

- `report_tasks`
- `crawler_tasks`
- `crawler_platform_configs`
- `crawler_identity_rules`
- `crawler_strategies`
- `app_configs`
- `task_events`
- `search_runs`

## Frontend and database

The BET-3 frontend calls this service through `NEXT_PUBLIC_API_BASE_URL`, for
example `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`. The repo-level
`python app.py` launcher now starts the same FastAPI service.

For local database-backed work, start the Postgres service from the repo-level
`docker-compose.yml`. The crawler-specific `BETTAFISH_CRAWLER_DB_URL` override
is no longer used. MediaCrawler/MindSpider tables are initialized before real
crawler runs and before Insight participates in report orchestration. Crawler
records are read from the configured PostgreSQL database by default; SQLite is
only used when `BETTAFISH_CRAWLER_SQLITE_PATH` is set explicitly for tests or
legacy diagnostics.
