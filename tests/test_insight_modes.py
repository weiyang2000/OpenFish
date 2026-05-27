import importlib.util
import json
import sys
import time
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import pytest

if importlib.util.find_spec("sentence_transformers") is None:
    sentence_transformers_module = ModuleType("sentence_transformers")


    class _DummySentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            pass


    sentence_transformers_module.SentenceTransformer = _DummySentenceTransformer
    sys.modules.setdefault("sentence_transformers", sentence_transformers_module)

if importlib.util.find_spec("openai") is None:
    openai_module = ModuleType("openai")


    class _DummyOpenAI:
        def __init__(self, *_args, **_kwargs):
            pass


    openai_module.OpenAI = _DummyOpenAI
    sys.modules.setdefault("openai", openai_module)


class _DummyDBResponse:
    def __init__(self, tool_name="", parameters=None, results=None, **_kwargs):
        self.tool_name = tool_name
        self.parameters = parameters or {}
        self.results = results or []


if importlib.util.find_spec("sqlalchemy") is None:
    tools_module = ModuleType("InsightEngine.tools")
    tools_module.__path__ = []
    tools_search_module = ModuleType("InsightEngine.tools.search")

    @dataclass
    class _DummyQueryResult:
        platform: str
        content_type: str
        title_or_content: str
        author_nickname: str | None = None
        url: str | None = None
        publish_time: object | None = None
        engagement: dict | None = None
        source_table: str = ""
        hotness_score: float = 0.0
        sentiment_label: str | None = None
        sentiment_score: float | None = None

    class _DummyMediaCrawlerDB:
        pass

    tools_module.DBResponse = _DummyDBResponse
    tools_module.MediaCrawlerDB = _DummyMediaCrawlerDB
    tools_module.keyword_optimizer = SimpleNamespace()
    tools_search_module.QueryResult = _DummyQueryResult
    sys.modules.setdefault("InsightEngine.tools", tools_module)
    sys.modules.setdefault("InsightEngine.tools.search", tools_search_module)


from InsightEngine.agent import DeepSearchAgent
from InsightEngine.modes import get_insight_mode_preset
from InsightEngine.nodes.report_structure_node import ReportStructureNode
from InsightEngine.state import State


def test_fast_mode_structure_is_trimmed_and_backfilled():
    preset = get_insight_mode_preset("fast")
    node = ReportStructureNode(None, "测试主题", preset)

    too_many_sections = [
        {"title": f"段落{i}", "content": f"内容{i}"}
        for i in range(1, 6)
    ]
    trimmed = node.process_output(json.dumps(too_many_sections, ensure_ascii=False))
    assert [section["title"] for section in trimmed] == ["段落1", "段落2", "段落3"]

    too_few_sections = [{"title": "自定义段落", "content": "自定义内容"}]
    backfilled = node.process_output(json.dumps(too_few_sections, ensure_ascii=False))
    assert len(backfilled) == 3
    assert backfilled[0]["title"] == "自定义段落"
    assert [section["title"] for section in backfilled[1:]] == [
        "核心观点与情绪",
        "风险判断与建议",
    ]


def test_fast_mode_caps_results_entering_llm_prompt():
    agent = object.__new__(DeepSearchAgent)
    agent.mode_preset = get_insight_mode_preset("fast")

    response = _DummyDBResponse(
        tool_name="search_topic_globally",
        parameters={},
        results=[
            SimpleNamespace(
                platform="wb",
                content_type="note",
                title_or_content=f"result-{index}",
                url="",
                hotness_score=0.0,
                publish_time=None,
                author_nickname=None,
                engagement={},
                sentiment_label=None,
                sentiment_score=None,
            )
            for index in range(25)
        ],
    )

    prompt_results = agent._search_results_from_response(response)
    assert len(prompt_results) == 20
    assert prompt_results[-1]["title"] == "result-19"


def test_normal_mode_keeps_legacy_configurable_defaults():
    config = SimpleNamespace(MAX_REFLECTIONS=2, MAX_SEARCH_RESULTS_FOR_LLM=7)
    preset = get_insight_mode_preset("normal", config)

    assert preset.paragraph_count == 5
    assert preset.reflection_rounds == 2
    assert preset.max_search_results_for_llm == 7


class _ParallelStubAgent(DeepSearchAgent):
    def __init__(self, fail_order: int | None = None):
        self.state = State(query="测试主题", report_title="测试报告")
        self.mode_preset = get_insight_mode_preset("fast")
        self.fail_order = fail_order
        for index in range(3):
            self.state.add_paragraph(f"段落{index}", f"内容{index}")

    def _initial_search_and_summary(
        self,
        paragraph_index: int,
        state: State | None = None,
    ) -> State:
        state = state or self.state
        paragraph = state.paragraphs[paragraph_index]
        time.sleep((3 - paragraph.order) * 0.01)
        if paragraph.order == self.fail_order:
            raise RuntimeError("simulated paragraph failure")
        paragraph.research.latest_summary = f"summary-{paragraph.order}"
        return state

    def _reflection_loop(
        self,
        paragraph_index: int,
        state: State | None = None,
    ) -> State:
        return state or self.state


def test_parallel_paragraph_processing_preserves_original_order():
    agent = _ParallelStubAgent()

    agent._process_paragraphs()

    assert [paragraph.order for paragraph in agent.state.paragraphs] == [0, 1, 2]
    assert [
        paragraph.research.latest_summary
        for paragraph in agent.state.paragraphs
    ] == ["summary-0", "summary-1", "summary-2"]
    assert all(paragraph.research.is_completed for paragraph in agent.state.paragraphs)


def test_parallel_paragraph_failure_keeps_original_state_incomplete():
    agent = _ParallelStubAgent(fail_order=1)

    with pytest.raises(RuntimeError, match="段落处理失败"):
        agent._process_paragraphs()

    assert [paragraph.research.latest_summary for paragraph in agent.state.paragraphs] == [
        "",
        "",
        "",
    ]
    assert not any(paragraph.research.is_completed for paragraph in agent.state.paragraphs)
