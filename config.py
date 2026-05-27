"""Shared runtime configuration loaded from environment variables.

This module intentionally contains no secrets. Copy `.env.example` to `.env`
or pass environment variables in deployment to provide actual credentials.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - python-dotenv is in requirements
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = Path.cwd() / ".env" if (Path.cwd() / ".env").exists() else PROJECT_ROOT / ".env"


def _load_env() -> None:
    if load_dotenv and ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Configuration object compatible with the legacy Engine modules."""

    def __init__(self) -> None:
        self.HOST = _env_str("HOST", "0.0.0.0")
        self.PORT = _env_int("PORT", 5000)

        self.DB_DIALECT = _env_str("DB_DIALECT", "postgresql")
        self.DB_HOST = _env_str("DB_HOST", "your_db_host")
        self.DB_PORT = _env_int("DB_PORT", 5432)
        self.DB_USER = _env_str("DB_USER", "your_db_user")
        self.DB_PASSWORD = _env_str("DB_PASSWORD", "your_db_password")
        self.DB_NAME = _env_str("DB_NAME", "your_db_name")
        self.DB_CHARSET = _env_str("DB_CHARSET", "utf8mb4")
        self.DATABASE_URL = _env_str("DATABASE_URL", "")

        self.INSIGHT_ENGINE_API_KEY = _env_str("INSIGHT_ENGINE_API_KEY")
        self.INSIGHT_ENGINE_BASE_URL = _env_str("INSIGHT_ENGINE_BASE_URL")
        self.INSIGHT_ENGINE_MODEL_NAME = _env_str("INSIGHT_ENGINE_MODEL_NAME")
        self.INSIGHT_MODE = _env_str("INSIGHT_MODE", "normal")
        self.MEDIA_ENGINE_API_KEY = _env_str("MEDIA_ENGINE_API_KEY")
        self.MEDIA_ENGINE_BASE_URL = _env_str("MEDIA_ENGINE_BASE_URL")
        self.MEDIA_ENGINE_MODEL_NAME = _env_str("MEDIA_ENGINE_MODEL_NAME")
        self.QUERY_ENGINE_API_KEY = _env_str("QUERY_ENGINE_API_KEY")
        self.QUERY_ENGINE_BASE_URL = _env_str("QUERY_ENGINE_BASE_URL")
        self.QUERY_ENGINE_MODEL_NAME = _env_str("QUERY_ENGINE_MODEL_NAME")
        self.REPORT_ENGINE_API_KEY = _env_str("REPORT_ENGINE_API_KEY")
        self.REPORT_ENGINE_BASE_URL = _env_str("REPORT_ENGINE_BASE_URL")
        self.REPORT_ENGINE_MODEL_NAME = _env_str("REPORT_ENGINE_MODEL_NAME")
        self.MINDSPIDER_API_KEY = _env_str("MINDSPIDER_API_KEY")
        self.MINDSPIDER_BASE_URL = _env_str("MINDSPIDER_BASE_URL")
        self.MINDSPIDER_MODEL_NAME = _env_str("MINDSPIDER_MODEL_NAME")
        self.FORUM_HOST_API_KEY = _env_str("FORUM_HOST_API_KEY")
        self.FORUM_HOST_BASE_URL = _env_str("FORUM_HOST_BASE_URL")
        self.FORUM_HOST_MODEL_NAME = _env_str("FORUM_HOST_MODEL_NAME")
        self.KEYWORD_OPTIMIZER_API_KEY = _env_str("KEYWORD_OPTIMIZER_API_KEY")
        self.KEYWORD_OPTIMIZER_BASE_URL = _env_str("KEYWORD_OPTIMIZER_BASE_URL")
        self.KEYWORD_OPTIMIZER_MODEL_NAME = _env_str("KEYWORD_OPTIMIZER_MODEL_NAME")

        self.TAVILY_API_KEY = _env_str("TAVILY_API_KEY")
        self.SEARCH_TOOL_TYPE = _env_str("SEARCH_TOOL_TYPE", "AnspireAPI")
        self.ANSPIRE_BASE_URL = _env_str("ANSPIRE_BASE_URL", "https://plugin.anspire.cn/api/ntsearch/search")
        self.ANSPIRE_API_KEY = _env_str("ANSPIRE_API_KEY")
        self.BOCHA_BASE_URL = _env_str("BOCHA_BASE_URL", "https://api.bocha.cn/v1/ai-search")
        self.BOCHA_WEB_SEARCH_API_KEY = _env_str("BOCHA_WEB_SEARCH_API_KEY")
        self.BOCHA_API_KEY = _env_str("BOCHA_API_KEY", self.BOCHA_WEB_SEARCH_API_KEY)

        self.SEARCH_TIMEOUT = _env_int("SEARCH_TIMEOUT", 240)
        self.DEFAULT_SEARCH_HOT_CONTENT_LIMIT = _env_int("DEFAULT_SEARCH_HOT_CONTENT_LIMIT", 100)
        self.DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE = _env_int(
            "DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE",
            50,
        )
        self.DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE = _env_int(
            "DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE",
            100,
        )
        self.DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT = _env_int("DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT", 500)
        self.DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT = _env_int("DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT", 200)
        self.MAX_SEARCH_RESULTS_FOR_LLM = _env_int("MAX_SEARCH_RESULTS_FOR_LLM", 0)
        self.MAX_HIGH_CONFIDENCE_SENTIMENT_RESULTS = _env_int("MAX_HIGH_CONFIDENCE_SENTIMENT_RESULTS", 0)
        self.MAX_REFLECTIONS = _env_int("MAX_REFLECTIONS", 3)
        self.MAX_PARAGRAPHS = _env_int("MAX_PARAGRAPHS", 6)
        self.MAX_SEARCH_RESULTS = _env_int("MAX_SEARCH_RESULTS", 20)

        self.OUTPUT_DIR = _env_str("OUTPUT_DIR", "final_reports")
        self.CHAPTER_OUTPUT_DIR = _env_str("CHAPTER_OUTPUT_DIR", "engine_reports/report_chapters")
        self.DOCUMENT_IR_OUTPUT_DIR = _env_str("DOCUMENT_IR_OUTPUT_DIR", "engine_reports/document_ir")
        self.JSON_ERROR_LOG_DIR = _env_str("JSON_ERROR_LOG_DIR", "logs/report_json_errors")
        self.TEMPLATE_DIR = _env_str("TEMPLATE_DIR", str(PROJECT_ROOT / "ReportEngine" / "report_template"))
        self.LOG_FILE = _env_str("LOG_FILE", "logs/report_engine.log")
        self.CHAPTER_JSON_MAX_ATTEMPTS = _env_int("CHAPTER_JSON_MAX_ATTEMPTS", 3)
        self.MAX_CONTENT_LENGTH = _env_int("MAX_CONTENT_LENGTH", 12000)
        self.SEARCH_CONTENT_MAX_LENGTH = _env_int("SEARCH_CONTENT_MAX_LENGTH", self.MAX_CONTENT_LENGTH)
        self.SAVE_INTERMEDIATE_STATES = _env_bool("SAVE_INTERMEDIATE_STATES", False)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def reload_settings() -> Settings:
    global settings
    _load_env()
    next_settings = Settings()
    current_settings = globals().get("settings")
    if isinstance(current_settings, Settings):
        current_settings.__dict__.clear()
        current_settings.__dict__.update(next_settings.__dict__)
        settings = current_settings
    else:
        settings = next_settings
    return settings


_load_env()
settings = Settings()
