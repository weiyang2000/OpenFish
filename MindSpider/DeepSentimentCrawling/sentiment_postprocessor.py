"""Post-process crawler rows with persisted sentiment fields."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from loguru import logger
from sqlalchemy import MetaData, Table, and_, create_engine, inspect, or_, select, update
from sqlalchemy.engine import Engine

from MindSpider.DeepSentimentCrawling.crawler_record_filters import (
    date_columns_for_table,
    normalize_date_range,
    row_matches_date_range,
)
from utils.runtime_database import load_runtime_database_config


SENTIMENT_COLUMNS = {
    "sentiment_label": "TEXT",
    "sentiment_score": "FLOAT",
    "sentiment_analyzed_at": "BIGINT",
}


@dataclass(frozen=True)
class SentimentTableSpec:
    platform: str
    table: str
    text_columns: tuple[str, ...]


SENTIMENT_TABLE_SPECS: tuple[SentimentTableSpec, ...] = (
    SentimentTableSpec("xhs", "xhs_note", ("title", "desc")),
    SentimentTableSpec("xhs", "xhs_note_comment", ("content",)),
    SentimentTableSpec("dy", "douyin_aweme", ("title", "desc")),
    SentimentTableSpec("dy", "douyin_aweme_comment", ("content",)),
    SentimentTableSpec("ks", "kuaishou_video", ("title", "desc")),
    SentimentTableSpec("ks", "kuaishou_video_comment", ("content",)),
    SentimentTableSpec("bili", "bilibili_video", ("title", "desc")),
    SentimentTableSpec("bili", "bilibili_video_comment", ("content",)),
    SentimentTableSpec("wb", "weibo_note", ("content",)),
    SentimentTableSpec("wb", "weibo_note_comment", ("content",)),
    SentimentTableSpec("tieba", "tieba_note", ("title", "desc")),
    SentimentTableSpec("tieba", "tieba_comment", ("content",)),
    SentimentTableSpec("zhihu", "zhihu_content", ("title", "desc", "content_text")),
    SentimentTableSpec("zhihu", "zhihu_comment", ("content",)),
)


def _normalize_sentiment(label: Any) -> str:
    text = str(label or "").strip().lower()
    if text in {"positive", "pos", "正面", "正向", "积极", "非常正面"}:
        return "positive"
    if text in {"negative", "neg", "负面", "负向", "消极", "非常负面"}:
        return "negative"
    if text in {"neutral", "neu", "中性", "一般", "客观"}:
        return "neutral"
    return "unknown"


def _signed_score(label: str, confidence: Any) -> float:
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(score, 1.0))
    if label == "negative":
        return -score
    if label == "neutral":
        return 0.0
    if label == "positive":
        return score
    return 0.0


def _unique_columns(columns: list[Any]) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for column in columns:
        key = getattr(column, "key", str(column))
        if key in seen:
            continue
        seen.add(key)
        unique.append(column)
    return unique


class CrawlerSentimentPostProcessor:
    """Analyze unprocessed crawler rows and persist normalized sentiment fields."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        batch_size: int | None = None,
        analyzer_factory: Callable[[], Any] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        touched_since_ms: int | None = None,
    ) -> None:
        self.database_url = database_url
        self.batch_size = batch_size or int(os.getenv("CRAWLER_SENTIMENT_BATCH_SIZE", "200"))
        self.analyzer_factory = analyzer_factory
        self._analyzer: Any | None = None
        self.date_range = normalize_date_range(start_date, end_date)
        self.touched_since_ms = touched_since_ms

    def run_for_platform(self, platform: str) -> dict[str, Any]:
        specs = [spec for spec in SENTIMENT_TABLE_SPECS if spec.platform == platform]
        return self.run(specs)

    def run(self, specs: list[SentimentTableSpec] | tuple[SentimentTableSpec, ...]) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "processed": 0,
            "updated": 0,
            "failed": 0,
            "tables": {},
        }
        if not specs:
            return stats

        engine = self._create_engine()
        try:
            self.ensure_sentiment_columns(engine, specs)
            existing_tables = set(inspect(engine).get_table_names())
            with engine.begin() as conn:
                for spec in specs:
                    if spec.table not in existing_tables:
                        continue
                    table_stats = self._process_table(conn, engine, spec)
                    stats["tables"][spec.table] = table_stats
                    stats["processed"] += table_stats["processed"]
                    stats["updated"] += table_stats["updated"]
                    stats["failed"] += table_stats["failed"]
        finally:
            engine.dispose()
        return stats

    def ensure_sentiment_columns(
        self,
        engine: Engine,
        specs: list[SentimentTableSpec] | tuple[SentimentTableSpec, ...] = SENTIMENT_TABLE_SPECS,
    ) -> None:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        for spec in specs:
            if spec.table not in table_names:
                continue
            existing = {column["name"] for column in inspector.get_columns(spec.table)}
            for column, generic_type in SENTIMENT_COLUMNS.items():
                if column in existing:
                    continue
                self._add_column(engine, spec.table, column, generic_type)

    def _create_engine(self) -> Engine:
        database_url = self.database_url or load_runtime_database_config().sync_sqlalchemy_url()
        return create_engine(database_url, pool_pre_ping=True)

    def _add_column(self, engine: Engine, table_name: str, column_name: str, generic_type: str) -> None:
        from sqlalchemy import text

        column_type = self._column_type(engine, generic_type)
        preparer = engine.dialect.identifier_preparer
        statement = (
            f"ALTER TABLE {preparer.quote(table_name)} "
            f"ADD COLUMN {preparer.quote(column_name)} {column_type}"
        )
        with engine.begin() as conn:
            conn.execute(text(statement))

    @staticmethod
    def _column_type(engine: Engine, generic_type: str) -> str:
        dialect = engine.dialect.name
        if generic_type == "TEXT":
            return "TEXT"
        if generic_type == "BIGINT":
            return "BIGINT" if dialect != "sqlite" else "INTEGER"
        if dialect == "postgresql":
            return "DOUBLE PRECISION"
        if dialect in {"mysql", "mariadb"}:
            return "DOUBLE"
        return "REAL"

    def _process_table(self, conn: Any, engine: Engine, spec: SentimentTableSpec) -> dict[str, int]:
        metadata = MetaData()
        table = Table(spec.table, metadata, autoload_with=engine)
        if "id" not in table.c or "sentiment_label" not in table.c or "sentiment_score" not in table.c:
            return {"processed": 0, "updated": 0, "failed": 0}

        text_columns = [table.c[column] for column in spec.text_columns if column in table.c]
        if not text_columns:
            return {"processed": 0, "updated": 0, "failed": 0}

        date_column_names = date_columns_for_table(spec.table)
        date_columns = [table.c[column] for column in date_column_names if column in table.c]
        selected_columns = [table.c.id, *text_columns, *date_columns]
        statement = (
            select(*_unique_columns(selected_columns))
            .where(
                and_(
                    or_(
                        table.c.sentiment_label.is_(None),
                        table.c.sentiment_label == "",
                        table.c.sentiment_score.is_(None),
                    ),
                    or_(*(column.is_not(None) for column in text_columns)),
                    self._touched_clause(table),
                )
            )
            .order_by(table.c.id.desc())
            .limit(self.batch_size * 5 if self.date_range else self.batch_size)
        )
        rows = conn.execute(statement).mappings().all()
        if self.date_range and date_column_names:
            start, end = self.date_range
            rows = [
                row
                for row in rows
                if row_matches_date_range(row, date_column_names, start, end)
            ][: self.batch_size]
        if not rows:
            return {"processed": 0, "updated": 0, "failed": 0}

        texts: list[str] = []
        row_ids: list[Any] = []
        for row in rows:
            text = self._row_text(row, spec.text_columns)
            if not text:
                continue
            row_ids.append(row["id"])
            texts.append(text)
        if not texts:
            return {"processed": len(rows), "updated": 0, "failed": len(rows)}

        batch_result = self._analyze(texts)
        result_items = getattr(batch_result, "results", [])
        if not getattr(batch_result, "analysis_performed", False):
            return {"processed": len(texts), "updated": 0, "failed": len(texts)}

        now_ms = int(time.time() * 1000)
        updated = 0
        failed = 0
        for row_id, result in zip(row_ids, result_items):
            if not getattr(result, "success", False):
                failed += 1
                continue
            label = _normalize_sentiment(getattr(result, "sentiment_label", None))
            if label == "unknown":
                failed += 1
                continue
            values = {
                "sentiment_label": label,
                "sentiment_score": _signed_score(label, getattr(result, "confidence", 0.0)),
                "sentiment_analyzed_at": now_ms,
            }
            conn.execute(update(table).where(table.c.id == row_id).values(**values))
            updated += 1

        return {"processed": len(texts), "updated": updated, "failed": failed}

    @staticmethod
    def _row_text(row: dict[str, Any], text_columns: tuple[str, ...]) -> str:
        for column in text_columns:
            value = row.get(column)
            if value and str(value).strip():
                return str(value).strip()
        return ""

    def _analyze(self, texts: list[str]) -> Any:
        analyzer = self._get_analyzer()
        if not getattr(analyzer, "is_initialized", False) and not getattr(analyzer, "is_disabled", False):
            analyzer.initialize()
        return analyzer.analyze_batch(texts, show_progress=False)

    def _touched_clause(self, table: Table) -> Any:
        if self.touched_since_ms is None:
            return True
        columns = [
            table.c[column]
            for column in ("last_modify_ts", "add_ts")
            if column in table.c
        ]
        if not columns:
            return False
        return or_(*(column >= self.touched_since_ms for column in columns))

    def _get_analyzer(self) -> Any:
        if self._analyzer is not None:
            return self._analyzer
        if self.analyzer_factory:
            self._analyzer = self.analyzer_factory()
            return self._analyzer
        from InsightEngine.tools.sentiment_analyzer import WeiboMultilingualSentimentAnalyzer

        self._analyzer = WeiboMultilingualSentimentAnalyzer()
        return self._analyzer


def run_crawler_sentiment_postprocessing(
    platform: str,
    *,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    touched_since_ms: int | None = None,
) -> dict[str, Any]:
    if os.getenv("CRAWLER_SENTIMENT_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return {"processed": 0, "updated": 0, "failed": 0, "disabled": True, "tables": {}}
    try:
        return CrawlerSentimentPostProcessor(
            start_date=start_date,
            end_date=end_date,
            touched_since_ms=touched_since_ms,
        ).run_for_platform(platform)
    except Exception as exc:
        logger.exception(f"爬虫情绪后处理失败: {exc}")
        return {
            "processed": 0,
            "updated": 0,
            "failed": 0,
            "error": str(exc),
            "tables": {},
        }
