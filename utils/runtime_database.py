"""Shared database configuration for crawler storage and Insight queries."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote, urlsplit


_SCHEMA_INIT_LOCK = threading.Lock()
_INITIALIZED_SCHEMA_KEYS: set[str] = set()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_placeholder(value: str) -> bool:
    value = _clean(value)
    return not value or value.startswith("your_")


def _with_driver(database_url: str, *, async_driver: bool) -> str:
    if not database_url:
        return database_url

    replacements = {
        True: {
            "postgres://": "postgresql+asyncpg://",
            "postgresql://": "postgresql+asyncpg://",
            "postgresql+psycopg://": "postgresql+asyncpg://",
            "postgresql+psycopg2://": "postgresql+asyncpg://",
            "mysql://": "mysql+aiomysql://",
            "mysql+pymysql://": "mysql+aiomysql://",
        },
        False: {
            "postgres://": "postgresql+psycopg://",
            "postgresql://": "postgresql+psycopg://",
            "postgresql+asyncpg://": "postgresql+psycopg://",
            "postgresql+psycopg2://": "postgresql+psycopg://",
            "mysql://": "mysql+pymysql://",
            "mysql+aiomysql://": "mysql+pymysql://",
        },
    }
    for prefix, replacement in replacements[async_driver].items():
        if database_url.startswith(prefix):
            return replacement + database_url[len(prefix) :]
    return database_url


def _parse_database_url(database_url: str) -> dict[str, str]:
    parsed = urlsplit(database_url)
    scheme = parsed.scheme.split("+", 1)[0].lower()
    if scheme == "postgres":
        scheme = "postgresql"

    return {
        "dialect": scheme,
        "host": parsed.hostname or "",
        "port": str(parsed.port or ""),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "name": unquote(parsed.path.lstrip("/")),
    }


@dataclass(frozen=True)
class RuntimeDatabaseConfig:
    dialect: str
    host: str
    port: str
    user: str
    password: str
    name: str
    charset: str = "utf8mb4"
    database_url: str = ""

    @classmethod
    def from_settings(cls, settings: Any) -> "RuntimeDatabaseConfig":
        database_url = _clean(getattr(settings, "DATABASE_URL", ""))
        parsed = _parse_database_url(database_url) if database_url else {}
        dialect = (_clean(parsed.get("dialect")) or _clean(getattr(settings, "DB_DIALECT", "")) or "postgresql").lower()
        if dialect == "postgres":
            dialect = "postgresql"
        return cls(
            dialect=dialect,
            host=_clean(parsed.get("host")) or _clean(getattr(settings, "DB_HOST", "")),
            port=_clean(parsed.get("port")) or _clean(getattr(settings, "DB_PORT", "")),
            user=_clean(parsed.get("user")) or _clean(getattr(settings, "DB_USER", "")),
            password=_clean(parsed.get("password")) or _clean(getattr(settings, "DB_PASSWORD", "")),
            name=_clean(parsed.get("name")) or _clean(getattr(settings, "DB_NAME", "")),
            charset=_clean(getattr(settings, "DB_CHARSET", "")) or "utf8mb4",
            database_url=database_url,
        )

    @property
    def save_data_option(self) -> str:
        if self.dialect in {"postgresql", "postgres"}:
            return "postgres"
        if self.dialect in {"mysql", "mariadb"}:
            return "db"
        if self.dialect == "sqlite":
            return "sqlite"
        raise RuntimeError(f"Unsupported crawler database dialect: {self.dialect}")

    @property
    def init_db_type(self) -> str:
        if self.save_data_option == "db":
            return "mysql"
        return self.save_data_option

    def require_configured(self) -> None:
        if self.database_url:
            return
        if self.dialect == "sqlite":
            return

        missing = [
            key
            for key, value in (
                ("DB_HOST", self.host),
                ("DB_USER", self.user),
                ("DB_NAME", self.name),
            )
            if _is_placeholder(value)
        ]
        if self.password.startswith("your_"):
            missing.append("DB_PASSWORD")
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                "Crawler/Insight database is not configured. "
                "Set DATABASE_URL or DB_DIALECT/DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME in .env. "
                f"Invalid field(s): {joined}."
            )

    def _build_url(self, *, async_driver: bool) -> str:
        if self.database_url:
            return _with_driver(self.database_url, async_driver=async_driver)
        self.require_configured()
        if self.dialect == "sqlite":
            driver = "sqlite+aiosqlite" if async_driver else "sqlite"
            sqlite_path = os.getenv(
                "BETTAFISH_CRAWLER_SQLITE_PATH",
                "MindSpider/DeepSentimentCrawling/MediaCrawler/database/sqlite_tables.db",
            )
            return f"{driver}:///{Path(sqlite_path).expanduser().resolve()}"

        password = quote_plus(self.password)
        if self.dialect in {"postgresql", "postgres"}:
            driver = "postgresql+asyncpg" if async_driver else "postgresql+psycopg"
            return f"{driver}://{self.user}:{password}@{self.host}:{self.port}/{quote_plus(self.name)}"

        driver = "mysql+aiomysql" if async_driver else "mysql+pymysql"
        return (
            f"{driver}://{self.user}:{password}@{self.host}:{self.port}/{quote_plus(self.name)}"
            f"?charset={quote_plus(self.charset or 'utf8mb4')}"
        )

    def async_sqlalchemy_url(self) -> str:
        return self._build_url(async_driver=True)

    def sync_sqlalchemy_url(self) -> str:
        return self._build_url(async_driver=False)

    def env_overrides(self) -> dict[str, str]:
        values = {
            "DB_DIALECT": self.dialect,
            "DB_HOST": self.host,
            "DB_PORT": self.port,
            "DB_USER": self.user,
            "DB_PASSWORD": self.password,
            "DB_NAME": self.name,
            "DB_CHARSET": self.charset,
        }
        if self.database_url:
            values["DATABASE_URL"] = self.database_url

        if self.dialect in {"postgresql", "postgres"}:
            values.update(
                {
                    "POSTGRES_DB_HOST": self.host,
                    "POSTGRES_DB_PORT": self.port,
                    "POSTGRES_DB_USER": self.user,
                    "POSTGRES_DB_PWD": self.password,
                    "POSTGRES_DB_NAME": self.name,
                }
            )
        elif self.dialect in {"mysql", "mariadb"}:
            values.update(
                {
                    "MYSQL_DB_HOST": self.host,
                    "MYSQL_DB_PORT": self.port,
                    "MYSQL_DB_USER": self.user,
                    "MYSQL_DB_PWD": self.password,
                    "MYSQL_DB_NAME": self.name,
                }
            )
        return {key: value for key, value in values.items() if value not in (None, "")}

    def apply_to_environment(self) -> None:
        os.environ.update(self.env_overrides())

    def cache_key(self) -> str:
        if self.database_url:
            return self.database_url
        return f"{self.dialect}:{self.host}:{self.port}:{self.user}:{self.name}"


def load_runtime_database_config(settings: Any | None = None) -> RuntimeDatabaseConfig:
    if settings is None:
        from config import reload_settings

        settings = reload_settings()
    return RuntimeDatabaseConfig.from_settings(settings)


def ensure_crawler_database_schema(
    repo_root: str | Path,
    settings: Any | None = None,
    *,
    timeout_seconds: int = 180,
) -> None:
    config = load_runtime_database_config(settings)
    config.require_configured()
    cache_key = config.cache_key()
    with _SCHEMA_INIT_LOCK:
        if cache_key in _INITIALIZED_SCHEMA_KEYS:
            return

        config.apply_to_environment()
        repo_root = Path(repo_root)
        env = {**os.environ, **config.env_overrides(), "PYTHONUNBUFFERED": "1"}

        if config.init_db_type == "sqlite":
            mediacrawler_dir = repo_root / "MindSpider" / "DeepSentimentCrawling" / "MediaCrawler"
            cmd = [sys.executable, "-u", "main.py", "--init_db", "sqlite"]
            cwd = mediacrawler_dir
        else:
            cmd = [sys.executable, "-u", str(repo_root / "MindSpider" / "schema" / "init_database.py")]
            cwd = repo_root

        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            stderr_tail = (result.stderr or result.stdout or "").strip()[-2000:]
            raise RuntimeError(
                "Failed to initialize crawler database schema "
                f"for {config.dialect} database {config.host}:{config.port}/{config.name}: {stderr_tail}"
            )

        _INITIALIZED_SCHEMA_KEYS.add(cache_key)
