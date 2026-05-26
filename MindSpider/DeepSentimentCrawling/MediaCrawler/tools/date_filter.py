# -*- coding: utf-8 -*-
"""Runtime date filtering for crawler records before persistence."""

from __future__ import annotations

import calendar
import os
import re
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from tools import utils


CONTENT_DATE_PATHS = {
    "xhs": (("time",),),
    "dy": (("create_time",),),
    "ks": (("photo", "timestamp"), ("create_time",)),
    "bili": (("View", "pubdate"), ("create_time",)),
    "wb": (("mblog", "created_at"), ("create_time",), ("create_date_time",)),
    "tieba": (("publish_time",),),
    "zhihu": (("created_time",),),
}

COMMENT_DATE_PATHS = {
    "xhs": (("create_time",),),
    "dy": (("create_time",),),
    "ks": (("timestamp",), ("create_time",)),
    "bili": (("ctime",), ("create_time",)),
    "wb": (("created_at",), ("create_time",), ("create_date_time",)),
    "tieba": (("publish_time",),),
    "zhihu": (("publish_time",), ("created_time",)),
}


def enabled() -> bool:
    return _configured_range() is not None


def should_keep_content(platform: str, item: Any) -> bool:
    return should_keep_record(platform, "content", item)


def should_keep_comment(platform: str, item: Any) -> bool:
    return should_keep_record(platform, "comment", item)


def should_keep_record(platform: str, record_type: str, item: Any) -> bool:
    configured = _configured_range()
    if not configured:
        return True
    start, end = configured
    paths = CONTENT_DATE_PATHS if record_type == "content" else COMMENT_DATE_PATHS
    for path in paths.get(platform, ()):
        record_range = parse_record_date_range(_read_path(item, path), reference_year=end.year)
        if not record_range:
            continue
        record_start, record_end = record_range
        return record_start <= end and record_end >= start
    return False


def filter_records(platform: str, record_type: str, records: Iterable[Any], limit: int | None = None) -> list[Any]:
    kept: list[Any] = []
    for item in records:
        if should_keep_record(platform, record_type, item):
            kept.append(item)
            if limit is not None and len(kept) >= limit:
                break
        elif enabled():
            utils.logger.info(f"[date_filter] skip {platform} {record_type} outside configured date range")
    return kept


def has_new_time_records(platform: str, record_type: str, records: Iterable[Any]) -> bool:
    configured = _configured_range()
    record_list = list(records)
    if not record_list:
        return False
    if not configured:
        return True

    start, end = configured
    paths = CONTENT_DATE_PATHS if record_type == "content" else COMMENT_DATE_PATHS
    saw_parseable_date = False
    for item in record_list:
        item_has_parseable_date = False
        for path in paths.get(platform, ()):
            record_range = parse_record_date_range(_read_path(item, path), reference_year=end.year)
            if not record_range:
                continue
            item_has_parseable_date = True
            saw_parseable_date = True
            _, record_end = record_range
            if record_end >= start:
                return True
            break
        if not item_has_parseable_date:
            return True
    return not saw_parseable_date


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


def _configured_range() -> tuple[date, date] | None:
    start_text = os.getenv("CRAWLER_START_DATE", "").strip()
    end_text = os.getenv("CRAWLER_END_DATE", "").strip()
    if not start_text or not end_text:
        return None
    try:
        start = date.fromisoformat(start_text[:10])
        end = date.fromisoformat(end_text[:10])
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def _read_path(item: Any, path: tuple[str, ...]) -> Any:
    value = item
    for key in path:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get(key)
        else:
            value = getattr(value, key, None)
    return value


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
