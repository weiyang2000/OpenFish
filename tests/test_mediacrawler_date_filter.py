import sys
from pathlib import Path


MEDIACRAWLER_ROOT = Path(__file__).resolve().parents[1] / "MindSpider" / "DeepSentimentCrawling" / "MediaCrawler"
if str(MEDIACRAWLER_ROOT) not in sys.path:
    sys.path.insert(0, str(MEDIACRAWLER_ROOT))

from tools import date_filter


def test_mediacrawler_date_filter_keeps_only_configured_range(monkeypatch):
    monkeypatch.setenv("CRAWLER_START_DATE", "2026-05-20")
    monkeypatch.setenv("CRAWLER_END_DATE", "2026-05-22")

    assert date_filter.should_keep_content("dy", {"create_time": 1779292800})
    assert not date_filter.should_keep_content("dy", {"create_time": 1778860800})
    assert date_filter.should_keep_content("xhs", {"time": 1779292800000})
    assert date_filter.should_keep_content(
        "wb",
        {"mblog": {"created_at": "Thu May 21 12:00:00 +0800 2026"}},
    )


def test_mediacrawler_date_filter_limit_counts_after_filter(monkeypatch):
    monkeypatch.setenv("CRAWLER_START_DATE", "2026-05-20")
    monkeypatch.setenv("CRAWLER_END_DATE", "2026-05-22")

    kept = date_filter.filter_records(
        "dy",
        "content",
        [
            {"aweme_id": "old", "create_time": 1778860800},
            {"aweme_id": "first", "create_time": 1779292800},
            {"aweme_id": "second", "create_time": 1779379200},
        ],
        limit=1,
    )

    assert [item["aweme_id"] for item in kept] == ["first"]


def test_mediacrawler_date_filter_detects_no_new_time_page(monkeypatch):
    monkeypatch.setenv("CRAWLER_START_DATE", "2026-05-20")
    monkeypatch.setenv("CRAWLER_END_DATE", "2026-05-22")

    assert not date_filter.has_new_time_records(
        "dy",
        "content",
        [
            {"aweme_id": "old-1", "create_time": 1778860800},
            {"aweme_id": "old-2", "create_time": 1778947200},
        ],
    )
    assert date_filter.has_new_time_records(
        "dy",
        "content",
        [
            {"aweme_id": "too-new", "create_time": 1779638400},
            {"aweme_id": "in-range", "create_time": 1779292800},
        ],
    )
