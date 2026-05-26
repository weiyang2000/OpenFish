"""Task lifecycle services for reports, crawlers, and search runs."""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from loguru import logger

from apps.api.schemas import (
    ApiError,
    CreateCrawlerTaskRequest,
    CreateReportTaskRequest,
    CrawlerStrategyInput,
    REPORT_FORMATS,
    UserRef,
)
from apps.api.services.common import new_id, slugify_filename, utc_now
from apps.api.services.report_templates import AUTO_REPORT_TEMPLATE_ID, read_report_template
from apps.api.storage import Store, dumps, loads


TERMINAL_REPORT_STATUSES = {"succeeded", "failed", "cancelled"}
TERMINAL_CRAWLER_STATUSES = {"succeeded", "failed", "stopped", "cancelled"}
CRAWLER_ADAPTER_ENV = "BETTAFISH_API_CRAWLER_ADAPTER"
MAX_CRAWLER_LOG_LINE_LENGTH = 4000
ENGINE_REPORT_DIRS = {
    "insight": Path("engine_reports/insight"),
    "media": Path("engine_reports/media"),
    "query": Path("engine_reports/query"),
}
REPORT_ORCHESTRATION_ENGINES = ("query", "media", "insight")
ENGINE_DISPLAY_NAMES = {
    "query": "Query Engine",
    "media": "Media Engine",
    "insight": "Insight Engine",
    "forum": "Forum Engine",
    "report": "Report Engine",
}


