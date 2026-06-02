# -*- coding: utf-8 -*-
"""Scrapling Spider bridge for Xiaohongshu."""

from media_platform.scrapling_bridge import (
    ScraplingBridgeSpec,
    dispatch_by_crawler_type,
    run_scrapling_bridge,
)

from .core import XiaoHongShuCrawler
from .login import XiaoHongShuLogin


async def _run_flow(crawler):
    await dispatch_by_crawler_type(
        crawler,
        detail="get_specified_notes",
        creator="get_creators_and_notes",
    )


async def run_scrapling_xhs_crawler() -> None:
    await run_scrapling_bridge(
        ScraplingBridgeSpec(
            platform="xhs",
            spider_name="xhs_scrapling",
            crawler_factory=XiaoHongShuCrawler,
            login_factory=XiaoHongShuLogin,
            client_attr="xhs_client",
            create_client_method="create_xhs_client",
            start_url=lambda crawler: crawler.index_url,
            run_flow=_run_flow,
            allowed_domains={"xiaohongshu.com", "www.xiaohongshu.com"},
        )
    )
