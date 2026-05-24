"""Task lifecycle services for reports, crawlers, and search runs."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from apps.api.schemas import (
    ApiError,
    CreateCrawlerTaskRequest,
    CreateReportTaskRequest,
    CrawlerStrategyInput,
    REPORT_FORMATS,
    UserRef,
)
from apps.api.services.common import new_id, slugify_filename, utc_now
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


class TaskService:
    def __init__(self, store: Store, artifact_dir: Path, run_workers: bool = False):
        self.store = store
        self.artifact_dir = artifact_dir
        self.run_workers = run_workers
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
        artifacts = [
            {
                "format": item,
                "ready": False,
                "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/{item}",
            }
            for item in formats
        ]
        self.store.execute(
            """
            INSERT INTO report_tasks (
                id, workspace_id, topic, status, progress, stage, template_id,
                source_scope_json, output_formats_json, artifacts_json,
                owner_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                workspace_id,
                payload.topic,
                "queued",
                0,
                "queued",
                payload.templateId,
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

    def list_report_tasks(
        self,
        workspace_id: str,
        status: str | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [workspace_id]
        where = "workspace_id = ?"
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
            "SELECT * FROM report_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
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
            WHERE workspace_id = ? AND id = ?
            """,
            (dumps(error), now, workspace_id, task_id),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "cancelled", {"task": task})
        return task

    def get_report_result(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_report_task(workspace_id, task_id)
        if task["status"] != "succeeded":
            raise ApiError(
                "EXPORT_UNAVAILABLE",
                "Report result is not ready",
                status_code=409,
                details={"status": task["status"]},
            )
        html_path = self.artifact_path(task_id, "html")
        html_content = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
        return {
            "success": True,
            "taskId": task_id,
            "htmlPreviewUrl": f"/api/v1/report-tasks/{task_id}/exports/html",
            "htmlContent": html_content,
            "artifacts": task.get("artifacts", []),
        }

    def artifact_path(self, task_id: str, report_format: str) -> Path:
        if report_format not in REPORT_FORMATS:
            raise ApiError("VALIDATION_ERROR", "Unsupported report format", status_code=400)
        suffix = "json" if report_format == "json" else report_format
        return self.artifact_dir / f"{task_id}.{suffix}"

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
        self.store.execute(
            """
            INSERT INTO crawler_tasks (
                id, workspace_id, strategy_id, run_mode, target_date,
                platforms_json, keywords_json, keyword_source,
                max_notes_per_keyword, max_comments_per_note, login_type,
                headless, overrides_json, status, progress, stats_json,
                owner_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                workspace_id,
                payload.strategyId,
                payload.runMode,
                payload.targetDate,
                dumps(payload.platforms),
                dumps(payload.keywords),
                payload.keywordSource,
                payload.maxNotesPerKeyword
                if payload.maxNotesPerKeyword is not None
                else 50,
                payload.maxCommentsPerNote
                if payload.maxCommentsPerNote is not None
                else 100,
                payload.loginType,
                1 if payload.headless is not False else 0,
                dumps([item.model_dump(mode="json") for item in payload.overrides]),
                "queued",
                0,
                dumps(stats),
                self._user_json(payload.owner),
                now,
                now,
            ),
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
            filters.append(
                """
                EXISTS (
                    SELECT 1
                    FROM json_each(crawler_tasks.platforms_json)
                    WHERE json_each.value = ?
                )
                """
            )
            params.append(platform)
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
            WHERE workspace_id = ? AND id = ?
            """,
            (progress, stage, now, workspace_id, task_id),
        )
        task = self.get_report_task(workspace_id, task_id)
        self.add_event(workspace_id, task_id, "report", "progress", {"task": task})

    def _run_real_report(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_report_task(workspace_id, task_id)
        reports, forum_logs = self._load_report_inputs(task)
        if not reports:
            raise RuntimeError(
                "No engine report inputs are available. Run Query/Media/Insight engines first "
                "or submit sourceScope.inputFileRefs."
            )

        from ReportEngine.agent import ReportAgent

        source_scope = task.get("sourceScope", {})

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

        self._mark_report_running(workspace_id, task_id, 20, "agent_running")
        result = ReportAgent().generate_report(
            query=task["topic"],
            reports=reports,
            forum_logs=forum_logs,
            custom_template=source_scope.get("customTemplate", ""),
            save_report=True,
            stream_handler=stream_handler,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Report Engine returned an invalid result.")
        return result

    def _load_report_inputs(self, task: dict[str, Any]) -> tuple[list[str], str]:
        source_scope = task.get("sourceScope", {})
        input_refs = source_scope.get("inputFileRefs") or []
        reports = [self._read_required_text(Path(ref)) for ref in input_refs]

        if not reports:
            reports = self._load_latest_engine_reports()

        forum_logs = ""
        if source_scope.get("includeForumLog", True):
            forum_log_path = Path("logs/forum.log")
            if forum_log_path.exists():
                forum_logs = forum_log_path.read_text(encoding="utf-8", errors="replace")

        return reports, forum_logs

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
                "SELECT output_formats_json FROM report_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            )["output_formats_json"],
            ["html"],
        )
        document_ir = self._load_document_ir(result.get("ir_filepath"))
        artifacts = []
        for report_format in formats:
            path = self.artifact_path(task_id, report_format)
            if report_format == "html":
                html_path = result.get("report_filepath")
                if html_path and Path(html_path).exists():
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
                    "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/{report_format}",
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
            WHERE workspace_id = ? AND id = ?
            """,
            (dumps(artifacts), now, workspace_id, task_id),
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
            WHERE workspace_id = ? AND id = ?
            """,
            (dumps(error), now, workspace_id, task_id),
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
        from MindSpider.DeepSentimentCrawling.platform_crawler import PlatformCrawler

        crawler = PlatformCrawler(
            log_callback=lambda source, line: self._record_crawler_log_event(task, source, line)
        )
        result = crawler.run_multi_platform_crawl_by_keywords(
            task["keywords"],
            task["platforms"],
            login_type=self._crawler_login_type(task),
            max_notes_per_keyword=task.get("maxNotesPerKeyword") or 50,
            headless=task.get("headless") is not False,
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
            row = self.store.query_one(
                """
                SELECT id
                FROM crawler_accounts
                WHERE workspace_id = ? AND platform_id = ? AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (workspace_id, platform),
            )
            if not row:
                missing.append(platform)
        return missing

    @staticmethod
    def _real_crawler_stats_to_api(result: dict[str, Any]) -> dict[str, Any]:
        platform_summary = {}
        for platform, summary in result.get("platform_summary", {}).items():
            platform_summary[platform] = {
                "successfulKeywords": summary.get("successful_keywords", 0),
                "failedKeywords": summary.get("failed_keywords", 0),
                "totalNotes": summary.get("total_notes", 0),
                "totalComments": summary.get("total_comments", 0),
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
            "platforms": loads(row["platforms_json"], []),
            "keywords": loads(row["keywords_json"], []),
            "keywordSource": row["keyword_source"],
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
