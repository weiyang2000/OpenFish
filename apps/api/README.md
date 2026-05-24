# BettaFish SaaS API

This FastAPI service implements the backend boundary described in
`docs/openapi/saas-platform.yaml`.

## Local start

```bash
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The versioned API base URL is `http://localhost:8000/api/v1`. Every SaaS
request must include `X-Workspace-Id`, for example:

```bash
curl -H "X-Workspace-Id: workspace_demo" \
  http://localhost:8000/api/v1/health
```

Docker Compose enables task workers by default. Report tasks call ReportEngine,
and crawler tasks call the real MindSpider/MediaCrawler adapter. Missing LLM
keys, engine inputs, database configuration, crawler accounts, or submodules
make the task fail with a structured error instead of creating placeholder
artifacts.

## Persistence

SQLite is used for the first service-layer migration. Override paths with:

```bash
export BETTAFISH_API_DB_PATH=./data/saas_api.sqlite3
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

For local database-backed crawler work, start the Postgres service from the
repo-level `docker-compose.yml`. The SaaS service itself only needs SQLite for
task/config metadata unless a crawler or engine adapter is enabled.
