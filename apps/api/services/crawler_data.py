"""Read-only search over crawler persistence tables."""

from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote_plus
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.api.schemas import ApiError, PLATFORM_IDS
from apps.api.storage import Store


@dataclass(frozen=True)
class TableSpec:
    platform: str
    content_type: str
    table: str
    source_id: str
    title: str | None = None
    body: str | None = None
    author: str | None = None
    url: str | None = None
    keyword: str | None = None
    created_at: str | None = None
    scraped_at: str | None = "add_ts"
    like_count: str | None = None
    comment_count: str | None = None


TABLE_SPECS = (
    TableSpec("xhs", "content", "xhs_note", "note_id", "title", "desc", "nickname", "note_url", "source_keyword", "time", "add_ts", "liked_count", "comment_count"),
    TableSpec("xhs", "comment", "xhs_note_comment", "comment_id", None, "content", "nickname", None, None, "create_time", "add_ts", "like_count", "sub_comment_count"),
    TableSpec("dy", "content", "douyin_aweme", "aweme_id", "title", "desc", "nickname", "aweme_url", "source_keyword", "create_time", "add_ts", "liked_count", "comment_count"),
    TableSpec("dy", "comment", "douyin_aweme_comment", "comment_id", None, "content", "nickname", None, None, "create_time", "add_ts", "like_count", "sub_comment_count"),
    TableSpec("ks", "content", "kuaishou_video", "video_id", "title", "desc", "nickname", "video_url", "source_keyword", "create_time", "add_ts", "liked_count", None),
    TableSpec("ks", "comment", "kuaishou_video_comment", "comment_id", None, "content", "nickname", None, None, "create_time", "add_ts", None, "sub_comment_count"),
    TableSpec("bili", "content", "bilibili_video", "video_id", "title", "desc", "nickname", "video_url", "source_keyword", "create_time", "add_ts", "liked_count", "video_comment"),
    TableSpec("bili", "comment", "bilibili_video_comment", "comment_id", None, "content", "nickname", None, None, "create_time", "add_ts", "like_count", "sub_comment_count"),
    TableSpec("wb", "content", "weibo_note", "note_id", None, "content", "nickname", "note_url", "source_keyword", "create_time", "add_ts", "liked_count", "comments_count"),
    TableSpec("wb", "comment", "weibo_note_comment", "comment_id", None, "content", "nickname", None, None, "create_time", "add_ts", "comment_like_count", "sub_comment_count"),
    TableSpec("tieba", "content", "tieba_note", "note_id", "title", "desc", "user_nickname", "note_url", "source_keyword", "publish_time", "add_ts", None, "total_replay_num"),
    TableSpec("tieba", "comment", "tieba_comment", "comment_id", None, "content", "user_nickname", "note_url", None, "publish_time", "add_ts", None, "sub_comment_count"),
    TableSpec("zhihu", "content", "zhihu_content", "content_id", "title", "content_text", "user_nickname", "content_url", "source_keyword", "created_time", "add_ts", "voteup_count", "comment_count"),
    TableSpec("zhihu", "comment", "zhihu_comment", "comment_id", None, "content", "user_nickname", None, None, "publish_time", "add_ts", "like_count", "sub_comment_count"),
)

SENTIMENT_COLUMNS = (
    "sentiment",
    "sentiment_label",
    "sentiment_type",
    "emotion",
    "emotion_label",
    "polarity",
)
SENTIMENT_SCORE_COLUMNS = (
    "sentiment_score",
    "emotion_score",
    "polarity_score",
    "score",
)
POSITIVE_SENTIMENTS = {"positive", "pos", "+", "1", "正向", "正面", "积极", "支持", "满意", "赞同", "利好"}
NEGATIVE_SENTIMENTS = {"negative", "neg", "-", "-1", "负向", "负面", "消极", "反对", "不满", "愤怒", "利空"}
NEUTRAL_SENTIMENTS = {"neutral", "neu", "0", "中性", "一般", "客观"}


