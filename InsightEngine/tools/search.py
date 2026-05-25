"""
专为 AI Agent 设计的本地舆情数据库查询工具集 (MediaCrawlerDB)。

查询结果直接携带爬虫表内的 sentiment_label / sentiment_score 字段；
InsightEngine 不再在查询阶段调用情感模型做二次分析。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from loguru import logger

from ..utils.db import fetch_all
from InsightEngine.utils.config import settings


@dataclass
class QueryResult:
    """统一的数据库查询结果数据类。"""

    platform: str
    content_type: str
    title_or_content: str
    author_nickname: Optional[str] = None
    url: Optional[str] = None
    publish_time: Optional[datetime] = None
    engagement: Dict[str, int] = field(default_factory=dict)
    source_keyword: Optional[str] = None
    hotness_score: float = 0.0
    source_table: str = ""
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_analyzed_at: Optional[Any] = None


@dataclass
class DBResponse:
    """封装工具的完整返回结果。"""

    tool_name: str
    parameters: Dict[str, Any]
    results: List[QueryResult] = field(default_factory=list)
    results_count: int = 0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


CONTENT_TABLE_CONFIGS: dict[str, dict[str, Any]] = {
    "bilibili_video": {
        "platform": "bilibili",
        "type": "video",
        "fields": ("title", "desc", "source_keyword"),
        "text_fields": ("title", "desc"),
        "author": "nickname",
        "url": "video_url",
        "time_col": "create_time",
        "time_type": "sec",
        "hotness": (
            ("liked_count", 1.0),
            ("video_comment", 5.0),
            ("video_share_count", 10.0),
            ("video_favorite_count", 10.0),
            ("video_coin_count", 10.0),
            ("video_danmaku", 0.5),
            ("video_play_count", 0.1),
        ),
    },
    "douyin_aweme": {
        "platform": "douyin",
        "type": "video",
        "fields": ("title", "desc", "source_keyword"),
        "text_fields": ("title", "desc"),
        "author": "nickname",
        "url": "aweme_url",
        "time_col": "create_time",
        "time_type": "ms",
        "hotness": (
            ("liked_count", 1.0),
            ("comment_count", 5.0),
            ("share_count", 10.0),
            ("collected_count", 10.0),
        ),
    },
    "kuaishou_video": {
        "platform": "kuaishou",
        "type": "video",
        "fields": ("title", "desc", "source_keyword"),
        "text_fields": ("title", "desc"),
        "author": "nickname",
        "url": "video_url",
        "time_col": "create_time",
        "time_type": "ms",
        "hotness": (("liked_count", 1.0), ("viewd_count", 0.1)),
    },
    "weibo_note": {
        "platform": "weibo",
        "type": "note",
        "fields": ("content", "source_keyword"),
        "text_fields": ("content",),
        "author": "nickname",
        "url": "note_url",
        "time_col": "create_date_time",
        "time_type": "str",
        "hotness": (("liked_count", 1.0), ("comments_count", 5.0), ("shared_count", 10.0)),
    },
    "xhs_note": {
        "platform": "xhs",
        "type": "note",
        "fields": ("title", "desc", "tag_list", "source_keyword"),
        "text_fields": ("title", "desc"),
        "author": "nickname",
        "url": "note_url",
        "time_col": "time",
        "time_type": "ms",
        "hotness": (
            ("liked_count", 1.0),
            ("comment_count", 5.0),
            ("share_count", 10.0),
            ("collected_count", 10.0),
        ),
    },
    "zhihu_content": {
        "platform": "zhihu",
        "type": "content",
        "fields": ("title", "desc", "content_text", "source_keyword"),
        "text_fields": ("title", "desc", "content_text"),
        "author": "user_nickname",
        "url": "content_url",
        "time_col": "created_time",
        "time_type": "sec_str",
        "hotness": (("voteup_count", 1.0), ("comment_count", 5.0)),
    },
    "tieba_note": {
        "platform": "tieba",
        "type": "note",
        "fields": ("title", "desc", "source_keyword"),
        "text_fields": ("title", "desc"),
        "author": "user_nickname",
        "url": "note_url",
        "time_col": "publish_time",
        "time_type": "str",
        "hotness": (("total_replay_num", 5.0),),
    },
}

COMMENT_TABLE_CONFIGS: dict[str, dict[str, Any]] = {
    "bilibili_video_comment": {
        "platform": "bilibili",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "nickname",
        "time_col": "create_time",
        "time_type": "sec",
        "like_col": "like_count",
    },
    "douyin_aweme_comment": {
        "platform": "douyin",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "nickname",
        "time_col": "create_time",
        "time_type": "ms",
        "like_col": "like_count",
    },
    "kuaishou_video_comment": {
        "platform": "kuaishou",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "nickname",
        "time_col": "create_time",
        "time_type": "ms",
        "like_col": None,
    },
    "weibo_note_comment": {
        "platform": "weibo",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "nickname",
        "time_col": "create_date_time",
        "time_type": "str",
        "like_col": "comment_like_count",
    },
    "xhs_note_comment": {
        "platform": "xhs",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "nickname",
        "time_col": "create_time",
        "time_type": "ms",
        "like_col": "like_count",
    },
    "zhihu_comment": {
        "platform": "zhihu",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "user_nickname",
        "time_col": "publish_time",
        "time_type": "sec_str",
        "like_col": "like_count",
    },
    "tieba_comment": {
        "platform": "tieba",
        "type": "comment",
        "fields": ("content",),
        "text_fields": ("content",),
        "author": "user_nickname",
        "time_col": "publish_time",
        "time_type": "str",
        "like_col": None,
    },
}

DAILY_NEWS_CONFIG = {
    "platform": "news",
    "type": "news",
    "fields": ("title",),
    "text_fields": ("title",),
    "author": None,
    "url": "url",
    "time_col": "crawl_date",
    "time_type": "date_str",
}


class MediaCrawlerDB:
    """包含多种专用舆情数据库查询工具的客户端。"""

    def __init__(self):
        self._table_columns_cache: dict[str, list[str]] = {}

    def _execute_query(self, query: str, params: Optional[dict[str, Any]] = None) -> List[Dict[str, Any]]:
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(fetch_all(query, params or {}))
        except Exception as exc:
            logger.exception(f"数据库查询时发生错误: {exc}")
            return []

    @property
    def dialect(self) -> str:
        dialect = (settings.DB_DIALECT or "").lower()
        if dialect == "postgres":
            return "postgresql"
        return dialect

    def _quote(self, identifier: str) -> str:
        quote = "`" if self.dialect in {"mysql", "mariadb"} else '"'
        return f"{quote}{identifier}{quote}"

    def _cast_text(self, column: str) -> str:
        quoted = self._quote(column)
        if self.dialect == "postgresql":
            return f"CAST({quoted} AS TEXT)"
        if self.dialect in {"mysql", "mariadb"}:
            return f"CAST({quoted} AS CHAR)"
        return f"CAST({quoted} AS TEXT)"

    def _numeric_expr(self, column: str) -> str:
        text_value = f"NULLIF({self._cast_text(column)}, '')"
        if self.dialect == "postgresql":
            return f"COALESCE(CAST({text_value} AS DOUBLE PRECISION), 0)"
        if self.dialect in {"mysql", "mariadb"}:
            return f"COALESCE(CAST({text_value} AS DECIMAL(20,2)), 0)"
        return f"COALESCE(CAST({text_value} AS REAL), 0)"

    def _like_clause(self, column: str, param_name: str) -> str:
        return f"{self._cast_text(column)} LIKE :{param_name}"

    def _text_expr(self, column: str) -> str:
        return f"NULLIF({self._cast_text(column)}, '')"

    def _coalesce_text_expr(self, columns: tuple[str, ...], existing: set[str]) -> str:
        parts = [self._text_expr(column) for column in columns if column in existing]
        if not parts:
            return "''"
        return f"COALESCE({', '.join(parts)}, '')"

    def _optional_select(self, column: str, alias: str, existing: set[str]) -> str:
        if column in existing:
            return f"{self._quote(column)} AS {self._quote(alias)}"
        return f"NULL AS {self._quote(alias)}"

    def _first_existing_numeric(self, columns: tuple[str, ...], existing: set[str]) -> str:
        for column in columns:
            if column and column in existing:
                return self._numeric_expr(column)
        return "0"

    def _get_table_columns(self, table_name: str) -> list[str]:
        if table_name in self._table_columns_cache:
            return self._table_columns_cache[table_name]
        if self.dialect == "postgresql":
            rows = self._execute_query(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = :table_name
                """,
                {"table_name": table_name},
            )
            columns = [row["column_name"] for row in rows]
        elif self.dialect in {"mysql", "mariadb"}:
            rows = self._execute_query(f"SHOW COLUMNS FROM {self._quote(table_name)}")
            columns = [row["Field"] for row in rows]
        else:
            rows = self._execute_query(f"PRAGMA table_info({self._quote(table_name)})")
            columns = [row["name"] for row in rows]
        self._table_columns_cache[table_name] = columns
        return columns

    @staticmethod
    def _to_datetime(ts: Any) -> Optional[datetime]:
        if not ts:
            return None
        try:
            if isinstance(ts, datetime):
                return ts
            if isinstance(ts, date):
                return datetime.combine(ts, datetime.min.time())
            if isinstance(ts, (int, float)) or str(ts).isdigit():
                val = float(ts)
                return datetime.fromtimestamp(val / 1000 if val > 1_000_000_000_000 else val)
            if isinstance(ts, str):
                return datetime.fromisoformat(ts.split("+")[0].strip())
        except (ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _normalize_sentiment(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"positive", "pos", "正面", "正向", "积极", "非常正面"}:
            return "positive"
        if text in {"negative", "neg", "负面", "负向", "消极", "非常负面"}:
            return "negative"
        if text in {"neutral", "neu", "中性", "一般", "客观"}:
            return "neutral"
        return "unknown"

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _time_param_value(self, dt: datetime, time_type: str) -> Any:
        if time_type == "ms":
            return int(dt.timestamp() * 1000)
        if time_type in {"sec", "sec_str"}:
            return int(dt.timestamp())
        return dt.strftime("%Y-%m-%d")

    def _time_clause(
        self,
        column: str,
        time_type: str,
        start_param: str,
        end_param: str | None = None,
    ) -> str:
        quoted = self._quote(column)
        if time_type == "sec_str":
            left = self._numeric_expr(column)
        else:
            left = quoted
        if end_param:
            return f"{left} >= :{start_param} AND {left} < :{end_param}"
        return f"{left} >= :{start_param}"

    def _extract_engagement(self, row: Dict[str, Any]) -> Dict[str, int]:
        engagement = {}
        mapping = {
            "likes": ("likes", "liked_count", "like_count", "voteup_count", "comment_like_count"),
            "comments": ("comments", "video_comment", "comments_count", "comment_count", "total_replay_num", "sub_comment_count"),
            "shares": ("shares", "video_share_count", "shared_count", "share_count", "total_forwards"),
            "views": ("views", "video_play_count", "viewd_count"),
            "favorites": ("favorites", "video_favorite_count", "collected_count"),
            "coins": ("coins", "video_coin_count"),
            "danmaku": ("danmaku", "video_danmaku"),
        }
        for key, potential_cols in mapping.items():
            for col in potential_cols:
                if col in row and row[col] is not None:
                    try:
                        engagement[key] = int(row[col])
                    except (ValueError, TypeError):
                        engagement[key] = 0
                    break
        return engagement

    def _hot_content_select(self, table: str, config: dict[str, Any], existing: set[str], formula: str) -> str:
        metrics = {
            "likes": ("liked_count", "like_count", "voteup_count", "comment_like_count"),
            "comments": ("video_comment", "comments_count", "comment_count", "total_replay_num", "sub_comment_count"),
            "shares": ("video_share_count", "shared_count", "share_count", "total_forwards"),
            "views": ("video_play_count", "viewd_count"),
            "favorites": ("video_favorite_count", "collected_count"),
            "coins": ("video_coin_count",),
            "danmaku": ("video_danmaku",),
        }
        metric_selects = [
            f"{self._first_existing_numeric(columns, existing)} AS {self._quote(alias)}"
            for alias, columns in metrics.items()
        ]
        author_select = (
            f"{self._quote(config['author'])} AS {self._quote('author_nickname')}"
            if config.get("author") in existing
            else f"NULL AS {self._quote('author_nickname')}"
        )
        url_select = (
            f"{self._quote(config['url'])} AS {self._quote('url')}"
            if config.get("url") in existing
            else f"NULL AS {self._quote('url')}"
        )
        source_keyword_select = (
            f"{self._quote('source_keyword')} AS {self._quote('source_keyword')}"
            if "source_keyword" in existing
            else f"NULL AS {self._quote('source_keyword')}"
        )
        return ", ".join(
            [
                f"'{config['platform']}' AS {self._quote('platform')}",
                f"'{config['type']}' AS {self._quote('content_type')}",
                f"{self._coalesce_text_expr(config['text_fields'], existing)} AS {self._quote('title_or_content')}",
                author_select,
                url_select,
                (
                    f"{self._quote(config['time_col'])} AS {self._quote('publish_ts')}"
                    if config.get("time_col") in existing
                    else f"NULL AS {self._quote('publish_ts')}"
                ),
                f"({formula}) AS {self._quote('hotness_score')}",
                source_keyword_select,
                f"'{table}' AS {self._quote('source_table')}",
                self._optional_select("sentiment_label", "sentiment_label", existing),
                self._optional_select("sentiment_score", "sentiment_score", existing),
                self._optional_select("sentiment_analyzed_at", "sentiment_analyzed_at", existing),
                *metric_selects,
            ]
        )

    def _comment_union_select(self, table: str, config: dict[str, Any], existing: set[str]) -> str:
        like_expr = self._first_existing_numeric((config.get("like_col") or "",), existing)
        return ", ".join(
            [
                f"'{config['platform']}' AS {self._quote('platform')}",
                f"'{config['type']}' AS {self._quote('content_type')}",
                f"{self._coalesce_text_expr(config['text_fields'], existing)} AS {self._quote('title_or_content')}",
                (
                    f"{self._quote(config['author'])} AS {self._quote('author_nickname')}"
                    if config.get("author") in existing
                    else f"NULL AS {self._quote('author_nickname')}"
                ),
                f"NULL AS {self._quote('url')}",
                (
                    f"{self._quote(config['time_col'])} AS {self._quote('publish_ts')}"
                    if config.get("time_col") in existing
                    else f"NULL AS {self._quote('publish_ts')}"
                ),
                f"0 AS {self._quote('hotness_score')}",
                f"NULL AS {self._quote('source_keyword')}",
                f"'{table}' AS {self._quote('source_table')}",
                self._optional_select("sentiment_label", "sentiment_label", existing),
                self._optional_select("sentiment_score", "sentiment_score", existing),
                self._optional_select("sentiment_analyzed_at", "sentiment_analyzed_at", existing),
                f"{like_expr} AS {self._quote('likes')}",
                f"0 AS {self._quote('comments')}",
                f"0 AS {self._quote('shares')}",
                f"0 AS {self._quote('views')}",
                f"0 AS {self._quote('favorites')}",
                f"0 AS {self._quote('coins')}",
                f"0 AS {self._quote('danmaku')}",
            ]
        )

    def _row_to_result(
        self,
        table: str,
        config: dict[str, Any],
        row: dict[str, Any],
        *,
        hotness_score: float = 0.0,
    ) -> QueryResult:
        content = row.get("title_or_content") or ""
        for field_name in config.get("text_fields", ()):
            if not content and row.get(field_name):
                content = str(row[field_name])
                break
        time_col = config.get("time_col")
        author_col = config.get("author")
        url_col = config.get("url")
        label = self._normalize_sentiment(row.get("sentiment_label") or row.get("sentiment"))
        score = self._to_float(row.get("sentiment_score"))
        return QueryResult(
            platform=config["platform"],
            content_type=config["type"],
            title_or_content=content,
            author_nickname=row.get("author_nickname") or (row.get(author_col) if author_col else None),
            url=row.get("url") or (row.get(url_col) if url_col else None),
            publish_time=self._to_datetime(row.get("publish_ts") or row.get(time_col)) if time_col else None,
            engagement=self._extract_engagement(row),
            source_keyword=row.get("source_keyword"),
            hotness_score=hotness_score,
            source_table=table,
            sentiment_label=label,
            sentiment_score=score,
            sentiment_analyzed_at=row.get("sentiment_analyzed_at"),
        )

    def _select_all_for_table(
        self,
        table: str,
        where_clause: str,
        params: dict[str, Any],
        *,
        limit: int,
        order_col: str = "id",
    ) -> list[dict[str, Any]]:
        query = (
            f"SELECT * FROM {self._quote(table)} "
            f"WHERE {where_clause} "
            f"ORDER BY {self._quote(order_col)} DESC LIMIT :limit"
        )
        return self._execute_query(query, {**params, "limit": limit})

    def search_hot_content(
        self,
        time_period: Literal["24h", "week", "year"] = "week",
        limit: int = 50,
    ) -> DBResponse:
        params_for_log = {"time_period": time_period, "limit": limit}
        logger.info(f"--- TOOL: 查找热点内容 (params: {params_for_log}) ---")

        now = datetime.now()
        start_time = now - timedelta(days={"24h": 1, "week": 7}.get(time_period, 365))

        all_queries: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        for index, (table, config) in enumerate(CONTENT_TABLE_CONFIGS.items()):
            cols = set(self._get_table_columns(table))
            time_col = config["time_col"]
            if time_col not in cols:
                continue
            terms = [
                f"{self._numeric_expr(column)} * {weight}"
                for column, weight in config.get("hotness", ())
                if column in cols
            ]
            formula = " + ".join(terms) if terms else "0"
            start_param = f"start_{index}"
            params[start_param] = self._time_param_value(start_time, config["time_type"])
            select_list = self._hot_content_select(table, config, cols, formula)
            all_queries.append(
                f"SELECT {select_list} "
                f"FROM {self._quote(table)} "
                f"WHERE {self._time_clause(time_col, config['time_type'], start_param)}"
            )

        if not all_queries:
            return DBResponse("search_hot_content", params_for_log, results=[], results_count=0)

        final_query = f"SELECT * FROM ({' UNION ALL '.join(all_queries)}) AS hot_items ORDER BY hotness_score DESC LIMIT :limit"
        raw_results = self._execute_query(final_query, params)
        results = []
        for row in raw_results:
            source_table = row.get("source_table")
            config = CONTENT_TABLE_CONFIGS.get(source_table)
            if config:
                results.append(
                    self._row_to_result(
                        source_table,
                        config,
                        row,
                        hotness_score=self._to_float(row.get("hotness_score")) or 0.0,
                    )
                )
        return DBResponse("search_hot_content", params_for_log, results=results, results_count=len(results))

    def search_topic_globally(self, topic: str, limit_per_table: int = 100) -> DBResponse:
        params_for_log = {"topic": topic, "limit_per_table": limit_per_table}
        logger.info(f"--- TOOL: 全局话题搜索 (params: {params_for_log}) ---")

        all_results: list[QueryResult] = []
        configs = {**CONTENT_TABLE_CONFIGS, **COMMENT_TABLE_CONFIGS, "daily_news": DAILY_NEWS_CONFIG}
        for table, config in configs.items():
            cols = set(self._get_table_columns(table))
            fields = [field for field in config["fields"] if field in cols]
            if not fields:
                continue
            params = {f"term_{idx}": f"%{topic}%" for idx, _ in enumerate(fields)}
            where_clause = " OR ".join(self._like_clause(field, f"term_{idx}") for idx, field in enumerate(fields))
            order_col = "id" if "id" in cols else config.get("time_col") or fields[0]
            rows = self._select_all_for_table(table, where_clause, params, limit=limit_per_table, order_col=order_col)
            all_results.extend(self._row_to_result(table, config, row) for row in rows)

        return DBResponse("search_topic_globally", params_for_log, results=all_results, results_count=len(all_results))

    def search_topic_by_date(
        self,
        topic: str,
        start_date: str,
        end_date: str,
        limit_per_table: int = 100,
    ) -> DBResponse:
        params_for_log = {
            "topic": topic,
            "start_date": start_date,
            "end_date": end_date,
            "limit_per_table": limit_per_table,
        }
        logger.info(f"--- TOOL: 按日期搜索话题 (params: {params_for_log}) ---")
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            return DBResponse("search_topic_by_date", params_for_log, error_message="日期格式错误，请使用 'YYYY-MM-DD' 格式。")

        all_results: list[QueryResult] = []
        configs = {**CONTENT_TABLE_CONFIGS, "daily_news": DAILY_NEWS_CONFIG}
        for table, config in configs.items():
            cols = set(self._get_table_columns(table))
            fields = [field for field in config["fields"] if field in cols]
            time_col = config.get("time_col")
            if not fields or not time_col or time_col not in cols:
                continue
            params = {f"term_{idx}": f"%{topic}%" for idx, _ in enumerate(fields)}
            params["start"] = self._time_param_value(start_dt, config["time_type"])
            params["end"] = self._time_param_value(end_dt, config["time_type"])
            topic_clause = " OR ".join(self._like_clause(field, f"term_{idx}") for idx, field in enumerate(fields))
            where_clause = f"({topic_clause}) AND ({self._time_clause(time_col, config['time_type'], 'start', 'end')})"
            rows = self._select_all_for_table(table, where_clause, params, limit=limit_per_table)
            all_results.extend(self._row_to_result(table, config, row) for row in rows)

        return DBResponse("search_topic_by_date", params_for_log, results=all_results, results_count=len(all_results))

    def get_comments_for_topic(self, topic: str, limit: int = 500) -> DBResponse:
        params_for_log = {"topic": topic, "limit": limit}
        logger.info(f"--- TOOL: 获取话题评论 (params: {params_for_log}) ---")

        all_queries: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        for index, (table, config) in enumerate(COMMENT_TABLE_CONFIGS.items()):
            cols = set(self._get_table_columns(table))
            if "content" not in cols:
                continue
            term_param = f"term_{index}"
            params[term_param] = f"%{topic}%"
            select_list = self._comment_union_select(table, config, cols)
            all_queries.append(
                f"SELECT {select_list} FROM {self._quote(table)} "
                f"WHERE {self._like_clause('content', term_param)}"
            )
        if not all_queries:
            return DBResponse("get_comments_for_topic", params_for_log, results=[], results_count=0)

        final_query = f"SELECT * FROM ({' UNION ALL '.join(all_queries)}) AS comments ORDER BY {self._quote('publish_ts')} DESC LIMIT :limit"
        rows = self._execute_query(final_query, params)
        results = []
        for row in rows:
            table = row.get("source_table")
            config = COMMENT_TABLE_CONFIGS.get(table)
            if config:
                results.append(self._row_to_result(table, config, row))
        return DBResponse("get_comments_for_topic", params_for_log, results=results, results_count=len(results))

    def search_topic_on_platform(
        self,
        platform: Literal["bilibili", "weibo", "douyin", "kuaishou", "xhs", "zhihu", "tieba"],
        topic: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
    ) -> DBResponse:
        params_for_log = {
            "platform": platform,
            "topic": topic,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
        }
        logger.info(f"--- TOOL: 平台定向搜索 (params: {params_for_log}) ---")
        configs = {
            table: config
            for table, config in {**CONTENT_TABLE_CONFIGS, **COMMENT_TABLE_CONFIGS}.items()
            if config["platform"] == platform
        }
        if not configs:
            return DBResponse("search_topic_on_platform", params_for_log, error_message=f"不支持的平台: {platform}")

        start_dt = end_dt = None
        if start_date and end_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                return DBResponse("search_topic_on_platform", params_for_log, error_message="日期格式错误，请使用 'YYYY-MM-DD' 格式。")

        all_results: list[QueryResult] = []
        for table, config in configs.items():
            cols = set(self._get_table_columns(table))
            fields = [field for field in config["fields"] if field in cols]
            if not fields:
                continue
            params = {f"term_{idx}": f"%{topic}%" for idx, _ in enumerate(fields)}
            topic_clause = " OR ".join(self._like_clause(field, f"term_{idx}") for idx, field in enumerate(fields))
            where_clause = f"({topic_clause})"
            time_col = config.get("time_col")
            if start_dt and end_dt and time_col in cols:
                params["start"] = self._time_param_value(start_dt, config["time_type"])
                params["end"] = self._time_param_value(end_dt, config["time_type"])
                where_clause += f" AND ({self._time_clause(time_col, config['time_type'], 'start', 'end')})"
            rows = self._select_all_for_table(table, where_clause, params, limit=limit)
            all_results.extend(self._row_to_result(table, config, row) for row in rows)

        return DBResponse("search_topic_on_platform", params_for_log, results=all_results, results_count=len(all_results))


def print_response_summary(response: DBResponse):
    """简化的打印函数，用于展示测试结果。"""
    if response.error_message:
        logger.info(f"工具 '{response.tool_name}' 执行出错: {response.error_message}")
        return

    params_str = ", ".join(f"{k}='{v}'" for k, v in response.parameters.items())
    logger.info(f"查询: 工具='{response.tool_name}', 参数=[{params_str}]")
    logger.info(f"找到 {response.results_count} 条相关记录。")

    output_lines = ["==== 查询结果预览（最多前5条） ===="]
    if response.results:
        for idx, res in enumerate(response.results[:5], 1):
            content_preview = (
                res.title_or_content.replace("\n", " ")[:70] + "..."
                if res.title_or_content and len(res.title_or_content) > 70
                else (res.title_or_content or "")
            )
            author_str = res.author_nickname or "N/A"
            publish_time_str = res.publish_time.strftime("%Y-%m-%d %H:%M") if res.publish_time else "N/A"
            hotness_str = f", hotness: {res.hotness_score:.2f}" if res.hotness_score > 0 else ""
            engagement_str = ", ".join(f"{k}: {v}" for k, v in (res.engagement or {}).items() if v)
            sentiment_str = res.sentiment_label or "unknown"
            output_lines.append(
                f"{idx}. [{res.platform.upper()}/{res.content_type}] {content_preview}\n"
                f"   作者: {author_str} | 时间: {publish_time_str}"
                f"{hotness_str} | 情绪: {sentiment_str} | 源关键词: '{res.source_keyword or 'N/A'}'\n"
                f"   链接: {res.url or 'N/A'}\n"
                f"   互动数据: {{{engagement_str}}}"
            )
    else:
        output_lines.append("暂无相关内容。")
    output_lines.append("=" * 60)
    logger.info("\n".join(output_lines))
