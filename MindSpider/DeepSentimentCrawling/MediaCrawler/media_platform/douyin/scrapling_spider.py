# -*- coding: utf-8 -*-
"""Scrapling Spider bridge for Douyin."""

from media_platform.scrapling_bridge import (
    ScraplingBridgeSpec,
    dispatch_by_crawler_type,
    run_scrapling_bridge,
)

from .core import DouYinCrawler
from .login import DouYinLogin


async def _pong(crawler, client) -> bool:
    return await client.pong(browser_context=crawler.browser_context)


async def _run_flow(crawler):
    await dispatch_by_crawler_type(
        crawler,
        detail="get_specified_awemes",
        creator="get_creators_and_videos",
    )


async def run_scrapling_douyin_crawler() -> None:
    await run_scrapling_bridge(
        ScraplingBridgeSpec(
            platform="dy",
            spider_name="douyin_scrapling",
            crawler_factory=DouYinCrawler,
            login_factory=DouYinLogin,
            client_attr="dy_client",
            create_client_method="create_douyin_client",
            start_url=lambda crawler: crawler.index_url,
            run_flow=_run_flow,
            allowed_domains={"douyin.com", "www.douyin.com"},
            pong=_pong,
        )
    )
