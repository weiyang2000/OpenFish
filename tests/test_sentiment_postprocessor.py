import sqlite3
from dataclasses import dataclass
from pathlib import Path

from MindSpider.DeepSentimentCrawling.sentiment_postprocessor import (
    CrawlerSentimentPostProcessor,
)


@dataclass
class FakeSentimentResult:
    sentiment_label: str
    confidence: float
    success: bool = True


@dataclass
class FakeBatchResult:
    results: list[FakeSentimentResult]
    analysis_performed: bool = True


class FakeAnalyzer:
    is_initialized = True
    is_disabled = False

    def analyze_batch(self, texts: list[str], show_progress: bool = False) -> FakeBatchResult:
        del texts, show_progress
        return FakeBatchResult([FakeSentimentResult("非常负面", 0.81)])


def test_sentiment_postprocessor_adds_columns_and_updates_rows(tmp_path: Path):
    db_path = tmp_path / "crawler.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE weibo_note (
                id INTEGER PRIMARY KEY,
                content TEXT
            )
            """
        )
        conn.execute("INSERT INTO weibo_note (content) VALUES (?)", ("服务太差了，很失望",))

    processor = CrawlerSentimentPostProcessor(
        database_url=f"sqlite:///{db_path}",
        analyzer_factory=FakeAnalyzer,
    )
    stats = processor.run_for_platform("wb")

    assert stats["processed"] == 1
    assert stats["updated"] == 1

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(weibo_note)").fetchall()}
        row = conn.execute(
            "SELECT sentiment_label, sentiment_score, sentiment_analyzed_at FROM weibo_note"
        ).fetchone()

    assert {"sentiment_label", "sentiment_score", "sentiment_analyzed_at"} <= columns
    assert row[0] == "negative"
    assert row[1] == -0.81
    assert row[2] is not None
