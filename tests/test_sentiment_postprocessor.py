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
        del show_progress
        return FakeBatchResult([FakeSentimentResult("非常负面", 0.81) for _ in texts])


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


def test_sentiment_postprocessor_limits_analysis_to_touched_date_range(tmp_path: Path):
    db_path = tmp_path / "crawler.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE weibo_note (
                id INTEGER PRIMARY KEY,
                content TEXT,
                create_time INTEGER,
                add_ts INTEGER,
                last_modify_ts INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO weibo_note (id, content, create_time, add_ts, last_modify_ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "范围内且本次更新", 1779292800, 2000, 2000),
                (2, "范围外但本次更新", 1778860800, 2000, 2000),
                (3, "范围内历史数据", 1779292800, 100, 100),
            ],
        )

    processor = CrawlerSentimentPostProcessor(
        database_url=f"sqlite:///{db_path}",
        analyzer_factory=FakeAnalyzer,
        start_date="2026-05-20",
        end_date="2026-05-22",
        touched_since_ms=1000,
    )
    stats = processor.run_for_platform("wb")

    assert stats["processed"] == 1
    assert stats["updated"] == 1

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, sentiment_label FROM weibo_note ORDER BY id"
        ).fetchall()

    assert rows == [(1, "negative"), (2, None), (3, None)]
