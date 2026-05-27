"""Central Insight Engine mode presets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class InsightMode(str, Enum):
    """Supported Insight Engine generation modes."""

    FAST = "fast"
    NORMAL = "normal"
    DEEP = "deep"


@dataclass(frozen=True)
class ReportSectionSpec:
    title: str
    content: str


@dataclass(frozen=True)
class InsightModePreset:
    mode: InsightMode
    paragraph_count: int
    reflection_rounds: int
    max_search_results_for_llm: int
    max_clustered_results: int
    results_per_cluster: int
    structure_guidance: str
    content_depth_guidance: str
    report_output_guidance: str
    fallback_sections: tuple[ReportSectionSpec, ...]


FAST_PRESET = InsightModePreset(
    mode=InsightMode.FAST,
    paragraph_count=3,
    reflection_rounds=1,
    max_search_results_for_llm=20,
    max_clustered_results=20,
    results_per_cluster=4,
    structure_guidance=(
        "聚焦最关键的舆情脉络、核心观点和行动建议，避免过细分支。"
    ),
    content_depth_guidance=(
        "每段给出2-3个关键分析点，优先覆盖高信号数据和代表性声音。"
    ),
    report_output_guidance=(
        "生成短报告，控制在1200-2000字，保留执行摘要、核心发现、"
        "分段分析和简明建议，不扩写低价值背景。"
    ),
    fallback_sections=(
        ReportSectionSpec(
            "舆情概览",
            "快速概述事件背景、讨论热度和主要传播场景。",
        ),
        ReportSectionSpec(
            "核心观点与情绪",
            "提炼公众主要态度、代表性评论和情绪分布。",
        ),
        ReportSectionSpec(
            "风险判断与建议",
            "总结关键风险、机会和可执行的后续建议。",
        ),
    ),
)

NORMAL_PRESET = InsightModePreset(
    mode=InsightMode.NORMAL,
    paragraph_count=5,
    reflection_rounds=3,
    max_search_results_for_llm=0,
    max_clustered_results=50,
    results_per_cluster=5,
    structure_guidance=(
        "保持完整舆情分析报告结构，从宏观背景递进到深层洞察。"
    ),
    content_depth_guidance=(
        "每段包含3-5个子分析点，覆盖数据、观点、平台和情绪维度。"
    ),
    report_output_guidance=(
        "生成完整专业舆情分析报告，维持现有深度报告风格和质量基线，"
        "不少于一万字。"
    ),
    fallback_sections=(
        ReportSectionSpec(
            "背景与事件概述",
            "全面梳理事件起因、发展脉络和关键节点。",
        ),
        ReportSectionSpec(
            "舆情热度与传播分析",
            "分析数据统计、平台分布、传播路径和影响范围。",
        ),
        ReportSectionSpec(
            "公众情感与观点分析",
            "分析情感倾向、观点分布、争议焦点和价值冲突。",
        ),
        ReportSectionSpec(
            "不同群体与平台差异",
            "比较不同平台、群体、地域和用户圈层的观点差异。",
        ),
        ReportSectionSpec(
            "深层原因与社会影响",
            "分析根本原因、社会心理、文化背景和长期影响。",
        ),
    ),
)

DEEP_PRESET = InsightModePreset(
    mode=InsightMode.DEEP,
    paragraph_count=7,
    reflection_rounds=4,
    max_search_results_for_llm=80,
    max_clustered_results=80,
    results_per_cluster=8,
    structure_guidance=(
        "展开更完整的深度研究结构，覆盖时间线、传播网络、群体差异、"
        "代表性民声、根因洞察和策略建议。"
    ),
    content_depth_guidance=(
        "每段包含4-6个子分析点，要求更细的数据拆解、对比和证据链。"
    ),
    report_output_guidance=(
        "生成更完整的深度舆情研究报告，强调证据链、平台/群体对比、"
        "趋势预判和策略建议，不少于一万二千字。"
    ),
    fallback_sections=(
        ReportSectionSpec(
            "事件背景与发展时间线",
            "梳理事件背景、关键节点、时间线和阶段性变化。",
        ),
        ReportSectionSpec(
            "舆情热度、传播路径与扩散机制",
            "分析声量、平台分布、传播链路和扩散机制。",
        ),
        ReportSectionSpec(
            "公众情绪结构与观点谱系",
            "拆解情绪分布、观点阵营、争议焦点和价值取向。",
        ),
        ReportSectionSpec(
            "平台、群体与地域差异",
            "比较不同平台、群体、地域和圈层的表达差异。",
        ),
        ReportSectionSpec(
            "代表性民声与典型案例",
            "提炼高赞评论、典型案例和关键用户声音。",
        ),
        ReportSectionSpec(
            "深层原因、社会心理与外部影响",
            "分析制度、文化、心理和社会环境等深层因素。",
        ),
        ReportSectionSpec(
            "趋势预判、风险分级与应对建议",
            "输出后续走势判断、风险等级和行动建议。",
        ),
    ),
)

INSIGHT_MODE_PRESETS: dict[InsightMode, InsightModePreset] = {
    preset.mode: preset
    for preset in (FAST_PRESET, NORMAL_PRESET, DEEP_PRESET)
}

DEFAULT_INSIGHT_MODE = InsightMode.NORMAL
INSIGHT_MODE_VALUES = tuple(mode.value for mode in InsightMode)


def normalize_insight_mode(value: str | InsightMode | None) -> InsightMode:
    if value is None or value == "":
        return DEFAULT_INSIGHT_MODE
    if isinstance(value, InsightMode):
        return value
    try:
        return InsightMode(str(value).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(INSIGHT_MODE_VALUES)
        raise ValueError(
            f"unsupported insight mode: {value!r}; expected one of {allowed}"
        ) from exc


def get_insight_mode_preset(
    mode: str | InsightMode | None = None,
    config: Any | None = None,
) -> InsightModePreset:
    """Return a resolved preset, preserving legacy normal-mode config knobs."""

    normalized_mode = normalize_insight_mode(mode)
    preset = INSIGHT_MODE_PRESETS[normalized_mode]
    if normalized_mode != InsightMode.NORMAL or config is None:
        return preset

    return replace(
        preset,
        reflection_rounds=max(
            0,
            int(getattr(config, "MAX_REFLECTIONS", preset.reflection_rounds)),
        ),
        max_search_results_for_llm=max(
            0,
            int(
                getattr(
                    config,
                    "MAX_SEARCH_RESULTS_FOR_LLM",
                    preset.max_search_results_for_llm,
                )
            ),
        ),
    )
