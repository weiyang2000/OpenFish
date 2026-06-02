# -*- coding: utf-8 -*-
"""Scrapling Spider bridge for Weibo."""

import asyncio

from media_platform.scrapling_bridge import (
    ScraplingBridgeSpec,
    dispatch_by_crawler_type,
    run_scrapling_bridge,
)
from tools import utils

from .core import WeiboCrawler
from .login import WeiboLogin


async def _bootstrap(crawler, page) -> None:
    del page
    await asyncio.sleep(2)


async def _after_login(crawler, client) -> None:
    utils.logger.info("[WeiboScraplingSpider] Redirect to mobile homepage and update mobile cookies")
    await crawler.context_page.goto(crawler.mobile_index_url)
    await asyncio.sleep(3)
    await client.update_cookies(
        browser_context=crawler.browser_context,
        urls=[crawler.mobile_index_url],
    )


async def _run_flow(crawler):
    await dispatch_by_crawler_type(
        crawler,
        detail="get_specified_notes",
        creator="get_creators_and_notes",
    )


async def run_scrapling_weibo_crawler() -> None:
    await run_scrapling_bridge(
        ScraplingBridgeSpec(
            platform="wb",
            spider_name="weibo_scrapling",
            crawler_factory=WeiboCrawler,
            login_factory=WeiboLogin,
            client_attr="wb_client",
            create_client_method="create_weibo_client",
            start_url=lambda crawler: crawler.index_url,
            run_flow=_run_flow,
            allowed_domains={"weibo.com", "www.weibo.com", "m.weibo.cn"},
            bootstrap=_bootstrap,
            after_login=_after_login,
        )
    )
