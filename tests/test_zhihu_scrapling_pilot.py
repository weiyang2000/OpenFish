import asyncio
import sys
from pathlib import Path

import pytest


MEDIACRAWLER_ROOT = (
    Path(__file__).resolve().parents[1]
    / "MindSpider"
    / "DeepSentimentCrawling"
    / "MediaCrawler"
)
if str(MEDIACRAWLER_ROOT) not in sys.path:
    sys.path.insert(0, str(MEDIACRAWLER_ROOT))

import config
from media_platform.zhihu import scrapling_spider
from media_platform.zhihu.core import ZhihuCrawler
from store.zhihu import _store_impl as zhihu_store_impl


def _configure_scrapling_default(monkeypatch):
    monkeypatch.delenv("CRAWLER_ENGINE", raising=False)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "search", raising=False)
    monkeypatch.setattr(config, "ENABLE_IP_PROXY", False, raising=False)


def test_zhihu_start_uses_scrapling_by_default(monkeypatch):
    _configure_scrapling_default(monkeypatch)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(scrapling_spider, "run_scrapling_zhihu_crawler", fake_scrapling)
    monkeypatch.setattr(ZhihuCrawler, "_start_legacy", fake_legacy)

    asyncio.run(ZhihuCrawler().start())

    assert calls == ["scrapling"]


def test_zhihu_detail_start_uses_scrapling_by_default(monkeypatch):
    _configure_scrapling_default(monkeypatch)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "detail", raising=False)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(scrapling_spider, "run_scrapling_zhihu_crawler", fake_scrapling)
    monkeypatch.setattr(ZhihuCrawler, "_start_legacy", fake_legacy)

    asyncio.run(ZhihuCrawler().start())

    assert calls == ["scrapling"]


def test_zhihu_creator_start_uses_scrapling_by_default(monkeypatch):
    _configure_scrapling_default(monkeypatch)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "creator", raising=False)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(scrapling_spider, "run_scrapling_zhihu_crawler", fake_scrapling)
    monkeypatch.setattr(ZhihuCrawler, "_start_legacy", fake_legacy)

    asyncio.run(ZhihuCrawler().start())

    assert calls == ["scrapling"]


def test_zhihu_start_raises_when_scrapling_fails(monkeypatch):
    _configure_scrapling_default(monkeypatch)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")
        raise RuntimeError("scrapling failed")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(scrapling_spider, "run_scrapling_zhihu_crawler", fake_scrapling)
    monkeypatch.setattr(ZhihuCrawler, "_start_legacy", fake_legacy)

    with pytest.raises(RuntimeError, match="scrapling failed"):
        asyncio.run(ZhihuCrawler().start())

    assert calls == ["scrapling"]


def test_zhihu_start_can_force_legacy(monkeypatch):
    _configure_scrapling_default(monkeypatch)
    monkeypatch.setenv("CRAWLER_ENGINE", "legacy")
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(scrapling_spider, "run_scrapling_zhihu_crawler", fake_scrapling)
    monkeypatch.setattr(ZhihuCrawler, "_start_legacy", fake_legacy)

    asyncio.run(ZhihuCrawler().start())

    assert calls == ["legacy"]


def test_zhihu_start_raises_for_ip_proxy_pool(monkeypatch):
    _configure_scrapling_default(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_IP_PROXY", True, raising=False)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(scrapling_spider, "run_scrapling_zhihu_crawler", fake_scrapling)
    monkeypatch.setattr(ZhihuCrawler, "_start_legacy", fake_legacy)

    with pytest.raises(RuntimeError, match="IP proxy pool"):
        asyncio.run(ZhihuCrawler().start())

    assert calls == []


def test_scrapling_cookie_normalization_supports_browser_cookies():
    cookies = (
        {"name": "d_c0", "value": "abc"},
        {"name": "z_c0", "value": "def"},
        {"name": "ignored"},
    )

    assert scrapling_spider._cookie_dict_from_response(cookies) == {
        "d_c0": "abc",
        "z_c0": "def",
    }


def test_scrapling_search_referer_encodes_chinese_keyword():
    referer = scrapling_spider._search_referer("https://www.zhihu.com", "养老服务")

    assert "养老服务" not in referer
    assert "q=%E5%85%BB%E8%80%81%E6%9C%8D%E5%8A%A1" in referer


def test_zhihu_db_store_coerces_time_fields_to_strings():
    content = zhihu_store_impl._coerce_zhihu_content_for_db(
        {"created_time": 1779410688, "updated_time": 1779410699}
    )
    comment = zhihu_store_impl._coerce_zhihu_comment_for_db({"publish_time": 1779410700})

    assert content == {"created_time": "1779410688", "updated_time": "1779410699"}
    assert comment == {"publish_time": "1779410700"}
