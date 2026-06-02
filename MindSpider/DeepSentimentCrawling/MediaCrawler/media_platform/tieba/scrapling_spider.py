# -*- coding: utf-8 -*-
"""Scrapling Spider bridge for Baidu Tieba."""

import config

from media_platform.scrapling_bridge import (
    ScraplingBridgeSpec,
    dispatch_by_crawler_type,
    run_scrapling_bridge,
)

from .core import TieBaCrawler
from .login import BaiduTieBaLogin


async def _bootstrap(crawler, page) -> None:
    del page
    await crawler._inject_anti_detection_scripts()
    await crawler._navigate_to_tieba_via_baidu()


def _create_client_args(crawler) -> tuple:
    del crawler
    return (None, None)


async def _pong(crawler, client) -> bool:
    return await client.pong(browser_context=crawler.browser_context)


async def _run_flow(crawler):
    if config.CRAWLER_TYPE == "search":
        await crawler.search()
        await crawler.get_specified_tieba_notes()
    else:
        await dispatch_by_crawler_type(
            crawler,
            detail="get_specified_notes",
            creator="get_creators_and_notes",
        )


async def run_scrapling_tieba_crawler() -> None:
    await run_scrapling_bridge(
        ScraplingBridgeSpec(
            platform="tieba",
            spider_name="tieba_scrapling",
            crawler_factory=TieBaCrawler,
            login_factory=BaiduTieBaLogin,
            client_attr="tieba_client",
            create_client_method="create_tieba_client",
            start_url=lambda crawler: crawler.index_url,
            run_flow=_run_flow,
            allowed_domains={"baidu.com", "www.baidu.com", "tieba.baidu.com"},
            bootstrap=_bootstrap,
            pong=_pong,
            create_client_args=_create_client_args,
        )
    )
