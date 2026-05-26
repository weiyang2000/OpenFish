"""Persistence for the SaaS service layer.

The service layer intentionally owns its own task/config tables instead of
reusing the legacy Flask globals. This gives the new API durable state while
the engine and crawler adapters can evolve independently.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from utils.runtime_database import RuntimeDatabaseConfig, load_runtime_database_config


SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS report_tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT,
    legacy_task_id TEXT,
    topic TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'queued',
    template_id TEXT,
    source_scope_json TEXT NOT NULL DEFAULT '{}',
    output_formats_json TEXT NOT NULL DEFAULT '[]',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    error_json TEXT,
    owner_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_tasks_workspace
    ON report_tasks(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS crawler_tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT,
    strategy_id TEXT,
    run_mode TEXT NOT NULL,
    target_date TEXT,
    start_date TEXT,
    end_date TEXT,
    schedule_json TEXT NOT NULL DEFAULT '{}',
    platforms_json TEXT NOT NULL DEFAULT '[]',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    keyword_source TEXT NOT NULL DEFAULT 'manual',
    crawl_depth INTEGER NOT NULL DEFAULT 3,
    max_notes_per_keyword INTEGER NOT NULL DEFAULT 50,
    max_comments_per_note INTEGER NOT NULL DEFAULT 100,
    login_type TEXT,
    headless INTEGER NOT NULL DEFAULT 1,
    overrides_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stats_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT,
    owner_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_crawler_tasks_workspace
    ON crawler_tasks(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS crawler_accounts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    username TEXT,
    display_name TEXT,
    avatar_url TEXT,
    profile_url TEXT,
    status TEXT NOT NULL,
    login_type TEXT,
    last_login_at TEXT,
    last_checked_at TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, platform_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_crawler_accounts_workspace
    ON crawler_accounts(workspace_id, platform_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS crawler_platform_configs (
    workspace_id TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    crawl_depth INTEGER NOT NULL,
    max_keywords INTEGER NOT NULL,
    max_notes_per_keyword INTEGER NOT NULL,
    max_comments_per_note INTEGER NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    keyword_source TEXT NOT NULL,
    frequency_json TEXT NOT NULL DEFAULT '{}',
    login_type TEXT NOT NULL,
    headless INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by_json TEXT,
    PRIMARY KEY (workspace_id, platform_id)
);

CREATE TABLE IF NOT EXISTS crawler_identity_rules (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    list_type TEXT NOT NULL,
    user_id TEXT NOT NULL,
    label TEXT,
    reason TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    created_by_json TEXT,
    UNIQUE (workspace_id, platform_id, list_type, user_id)
);

CREATE INDEX IF NOT EXISTS idx_identity_rules_platform
    ON crawler_identity_rules(workspace_id, platform_id, list_type);

CREATE TABLE IF NOT EXISTS crawler_strategies (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT,
    name TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    platform_policies_json TEXT NOT NULL DEFAULT '[]',
    owner_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_crawler_strategies_workspace
    ON crawler_strategies(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_configs (
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    updated_by_json TEXT,
    PRIMARY KEY (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task
    ON task_events(workspace_id, task_id, id);

CREATE TABLE IF NOT EXISTS search_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    tenant_id TEXT,
    query TEXT NOT NULL,
    status TEXT NOT NULL,
    engines_json TEXT NOT NULL DEFAULT '[]',
    owner_json TEXT,
    created_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA_SQL = SQLITE_SCHEMA_SQL.replace(
    "id INTEGER PRIMARY KEY AUTOINCREMENT",
    "id BIGSERIAL PRIMARY KEY",
)

MYSQL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS report_tasks (
    id VARCHAR(128) PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    tenant_id VARCHAR(255),
    legacy_task_id VARCHAR(255),
    topic TEXT NOT NULL,
    status VARCHAR(64) NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage VARCHAR(64) NOT NULL DEFAULT 'queued',
    template_id VARCHAR(255),
    source_scope_json LONGTEXT NOT NULL,
    output_formats_json LONGTEXT NOT NULL,
    artifacts_json LONGTEXT NOT NULL,
    error_json LONGTEXT,
    owner_json LONGTEXT,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL
);

CREATE INDEX idx_report_tasks_workspace
    ON report_tasks(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS crawler_tasks (
    id VARCHAR(128) PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    tenant_id VARCHAR(255),
    strategy_id VARCHAR(255),
    run_mode VARCHAR(64) NOT NULL,
    target_date VARCHAR(64),
    start_date VARCHAR(64),
    end_date VARCHAR(64),
    schedule_json LONGTEXT NOT NULL,
    platforms_json LONGTEXT NOT NULL,
    keywords_json LONGTEXT NOT NULL,
    keyword_source VARCHAR(64) NOT NULL DEFAULT 'manual',
    crawl_depth INTEGER NOT NULL DEFAULT 3,
    max_notes_per_keyword INTEGER NOT NULL DEFAULT 50,
    max_comments_per_note INTEGER NOT NULL DEFAULT 100,
    login_type VARCHAR(64),
    headless INTEGER NOT NULL DEFAULT 1,
    overrides_json LONGTEXT NOT NULL,
    status VARCHAR(64) NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stats_json LONGTEXT NOT NULL,
    error_json LONGTEXT,
    owner_json LONGTEXT,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL
);

CREATE INDEX idx_crawler_tasks_workspace
    ON crawler_tasks(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS crawler_accounts (
    id VARCHAR(128) PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    platform_id VARCHAR(64) NOT NULL,
    account_id VARCHAR(255) NOT NULL,
    username VARCHAR(255),
    display_name VARCHAR(255),
    avatar_url TEXT,
    profile_url TEXT,
    status VARCHAR(64) NOT NULL,
    login_type VARCHAR(64),
    last_login_at VARCHAR(64),
    last_checked_at VARCHAR(64),
    details_json LONGTEXT NOT NULL,
    error_json LONGTEXT,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    UNIQUE (workspace_id, platform_id, account_id)
);

CREATE INDEX idx_crawler_accounts_workspace
    ON crawler_accounts(workspace_id, platform_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS crawler_platform_configs (
    workspace_id VARCHAR(255) NOT NULL,
    platform_id VARCHAR(64) NOT NULL,
    enabled INTEGER NOT NULL,
    crawl_depth INTEGER NOT NULL,
    max_keywords INTEGER NOT NULL,
    max_notes_per_keyword INTEGER NOT NULL,
    max_comments_per_note INTEGER NOT NULL,
    keywords_json LONGTEXT NOT NULL,
    keyword_source VARCHAR(64) NOT NULL,
    frequency_json LONGTEXT NOT NULL,
    login_type VARCHAR(64) NOT NULL,
    headless INTEGER NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    updated_by_json LONGTEXT,
    PRIMARY KEY (workspace_id, platform_id)
);

CREATE TABLE IF NOT EXISTS crawler_identity_rules (
    id VARCHAR(128) PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    platform_id VARCHAR(64) NOT NULL,
    list_type VARCHAR(64) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    label VARCHAR(255),
    reason TEXT,
    expires_at VARCHAR(64),
    created_at VARCHAR(64) NOT NULL,
    created_by_json LONGTEXT,
    UNIQUE (workspace_id, platform_id, list_type, user_id)
);

CREATE INDEX idx_identity_rules_platform
    ON crawler_identity_rules(workspace_id, platform_id, list_type);

CREATE TABLE IF NOT EXISTS crawler_strategies (
    id VARCHAR(128) PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    tenant_id VARCHAR(255),
    name VARCHAR(255) NOT NULL,
    run_mode VARCHAR(64) NOT NULL,
    platform_policies_json LONGTEXT NOT NULL,
    owner_json LONGTEXT,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL
);

CREATE INDEX idx_crawler_strategies_workspace
    ON crawler_strategies(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_configs (
    workspace_id VARCHAR(255) NOT NULL,
    `key` VARCHAR(255) NOT NULL,
    value_json LONGTEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,
    updated_at VARCHAR(64) NOT NULL,
    updated_by_json LONGTEXT,
    PRIMARY KEY (workspace_id, `key`)
);

CREATE TABLE IF NOT EXISTS task_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    task_id VARCHAR(255) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload_json LONGTEXT NOT NULL,
    created_at VARCHAR(64) NOT NULL
);

CREATE INDEX idx_task_events_task
    ON task_events(workspace_id, task_id, id);

CREATE TABLE IF NOT EXISTS search_runs (
    id VARCHAR(128) PRIMARY KEY,
    workspace_id VARCHAR(255) NOT NULL,
    tenant_id VARCHAR(255),
    query TEXT NOT NULL,
    status VARCHAR(64) NOT NULL,
    engines_json LONGTEXT NOT NULL,
    owner_json LONGTEXT,
    created_at VARCHAR(64) NOT NULL
);
"""


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class Store:
    """Small repository used by FastAPI route handlers and tests."""

    def __init__(self, db_path: str | Path | None = None, runtime_config: RuntimeDatabaseConfig | None = None):
        self.runtime_config = runtime_config
        self.db_path = str(db_path) if db_path is not None else None
        if self.db_path is not None:
            self.dialect = "sqlite"
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self.runtime_config = runtime_config or load_runtime_database_config()
            self.runtime_config.require_configured()
            self.dialect = self.runtime_config.dialect
        self.initialize()

    @property
    def is_sqlite(self) -> bool:
        return self.dialect == "sqlite"

    @property
    def is_postgres(self) -> bool:
        return self.dialect in {"postgresql", "postgres"}

    @property
    def is_mysql(self) -> bool:
        return self.dialect in {"mysql", "mariadb"}

    @contextmanager
    def _connect(self):
        if self.is_sqlite:
            conn = sqlite3.connect(self.db_path or ":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
            return

        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row

            assert self.runtime_config is not None
            conn = psycopg.connect(
                host=self.runtime_config.host,
                port=int(self.runtime_config.port),
                dbname=self.runtime_config.name,
                user=self.runtime_config.user,
                password=self.runtime_config.password,
                row_factory=dict_row,
            )
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
            return

        if self.is_mysql:
            import pymysql

            assert self.runtime_config is not None
            conn = pymysql.connect(
                host=self.runtime_config.host,
                port=int(self.runtime_config.port),
                database=self.runtime_config.name,
                user=self.runtime_config.user,
                password=self.runtime_config.password,
                charset=self.runtime_config.charset or "utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
            return

        raise RuntimeError(f"Unsupported SaaS store dialect: {self.dialect}")

    def initialize(self) -> None:
        with self._connect() as conn:
            self._exec_schema(conn)
            self._ensure_column(
                conn,
                "report_tasks",
                "tenant_id",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "report_tasks",
                "legacy_task_id",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "keywords_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "start_date",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "end_date",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "schedule_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "keyword_source",
                "TEXT NOT NULL DEFAULT 'manual'",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "crawl_depth",
                "INTEGER NOT NULL DEFAULT 3",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "max_notes_per_keyword",
                "INTEGER NOT NULL DEFAULT 50",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "max_comments_per_note",
                "INTEGER NOT NULL DEFAULT 100",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "login_type",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "crawler_tasks",
                "headless",
                "INTEGER NOT NULL DEFAULT 1",
            )

    def _exec_schema(self, conn: Any) -> None:
        if self.is_sqlite:
            conn.executescript(SQLITE_SCHEMA_SQL)
            return

        schema = POSTGRES_SCHEMA_SQL if self.is_postgres else MYSQL_SCHEMA_SQL
        cursor = conn.cursor()
        try:
            for statement in (part.strip() for part in schema.split(";")):
                if statement:
                    try:
                        cursor.execute(statement)
                    except Exception as exc:
                        if self.is_mysql and "Duplicate key name" in str(exc):
                            continue
                        raise
        finally:
            cursor.close()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = self._table_columns(conn, table)
        if column not in columns:
            conn.cursor().execute(f"ALTER TABLE {table} ADD COLUMN {column} {self._column_definition(definition)}")

    def _table_columns(self, conn: Any, table: str) -> set[str]:
        cursor = conn.cursor()
        try:
            if self.is_sqlite:
                rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
                return {row["name"] for row in rows}
            if self.is_postgres:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                    """,
                    (table,),
                )
                return {row["column_name"] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = %s
                """,
                (table,),
            )
            return {row["COLUMN_NAME"] if "COLUMN_NAME" in row else row["column_name"] for row in cursor.fetchall()}
        finally:
            cursor.close()

    def _column_definition(self, definition: str) -> str:
        if not self.is_mysql:
            return definition
        if definition.startswith("TEXT NOT NULL DEFAULT"):
            return "LONGTEXT NOT NULL"
        if definition == "TEXT":
            return "LONGTEXT"
        return definition

    def _translate_sql(self, sql: str) -> str:
        if self.is_sqlite:
            return sql
        translated = sql.replace("?", "%s")
        if self.is_mysql:
            translated = self._translate_mysql_upsert(translated)
        return translated

    @staticmethod
    def _translate_mysql_upsert(sql: str) -> str:
        match = re.search(r"\s+ON CONFLICT\s*\([^)]+\)\s+DO UPDATE SET\s+", sql, flags=re.IGNORECASE)
        if not match:
            return sql
        prefix = sql[: match.start()]
        update_clause = sql[match.end() :]
        update_clause = re.sub(
            r"\bexcluded\.([a-zA-Z_][a-zA-Z0-9_]*)",
            r"VALUES(\1)",
            update_clause,
            flags=re.IGNORECASE,
        )
        return f"{prefix} ON DUPLICATE KEY UPDATE {update_clause}"

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    def execute(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(self._translate_sql(sql), tuple(params))
            finally:
                cursor.close()

    def execute_returning_row(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            if self.is_mysql and "RETURNING" in sql.upper():
                return self._execute_mysql_returning(conn, sql, tuple(params))
            cursor = conn.cursor()
            try:
                cursor.execute(self._translate_sql(sql), tuple(params))
                return self._row_to_dict(cursor.fetchone())
            finally:
                cursor.close()

    def _execute_mysql_returning(self, conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        if "INSERT INTO task_events" not in sql:
            raise RuntimeError("RETURNING is not supported for this MySQL statement")
        insert_sql = re.split(r"\s+RETURNING\s+", sql, maxsplit=1, flags=re.IGNORECASE)[0]
        cursor = conn.cursor()
        try:
            cursor.execute(self._translate_sql(insert_sql), params)
            row_id = cursor.lastrowid
            cursor.execute(
                "SELECT id, event_type, payload_json, created_at FROM task_events WHERE id = %s",
                (row_id,),
            )
            return self._row_to_dict(cursor.fetchone())
        finally:
            cursor.close()

    @staticmethod
    def is_integrity_error(exc: Exception) -> bool:
        if isinstance(exc, sqlite3.IntegrityError):
            return True
        try:
            import psycopg

            if isinstance(exc, psycopg.IntegrityError):
                return True
        except Exception:
            pass
        try:
            import pymysql

            if isinstance(exc, pymysql.err.IntegrityError):
                return True
        except Exception:
            pass
        return False

    def query_one(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(self._translate_sql(sql), tuple(params))
                return self._row_to_dict(cursor.fetchone())
            finally:
                cursor.close()

    def query_all(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(self._translate_sql(sql), tuple(params))
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