class TaskService:
    def __init__(
        self,
        store: Store,
        artifact_dir: Path,
        run_workers: bool = False,
        repo_root: Path | None = None,
    ):
        self.store = store
        self.artifact_dir = artifact_dir
        self.run_workers = run_workers
        self.repo_root = Path(repo_root or Path.cwd())
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._repair_interrupted_crawler_tasks()
        self._repair_inconsistent_crawler_task_statuses()

    def create_search_run(
        self,
        workspace_id: str,
        query: str,
        engines: list[str],
        owner: UserRef | None,
    ) -> dict[str, Any]:
        run_id = new_id("search")
        created_at = utc_now()
        self.store.execute(
            """
            INSERT INTO search_runs (
                id, workspace_id, query, status, engines_json, owner_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                workspace_id,
                query,
                "queued",
                dumps(engines),
                self._user_json(owner),
                created_at,
            ),
        )
        return {
            "id": run_id,
            "workspaceId": workspace_id,
            "query": query,
            "status": "queued",
            "engines": engines,
            "createdAt": created_at,
            **self._optional_user("owner", owner),
        }

    def create_report_task(
        self,
        workspace_id: str,
        payload: CreateReportTaskRequest,
    ) -> dict[str, Any]:
        task_id = new_id("report")
        now = utc_now()
        formats = payload.outputFormats or ["html"]
        template_id = self._normalize_report_template_id(payload.templateId)
        artifacts = [
            {
                "format": item,
                "ready": False,
                "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/{item}?workspaceId={workspace_id}",
            }
            for item in formats
        ]
        self.store.execute(
            """
            INSERT INTO report_tasks (
                id, workspace_id, tenant_id, topic, status, progress, stage, template_id,
                source_scope_json, output_formats_json, artifacts_json,
                owner_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                workspace_id,
                None,
                payload.topic,
                "queued",
                0,
                "queued",
                template_id,
                dumps(self._report_source_scope(payload)),
                dumps(formats),
                dumps(artifacts),
                self._user_json(payload.owner),
                now,
                now,
            ),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "status", {"task": task})
        if self.run_workers:
            threading.Thread(
                target=self._run_report_worker,
                args=(workspace_id, task_id),
                daemon=True,
            ).start()
        return task

    def rerun_report_task(
        self,
        workspace_id: str,
        task_id: str,
        engines: list[str],
    ) -> dict[str, Any]:
        task = self.get_report_task(workspace_id, task_id)
        if task["status"] in {"queued", "pending", "running"}:
            raise ApiError(
                "CONFLICT",
                "Only non-running report tasks can be rerun",
                status_code=409,
            )
        engine_ids = [engine for engine in REPORT_ORCHESTRATION_ENGINES if engine in set(engines)]
        if not engine_ids:
            raise ApiError("VALIDATION_ERROR", "At least one report engine is required", status_code=400)

        source_scope = task.get("sourceScope", {})
        source_scope["orchestration"] = {
            "enabled": True,
            "engines": engine_ids,
            "rerun": True,
        }
        output_formats = [item["format"] for item in task.get("artifacts", [])] or ["html"]
        artifacts = self._pending_report_artifacts(task_id, task["workspaceId"], output_formats)
        now = utc_now()
        self.store.execute(
            """
            UPDATE report_tasks
            SET status = 'queued', progress = 0, stage = 'queued',
                source_scope_json = ?, output_formats_json = ?, artifacts_json = ?,
                error_json = NULL, updated_at = ?
            WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)
            """,
            (
                dumps(source_scope),
                dumps(output_formats),
                dumps(artifacts),
                now,
                task_id,
                workspace_id,
                workspace_id,
            ),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "status", {"task": task})
        if self.run_workers:
            threading.Thread(
                target=self._run_report_worker,
                args=(workspace_id, task_id),
                daemon=True,
            ).start()
        return task

    def list_report_tasks(
        self,
        workspace_id: str,
        status: str | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [workspace_id, workspace_id]
        where = "(tenant_id = ? OR workspace_id = ?)"
        if status:
            where += " AND status = ?"
            params.append(status)
        params.append(page_size)
        rows = self.store.query_all(
            f"""
            SELECT *
            FROM report_tasks
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        )
        return [self._report_row(row) for row in rows]

    def get_report_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM report_tasks WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)",
            (task_id, workspace_id, workspace_id),
        )
        if not row:
            raise ApiError("NOT_FOUND", "Report task not found", status_code=404)
        return self._report_row(row)

    def cancel_report_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_report_task(workspace_id, task_id)
        if task["status"] in TERMINAL_REPORT_STATUSES:
            raise ApiError(
                "TASK_NOT_CANCELLABLE",
                "Report task is already terminal",
                status_code=409,
            )
        now = utc_now()
        error = {
            "success": False,
            "error": {
                "code": "TASK_NOT_CANCELLABLE",
                "message": "Task cancelled by user",
            },
        }
        self.store.execute(
            """
            UPDATE report_tasks
            SET status = 'cancelled', progress = progress, stage = 'failed',
                error_json = ?, updated_at = ?
            WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)
            """,
            (dumps(error), now, task_id, workspace_id, workspace_id),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "cancelled", {"task": task})
        return task

    def delete_report_task(self, workspace_id: str, task_id: str) -> None:
        task = self.get_report_task(workspace_id, task_id)
        if task["status"] in {"pending", "running"}:
            raise ApiError(
                "CONFLICT",
                "Report task is still running. Cancel it before deleting.",
                status_code=409,
            )
        self._delete_report_artifacts(task_id, task["workspaceId"])
        self.store.execute(
            "DELETE FROM task_events WHERE task_id = ? AND task_type = 'report'",
            (task_id,),
        )
        self.store.execute(
            "DELETE FROM report_tasks WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)",
            (task_id, workspace_id, workspace_id),
        )

    def get_report_result(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_report_task(workspace_id, task_id)
        if task["status"] != "succeeded":
            raise ApiError(
                "EXPORT_UNAVAILABLE",
                "Report result is not ready",
                status_code=409,
                details={"status": task["status"]},
            )
        html_path = self.artifact_path(task_id, "html", task["workspaceId"])
        html_content = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
        return {
            "success": True,
            "taskId": task_id,
            "htmlPreviewUrl": f"/api/v1/report-tasks/{task_id}/exports/html?workspaceId={task['workspaceId']}",
            "htmlContent": html_content,
            "artifacts": task.get("artifacts", []),
        }

    def artifact_path(self, task_id: str, report_format: str, workspace_id: str | None = None) -> Path:
        if report_format not in REPORT_FORMATS:
            raise ApiError("VALIDATION_ERROR", "Unsupported report format", status_code=400)
        suffix = "json" if report_format == "json" else report_format
        if workspace_id:
            return self._report_task_workspace(workspace_id, task_id) / "report" / "exports" / f"{task_id}.{suffix}"
        return self.artifact_dir / f"{task_id}.{suffix}"

    @staticmethod
    def _pending_report_artifacts(task_id: str, workspace_id: str, formats: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "format": item,
                "ready": False,
                "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/{item}?workspaceId={workspace_id}",
            }
            for item in formats
        ]

    def _delete_report_artifacts(self, task_id: str, workspace_id: str | None = None) -> None:
        for report_format in REPORT_FORMATS:
            path = self.artifact_path(task_id, report_format)
            if path.exists() and path.is_file():
                path.unlink()
        if workspace_id:
            path = self._report_task_workspace(workspace_id, task_id)
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
            return
        workspace_root = self.artifact_dir / "workspaces"
        for path in workspace_root.glob(f"*/{task_id}"):
            if path.exists() and path.is_dir():
                shutil.rmtree(path)

    def add_event(
        self,
        workspace_id: str,
        task_id: str,
        task_type: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = utc_now()
        row = self.store.execute_returning_row(
            """
            INSERT INTO task_events (
                workspace_id, task_id, task_type, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id, event_type, payload_json, created_at
            """,
            (workspace_id, task_id, task_type, event_type, dumps(payload), created_at),
        )
        return self._event_row(task_id, row)

    def list_events(self, workspace_id: str, task_id: str, after_id: int | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [workspace_id, task_id]
        where = "workspace_id = ? AND task_id = ?"
        if after_id is not None:
            where += " AND id > ?"
            params.append(after_id)
        rows = self.store.query_all(
            f"""
            SELECT id, event_type, payload_json, created_at
            FROM task_events
            WHERE {where}
            ORDER BY id ASC
            """,
            params,
        )
        return [self._event_row(task_id, row) for row in rows]

    def create_crawler_strategy(
        self,
        workspace_id: str,
        payload: CrawlerStrategyInput,
    ) -> dict[str, Any]:
        strategy_id = new_id("strategy")
        now = utc_now()
        policies = [
            policy.to_policy(policy.platformId, now) for policy in payload.platformPolicies
        ]
        self.store.execute(
            """
            INSERT INTO crawler_strategies (
                id, workspace_id, name, run_mode, platform_policies_json,
                owner_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                workspace_id,
                payload.name,
                payload.runMode,
                dumps(policies),
                self._user_json(payload.owner),
                now,
                now,
            ),
        )
        return self.get_crawler_strategy(workspace_id, strategy_id)

    def list_crawler_strategies(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self.store.query_all(
            """
            SELECT *
            FROM crawler_strategies
            WHERE workspace_id = ?
            ORDER BY created_at DESC
            """,
            (workspace_id,),
        )
        return [self._strategy_row(row) for row in rows]

    def get_crawler_strategy(self, workspace_id: str, strategy_id: str) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM crawler_strategies WHERE workspace_id = ? AND id = ?",
            (workspace_id, strategy_id),
        )
        if not row:
            raise ApiError("NOT_FOUND", "Crawler strategy not found", status_code=404)
        return self._strategy_row(row)

    def create_crawler_task(
        self,
        workspace_id: str,
        payload: CreateCrawlerTaskRequest,
    ) -> dict[str, Any]:
        task_id = new_id("crawler")
        now = utc_now()
        stats = {
            "totalKeywords": len(payload.keywords),
            "totalPlatforms": len(payload.platforms),
            "totalTasks": len(payload.keywords) * len(payload.platforms),
            "successfulTasks": 0,
            "failedTasks": 0,
            "totalNotes": 0,
            "totalComments": 0,
            "platformSummary": {},
        }
        start_date, end_date = self._crawler_date_range(payload)
        schedule = payload.schedule.model_dump(mode="json")
        initial_status = "pending" if schedule.get("mode") != "manual" else "queued"
        self.store.execute(
            """
            INSERT INTO crawler_tasks (
                id, workspace_id, strategy_id, run_mode, target_date,
                start_date, end_date, schedule_json,
                platforms_json, keywords_json, keyword_source,
                crawl_depth, max_notes_per_keyword, max_comments_per_note, login_type,
                headless, overrides_json, status, progress, stats_json,
                owner_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                workspace_id,
                payload.strategyId,
                payload.runMode,
                payload.targetDate or start_date,
                start_date,
                end_date,
                dumps(schedule),
                dumps(payload.platforms),
                dumps(payload.keywords),
                payload.keywordSource,
                payload.crawlDepth,
                payload.maxNotesPerKeyword
                if payload.maxNotesPerKeyword is not None
                else 50,
                payload.maxCommentsPerNote
                if payload.maxCommentsPerNote is not None
                else 100,
                payload.loginType,
                1 if payload.headless is not False else 0,
                dumps([item.model_dump(mode="json") for item in payload.overrides]),
                initial_status,
                0,
                dumps(stats),
                self._user_json(payload.owner),
                now,
                now,
            ),
        )
        task = self.get_crawler_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "crawler", "status", {"task": task})
        if self.run_workers and initial_status == "queued":
            threading.Thread(
                target=self._run_crawler_worker,
                args=(workspace_id, task_id),
                daemon=True,
            ).start()
        return task

    def list_crawler_tasks(
        self,
        workspace_id: str,
        status: str | None,
        platform: str | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [workspace_id]
        filters = ["workspace_id = ?"]
        if status:
            filters.append("status = ?")
            params.append(status)
        if platform:
            rows = self.store.query_all(
                """
                SELECT *
                FROM crawler_tasks
                WHERE """ + " AND ".join(filters) + """
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = [row for row in rows if platform in loads(row["platforms_json"], [])][:page_size]
        else:
            params.append(page_size)
            rows = self.store.query_all(
                """
                SELECT *
                FROM crawler_tasks
                WHERE """ + " AND ".join(filters) + """
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            )
        return [self._crawler_row(row) for row in rows]

    def get_crawler_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM crawler_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        )
        if not row:
            raise ApiError("NOT_FOUND", "Crawler task not found", status_code=404)
        return self._crawler_row(row)

    def stop_crawler_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_crawler_task(workspace_id, task_id)
        if task["status"] in TERMINAL_CRAWLER_STATUSES:
            raise ApiError(
                "TASK_NOT_CANCELLABLE",
                "Crawler task is already terminal",
                status_code=409,
            )
        now = utc_now()
        self.store.execute(
            """
            UPDATE crawler_tasks
            SET status = 'stopped', progress = progress, updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (now, workspace_id, task_id),
        )
        task = self.get_crawler_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "crawler", "stopped", {"task": task})
        return task

    def retry_crawler_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_crawler_task(workspace_id, task_id)
        if task["status"] not in {"failed", "stopped", "cancelled"}:
            raise ApiError(
                "CONFLICT",
                "Only failed, stopped, or cancelled crawler tasks can be retried",
                status_code=409,
            )
        now = utc_now()
        self.store.execute(
            """
            UPDATE crawler_tasks
            SET status = 'queued', progress = 0, error_json = NULL, updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (now, workspace_id, task_id),
        )
        task = self.get_crawler_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "crawler", "status", {"task": task})
        if self.run_workers:
            threading.Thread(
                target=self._run_crawler_worker,
                args=(workspace_id, task_id),
                daemon=True,
            ).start()
        return task

    def delete_crawler_task(self, workspace_id: str, task_id: str) -> None:
        task = self.get_crawler_task(workspace_id, task_id)
        if task["status"] in {"running", "stopping"}:
            raise ApiError(
                "CONFLICT",
                "Crawler task is still running. Stop it before deleting.",
                status_code=409,
            )
        self.store.execute(
            "DELETE FROM task_events WHERE workspace_id = ? AND task_id = ? AND task_type = 'crawler'",
            (workspace_id, task_id),
        )
        self.store.execute(
            "DELETE FROM crawler_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        )

    def _run_report_worker(self, workspace_id: str, task_id: str) -> None:
        try:
            if self.get_report_task(workspace_id, task_id)["status"] == "cancelled":
                return
            self._mark_report_running(workspace_id, task_id, 10, "prepare")
            result = self._run_real_report(workspace_id, task_id)
            if self.get_report_task(workspace_id, task_id)["status"] == "cancelled":
                return
            artifacts = self._persist_report_artifacts(workspace_id, task_id, result)
            self._complete_report_task(workspace_id, task_id, artifacts)
        except Exception as exc:
            self._fail_report_task(workspace_id, task_id, exc)

    def _mark_report_running(
        self,
        workspace_id: str,
        task_id: str,
        progress: int,
        stage: str,
    ) -> None:
        now = utc_now()
        self.store.execute(
            """
            UPDATE report_tasks
            SET status = 'running', progress = ?, stage = ?, updated_at = ?
            WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)
            """,
            (progress, stage, now, task_id, workspace_id, workspace_id),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "progress", {"task": task})

    def _run_real_report(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_report_task(workspace_id, task_id)
        source_scope = task.get("sourceScope", {})
        orchestration_enabled = self._report_orchestration_enabled(source_scope)
        reports, forum_logs = self._load_report_inputs(
            task,
            include_latest=not orchestration_enabled,
            include_global_forum=not orchestration_enabled,
        )

        self._apply_workspace_runtime_config(workspace_id)
        from config import reload_settings

        base_settings = reload_settings()
        task_workspace = self._report_task_workspace(task["workspaceId"], task_id)
        if orchestration_enabled:
            orchestration_reports, orchestration_forum_logs = self._run_report_orchestration(
                workspace_id,
                task,
                task_workspace,
                base_settings,
            )
            reports.extend(orchestration_reports)
            if orchestration_forum_logs:
                forum_logs = "\n".join(part for part in (forum_logs, orchestration_forum_logs) if part).strip()

        if not reports:
            reports = [self._topic_only_report_seed(task)]
            self.add_event(
                workspace_id,
                task_id,
                "report",
                "warning",
                {
                    "payload": {
                        "code": "NO_REPORT_INPUTS",
                        "message": (
                            "No Query/Media/Insight report inputs were found. "
                            "Generated a topic-only draft input instead."
                        ),
                    }
                },
            )

        custom_template = source_scope.get("customTemplate", "")
        if not custom_template:
            custom_template = self._load_manual_report_template(task)

        def stream_handler(event_type: str, payload: dict[str, Any]) -> None:
            if event_type == "progress" and "progress" in payload:
                progress = int(payload["progress"])
                self._mark_report_running(
                    workspace_id,
                    task_id,
                    max(10, min(progress, 95)),
                    "agent_running",
                )
            elif event_type in {"stage", "chapter_status", "warning"}:
                self.add_event(
                    workspace_id,
                    task_id,
                    "report",
                    event_type,
                    {"payload": payload},
                )

        self._mark_report_running(workspace_id, task_id, 80, "agent_running")
        from ReportEngine.agent import ReportAgent

        report_config = self._task_engine_settings(base_settings, "report", task_workspace)
        report_config.REPORT_TASK_ID = task_id
        with logger.contextualize(report_task_id=task_id):
            result = ReportAgent(config=report_config).generate_report(
                query=task["topic"],
                reports=reports,
                forum_logs=forum_logs,
                custom_template=custom_template,
                save_report=True,
                stream_handler=stream_handler,
            )
        if not isinstance(result, dict):
            raise RuntimeError("Report Engine returned an invalid result.")
        return result

    def _load_report_inputs(
        self,
        task: dict[str, Any],
        *,
        include_latest: bool = True,
        include_global_forum: bool = True,
    ) -> tuple[list[str], str]:
        source_scope = task.get("sourceScope", {})
        input_refs = source_scope.get("inputFileRefs") or []
        reports = [self._read_required_text(Path(ref)) for ref in input_refs]

        if include_latest and not reports:
            reports = self._load_latest_engine_reports()

        forum_logs = ""
        if include_global_forum and source_scope.get("includeForumLog", True):
            forum_log_path = Path("logs/forum.log")
            if forum_log_path.exists():
                forum_logs = forum_log_path.read_text(encoding="utf-8", errors="replace")

        return reports, forum_logs

    def _run_report_orchestration(
        self,
        workspace_id: str,
        task: dict[str, Any],
        task_workspace: Path,
        base_settings: Any,
    ) -> tuple[list[str], str]:
        task_id = task["id"]
        task_workspace.mkdir(parents=True, exist_ok=True)
        engine_ids = self._report_orchestration_engines(task.get("sourceScope", {}))
        historical_results = self._load_task_historical_engine_reports(task_workspace, set(engine_ids))
        started_at = utc_now()
        orchestration_meta: dict[str, Any] = {
            "enabled": True,
            "mode": "single_engine" if len(engine_ids) == 1 else "multi_engine",
            "status": "running",
            "workspacePath": str(task_workspace),
            "rerunEngines": list(engine_ids),
            "historyEngines": list(historical_results),
            "engines": {
                engine_id: {
                    "status": "queued",
                    "artifactPath": str(task_workspace / engine_id / f"{engine_id}_report.md"),
                }
                for engine_id in engine_ids
            },
            "forum": {
                "status": "queued",
                "artifactPath": str(task_workspace / "forum" / "forum.log"),
            },
            "startedAt": started_at,
        }
        for engine_id, result in historical_results.items():
            orchestration_meta["engines"][engine_id] = {
                "status": "reused",
                "artifactPath": result["artifactPath"],
                "completedAt": started_at,
            }
        self._update_report_orchestration(workspace_id, task_id, orchestration_meta)
        self._mark_report_running(workspace_id, task_id, 15, "orchestrating")
        forum_monitor = self._start_report_forum_monitor(workspace_id, task_id, task_workspace)
        self.add_event(
            workspace_id,
            task_id,
            "report",
            "orchestration",
            {
                "payload": {
                    "stage": "workspace_ready",
                    "workspacePath": str(task_workspace),
                    "engines": list(engine_ids),
                    "historyEngines": list(historical_results),
                }
            },
        )
        for engine_id, result in historical_results.items():
            self.add_event(
                workspace_id,
                task_id,
                "report",
                "orchestration",
                {
                    "payload": {
                        "stage": f"{engine_id}_historical_reused",
                        "engine": engine_id,
                        "status": "reused",
                        "artifactPath": result["artifactPath"],
                    }
                },
            )

        engine_results: dict[str, dict[str, Any]] = {}
        forum_logs = ""
        try:
            with ThreadPoolExecutor(max_workers=max(1, len(engine_ids))) as executor:
                futures = {
                    executor.submit(
                        self._run_pre_report_engine,
                        engine_id,
                        task["topic"],
                        task_workspace,
                        base_settings,
                        task_id,
                    ): engine_id
                    for engine_id in engine_ids
                }
                completed_count = 0
                for future in as_completed(futures):
                    engine_id = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            "engine": engine_id,
                            "status": "failed",
                            "error": str(exc),
                            "completedAt": utc_now(),
                        }
                    engine_results[engine_id] = result
                    completed_count += 1
                    orchestration_meta["engines"][engine_id].update(
                        {
                            "status": result["status"],
                            "startedAt": result.get("startedAt"),
                            "completedAt": result.get("completedAt"),
                        }
                    )
                    if result.get("artifactPath"):
                        orchestration_meta["engines"][engine_id]["artifactPath"] = result["artifactPath"]
                    if result.get("error"):
                        orchestration_meta["engines"][engine_id]["error"] = result["error"]
                    self._update_report_orchestration(workspace_id, task_id, orchestration_meta)
                    self.add_event(
                        workspace_id,
                        task_id,
                        "report",
                        "orchestration",
                        {
                            "payload": {
                                "stage": f"{engine_id}_{result['status']}",
                                "engine": engine_id,
                                "status": result["status"],
                                "artifactPath": result.get("artifactPath"),
                                "error": result.get("error"),
                            }
                        },
                    )
                    progress = 15 + int(completed_count / len(engine_ids) * 45)
                    self._mark_report_running(workspace_id, task_id, progress, "orchestrating")
        finally:
            forum_logs = self._stop_report_forum_monitor(workspace_id, task_id, forum_monitor)
            orchestration_meta["forum"]["status"] = "succeeded"
            orchestration_meta["forum"]["completedAt"] = utc_now()
            self._update_report_orchestration(workspace_id, task_id, orchestration_meta)

        failed = [result for result in engine_results.values() if result["status"] != "succeeded"]
        if failed:
            orchestration_meta["status"] = "failed"
            orchestration_meta["completedAt"] = utc_now()
            self._update_report_orchestration(workspace_id, task_id, orchestration_meta)
            details = "; ".join(
                f"{ENGINE_DISPLAY_NAMES.get(item['engine'], item['engine'])}: {item.get('error', 'failed')}"
                for item in failed
            )
            raise RuntimeError(f"Pre-report orchestration failed. {details}")

        orchestration_meta["status"] = "succeeded"
        orchestration_meta["completedAt"] = utc_now()
        self._update_report_orchestration(workspace_id, task_id, orchestration_meta)
        self._mark_report_running(workspace_id, task_id, 75, "data_loaded")

        combined_results = {**historical_results, **engine_results}
        reports = [
            combined_results[engine_id]["report"]
            for engine_id in REPORT_ORCHESTRATION_ENGINES
            if combined_results.get(engine_id, {}).get("report")
        ]
        return reports, forum_logs

    @staticmethod
    def _load_task_historical_engine_reports(
        task_workspace: Path,
        rerun_engine_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for engine_id in REPORT_ORCHESTRATION_ENGINES:
            if engine_id in rerun_engine_ids:
                continue
            artifact_path = task_workspace / engine_id / f"{engine_id}_report.md"
            if not artifact_path.exists() or not artifact_path.is_file():
                continue
            results[engine_id] = {
                "engine": engine_id,
                "status": "reused",
                "report": artifact_path.read_text(encoding="utf-8", errors="replace"),
                "artifactPath": str(artifact_path),
            }
        return results

    def _run_pre_report_engine(
        self,
        engine_id: str,
        topic: str,
        task_workspace: Path,
        base_settings: Any,
        task_id: str,
    ) -> dict[str, Any]:
        started_at = utc_now()
        engine_dir = task_workspace / engine_id
        engine_dir.mkdir(parents=True, exist_ok=True)
        forum_dir = task_workspace / "forum"
        engine_config = self._task_engine_settings(base_settings, engine_id, task_workspace)
        handler_id = self._add_forum_engine_log_handler(engine_id, task_id, forum_dir)
        forum_token = None
        reset_forum_log_dir = None
        try:
            from utils.forum_reader import reset_forum_log_dir, set_forum_log_dir

            forum_token = set_forum_log_dir(str(forum_dir))
            with logger.contextualize(report_task_id=task_id):
                if engine_id == "query":
                    from QueryEngine.agent import DeepSearchAgent

                    agent = DeepSearchAgent(engine_config)
                elif engine_id == "media":
                    from MediaEngine.agent import AnspireSearchAgent, DeepSearchAgent

                    if getattr(engine_config, "SEARCH_TOOL_TYPE", "") == "AnspireAPI":
                        agent = AnspireSearchAgent(engine_config)
                    else:
                        agent = DeepSearchAgent(engine_config)
                elif engine_id == "insight":
                    from utils.runtime_database import ensure_crawler_database_schema

                    ensure_crawler_database_schema(self.repo_root, engine_config)
                    from InsightEngine.agent import DeepSearchAgent

                    agent = DeepSearchAgent(engine_config)
                else:
                    raise ValueError(f"Unsupported orchestration engine: {engine_id}")

                report = agent.research(topic, save_report=True)
            if not isinstance(report, str):
                report = str(report or "")
            artifact_path = engine_dir / f"{engine_id}_report.md"
            artifact_path.write_text(report, encoding="utf-8")
            return {
                "engine": engine_id,
                "status": "succeeded",
                "report": report,
                "artifactPath": str(artifact_path),
                "startedAt": started_at,
                "completedAt": utc_now(),
            }
        finally:
            if forum_token is not None and reset_forum_log_dir is not None:
                reset_forum_log_dir(forum_token)
            logger.remove(handler_id)

    def _start_report_forum_monitor(
        self,
        workspace_id: str,
        task_id: str,
        task_workspace: Path,
    ) -> Any:
        forum_dir = task_workspace / "forum"
        forum_dir.mkdir(parents=True, exist_ok=True)
        for engine_id in REPORT_ORCHESTRATION_ENGINES:
            (forum_dir / f"{engine_id}.log").touch()
        from ForumEngine.monitor import LogMonitor

        monitor = LogMonitor(log_dir=str(forum_dir))
        monitor.start_monitoring()
        self.add_event(
            workspace_id,
            task_id,
            "report",
            "orchestration",
            {
                "payload": {
                    "stage": "forum_monitor_started",
                    "engine": "forum",
                    "status": "running",
                    "artifactPath": str(forum_dir / "forum.log"),
                }
            },
        )
        return monitor

    def _stop_report_forum_monitor(
        self,
        workspace_id: str,
        task_id: str,
        monitor: Any,
    ) -> str:
        time.sleep(float(os.getenv("BETTAFISH_FORUM_MONITOR_DRAIN_SECONDS", "1.2")))
        monitor.stop_monitoring()
        forum_logs = "\n".join(monitor.get_forum_log_content())
        self.add_event(
            workspace_id,
            task_id,
            "report",
            "orchestration",
            {
                "payload": {
                    "stage": "forum_monitor_stopped",
                    "engine": "forum",
                    "status": "succeeded",
                    "artifactPath": str(monitor.forum_log_file),
                }
            },
        )
        return forum_logs

    @staticmethod
    def _add_forum_engine_log_handler(engine_id: str, task_id: str, forum_dir: Path) -> int:
        package_prefix = {
            "query": "QueryEngine",
            "media": "MediaEngine",
            "insight": "InsightEngine",
        }[engine_id]
        return logger.add(
            str(forum_dir / f"{engine_id}.log"),
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
            filter=lambda record: (
                record["extra"].get("report_task_id") == task_id
                and str(record["name"]).startswith(package_prefix)
            ),
        )

    def _task_engine_settings(self, base_settings: Any, engine_id: str, task_workspace: Path) -> Any:
        config = copy.copy(base_settings)
        engine_dir = task_workspace / engine_id
        config.OUTPUT_DIR = str(engine_dir)
        config.LOG_FILE = str(engine_dir / f"{engine_id}.log")
        if engine_id == "report":
            config.LOG_FILE = str(engine_dir / "report_engine.log")
            config.CHAPTER_OUTPUT_DIR = str(engine_dir / "chapters")
            config.DOCUMENT_IR_OUTPUT_DIR = str(engine_dir / "document_ir")
            config.JSON_ERROR_LOG_DIR = str(engine_dir / "json_errors")
        engine_dir.mkdir(parents=True, exist_ok=True)
        return config

    def _report_task_workspace(self, workspace_id: str, task_id: str) -> Path:
        workspace_segment = slugify_filename(workspace_id, "workspace")
        return self.artifact_dir / "workspaces" / workspace_segment / task_id

    @staticmethod
    def _report_orchestration_enabled(source_scope: dict[str, Any]) -> bool:
        orchestration = source_scope.get("orchestration") or {}
        return orchestration.get("enabled", True) is not False

    @staticmethod
    def _report_orchestration_engines(source_scope: dict[str, Any]) -> tuple[str, ...]:
        orchestration = source_scope.get("orchestration") or {}
        requested = orchestration.get("engines") or list(REPORT_ORCHESTRATION_ENGINES)
        return tuple(engine for engine in REPORT_ORCHESTRATION_ENGINES if engine in requested)

    def _update_report_orchestration(
        self,
        workspace_id: str,
        task_id: str,
        orchestration: dict[str, Any],
    ) -> None:
        row = self.store.query_one(
            "SELECT source_scope_json FROM report_tasks WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)",
            (task_id, workspace_id, workspace_id),
        )
        if not row:
            return
        source_scope = loads(row["source_scope_json"], {})
        source_scope["orchestration"] = orchestration
        self.store.execute(
            """
            UPDATE report_tasks
            SET source_scope_json = ?, updated_at = ?
            WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)
            """,
            (dumps(source_scope), utc_now(), task_id, workspace_id, workspace_id),
        )

    def _apply_workspace_runtime_config(self, workspace_id: str) -> None:
        rows = self.store.query_all(
            "SELECT key, value_json FROM app_configs WHERE workspace_id = ?",
            (workspace_id,),
        )
        for row in rows:
            value = loads(row["value_json"], None)
            if value is None:
                continue
            os.environ[row["key"]] = str(value)

    @staticmethod
    def _read_required_text(path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Report input file not found: {path}")
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _load_latest_engine_reports() -> list[str]:
        reports = []
        for directory in ENGINE_REPORT_DIRS.values():
            if not directory.exists():
                continue
            latest = max(
                directory.glob("*.md"),
                key=lambda path: path.stat().st_mtime,
                default=None,
            )
            if latest:
                reports.append(latest.read_text(encoding="utf-8", errors="replace"))
        return reports

    @staticmethod
    def _topic_only_report_seed(task: dict[str, Any]) -> str:
        topic = task.get("topic") or "未命名报告"
        return "\n".join(
            [
                "# 主题驱动报告输入",
                "",
                f"报告主题：{topic}",
                "",
                "## 输入状态",
                "本次报告任务未提供 Query、Media、Insight 引擎报告，也未提供 sourceScope.inputFileRefs。",
                "请将输出定位为基于主题的舆情分析初稿，并在报告中明确标注缺少外部采集与检索材料的限制。",
                "",
                "## 写作要求",
                "- 围绕报告主题组织舆情背景、关注点、风险线索、机会判断与后续数据采集建议。",
                "- 不要编造具体平台声量、转评赞数量、用户原文或来源链接。",
                "- 如需要数据支撑，应以待补充事项或建议采集方向表述。",
            ]
        )

    def _persist_report_artifacts(
        self,
        workspace_id: str,
        task_id: str,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        task = self.get_report_task(workspace_id, task_id)
        safe_topic = slugify_filename(task["topic"], "report")
        formats = loads(
            self.store.query_one(
                "SELECT output_formats_json FROM report_tasks WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)",
                (task_id, workspace_id, workspace_id),
            )["output_formats_json"],
            ["html"],
        )
        document_ir = self._load_document_ir(result.get("ir_filepath"))
        artifact_workspace_id = task["workspaceId"]
        export_dir = self._report_task_workspace(artifact_workspace_id, task_id) / "report" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for report_format in formats:
            path = self.artifact_path(task_id, report_format, artifact_workspace_id)
            if report_format == "html":
                html_path = result.get("report_filepath")
                if html_path and Path(html_path).exists():
                    if Path(html_path).resolve() != path.resolve():
                        shutil.copyfile(html_path, path)
                else:
                    path.write_text(result.get("html_content", ""), encoding="utf-8")
            elif report_format == "json":
                content = document_ir if document_ir is not None else result
                path.write_text(dumps(content), encoding="utf-8")
            elif report_format == "md":
                if document_ir is None:
                    raise RuntimeError("Report Engine did not return Document IR for Markdown export.")
                from ReportEngine.renderers import MarkdownRenderer

                markdown = MarkdownRenderer().render(
                    document_ir,
                    ir_file_path=result.get("ir_filepath"),
                )
                path.write_text(markdown, encoding="utf-8")
            elif report_format == "pdf":
                if document_ir is None:
                    raise RuntimeError("Report Engine did not return Document IR for PDF export.")
                from ReportEngine.renderers import PDFRenderer

                PDFRenderer().render_to_pdf(
                    document_ir,
                    path,
                    optimize_layout=True,
                    ir_file_path=result.get("ir_filepath"),
                )
            artifacts.append(
                {
                    "format": report_format,
                    "ready": True,
                    "filename": f"{safe_topic}.{report_format}",
                    "sizeBytes": path.stat().st_size,
                    "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/{report_format}?workspaceId={artifact_workspace_id}",
                }
            )
        return artifacts

    @staticmethod
    def _load_document_ir(ir_path: str | None) -> dict[str, Any] | None:
        if not ir_path:
            return None
        path = Path(ir_path)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _complete_report_task(
        self,
        workspace_id: str,
        task_id: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        self.store.execute(
            """
            UPDATE report_tasks
            SET status = 'succeeded', progress = 100, stage = 'completed',
                artifacts_json = ?, updated_at = ?
            WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)
            """,
            (dumps(artifacts), now, task_id, workspace_id, workspace_id),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "completed", {"task": task})

    def _fail_report_task(self, workspace_id: str, task_id: str, exc: Exception) -> None:
        now = utc_now()
        error = {
            "success": False,
            "error": {
                "code": "REPORT_ADAPTER_FAILED",
                "message": str(exc),
            },
        }
        self.store.execute(
            """
            UPDATE report_tasks
            SET status = 'failed', progress = 100, stage = 'failed',
                error_json = ?, updated_at = ?
            WHERE id = ? AND (tenant_id = ? OR workspace_id = ?)
            """,
            (dumps(error), now, task_id, workspace_id, workspace_id),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "failed", {"task": task})

    def _run_crawler_worker(self, workspace_id: str, task_id: str) -> None:
        task = self.get_crawler_task(workspace_id, task_id)
        if task["status"] in TERMINAL_CRAWLER_STATUSES:
            return
        self._mark_crawler_running(workspace_id, task_id)

        adapter_mode = os.getenv(CRAWLER_ADAPTER_ENV, "real").lower()
        if adapter_mode != "real":
            self._fail_crawler_task(
                workspace_id,
                task_id,
                RuntimeError("Only the real crawler adapter is supported in production."),
            )
            return

        try:
            stats = self._run_real_crawler(task)
            if stats.get("failedTasks", 0):
                self._fail_crawler_task(
                    workspace_id,
                    task_id,
                    RuntimeError(self._crawler_failure_message(stats)),
                    stats=stats,
                )
            else:
                self._complete_crawler_task(workspace_id, task_id, stats)
        except Exception as exc:
            self._fail_crawler_task(workspace_id, task_id, exc)

    def _mark_crawler_running(self, workspace_id: str, task_id: str) -> None:
        now = utc_now()
        self.store.execute(
            """
            UPDATE crawler_tasks
            SET status = 'running', progress = 10, updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (now, workspace_id, task_id),
        )
        task = self.get_crawler_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "crawler", "progress", {"task": task})

    def _run_real_crawler(self, task: dict[str, Any]) -> dict[str, Any]:
        login_type = self._crawler_login_type(task)

        from MindSpider.DeepSentimentCrawling.platform_crawler import PlatformCrawler

        crawler = PlatformCrawler(
            log_callback=lambda source, line: self._record_crawler_log_event(task, source, line)
        )
        result = crawler.run_multi_platform_crawl_by_keywords(
            task["keywords"],
            task["platforms"],
            login_type=login_type,
            crawl_depth=task.get("crawlDepth") or 3,
            max_notes_per_keyword=task.get("maxNotesPerKeyword") or 50,
            headless=task.get("headless") is not False,
            start_date=task.get("startDate") or task.get("targetDate"),
            end_date=task.get("endDate") or task.get("targetDate"),
        )
        return self._real_crawler_stats_to_api(result)

    def _record_crawler_log_event(self, task: dict[str, Any], source: str, line: str) -> None:
        workspace_id = task.get("workspaceId")
        task_id = task.get("id")
        if not workspace_id or not task_id:
            return

        truncated = len(line) > MAX_CRAWLER_LOG_LINE_LENGTH
        clean_line = line[:MAX_CRAWLER_LOG_LINE_LENGTH] if truncated else line
        if truncated:
            clean_line = f"{clean_line}... [truncated]"
        level = "error" if source == "stderr" or self._crawler_log_line_looks_error(line) else "info"
        try:
            self.add_event(
                workspace_id,
                task_id,
                "crawler",
                "log",
                {
                    "source": source,
                    "level": level,
                    "line": clean_line,
                    "truncated": truncated,
                },
            )
        except Exception:
            # Logging must not make a crawl fail.
            return

    @staticmethod
    def _crawler_log_line_looks_error(line: str) -> bool:
        lowered = line.lower()
        return any(marker in lowered for marker in ("error", "exception", "traceback", "failed", "失败", "异常"))

    def _crawler_login_type(self, task: dict[str, Any]) -> str:
        workspace_id = task.get("workspaceId")
        platforms = task.get("platforms") or []
        if workspace_id:
            missing = self._missing_active_account_platforms(workspace_id, platforms)
            if missing:
                raise RuntimeError(
                    "No active crawler account available for platform(s): "
                    + ", ".join(missing)
                    + ". Please complete account login before starting a crawl task."
                )
            return "cookie"
        return task.get("loginType") or "qrcode"

    def _missing_active_account_platforms(
        self,
        workspace_id: str,
        platforms: list[str],
    ) -> list[str]:
        missing = []
        for platform in platforms:
            rows = self.store.query_all(
                """
                SELECT id, details_json
                FROM crawler_accounts
                WHERE workspace_id = ? AND platform_id = ? AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                (workspace_id, platform),
            )
            if not any(self._active_account_has_login_state(platform, row) for row in rows):
                missing.append(platform)
        return missing

    @staticmethod
    def _active_account_has_login_state(platform: str, row: dict[str, Any]) -> bool:
        details = loads(row.get("details_json"), {})
        if not isinstance(details, dict):
            return True

        state_names = details.get("loginStateNames")
        if not isinstance(state_names, list):
            state_names = details.get("stateNames")
        if not isinstance(state_names, list):
            return True

        from apps.api.services.accounts import PLATFORM_LOGIN_MARKERS

        required = set(PLATFORM_LOGIN_MARKERS.get(platform, ()))
        observed = {str(name) for name in state_names}
        return bool(required & observed)

    @staticmethod
    def _real_crawler_stats_to_api(result: dict[str, Any]) -> dict[str, Any]:
        platform_summary = {}
        for platform, summary in result.get("platform_summary", {}).items():
            platform_summary[platform] = {
                "successfulKeywords": summary.get("successful_keywords", 0),
                "failedKeywords": summary.get("failed_keywords", 0),
                "totalNotes": summary.get("total_notes", 0),
                "totalComments": summary.get("total_comments", 0),
                "sentiment": summary.get("sentiment", {}),
            }
        return {
            "totalKeywords": result.get("total_keywords", 0),
            "totalPlatforms": result.get("total_platforms", 0),
            "totalTasks": result.get("total_tasks", 0),
            "successfulTasks": result.get("successful_tasks", 0),
            "failedTasks": result.get("failed_tasks", 0),
            "totalNotes": result.get("total_notes", 0),
            "totalComments": result.get("total_comments", 0),
            "platformSummary": platform_summary,
        }

    @staticmethod
    def _crawler_failure_message(stats: dict[str, Any]) -> str:
        failed = stats.get("failedTasks", 0)
        total = stats.get("totalTasks", 0)
        return f"Real crawler failed {failed}/{total} task(s); see api container logs for MediaCrawler stderr."

    @classmethod
    def _crawler_failure_error(cls, stats: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": False,
            "error": {
                "code": "CRAWLER_ADAPTER_FAILED",
                "message": cls._crawler_failure_message(stats),
            },
        }

    @staticmethod
    def _crawler_stats_indicate_failure(stats: dict[str, Any]) -> bool:
        return int(stats.get("failedTasks") or 0) > 0

    def _complete_crawler_task(
        self,
        workspace_id: str,
        task_id: str,
        stats: dict[str, Any],
    ) -> None:
        now = utc_now()
        self.store.execute(
            """
            UPDATE crawler_tasks
            SET status = 'succeeded', progress = 100, stats_json = ?, updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (dumps(stats), now, workspace_id, task_id),
        )
        task = self.get_crawler_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "crawler", "completed", {"task": task})

    def _fail_crawler_task(
        self,
        workspace_id: str,
        task_id: str,
        exc: Exception,
        stats: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        error = self._crawler_failure_error(stats) if stats else {
            "success": False,
            "error": {
                "code": "CRAWLER_ADAPTER_FAILED",
                "message": str(exc),
            },
        }
        self.store.execute(
            """
            UPDATE crawler_tasks
            SET status = 'failed',
                progress = 100,
                stats_json = COALESCE(?, stats_json),
                error_json = ?,
                updated_at = ?
            WHERE workspace_id = ? AND id = ?
            """,
            (dumps(stats) if stats else None, dumps(error), now, workspace_id, task_id),
        )
        task = self.get_crawler_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "crawler", "failed", {"task": task})

    def _repair_inconsistent_crawler_task_statuses(self) -> None:
        rows = self.store.query_all(
            """
            SELECT workspace_id, id, stats_json, error_json
            FROM crawler_tasks
            WHERE status = 'succeeded'
            """,
            (),
        )
        now = utc_now()
        for row in rows:
            stats = loads(row["stats_json"], {})
            if not self._crawler_stats_indicate_failure(stats):
                continue
            self.store.execute(
                """
                UPDATE crawler_tasks
                SET status = 'failed',
                    progress = 100,
                    error_json = COALESCE(error_json, ?),
                    updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    dumps(self._crawler_failure_error(stats)),
                    now,
                    row["workspace_id"],
                    row["id"],
                ),
            )

    def _repair_interrupted_crawler_tasks(self) -> None:
        now = utc_now()
        error = {
            "success": False,
            "error": {
                "code": "CRAWLER_WORKER_INTERRUPTED",
                "message": "Crawler task was interrupted by an API restart. Please retry the task.",
            },
        }
        rows = self.store.query_all(
            """
            SELECT workspace_id, id
            FROM crawler_tasks
            WHERE status = 'running'
            """,
            (),
        )
        for row in rows:
            self.store.execute(
                """
                UPDATE crawler_tasks
                SET status = 'failed',
                    progress = 100,
                    error_json = COALESCE(error_json, ?),
                    updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = 'running'
                """,
                (dumps(error), now, row["workspace_id"], row["id"]),
            )
            task = self.get_crawler_task(row["workspace_id"], row["id"])
            self.add_event(row["workspace_id"], row["id"], "crawler", "failed", {"task": task})

    def _report_row(self, row: dict[str, Any]) -> dict[str, Any]:
        task = {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "tenantId": row["tenant_id"],
            "legacyTaskId": row["legacy_task_id"],
            "topic": row["topic"],
            "status": row["status"],
            "progress": row["progress"],
            "stage": row["stage"],
            "templateId": row["template_id"],
            "sourceScope": loads(row["source_scope_json"], {}),
            "artifacts": loads(row["artifacts_json"], []),
            "error": loads(row["error_json"], None),
            "owner": loads(row["owner_json"], None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        return {key: value for key, value in task.items() if value is not None}

    def _crawler_row(self, row: dict[str, Any]) -> dict[str, Any]:
        stats = loads(row["stats_json"], {})
        status = row["status"]
        error = loads(row["error_json"], None)
        schedule = loads(row.get("schedule_json"), {}) or {}
        if not schedule:
            schedule = {"mode": "manual", "timezone": "Asia/Shanghai"}
        start_date = row.get("start_date") or row["target_date"]
        end_date = row.get("end_date") or row["target_date"]
        if status == "succeeded" and self._crawler_stats_indicate_failure(stats):
            status = "failed"
            error = error or self._crawler_failure_error(stats)
        task = {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "tenantId": row["tenant_id"],
            "strategyId": row["strategy_id"],
            "runMode": row["run_mode"],
            "targetDate": row["target_date"],
            "startDate": start_date,
            "endDate": end_date,
            "schedule": schedule,
            "platforms": loads(row["platforms_json"], []),
            "keywords": loads(row["keywords_json"], []),
            "keywordSource": row["keyword_source"],
            "crawlDepth": row.get("crawl_depth") or 3,
            "maxNotesPerKeyword": row["max_notes_per_keyword"],
            "maxCommentsPerNote": row["max_comments_per_note"],
            "loginType": row["login_type"],
            "headless": bool(row["headless"]),
            "status": status,
            "progress": row["progress"],
            "stats": stats,
            "error": error,
            "owner": loads(row["owner_json"], None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        return {key: value for key, value in task.items() if value is not None}

    @staticmethod
    def _crawler_date_range(payload: CreateCrawlerTaskRequest) -> tuple[str | None, str | None]:
        if payload.startDate and payload.endDate:
            return payload.startDate, payload.endDate
        if payload.targetDate:
            return payload.targetDate, payload.targetDate
        return None, None

    def _strategy_row(self, row: dict[str, Any]) -> dict[str, Any]:
        strategy = {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "tenantId": row["tenant_id"],
            "name": row["name"],
            "runMode": row["run_mode"],
            "platformPolicies": loads(row["platform_policies_json"], []),
            "owner": loads(row["owner_json"], None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        return {key: value for key, value in strategy.items() if value is not None}

    @staticmethod
    def _event_row(task_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            raise ApiError("INTERNAL_ERROR", "Failed to persist task event", status_code=500)
        return {
            "id": str(row["id"]),
            "type": row["event_type"],
            "taskId": task_id,
            "timestamp": row["created_at"],
            "payload": loads(row["payload_json"], {}),
        }

    @staticmethod
    def _user_json(user: UserRef | None) -> str | None:
        return dumps(user.model_dump(exclude_none=True)) if user else None

    @staticmethod
    def _optional_user(key: str, user: UserRef | None) -> dict[str, Any]:
        return {key: user.model_dump(exclude_none=True)} if user else {}

    @staticmethod
    def _report_source_scope(payload: CreateReportTaskRequest) -> dict[str, Any]:
        source_scope = payload.sourceScope.model_dump(mode="json")
        if payload.customTemplate:
            source_scope["customTemplate"] = payload.customTemplate
        return source_scope

    @staticmethod
    def _normalize_report_template_id(template_id: str | None) -> str:
        if not template_id or not template_id.strip():
            return AUTO_REPORT_TEMPLATE_ID
        return template_id.strip()

    def _load_manual_report_template(self, task: dict[str, Any]) -> str:
        template_id = task.get("templateId") or AUTO_REPORT_TEMPLATE_ID
        if template_id == AUTO_REPORT_TEMPLATE_ID:
            return ""
        template_name, template_content = read_report_template(self.repo_root, template_id)
        self.add_event(
            task.get("tenantId") or task["workspaceId"],
            task["id"],
            "report",
            "stage",
            {
                "payload": {
                    "stage": "template_loaded",
                    "templateId": template_id,
                    "templateName": template_name,
                }
            },
        )
        return template_content
