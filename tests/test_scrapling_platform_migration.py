import asyncio
import importlib
import os
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

_ORIGINAL_CWD = Path.cwd()
_ORIGINAL_CONFIG_MODULE = sys.modules.get("config")
os.chdir(MEDIACRAWLER_ROOT)
try:
    MEDIACRAWLER_CONFIG = importlib.import_module("config")
    from media_platform.bilibili import scrapling_spider as bilibili_scrapling
    from media_platform.bilibili.core import BilibiliCrawler
    from media_platform.douyin import scrapling_spider as douyin_scrapling
    from media_platform.douyin.core import DouYinCrawler
    from media_platform.kuaishou import scrapling_spider as kuaishou_scrapling
    from media_platform.kuaishou.core import KuaishouCrawler
    from media_platform.scrapling_bridge import should_use_scrapling_engine
    from media_platform.tieba import scrapling_spider as tieba_scrapling
    from media_platform.tieba.core import TieBaCrawler
    from media_platform.weibo import scrapling_spider as weibo_scrapling
    from media_platform.weibo.core import WeiboCrawler
    from media_platform.xhs import scrapling_spider as xhs_scrapling
    from media_platform.xhs.core import XiaoHongShuCrawler
finally:
    os.chdir(_ORIGINAL_CWD)
    if _ORIGINAL_CONFIG_MODULE is None:
        sys.modules.pop("config", None)
    else:
        sys.modules["config"] = _ORIGINAL_CONFIG_MODULE


PLATFORMS = [
    ("xhs", XiaoHongShuCrawler, xhs_scrapling, "run_scrapling_xhs_crawler"),
    ("dy", DouYinCrawler, douyin_scrapling, "run_scrapling_douyin_crawler"),
    ("ks", KuaishouCrawler, kuaishou_scrapling, "run_scrapling_kuaishou_crawler"),
    ("bili", BilibiliCrawler, bilibili_scrapling, "run_scrapling_bilibili_crawler"),
    ("wb", WeiboCrawler, weibo_scrapling, "run_scrapling_weibo_crawler"),
    ("tieba", TieBaCrawler, tieba_scrapling, "run_scrapling_tieba_crawler"),
]

def _configure_default(monkeypatch, platform: str):
    del platform
    monkeypatch.delenv("CRAWLER_ENGINE", raising=False)
    monkeypatch.setattr(MEDIACRAWLER_CONFIG, "CRAWLER_TYPE", "search", raising=False)
    monkeypatch.setattr(MEDIACRAWLER_CONFIG, "ENABLE_IP_PROXY", False, raising=False)


@pytest.mark.parametrize("platform,crawler_cls,spider_module,runner_name", PLATFORMS)
def test_platform_start_uses_scrapling_by_default(
    monkeypatch,
    platform,
    crawler_cls,
    spider_module,
    runner_name,
):
    _configure_default(monkeypatch, platform)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(spider_module, runner_name, fake_scrapling)
    monkeypatch.setattr(crawler_cls, "_start_legacy", fake_legacy)

    asyncio.run(crawler_cls().start())

    assert calls == ["scrapling"]


@pytest.mark.parametrize("platform,crawler_cls,spider_module,runner_name", PLATFORMS)
def test_platform_start_can_force_legacy(
    monkeypatch,
    platform,
    crawler_cls,
    spider_module,
    runner_name,
):
    _configure_default(monkeypatch, platform)
    monkeypatch.setenv("CRAWLER_ENGINE", "legacy")
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(spider_module, runner_name, fake_scrapling)
    monkeypatch.setattr(crawler_cls, "_start_legacy", fake_legacy)

    asyncio.run(crawler_cls().start())

    assert calls == ["legacy"]


def test_platform_engine_uses_global_config(monkeypatch):
    _configure_default(monkeypatch, "dy")
    monkeypatch.setenv("CRAWLER_ENGINE", "legacy")

    assert should_use_scrapling_engine("dy") is False


@pytest.mark.parametrize("platform,crawler_cls,spider_module,runner_name", PLATFORMS)
def test_platform_start_raises_when_scrapling_fails(
    monkeypatch,
    platform,
    crawler_cls,
    spider_module,
    runner_name,
):
    _configure_default(monkeypatch, platform)
    calls: list[str] = []

    async def fake_scrapling():
        calls.append("scrapling")
        raise RuntimeError("scrapling failed")

    async def fake_legacy(self):
        calls.append("legacy")

    monkeypatch.setattr(spider_module, runner_name, fake_scrapling)
    monkeypatch.setattr(crawler_cls, "_start_legacy", fake_legacy)

    with pytest.raises(RuntimeError, match="scrapling failed"):
        asyncio.run(crawler_cls().start())

    assert calls == ["scrapling"]
