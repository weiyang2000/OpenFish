"""Report template discovery and lookup helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


AUTO_REPORT_TEMPLATE_ID = "auto"
AUTO_REPORT_TEMPLATE = {
    "id": AUTO_REPORT_TEMPLATE_ID,
    "name": "自动选择",
    "filename": "",
    "description": "根据报告主题和输入材料自动选择最合适的报告模板。",
    "sizeBytes": 0,
}

TEMPLATE_ID_BY_NAME = {
    "日常或定期舆情监测报告模板": "daily-monitoring",
    "突发事件与危机公关舆情报告模板": "crisis-response",
    "特定政策或行业动态舆情分析报告模板": "industry-policy",
    "企业品牌声誉分析报告模板": "brand-reputation",
    "市场竞争格局舆情分析报告模板": "market-competition",
    "社会公共热点事件分析报告模板": "public-hot-topic",
}


def template_dir(repo_root: Path) -> Path:
    return repo_root / "ReportEngine" / "report_template"


def template_id(name: str) -> str:
    return TEMPLATE_ID_BY_NAME.get(name, name.lower().replace(" ", "-"))


def list_report_templates(repo_root: Path) -> list[dict[str, Any]]:
    templates = [dict(AUTO_REPORT_TEMPLATE)]
    directory = template_dir(repo_root)
    if not directory.exists():
        return templates

    for path in sorted(directory.glob("*.md")):
        stat = path.stat()
        templates.append(
            {
                "id": template_id(path.stem),
                "name": path.stem,
                "filename": path.name,
                "description": template_description(path),
                "sizeBytes": stat.st_size,
            }
        )
    return templates


def find_report_template(repo_root: Path, selected_template_id: str) -> Path | None:
    if selected_template_id == AUTO_REPORT_TEMPLATE_ID:
        return None

    directory = template_dir(repo_root)
    if not directory.exists():
        return None

    for path in sorted(directory.glob("*.md")):
        if template_id(path.stem) == selected_template_id:
            return path
    return None


def read_report_template(repo_root: Path, selected_template_id: str) -> tuple[str, str]:
    path = find_report_template(repo_root, selected_template_id)
    if path is None:
        raise FileNotFoundError(f"Unsupported report template: {selected_template_id}")
    return path.stem, path.read_text(encoding="utf-8")


def template_description(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip("# ").strip()
            if line:
                return line[:120]
    except OSError:
        pass
    return path.stem
