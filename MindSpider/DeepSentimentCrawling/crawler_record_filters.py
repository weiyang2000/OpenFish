"""Date-range matching helpers for crawler persistence tables."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

@dataclass(frozen=True)
class CrawlerDateTableSpec:
    platform: str
    table: str
    record_type: str
    date_columns: tuple[str, ...]


CRAWLER_DATE_TABLE_SPECS: tuple[CrawlerDateTableSpec, ...] = (
    CrawlerDateTableSpec("xhs", "xhs_note", "notes", ("time",)),
    CrawlerDateTableSpec("xhs", "xhs_note_comment", "comments", ("create_time",)),
    CrawlerDateTableSpec("dy", "douyin_aweme", "notes", ("create_time",)),
    CrawlerDateTableSpec("dy", "douyin_aweme_comment", "comments", ("create_time",)),
    CrawlerDateTableSpec("ks", "kuaishou_video", "notes", ("create_time",)),
    CrawlerDateTableSpec("ks", "kuaishou_video_comment", "comments", ("create_time",)),
    CrawlerDateTableSpec("bili", "bilibili_video", "notes", ("create_time",)),
    CrawlerDateTableSpec("bili", "bilibili_video_comment", "comments", ("create_time",)),
    CrawlerDateTableSpec("wb", "weibo_note", "notes", ("create_time", "create_date_time")),
    CrawlerDateTableSpec("wb", "weibo_note_comment", "comments", ("create_time", "create_date_time")),
    CrawlerDateTableSpec("tieba", "tieba_note", "notes", ("publish_time",)),
    CrawlerDateTableSpec("tieba", "tieba_comment", "comments", ("publish_time",)),
    CrawlerDateTableSpec("zhihu", "zhihu_content", "notes", ("created_time",)),
    CrawlerDateTableSpec("zhihu", "zhihu_comment", "comments", ("publish_time",)),
)

_DATE_SPECS_BY_TABLE = {spec.table: spec for spec in CRAWLER_DATE_TABLE_SPECS}


def date_columns_for_table(table_name: str) -> tuple[str, ...]:
    spec = _DATE_SPECS_BY_TABLE.get(table_name)
    return spec.date_columns if spec else ()


def normalize_date_range(
    start_date: str | date | None,
    end_date: str | date | None,
) -> tuple[date, date] | None:
    if not start_date or not end_date:
        return None
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    if not start or not end:
        return None
    if end < start:
        raise ValueError("end_date must be greater than or equal to start_date")
    return start, end


def row_matches_date_range(
    row: Mapping[str, Any],
    date_columns: tuple[str, ...],
    start: date,
    end: date,
) -> bool:
    reference_year = end.year
    for column in date_columns:
        record_range = parse_record_date_range(row.get(column), reference_year=reference_year)
        if not record_range:
            continue
        record_start, record_end = record_range
        if record_start <= end and record_end >= start:
            return True
    return False


def parse_record_date_range(value: Any, *, reference_year: int | None = None) -> tuple[date, date] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        record_date = value.date()
        return record_date, record_date
    if isinstance(value, date):
        return value, value
    if isinstance(value, (int, float)):
        return _timestamp_to_date_range(value)

    text = str(value).strip()
    if not text:
        return None

    numeric = text.replace(".", "", 1)
    if numeric.isdigit():
        return _timestamp_to_date_range(float(text))

    normalized = text.replace("/", "-").replace(".", "-")
    normalized = re.sub(r"\s+", " ", normalized)

    complete_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if complete_match:
        return _date_from_parts(
            int(complete_match.group(1)),
            int(complete_match.group(2)),
            int(complete_match.group(3)),
        )

    chinese_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", normalized)
    if chinese_match:
        return _date_from_parts(
            int(chinese_match.group(1)),
            int(chinese_match.group(2)),
            int(chinese_match.group(3)),
        )

    month_match = re.fullmatch(r"(\d{4})-(\d{1,2})", normalized)
    if month_match:
        return _month_range(int(month_match.group(1)), int(month_match.group(2)))

    chinese_month_match = re.fullmatch(r"(\d{4})年(\d{1,2})月", normalized)
    if chinese_month_match:
        return _month_range(int(chinese_month_match.group(1)), int(chinese_month_match.group(2)))

    partial_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?: .*)?", normalized)
    if partial_match and reference_year:
        return _date_from_parts(reference_year, int(partial_match.group(1)), int(partial_match.group(2)))

    chinese_partial_match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日?(?: .*)?", normalized)
    if chinese_partial_match and reference_year:
        return _date_from_parts(
            reference_year,
            int(chinese_partial_match.group(1)),
            int(chinese_partial_match.group(2)),
        )

    try:
        parsed_datetime = parsedate_to_datetime(text)
        if parsed_datetime:
            return parsed_datetime.date(), parsed_datetime.date()
    except (TypeError, ValueError, IndexError, OverflowError):
        pass

    today = date.today()
    if normalized.startswith("今天") or normalized in {"刚刚", "刚才"}:
        return today, today
    if normalized.startswith("昨天"):
        record_date = today - timedelta(days=1)
        return record_date, record_date

    return None


def _coerce_date(value: str | date) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        record_range = parse_record_date_range(text)
        return record_range[0] if record_range else None


def _timestamp_to_date_range(value: int | float) -> tuple[date, date] | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 1000000000000:
        timestamp = timestamp / 1000
    try:
        record_date = datetime.fromtimestamp(timestamp).date()
    except (OverflowError, OSError, ValueError):
        return None
    return record_date, record_date


def _date_from_parts(year: int, month: int, day: int) -> tuple[date, date] | None:
    try:
        record_date = date(year, month, day)
    except ValueError:
        return None
    return record_date, record_date


def _month_range(year: int, month: int) -> tuple[date, date] | None:
    try:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    except ValueError:
        return None
