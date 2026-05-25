"""Adapters around legacy engines and filesystem runtime state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.api.schemas import ApiError, COMPONENT_IDS
from apps.api.services.common import utc_now
from apps.api.services.report_templates import list_report_templates


COMPONENT_NAMES = {
    "query": "Query Engine",
    "media": "Media Engine",
    "insight": "Insight Engine",
    "forum": "Forum Engine",
    "report": "Report Engine",
    "mindspider": "MindSpider",
    "database": "Database",
}

ADAPTER_COMPONENT_DIRS = {
    "query": "QueryEngine",
    "media": "MediaEngine",
    "insight": "InsightEngine",
    "forum": "ForumEngine",
    "report": "ReportEngine",
    "mindspider": "MindSpider",
}


class EngineFacade:
    """Thin boundary between the SaaS API and legacy engine/runtime files."""

    def __init__(self, repo_root: Path, crawler_workers_enabled: bool = False):
        self.repo_root = repo_root
        self.crawler_workers_enabled = crawler_workers_enabled
        self._component_overrides: dict[str, str] = {}

    def list_components(self) -> list[dict[str, Any]]:
        components = []
        now = utc_now()
        for component_id in COMPONENT_IDS:
            status = self._component_overrides.get(component_id)
            if not status:
                status = self._infer_status(component_id)
            item = {
                "id": component_id,
                "name": COMPONENT_NAMES[component_id],
                "status": status,
                "lastHeartbeatAt": now if status in {"running", "degraded"} else None,
                "message": self._message(component_id, status),
            }
            output_lines = self._count_output_lines(component_id)
            if output_lines:
                item["outputLines"] = output_lines
            components.append({key: value for key, value in item.items() if value is not None})
        return components

    def set_component_status(self, component_id: str, action: str) -> dict[str, Any]:
        if component_id not in COMPONENT_IDS:
            raise ApiError("VALIDATION_ERROR", "Unsupported component", status_code=400)
        status = "running" if action == "start" else "stopped"
        self._component_overrides[component_id] = status
        return next(item for item in self.list_components() if item["id"] == component_id)

    def list_report_templates(self) -> list[dict[str, Any]]:
        return list_report_templates(self.repo_root)

    def list_logs(
        self,
        source: str | None,
        level: str | None,
        tail: int,
    ) -> list[dict[str, Any]]:
        lines = []
        log_dir = self.repo_root / "logs"
        if log_dir.exists():
            for path in sorted(log_dir.glob("*.log")):
                source_name = self._source_from_log_name(path.name)
                if source and source_name != source:
                    continue
                try:
                    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for index, message in enumerate(raw_lines[-tail:]):
                    detected_level = self._detect_level(message)
                    if level and detected_level != level:
                        continue
                    lines.append(
                        {
                            "id": f"{path.stem}-{index}",
                            "source": source_name,
                            "level": detected_level,
                            "timestamp": utc_now(),
                            "message": message[-1000:],
                        }
                    )
        if not lines:
            lines.append(
                {
                    "id": "system-empty",
                    "source": source or "system",
                    "level": "info",
                    "timestamp": utc_now(),
                    "message": "No runtime logs are available yet.",
                }
            )
        return lines[-tail:]

    def _infer_status(self, component_id: str) -> str:
        if component_id == "database":
            return "running"
        if component_id == "mindspider":
            if not self._component_directory_exists(component_id):
                return "unknown"
            return "running" if self.crawler_workers_enabled else "stopped"
        if component_id in ADAPTER_COMPONENT_DIRS:
            return "running" if self._component_directory_exists(component_id) else "unknown"
        return "unknown"

    def _message(self, component_id: str, status: str) -> str:
        if component_id == "database":
            return "SaaS persistence is initialized"
        if status == "running":
            if component_id == "mindspider":
                return "Crawler worker is enabled through the service adapter"
            return "Component is available through the service adapter"
        if component_id == "mindspider":
            return "No crawler worker is currently running"
        if status == "unknown":
            return "Component files were not found in the application workspace"
        return "Legacy component status is not directly managed by the SaaS API"

    def _component_directory_exists(self, component_id: str) -> bool:
        directory = ADAPTER_COMPONENT_DIRS.get(component_id)
        return bool(directory and (self.repo_root / directory).exists())

    def _count_output_lines(self, component_id: str) -> int:
        log_dir = self.repo_root / "logs"
        if not log_dir.exists():
            return 0
        count = 0
        for path in log_dir.glob(f"*{component_id}*.log"):
            try:
                count += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
        return count

    @staticmethod
    def _source_from_log_name(filename: str) -> str:
        lowered = filename.lower()
        for source in ("query", "media", "insight", "forum", "report", "mindspider", "crawler"):
            if source in lowered:
                return source
        return "system"

    @staticmethod
    def _detect_level(message: str) -> str:
        upper = message.upper()
        if "CRITICAL" in upper:
            return "critical"
        if "ERROR" in upper or "失败" in message:
            return "error"
        if "WARN" in upper or "警告" in message:
            return "warning"
        if "DEBUG" in upper:
            return "debug"
        return "info"
