from MindSpider.DeepSentimentCrawling.crawler_record_filters import (
    parse_record_date_range,
    row_matches_date_range,
)


def test_row_matches_date_range_checks_source_publish_date():
    start, end = parse_record_date_range("2026-05-20")[0], parse_record_date_range("2026-05-22")[0]

    assert row_matches_date_range({"create_time": 1779292800}, ("create_time",), start, end)
    assert not row_matches_date_range({"create_time": 1778860800}, ("create_time",), start, end)


def test_parse_record_date_range_handles_month_ranges_and_partial_dates():
    assert parse_record_date_range("2026-05")[0].isoformat() == "2026-05-01"
    assert parse_record_date_range("2026-05")[1].isoformat() == "2026-05-31"
    assert parse_record_date_range("5-21", reference_year=2026)[0].isoformat() == "2026-05-21"
