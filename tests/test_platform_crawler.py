import subprocess
import sys
from pathlib import Path

import pytest

from MindSpider.DeepSentimentCrawling.platform_crawler import PlatformCrawler


def _crawler(tmp_path: Path) -> PlatformCrawler:
    crawler = PlatformCrawler.__new__(PlatformCrawler)
    crawler.mediacrawler_path = tmp_path
    crawler.supported_platforms = ["wb"]
    crawler.crawl_stats = {}
    crawler._schema_initialized = {"postgres"}
    crawler.log_callback = None
    return crawler


def test_run_crawler_treats_qrcode_login_failure_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    crawler = _crawler(tmp_path)
    monkeypatch.setattr(crawler, "configure_mediacrawler_db", lambda: True)
    monkeypatch.setattr(crawler, "create_base_config", lambda *_: True)
    monkeypatch.setattr(crawler, "_count_platform_records", lambda _: {"notes": 0, "comments": 0})

    def fake_run(cmd, timeout):
        del timeout
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="[WeiboLogin.login_by_qrcode] login failed , have not found qrcode please check ....\n",
            stderr="",
        )

    monkeypatch.setattr(crawler, "_run_media_crawler_command", fake_run)

    result = crawler.run_crawler("wb", ["AI"], login_type="qrcode", headless=True)

    assert result["success"] is False
    assert "have not found qrcode" in result["error"]
    assert result["notes_count"] == 0


def test_run_crawler_reports_new_database_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    crawler = _crawler(tmp_path)
    counts = iter([{"notes": 10, "comments": 4}, {"notes": 13, "comments": 9}])
    monkeypatch.setattr(crawler, "configure_mediacrawler_db", lambda: True)
    monkeypatch.setattr(crawler, "create_base_config", lambda *_: True)
    monkeypatch.setattr(crawler, "_count_platform_records", lambda _: next(counts))

    def fake_run(cmd, timeout):
        del timeout
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(crawler, "_run_media_crawler_command", fake_run)

    result = crawler.run_crawler("wb", ["AI"], login_type="cookie", headless=True)

    assert result["success"] is True
    assert result["notes_count"] == 3
    assert result["comments_count"] == 5


def test_run_crawler_treats_invalid_cookie_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    crawler = _crawler(tmp_path)
    monkeypatch.setattr(crawler, "configure_mediacrawler_db", lambda: True)
    monkeypatch.setattr(crawler, "create_base_config", lambda *_: True)
    monkeypatch.setattr(crawler, "_count_platform_records", lambda _: {"notes": 0, "comments": 0})

    def fake_run(cmd, timeout):
        del timeout
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="[WeiboClient.pong] cookie may be invalid and again login...\n",
            stderr="",
        )

    monkeypatch.setattr(crawler, "_run_media_crawler_command", fake_run)

    result = crawler.run_crawler("wb", ["AI"], login_type="cookie", headless=True)

    assert result["success"] is False
    assert "cookie may be invalid" in result["error"]


def test_streaming_command_relays_stdout_and_stderr(tmp_path: Path):
    events: list[tuple[str, str]] = []
    crawler = _crawler(tmp_path)
    crawler.log_callback = lambda source, line: events.append((source, line))

    result = crawler._run_command_streaming(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('crawler stdout line'); print('crawler stderr line', file=sys.stderr)",
        ],
        cwd=tmp_path,
        timeout=5,
    )

    assert result.returncode == 0
    assert "crawler stdout line" in result.stdout
    assert "crawler stderr line" in result.stderr
    assert ("stdout", "crawler stdout line") in events
    assert ("stderr", "crawler stderr line") in events