class CrawlerDataService:
    def __init__(self, store: Store, repo_root: str | Path):
        self.store = store
        self.repo_root = Path(repo_root)

    def list_records(
        self,
        *,
        platform: str | None = None,
        content_type: str | None = None,
        query: str | None = None,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if platform and platform not in PLATFORM_IDS:
            raise ApiError("VALIDATION_ERROR", f"Unsupported platform: {platform}", status_code=400)
        if content_type and content_type not in {"content", "comment"}:
            raise ApiError("VALIDATION_ERROR", "Unsupported crawler data type", status_code=400)

        specs = [
            spec
            for spec in TABLE_SPECS
            if (not platform or spec.platform == platform)
            and (not content_type or spec.content_type == content_type)
        ]

        records: list[dict[str, Any]] = []
        sources: list[str] = []
        messages: list[str] = []

        source = self._sqlite_source()
        if source:
            records.extend(self._query_sqlite(source, specs, query, page_size))
            sources.append(str(source))

        try:
            external_records, external_source = self._query_external(specs, query, page_size)
            if external_source:
                sources.append(external_source)
            records.extend(external_records)
        except Exception as exc:
            messages.append(f"外部爬取数据库读取失败: {exc}")

        if not sources:
            return {
                "records": [],
                "summary": self._summary([]),
                "source": "unavailable",
                "message": "未发现可读取的爬取数据库",
            }

        records.sort(key=lambda item: item.get("sortValue") or "", reverse=True)
        for record in records:
            record.pop("sortValue", None)
        return {
            "records": records[:page_size],
            "summary": self._summary(records),
            "source": ", ".join(sources),
            **({"message": "；".join(messages)} if messages else {}),
        }

    def _sqlite_source(self) -> Path | None:
        candidates: list[Path] = []
        configured = os.getenv("BETTAFISH_CRAWLER_SQLITE_PATH")
        if configured:
            candidates.append(Path(configured))
        candidates.append(
            self.repo_root
            / "MindSpider"
            / "DeepSentimentCrawling"
            / "MediaCrawler"
            / "database"
            / "sqlite_tables.db"
        )
        if self.store.db_path != ":memory:":
            candidates.append(Path(self.store.db_path))

        for path in candidates:
            if path.exists() and path.is_file():
                return path
        return None

    def _query_sqlite(
        self,
        db_path: Path,
        specs: list[TableSpec],
        query: str | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            table_columns = self._table_columns(conn)
            records: list[dict[str, Any]] = []
            for spec in specs:
                columns = table_columns.get(spec.table)
                if not columns or spec.source_id not in columns:
                    continue
                records.extend(self._query_table(conn, columns, spec, query, page_size))

        records.sort(key=lambda item: item.get("sortValue") or "", reverse=True)
        return records

    def _query_external(
        self,
        specs: list[TableSpec],
        query: str | None,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        db_url = self._external_db_url()
        if not db_url:
            return [], None

        try:
            from sqlalchemy import MetaData, String, Table, cast, create_engine, desc, inspect, or_, select
        except ModuleNotFoundError:
            return [], None

        engine = create_engine(db_url, pool_pre_ping=True)
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        metadata = MetaData()
        records: list[dict[str, Any]] = []
        with engine.connect() as conn:
            for spec in specs:
                if spec.table not in table_names:
                    continue
                table = Table(spec.table, metadata, autoload_with=engine)
                columns = set(table.c.keys())
                if spec.source_id not in columns:
                    continue
                selected = sorted(
                    {
                        "id",
                        spec.source_id,
                        *(column for column in (
                            spec.title,
                            spec.body,
                            spec.author,
                            spec.url,
                            spec.keyword,
                            spec.created_at,
                            spec.scraped_at,
                            spec.like_count,
                            spec.comment_count,
                        ) if column),
                        *self._sentiment_columns(columns),
                    }
                    & columns
                )
                if not selected:
                    continue
                stmt = select(*(table.c[column] for column in selected))
                searchable = [
                    column
                    for column in (spec.source_id, spec.title, spec.body, spec.author, spec.keyword)
                    if column and column in columns
                ]
                if query and searchable:
                    stmt = stmt.where(
                        or_(*(cast(table.c[column], String).like(f"%{query}%") for column in searchable))
                    )
                order_column = next(
                    (column for column in (spec.scraped_at, spec.created_at, "id") if column and column in columns),
                    selected[0],
                )
                stmt = stmt.order_by(desc(table.c[order_column])).limit(page_size)
                rows = conn.execute(stmt).mappings().all()
                records.extend(self._record_from_row(spec, dict(row)) for row in rows)
        engine.dispose()
        return records, self._masked_db_url(db_url)

    @staticmethod
    def _external_db_url() -> str | None:
        configured = os.getenv("BETTAFISH_CRAWLER_DB_URL")
        if configured:
            return configured

        try:
            from config import reload_settings, settings

            reload_settings()
            dialect = (settings.DB_DIALECT or "").lower()
            host = settings.DB_HOST
            user = settings.DB_USER
            password = settings.DB_PASSWORD
            name = settings.DB_NAME
            port = settings.DB_PORT
            if not host or host.startswith("your_") or not user or user.startswith("your_") or not name or name.startswith("your_"):
                return None
            if dialect in {"postgres", "postgresql"}:
                return f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(name)}"
            if dialect == "mysql":
                charset = settings.DB_CHARSET or "utf8mb4"
                return f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(name)}?charset={quote_plus(charset)}"
        except Exception:
            return None
        return None

    @staticmethod
    def _masked_db_url(db_url: str) -> str:
        if "://" not in db_url or "@" not in db_url:
            return db_url
        scheme, rest = db_url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"

    @staticmethod
    def _table_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        result = {}
        for row in rows:
            name = row["name"]
            result[name] = {column["name"] for column in conn.execute(f"PRAGMA table_info({name})")}
        return result

    def _query_table(
        self,
        conn: sqlite3.Connection,
        columns: set[str],
        spec: TableSpec,
        query: str | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        selected = sorted(
            {
                "id",
                spec.source_id,
                *(column for column in (
                    spec.title,
                    spec.body,
                    spec.author,
                    spec.url,
                    spec.keyword,
                    spec.created_at,
                    spec.scraped_at,
                    spec.like_count,
                    spec.comment_count,
                ) if column),
                *self._sentiment_columns(columns),
            }
            & columns
        )
        if not selected:
            return []

        where = ""
        params: list[Any] = []
        searchable = [
            column
            for column in (spec.source_id, spec.title, spec.body, spec.author, spec.keyword)
            if column and column in columns
        ]
        if query and searchable:
            where = "WHERE " + " OR ".join(f"CAST({column} AS TEXT) LIKE ?" for column in searchable)
            params.extend([f"%{query}%"] * len(searchable))

        order_column = next(
            (column for column in (spec.scraped_at, spec.created_at, "id") if column and column in columns),
            selected[0],
        )
        params.append(page_size)
        rows = conn.execute(
            f"""
            SELECT {", ".join(selected)}
            FROM {spec.table}
            {where}
            ORDER BY {order_column} DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._record_from_row(spec, dict(row)) for row in rows]

    @staticmethod
    def _sentiment_columns(columns: set[str]) -> set[str]:
        return {
            column
            for column in (*SENTIMENT_COLUMNS, *SENTIMENT_SCORE_COLUMNS)
            if column in columns
        }

    @classmethod
    def _record_from_row(cls, spec: TableSpec, row: dict[str, Any]) -> dict[str, Any]:
        body = row.get(spec.body or "") or ""
        title = row.get(spec.title or "") or ""
        scraped_at = row.get(spec.scraped_at or "") or row.get(spec.created_at or "")
        return {
            "id": f"{spec.table}:{row.get(spec.source_id)}",
            "platformId": spec.platform,
            "contentType": spec.content_type,
            "tableName": spec.table,
            "sourceId": str(row.get(spec.source_id) or ""),
            "title": str(title or body[:48] or row.get(spec.source_id) or ""),
            "textSnippet": str(body or title or "")[:220],
            "author": row.get(spec.author or "") or "",
            "keyword": row.get(spec.keyword or "") or "",
            "url": row.get(spec.url or "") or "",
            "createdAt": row.get(spec.created_at or "") or "",
            "scrapedAt": scraped_at or "",
            "sentiment": cls._extract_sentiment(row),
            "metrics": {
                "likes": row.get(spec.like_count or "") or "",
                "comments": row.get(spec.comment_count or "") or "",
            },
            "sortValue": str(scraped_at or row.get("id") or ""),
        }

    @classmethod
    def _extract_sentiment(cls, row: dict[str, Any]) -> str:
        label = next(
            (
                row.get(column)
                for column in SENTIMENT_COLUMNS
                if row.get(column) not in (None, "")
            ),
            None,
        )
        normalized = cls._normalize_sentiment(label)
        if normalized != "unknown":
            return normalized

        score = next(
            (
                cls._to_float(row.get(column))
                for column in SENTIMENT_SCORE_COLUMNS
                if cls._to_float(row.get(column)) is not None
            ),
            None,
        )
        if score is None:
            return "unknown"
        if score > 0.2:
            return "positive"
        if score < -0.2:
            return "negative"
        return "neutral"

    @staticmethod
    def _normalize_sentiment(value: Any) -> str:
        if value in (None, ""):
            return "unknown"
        text = str(value).strip().lower()
        if text in POSITIVE_SENTIMENTS:
            return "positive"
        if text in NEGATIVE_SENTIMENTS:
            return "negative"
        if text in NEUTRAL_SENTIMENTS:
            return "neutral"
        numeric = CrawlerDataService._to_float(value)
        if numeric is None:
            return "unknown"
        if numeric > 0.2:
            return "positive"
        if numeric < -0.2:
            return "negative"
        return "neutral"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        by_platform: dict[str, int] = {}
        by_type = {"content": 0, "comment": 0}
        for record in records:
            by_platform[record["platformId"]] = by_platform.get(record["platformId"], 0) + 1
            by_type[record["contentType"]] = by_type.get(record["contentType"], 0) + 1
        return {
            "totalRecords": len(records),
            "byPlatform": by_platform,
            "byType": by_type,
        }
